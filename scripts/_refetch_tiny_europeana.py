"""Re-fetch existing tiny (<30KB) Europeana images at full resolution."""
from __future__ import annotations
import json, sys, hashlib, time
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"
MAX_TO_TRY = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
TIMEOUT_S = 8.0  # short — we've got hundreds to try, can't wait 30s each

def _first(x):
    if isinstance(x, list) and x: return x[0]
    return x

# Standard headers so hotlink URLs don't 403 us
client = httpx.Client(timeout=TIMEOUT_S, follow_redirects=True, headers={
    "User-Agent": "folk-patterns/1.0 (educational atlas; https://folk-patterns.example.org)",
    "Accept": "image/*,*/*;q=0.8",
})

upgraded = 0
skipped_same = 0
skipped_smaller = 0
skipped_no_url = 0
failed_http = 0
failed_exc = 0
total_bytes_gained = 0

tiny_records = []
for meta in LIB.rglob("metadata.json"):
    try: recs = json.loads(meta.read_text(encoding="utf-8"))
    except: continue
    for rec_idx, rec in enumerate(recs):
        if (rec.get("source") or {}).get("museum") != "europeana": continue
        for img_idx, img in enumerate(rec.get("images") or []):
            b = img.get("bytes")
            if b is None or b >= 30000: continue
            tiny_records.append((meta, rec_idx, img_idx))

print(f"Tiny records to try: {len(tiny_records)} (cap {MAX_TO_TRY})", flush=True)

start = time.time()
tried = 0
for meta, rec_idx, img_idx in tiny_records[:MAX_TO_TRY]:
    tried += 1
    if tried % 25 == 0:
        elapsed = time.time() - start
        rate = tried / elapsed if elapsed > 0 else 0
        print(f"  [{tried}/{len(tiny_records)}] elapsed={elapsed:.0f}s rate={rate:.1f}/s upgraded={upgraded} failed={failed_http + failed_exc}", flush=True)
    try:
        recs = json.loads(meta.read_text(encoding="utf-8"))
    except:
        continue
    if rec_idx >= len(recs): continue
    rec = recs[rec_idx]
    imgs = rec.get("images") or []
    if img_idx >= len(imgs): continue
    img = imgs[img_idx]
    b = img.get("bytes")
    if b is None or b >= 30000: continue

    raw = rec.get("raw") or {}
    full_url = _first(raw.get("edmIsShownBy"))
    preview_url = _first(raw.get("edmPreview"))
    current_url = img.get("url") or ""
    candidate = full_url if (full_url and full_url != current_url) else None
    if not candidate:
        skipped_no_url += 1
        continue

    # figure out local path
    lp = img.get("local_path") or ""
    fname = Path(lp).name
    if not fname:
        skipped_no_url += 1
        continue
    local_path = meta.parent / "images" / fname

    try:
        r = client.get(candidate)
        if r.status_code != 200:
            failed_http += 1
            continue
        data = r.content
        if len(data) <= b + 5000:
            # not appreciably bigger
            skipped_smaller += 1
            continue
        # Save
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
        new_sha = hashlib.sha256(data).hexdigest()
        new_bytes = len(data)
        total_bytes_gained += (new_bytes - b)
        img["url"] = candidate
        img["sha256"] = new_sha
        img["bytes"] = new_bytes
        upgraded += 1
        meta.write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
    except httpx.TimeoutException:
        failed_http += 1
    except Exception:
        failed_exc += 1

client.close()
print(f"\n== DONE ==")
print(f"Upgraded: {upgraded}  bytes gained: {total_bytes_gained/1024/1024:.1f} MB")
print(f"Skipped (no better URL/not larger): {skipped_no_url + skipped_smaller + skipped_same}")
print(f"Failed HTTP: {failed_http}")
print(f"Failed exc: {failed_exc}")
