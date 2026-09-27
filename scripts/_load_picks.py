"""Write one people's vetted picks (data/world/picks/<key>.json, from
`world_peoples.py pick`) into the library as ordinary records, instead of a
scrape. Used by `add_culture.py --from-picks <key>`.

Every kept object (QUALITY >= 3) is written, not only the five featured ones;
`cultural.pick_quality` and `cultural.pick_featured` carry the ranking. The
pick's verdict is written as the record's vetting verdict (vision_* fields,
the same ones vet_images.py writes), so build_index treats it as judged.
Objects already in the library are skipped.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from folk_patterns.util import append_metadata, download_image, library_path, RateLimitedClient  # noqa: E402

PICKS = ROOT / "data" / "world" / "picks"
JUDGE = "pick:claude-sonnet-5"


def _record(o: dict, cultural: dict, bm_client, http: RateLimitedClient) -> dict | None:
    if o["source"] == "bm":
        from folk_patterns.museums.british_museum import fetch_detail, _to_canonical
        d = fetch_detail(bm_client, o["id"])
        return d and _to_canonical(o["id"], d, cultural)
    if o["source"] == "met":
        from folk_patterns.schema import from_met
        obj = http.get_json(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{o['id']}")
        r = from_met(obj, cultural)
        r["images"] = [i for i in r["images"] if i.get("role") == "primary"][:1]
        return r
    if o["source"] == "europeana":
        from folk_patterns.museums.europeana import _to_canonical
        sys.path.insert(0, str(ROOT / "scripts"))
        from world_peoples import _eu_index
        it = _eu_index().get(o["id"])
        return it and _to_canonical(it, cultural)
    from folk_patterns.museums.cleveland import _to_canonical
    return _to_canonical(http.get_json(f"https://openaccess-api.clevelandart.org/api/artworks/{o['id']}")["data"], cultural)


def load(key: str, region: str, country: str, ethnicity: str) -> int:
    from folk_patterns.museums.british_museum import _client, _in_library
    ranked = json.loads((PICKS / f"{key}.json").read_text(encoding="utf-8"))["ranked"]
    bm_client = _client() if os.environ.get("BM_CDP_URL") else None
    saved = 0
    with RateLimitedClient(min_interval_s=0.3) as http:
        for af, objs in ranked.items():
            for o in objs:
                if o["source"] == "bm" and (bm_client is None or _in_library(o["id"])):
                    continue
                cultural = {"region": region, "country": country, "ethnicity": ethnicity, "tradition": ethnicity,
                            "art_form": af, "pattern_density": 0,
                            "vision_vetted": True, "vision_by": JUDGE, "vision_reason": o.get("reason"),
                            "vision_confidence": o.get("confidence"), "vision_image": o.get("image"),
                            "vision_era": o.get("era"), "art_form_vision": af,
                            "pick_quality": o.get("quality"), "pick_featured": o.get("featured")}
                try:
                    rec = _record(o, cultural, bm_client, http)
                except Exception as e:
                    print(f"  ! {o['source']} {o['id']}: {type(e).__name__} {e}", flush=True)
                    continue
                if not rec or not rec["images"]:
                    print(f"  ! {o['source']} {o['id']}: no record or image", flush=True)
                    continue
                dest = library_path(region, country, ethnicity, af, ethnicity)
                prefix = {"bm": "bm", "met": "met", "cleveland": "cle", "europeana": "eu"}[o["source"]]
                dst = dest / "images" / f"{prefix}_{re.sub(r'[^A-Za-z0-9]+', '_', o['id']).strip('_')[-80:]}.jpg"
                sha = None
                for url in filter(None, [rec["images"][0]["url"], rec["images"][0].get("fallback_url")]):
                    try:   # Europeana: the museum's full image first, its cached thumbnail if that fails
                        sha, size = download_image(bm_client if o["source"] == "bm" else http, url, dst)
                        break
                    except Exception as e:
                        print(f"  ! {o['source']} {o['id']} image: {e}", flush=True)
                if sha is None:
                    continue
                rec["images"][0].update(sha256=sha, bytes=size, local_path=str(dst.relative_to(dest.parents[5])))
                append_metadata(dest, rec)
                saved += 1
    print(f"  {ethnicity}: {saved} records written from {key}", flush=True)
    return saved
