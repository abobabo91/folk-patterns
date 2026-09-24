"""Cloud-side helper for a vetting batch. Standard library only (with
scripts/vet_judge.py), so a cloud session runs it with no setup script; with
Pillow installed, `fetch` also downscales images to 1024 px.

    python scripts/cloud_vet_batch.py fetch   pilot   # images + prompts into work/
    python scripts/cloud_vet_batch.py judge   pilot [N]  # one bare `claude --print` per pending record (at most N)
    python scripts/cloud_vet_batch.py status  pilot   # fetched / answered / pending
    python scripts/cloud_vet_batch.py collect pilot   # replies -> data/vet_verdicts/pilot.jsonl

`judge` makes the same call as the local vetter (vet_judge.judge): the fixed
rules as a cached system prompt, the record's text and its image inline as the
user message. Replies go to work/replies/<key>.txt; every raw result, with its
reported cost and token usage, is appended to work/judge_<batch>.jsonl.
The procedure is docs/cloud-vetting.md.

`collect` never parses the replies — that happens locally in
scripts/apply_vet_verdicts.py with the same parser the local vetter uses.
"""
from __future__ import annotations

import json
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vet_judge  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
UA = "folk-patterns/0.1 (research atlas)"
MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF")
# Wikimedia answers 429 at 6 parallel downloads (measured 2026-09-24), so
# requests to one host are spaced out; different hosts still run in parallel.
HOST_INTERVAL = 1.0
# media.britishmuseum.org serves its leaf certificate without the intermediate
# (measured 2026-09-24). Browsers fetch it via AIA; Python does not, and the
# cloud sandbox cannot reach crt.sectigo.com. So the missing intermediates ship
# in the repo and are added to the default trust store.
SSL_CTX = ssl.create_default_context()
SSL_CTX.load_verify_locations(str(Path(__file__).with_name("certs") / "extra-intermediates.pem"))
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
                with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
                    data = r.read()
                if len(data) > 1000 and data.startswith(MAGIC):
                    dst.write_bytes(vet_judge.downscale(data))
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


JUDGE_WORKERS = 3
_log_lock = threading.Lock()


def _judge_one(key: str, name: str) -> str:
    record = (WORK / "prompts" / f"{key}.txt").read_text(encoding="utf-8")
    if record.startswith("Read the image at path"):
        raise SystemExit(f"{name} was exported before the judge prompt was split into "
                         "system prompt + record; re-export it with export_vet_batch.py")

    def log(attempt, seconds, result, err):
        with _log_lock, open(WORK / f"judge_{name}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "attempt": attempt, "seconds": seconds,
                                "cost_usd": (result or {}).get("total_cost_usd"),
                                "usage": (result or {}).get("usage"),
                                "result": (result or {}).get("result") or "",
                                "stderr": err[-500:]}, ensure_ascii=False) + "\n")

    reply, error = vet_judge.judge(record, (WORK / "img" / f"{key}.jpg").read_bytes(),
                                   on_attempt=log)
    if reply:
        (WORK / "replies" / f"{key}.txt").write_text(reply, encoding="utf-8")
        return "ok"
    return f"failed: {error}"


def judge(name: str, limit: int = 0) -> None:
    status(name)
    keys = [k for k in (WORK / f"pending_{name}.txt").read_text(encoding="utf-8").splitlines() if k]
    if limit:
        keys = keys[:limit]
    (WORK / "replies").mkdir(parents=True, exist_ok=True)
    done = failed = 0
    t0 = time.time()
    with ThreadPoolExecutor(JUDGE_WORKERS) as ex:
        futures = {ex.submit(_judge_one, k, name): k for k in keys}
        for fut in as_completed(futures):
            r = fut.result()
            done += 1
            if r != "ok":
                failed += 1
                print(f"  {futures[fut]}: {r}")
            if done % 10 == 0 or done == len(keys):
                print(f"judged {done}/{len(keys)} ({failed} failed, {time.time() - t0:.0f}s)")
    costs = [json.loads(l).get("cost_usd") or 0
             for l in (WORK / f"judge_{name}.jsonl").read_text(encoding="utf-8").splitlines()]
    print(f"reported cost of all calls so far: ${sum(costs):.2f} over {len(costs)} calls")


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
    sys.stdout.reconfigure(line_buffering=True)
    if len(sys.argv) not in (3, 4) or sys.argv[1] not in ("fetch", "judge", "status", "collect"):
        raise SystemExit(__doc__)
    if sys.argv[1] == "judge":
        judge(sys.argv[2], int(sys.argv[3]) if len(sys.argv) == 4 else 0)
    else:
        {"fetch": fetch, "status": status, "collect": collect}[sys.argv[1]](sys.argv[2])
