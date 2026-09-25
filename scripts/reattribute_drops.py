"""Recover vetter drops that belong to a different atlas culture.

The vetter answers one question: does this object belong to the culture it is
filed under? A Shan cloth filed under Bamar, or Hassan Fathy's New Gourna
mosque filed under Nubian, is a NO there, although the object is fine. This
script finds those and moves them.

    python scripts/reattribute_drops.py propose [--limit N]   # text pass
    python scripts/reattribute_drops.py rejudge [--limit N]   # image pass
    python scripts/reattribute_drops.py apply                 # write to library
    python scripts/reattribute_drops.py report                # counts per people

1. propose — `claude --print`, text only, 40 dropped records per call: from
   the museum's metadata and the vetter's own reason, name the people who
   made the object (free text, or none) and the atlas culture that is that
   people (or none). -> data/reattribution/proposals.jsonl
2. rejudge — every proposal with an atlas culture goes back through the
   normal image judge (vet_judge.judge), now claiming the proposed culture.
   Only a YES moves the record. -> data/reattribution/verdicts.jsonl
3. apply — writes cultural.reattribution {to, people, belongs, art_form,
   image, era, reason} onto the library record; build_index.py files a
   dropped record with belongs=true under `to`.

`report` counts the peoples named in proposals that have no atlas culture —
the list to read before adding a new culture to the atlas.
Raw replies of both passes are appended to data/reattribution/raw.jsonl.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import vet_judge  # noqa: E402
from vet_images import _first_local_path, _record_text, parse_reply  # noqa: E402
from folk_patterns.util import LIBRARY_DIR, DATA_DIR  # noqa: E402

OUT = DATA_DIR / "reattribution"
PROPOSALS = OUT / "proposals.jsonl"
VERDICTS = OUT / "verdicts.jsonl"
RAW = OUT / "raw.jsonl"
CHUNK = 40
_lock = threading.Lock()

PROPOSE_PROMPT = """\
Each record below is a museum object that an image vetter REJECTED for the
culture it was filed under. The vetter saw the picture; its reason is given.
For each record decide who actually made the object, from the metadata and
the reason.

people: the specific people or culture that made or built it ("Shan",
  "Armenian", "Sumba", "Chinese", "English"), or null when there is no made
  object of a people at all — a natural-history specimen, a scanned document,
  a floor plan, a stock photo of modern life, a place-name collision with
  nothing cultural left (San Francisco postcards).
atlas: the key from the ATLAS list below that IS that people — same people,
  not a neighbour or a related group — else null. Pick it only when the
  record makes that attribution clear; a guess is null. Outsiders' pictures
  OF a people (a European print of an Egyptian scene) are people "English"
  etc., atlas null.

ATLAS
{atlas}

Answer with one JSON object per line and nothing else:
{{"id": "<id>", "people": "<people or null>", "atlas": "<key or null>"}}

