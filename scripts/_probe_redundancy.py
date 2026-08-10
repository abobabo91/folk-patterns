"""Download top-9 thumbnails of suspicious shards for visual inspection.
Throwaway diagnostic — not part of the pipeline."""
import json, pathlib, shutil, urllib.request, re

TARGETS = [
    ('central-asia__china-xinjiang__uyghur', 'architectural'),
    ('central-asia__afghanistan__uzbek-afghanistan', 'photo'),
    ('central-asia__tajikistan__pamiri', 'photo'),
    ('southeast-asia__indonesia__toraja', 'photo'),
    ('central-asia__uzbekistan__bukharan-jew', 'photo'),
    ('southeast-asia__indonesia__batak', 'photo'),
    ('southeast-asia__indonesia__batak', 'unclassified'),
    ('southeast-asia__indonesia__javanese', 'architectural'),
]

root = pathlib.Path(r'C:\Users\abele\Desktop\github\folk-patterns')
out = root / 'data' / 'probes' / 'redundancy_check'
if out.exists():
    shutil.rmtree(out)
out.mkdir(parents=True)

def safe_name(s):
    return re.sub(r'[<>:"/\\|?*]', '_', s or '')[:60]

for key, af in TARGETS:
    sh = json.load(open(root / 'data' / 'ethnicities' / f'{key}.json', 'r', encoding='utf-8'))
    items = sh.get('art_form_buckets', {}).get(af, [])[:9]
    sub = out / f'{key}__{af}'
    sub.mkdir(exist_ok=True)
    for i, it in enumerate(items):
        url = it.get('image')
        title = safe_name(it.get('title') or '')
        dst = sub / f'{i+1:02d}_{it.get("source","?")}_{title}.jpg'
        if url and url.startswith('/library/'):
            local = root / 'library' / url[len('/library/'):]
            try:
                shutil.copy(local, dst)
            except Exception as e:
                print(f'  skip {i}: {e}')
        elif url:
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'folk-patterns/0.1'})
                with urllib.request.urlopen(req, timeout=20) as r:
                    dst.write_bytes(r.read())
            except Exception as e:
                print(f'  dl skip {i}: {e}')
    print(f'{sub.name}: {len(list(sub.iterdir()))} imgs')
