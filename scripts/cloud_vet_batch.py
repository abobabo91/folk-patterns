"""Cloud-side helper for a vetting batch. Standard library only, so a cloud
session runs it with no setup script.

    python scripts/cloud_vet_batch.py fetch   pilot   # images + prompts into work/
    python scripts/cloud_vet_batch.py status  pilot   # fetched / answered / pending
    python scripts/cloud_vet_batch.py collect pilot   # replies -> data/vet_verdicts/pilot.jsonl

The judging itself is done by Sonnet subagents between `fetch` and `collect`:
each reads work/prompts/<key>.txt, does exactly what it says (the prompt
points at work/img/<key>.jpg), and writes its reply verbatim to
work/replies/<key>.txt. The procedure is docs/cloud-vetting.md.

`collect` never parses the replies — that happens locally in
scripts/apply_vet_verdicts.py with the same parser the local vetter uses.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
UA = "folk-patterns/0.1 (research atlas)"
MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF")
# Wikimedia answers 429 at 6 parallel downloads (measured 2026-09-24), so
# requests to one host are spaced out; different hosts still run in parallel.
HOST_INTERVAL = 1.0
_host_lock: dict[str, threading.Lock] = {}
_host_last: dict[str, float] = {}
_locks_guard = threading.Lock()


def _wait_for_host(url: str) -> None:
    host = urllib.parse.urlparse(url).netloc
    with _locks_guard:
        lock = _host_lock.setdefault(host, threading.Lock())
    with lock:
        wait = HOST_INTERVAL - (time.time() - _host_last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _host_last[host] = time.time()


def _rows(name: str) -> list[dict]:
    p = ROOT / "data" / "vet_batches" / f"{name}.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _fetch_one(row: dict) -> tuple[str, str]:
    dst = ROOT / row["image_path"]
    if dst.exists() and dst.stat().st_size > 1000:
        return row["key"], "ok"
    last = "no-url"
    for url in row["urls"]:
        for attempt in range(4):
            _wait_for_host(url)
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": UA, "Accept": "image/*,*/*;q=0.8",
                    "Referer": "https://commons.wikimedia.org/"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
                if len(data) > 1000 and data.startswith(MAGIC):
                    dst.write_bytes(data)
                    return row["key"], "ok"
                last = f"not-an-image ({len(data)} bytes) from {url[:80]}"
                break
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code} from {url[:80]}"
                if e.code == 429:
                    time.sleep(10 * (attempt + 1))
                    continue
                if e.code >= 500:
                    time.sleep(3)
                    continue
                break  # 403/404: this URL will not work, try the next one
            except Exception as e:  # timeout / DNS / reset: retry, then next URL
                last = f"{type(e).__name__}: {str(e)[:100]} from {url[:80]}"
                time.sleep(2 * (attempt + 1))
    return row["key"], last


def fetch(name: str) -> None:
    rows = _rows(name)
    (WORK / "img").mkdir(parents=True, exist_ok=True)
    (WORK / "prompts").mkdir(parents=True, exist_ok=True)
    (WORK / "replies").mkdir(parents=True, exist_ok=True)
    for row in rows:
        (WORK / "prompts" / f"{row['key']}.txt").write_text(row["prompt"], encoding="utf-8")
    with ThreadPoolExecutor(6) as ex:
        results = dict(ex.map(_fetch_one, rows))
    report = WORK / f"fetch_{name}.json"
    report.write_text(json.dumps(results, indent=1), encoding="utf-8")
    bad = {k: v for k, v in results.items() if v != "ok"}
    print(f"fetched {len(rows) - len(bad)}/{len(rows)}; failures in {report.relative_to(ROOT)}")
    for k, why in list(bad.items())[:20]:
        print(f"  {k}: {why}")


def status(name: str) -> None:
    rows = _rows(name)
    fetched = sum((ROOT / r["image_path"]).exists() for r in rows)
    answered = [r for r in rows if (WORK / "replies" / f"{r['key']}.txt").exists()]
    print(f"{name}: {len(rows)} records, {fetched} images fetched, "
          f"{len(answered)} answered, {len(rows) - len(answered)} pending")
    pending = [r["key"] for r in rows
               if not (WORK / "replies" / f"{r['key']}.txt").exists()
               and (ROOT / r["image_path"]).exists()]
    (WORK / f"pending_{name}.txt").write_text("\n".join(pending), encoding="utf-8")
    print(f"pending keys with an image: {len(pending)} -> work/pending_{name}.txt")


def collect(name: str) -> None:
    rows = _rows(name)
    fetch_report = WORK / f"fetch_{name}.json"
    fetched = json.loads(fetch_report.read_text(encoding="utf-8")) if fetch_report.exists() else {}
    out_dir = ROOT / "data" / "vet_verdicts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.jsonl"
    n_reply = n_fail = n_pending = 0
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            base = {"id": r["id"], "key": r["key"], "meta": r["meta"]}
            rp = WORK / "replies" / f"{r['key']}.txt"
            if rp.exists():
                f.write(json.dumps({**base, "reply": rp.read_text(encoding="utf-8")},
                                   ensure_ascii=False) + "\n")
                n_reply += 1
            elif fetched.get(r["key"], "ok") != "ok":
                f.write(json.dumps({**base, "error": "download-failed: " + fetched[r["key"]]},
                                   ensure_ascii=False) + "\n")
                n_fail += 1
            else:
                n_pending += 1
    print(f"wrote {out.relative_to(ROOT)}: {n_reply} replies, {n_fail} download failures, "
          f"{n_pending} still pending (not written)")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("fetch", "status", "collect"):
        raise SystemExit(__doc__)
    {"fetch": fetch, "status": status, "collect": collect}[sys.argv[1]](sys.argv[2])
