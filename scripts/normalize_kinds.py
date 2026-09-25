"""Turn the pool's object names — every language and cataloguing habit a museum
has ("dolk", "Dagger with sheath", "kris with sheath", "necklet") — into one
English kind per object and the site's art_form, so assign_pool.py can count
a culture's pool by kind. Works on distinct names, not rows: ~13k names cover
all 114k pool rows.

    python scripts/normalize_kinds.py --sample 250    # one batch, printed, cached
    python scripts/normalize_kinds.py                 # every name not yet cached
    python scripts/normalize_kinds.py --world         # names from world_peoples.py harvest
    python scripts/normalize_kinds.py --compare       # current MODEL vs the cached answers

Cache: data/pool/kinds.json {name: {"kind": ..., "art_form": ...}}.
Raw replies: data/pool/kinds_raw.jsonl. `claude --print`, no tools, no MCP.
Model: claude-haiku-4-5-20251001, ~$0.04 per 250 names. Measured 2026-09-25 with
--compare against claude-sonnet-5 on the same 250 names: 235 same art_form.
The 15 differences are mostly borderline (bag: textile vs household, axe: tool vs
arms), plus a few Haiku errors (rattle -> household). Sonnet cost $0.43 for that
batch because it ignores MAX_THINKING_TOKENS=0.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import DATA_DIR  # noqa: E402

POOL = DATA_DIR / "pool"
CACHE = POOL / "kinds.json"
RAW = POOL / "kinds_raw.jsonl"
MODEL = "claude-haiku-4-5-20251001"
BATCH = 250
ART_FORMS = ["textile", "garment", "jewelry", "ceramic", "metalwork", "arms", "masks-ritual", "sculpture",
             "instruments", "household", "architectural", "painting-mss", "photo", "unclassified"]
_lock = threading.Lock()

PROMPT = """Each line below is the name or title a museum gave one object (any language:
English, Swedish, Dutch, Spanish, Czech, German, French...). For each, give:

kind: what the object IS, in plain English, singular, lower case, 1-3 words, as
  generic as still useful — "dagger" for "dolk", "Dagger with sheath",
  "kris with sheath"; "necklace" for "necklet"; "headrest"; "divination board";
  "ibeji figure" stays specific when the name is a known object type. A photo,
  negative, slide or print is "photograph"; a drawing/watercolour/painting is
  "painting"; a title that is a sentence about a scene is what the picture
  shows as an object ("photograph" / "painting"). Unknown or meaningless -> "?".
art_form: one of {forms}

Answer one JSON object per line, nothing else:
{{"i": <line number>, "kind": "...", "art_form": "..."}}

NAMES
{names}
"""


def _names(world: bool = False) -> collections.Counter:
    c = collections.Counter()
    if world:   # object names from world_peoples.py harvest + local
        for fn in ("bm_objects.jsonl", "local_objects.jsonl"):
          for l in (DATA_DIR / "world" / fn).read_text(encoding="utf-8").splitlines():
            for o in json.loads(l)["objects"]:
                n = (o.get("name") or "").strip()[:120]
                if n:
                    c[n] += 1
        return c
    for l in (POOL / "assigned.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(l)
        if "held" in r["flags"]:
            continue
        n = (r.get("object_name") or r.get("title") or "").strip()[:120]
        if n:
            c[n] += 1
    return c


def _cache() -> dict:
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def _claude(prompt: str) -> tuple[str, dict]:
    d = Path(tempfile.mkdtemp(prefix="kinds-"))
    (d / "empty_mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")
    cmd = [shutil.which("claude") or "claude", "--print", "--no-session-persistence", "--setting-sources", "local",
           "--model", MODEL, "--output-format", "json", "--tools", "",
           "--strict-mcp-config", "--mcp-config", str(d / "empty_mcp.json")]
    env = {**os.environ, "MAX_THINKING_TOKENS": "0"}
    res = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                         timeout=900, cwd=d, env=env)
    try:
        ev = json.loads(res.stdout)
    except json.JSONDecodeError:
        return "", {"error": (res.stdout + res.stderr)[-500:]}
    return ev.get("result") or "", ev


def run_batch(names: list[str]) -> dict:
    text, ev = _claude(PROMPT.format(forms=", ".join(ART_FORMS),
                                     names="\n".join(f"{i}. {n}" for i, n in enumerate(names))))
    out = {}
    for line in text.splitlines():
        line = line.strip().strip(",")
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
            n = names[int(row["i"])]
        except (json.JSONDecodeError, KeyError, ValueError, IndexError):
            continue
        af = row.get("art_form") if row.get("art_form") in ART_FORMS else "unclassified"
        out[n] = {"kind": (row.get("kind") or "?").strip().lower(), "art_form": af}
    with _lock:
        with open(RAW, "a", encoding="utf-8") as f:
            f.write(json.dumps({"n": len(names), "parsed": len(out), "cost_usd": ev.get("total_cost_usd"),
                                "seconds": (ev.get("duration_ms") or 0) / 1000, "error": ev.get("error"),
                                "result": text}, ensure_ascii=False) + "\n")
        cache = _cache()
        cache.update(out)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"batch {len(names)}: parsed {len(out)}, ${ev.get('total_cost_usd') or 0:.3f}, "
          f"{(ev.get('duration_ms') or 0) / 1000:.0f}s", flush=True)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--world", action="store_true", help="names from data/world/bm_objects.jsonl")
    ap.add_argument("--compare", action="store_true", help="re-run the cached names, print agreement; cache untouched")
    a = ap.parse_args()
    if a.compare:
        cache = _cache()
        old = dict(cache)
        pick = list(old)[:BATCH]
        text, ev = _claude(PROMPT.format(forms=", ".join(ART_FORMS), names="\n".join(f"{i}. {n}" for i, n in enumerate(pick))))
        new = {}
        for line in text.splitlines():
            try:
                row = json.loads(line.strip().strip(","))
                new[pick[int(row["i"])]] = row
            except (json.JSONDecodeError, KeyError, ValueError, IndexError):
                pass
        same = [n for n in pick if n in new and new[n].get("art_form") == old[n]["art_form"]]
        print(f"{MODEL}: parsed {len(new)}/{len(pick)}, art_form agrees on {len(same)}, ${ev.get('total_cost_usd') or 0:.3f}")
        for n in pick:
            if n in new and new[n].get("art_form") != old[n]["art_form"]:
                print(f"  {n[:50]:50s} cached {old[n]['kind']}/{old[n]['art_form']:14s} now {new[n].get('kind')}/{new[n].get('art_form')}")
        sys.exit()
    names = _names(a.world)
    cache = _cache()
    todo = [n for n, _ in names.most_common() if n not in cache]
    print(f"{len(names)} distinct names, {len(todo)} not cached")
    if a.sample:
        random.seed(1)
        pick = todo[:a.sample // 2] + random.sample(todo[a.sample // 2:], a.sample - a.sample // 2)
        res = run_batch(pick)
        for n in pick:
            v = res.get(n)
            print(f"  {n[:60]:60s} -> {v['kind'] if v else 'MISSING':25s} {v['art_form'] if v else ''}")
    else:
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        with ThreadPoolExecutor(a.workers) as ex:
            list(ex.map(run_batch, batches))