RECORDS
{records}
"""


def _atlas() -> dict[str, str]:
    """{key: "Ethnicity (Country)"} for every culture on the map."""
    out = {}
    for p in sorted((DATA_DIR / "ethnicities").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out[d["key"]] = f"{d['ethnicity']} ({d['country']})"
    return out


def _name_match(people: str | None, atlas: dict[str, str]) -> str | None:
    """The atlas key whose ethnicity name IS `people` ("Batak"), else None.
    The model names the people but sometimes leaves `atlas` null (measured
    2026-09-24: two Batak wedding jackets); an exact name is not a guess."""
    if not people:
        return None
    hits = [k for k, v in atlas.items() if v.split(" (")[0].lower() == people.strip().lower()]
    return hits[0] if len(hits) == 1 else None


def _drops() -> list[tuple[Path, dict]]:
    out = []
    for mp in sorted(LIBRARY_DIR.glob("*/*/*/*/*/metadata.json")):
        for r in json.loads(mp.read_text(encoding="utf-8")):
            if (r.get("cultural") or {}).get("vision_vetted") is False:
                out.append((mp, r))
    return out


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _append(p: Path, row: dict) -> None:
    with _lock:
        OUT.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _claude_text(prompt: str) -> tuple[str, dict]:
    d = Path(tempfile.mkdtemp(prefix="reattr-"))
    (d / "empty_mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")
    cmd = [shutil.which("claude") or "claude", "--print", "--no-session-persistence",
           "--setting-sources", "local", "--model", vet_judge.MODEL,
           "--output-format", "json", "--tools", "",
           "--strict-mcp-config", "--mcp-config", str(d / "empty_mcp.json")]
    res = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                         encoding="utf-8", timeout=600, cwd=d)
    try:
        ev = json.loads(res.stdout)
    except json.JSONDecodeError:
        return "", {"error": (res.stdout + res.stderr)[-500:]}
    return ev.get("result") or "", ev


def propose(limit: int) -> None:
    atlas = _atlas()
    done = {r["id"] for r in _read_jsonl(PROPOSALS)}
    todo = [r for _, r in _drops() if r.get("id") not in done]
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} dropped records to propose for ({len(done)} already done)")
    atlas_txt = "\n".join(f"{k}: {v}" for k, v in atlas.items())
    chunks = [todo[i:i + CHUNK] for i in range(0, len(todo), CHUNK)]

    def run(chunk: list[dict]) -> int:
        lines = []
        for r in chunk:
            cul = r.get("cultural") or {}
            title, desc, place = _record_text(r)
            lines.append(json.dumps({
                "id": r["id"], "filed_under": f"{cul.get('ethnicity')} ({cul.get('country')})",
                "title": title[:160], "description": desc[:400], "place": place[:120],
                "vetter_reason": (cul.get("vision_reason") or "")[:400]}, ensure_ascii=False))
        text, ev = _claude_text(PROPOSE_PROMPT.format(atlas=atlas_txt, records="\n".join(lines)))
        _append(RAW, {"stage": "propose", "ids": [r["id"] for r in chunk], "result": text,
                      "cost_usd": ev.get("total_cost_usd"), "error": ev.get("error")})
        ids = {r["id"] for r in chunk}
        n = 0
        for line in text.splitlines():
            line = line.strip().strip(",")
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("id") not in ids:
                continue
            for k in ("people", "atlas"):
                if row.get(k) in ("null", "", "None"):
                    row[k] = None
            if row.get("atlas") and row["atlas"] not in atlas:
                row["atlas_invalid"], row["atlas"] = row["atlas"], None
            if not row.get("atlas"):
                row["atlas"] = _name_match(row.get("people"), atlas)
            _append(PROPOSALS, row)
            n += 1
        return n

    with ThreadPoolExecutor(3) as ex:
        futs = [ex.submit(run, c) for c in chunks]
        for i, f in enumerate(as_completed(futs), 1):
            print(f"chunk {i}/{len(chunks)}: {f.result()} proposals")


def rejudge(limit: int) -> None:
    atlas = _atlas()
    by_id = {r["id"]: (mp, r) for mp, r in _drops()}
    done = {r["id"] for r in _read_jsonl(VERDICTS)}
    todo = [p for p in _read_jsonl(PROPOSALS)
            if p.get("atlas") and p["id"] not in done and p["id"] in by_id]
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} proposals to re-judge")

    def run(p: dict) -> str:
        _, r = by_id[p["id"]]
        img = _first_local_path(r)
        if not img:
            return "no-image"
        d = json.loads((DATA_DIR / "ethnicities" / f"{p['atlas']}.json").read_text(encoding="utf-8"))
        title, desc, place = _record_text(r)
        record = vet_judge.build_record(d["ethnicity"], d["country"],
                                        (r.get("cultural") or {}).get("art_form") or "unclassified",
                                        title=title, desc=desc, place=place)
        reply, error = vet_judge.judge(record, img.read_bytes(),
                                       on_attempt=lambda a, s, res, err: _append(RAW, {
                                           "stage": "rejudge", "id": p["id"], "attempt": a,
                                           "seconds": s, "cost_usd": (res or {}).get("total_cost_usd"),
                                           "result": (res or {}).get("result") or "",
                                           "stderr": err[-300:]}))
        if not reply:
            return f"failed: {error}"
        belongs, af, reason, conf, image, era = parse_reply(reply)
        _append(VERDICTS, {"id": p["id"], "to": p["atlas"], "people": p.get("people"),
                           "belongs": belongs, "art_form": af, "image": image, "era": era,
                           "confidence": conf, "reason": reason})
        return "YES" if belongs else "NO"

    c = Counter()
    with ThreadPoolExecutor(3) as ex:
        futs = {ex.submit(run, p): p for p in todo}
        for i, f in enumerate(as_completed(futs), 1):
            c[f.result()] += 1
            if i % 10 == 0 or i == len(todo):
                print(f"{i}/{len(todo)} {dict(c)}")


def apply() -> None:
    verdicts = {v["id"]: v for v in _read_jsonl(VERDICTS)}
    changed = moved = 0
    for mp in sorted(LIBRARY_DIR.glob("*/*/*/*/*/metadata.json")):
        recs = json.loads(mp.read_text(encoding="utf-8"))
        dirty = False
        for r in recs:
            v = verdicts.get(r.get("id"))
            if not v:
                continue
            new = {k: v.get(k) for k in ("to", "people", "belongs", "art_form", "image", "era", "reason")}
            cul = r.setdefault("cultural", {})
            if cul.get("reattribution") != new:
                cul["reattribution"] = new
                dirty = True
                changed += 1
            moved += bool(v.get("belongs"))
        if dirty:
            mp.write_text(json.dumps(recs, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(verdicts)} verdicts, {moved} move to another culture, {changed} records updated")


def report() -> None:
    props = _read_jsonl(PROPOSALS)
    verdicts = {v["id"]: v for v in _read_jsonl(VERDICTS)}
    print(f"{len(props)} proposals: {sum(1 for p in props if p.get('atlas'))} name an atlas culture, "
          f"{sum(1 for p in props if not p.get('people'))} name no people")
    yes = Counter(v["to"] for v in verdicts.values() if v.get("belongs"))
    no = Counter(v["to"] for v in verdicts.values() if v.get("belongs") is False)
    print("re-judged, by target culture (YES / NO):")
    for k in sorted(set(yes) | set(no), key=lambda k: -yes[k]):
        print(f"  {k:45s} {yes[k]:4d} {no[k]:4d}")
    off = Counter(p["people"] for p in props if p.get("people") and not p.get("atlas"))
    print("peoples with no atlas culture (top 40):")
    for people, n in off.most_common(40):
        print(f"  {n:4d}  {people}")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["propose", "rejudge", "apply", "report"])
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    {"propose": lambda: propose(a.limit), "rejudge": lambda: rejudge(a.limit),
     "apply": apply, "report": report}[a.stage]()
