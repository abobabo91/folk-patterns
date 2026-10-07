"""Fetch every museum image URL the site loads directly and list the broken ones.

    python scripts/check_hotlinks.py [--workers 16] [--host upload.wikimedia.org] [--delay 0]

Reads data/objects/*.json (written by build_index.py) and GETs each image URL
that is not on R2, with a browser User-Agent and the live site as Referer. A URL
counts as working when it answers 200 with an image content type. The broken
ones are added to data/hotlink_broken.json, which build_index.py reads to keep
those images on R2; rerun build_index.py afterwards. Prints the result per host.

Wikimedia answers 429 to 16 parallel requests (measured 2026-10-07: 1,151 of
1,172 "broken"), so check it on its own: --host upload.wikimedia.org
--workers 2 --delay 0.5. A 429 is never recorded as broken.
"""
import time
import argparse
import collections
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROKEN = ROOT / "data" / "hotlink_broken.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; folk-patterns link check; https://folk-patterns.vercel.app)",
    "Referer": "https://folk-patterns.vercel.app/",
    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
}


DELAY = 0.0


def check(url):
    time.sleep(DELAY)
    err = ""
    for attempt in range(2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=40) as r:
                ctype = r.headers.get("Content-Type", "")
                body = r.read()
                if r.status == 200 and ctype.startswith("image/") and len(body) > 500:
                    return url, True, len(body), ctype
                return url, False, len(body), f"{r.status} {ctype}"
        except Exception as e:
            err = str(e)[:80]
            if "429" in err:
                return url, None, 0, err
    return url, False, 0, err


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--host", help="only URLs on this host")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds each worker waits before a request")
    args = ap.parse_args()
    global DELAY
    DELAY = args.delay
    urls = set()
    for f in (ROOT / "data" / "objects").glob("*.json"):
        for img in json.loads(f.read_text(encoding="utf-8")).get("images") or []:
            u = img.get("url") or ""
            if u.startswith("https://") and "r2.dev" not in u and (not args.host or u.split("/")[2] == args.host):
                urls.add(u)
    print(f"{len(urls)} hotlinked URLs")
    ok, bad, limited, size = collections.Counter(), collections.Counter(), collections.Counter(), collections.defaultdict(list)
    broken, examples = [], {}
    with ThreadPoolExecutor(args.workers) as pool:
        for i, (url, good, n, info) in enumerate(pool.map(check, sorted(urls)), 1):
            host = url.split("/")[2]
            if good is None:
                limited[host] += 1
            elif good:
                ok[host] += 1
                size[host].append(n)
            else:
                bad[host] += 1
                broken.append(url)
                examples.setdefault(host, f"{info} {url[:90]}")
            if i % 1000 == 0:
                print(f"  {i}/{len(urls)} checked, {len(broken)} broken")
    for host in sorted(set(ok) | set(bad), key=lambda h: -(ok[h] + bad[h])):
        s = sorted(size[host])
        print(f"{host:40s} ok {ok[host]:6d}  broken {bad[host]:4d}  rate-limited {limited[host]:4d}  median {s[len(s) // 2] // 1024 if s else 0} KB")
    for host, ex in examples.items():
        print("  e.g.", host, ex)
    old = set(json.loads(BROKEN.read_text(encoding="utf-8"))) if BROKEN.exists() else set()
    old = {u for u in old if u not in urls}   # a URL checked now counts by this run
    BROKEN.write_text(json.dumps(sorted(old | set(broken)), indent=1), encoding="utf-8")
    print(f"{len(broken)} broken; {BROKEN.relative_to(ROOT)} now lists {len(old | set(broken))}")


if __name__ == "__main__":
    main()
