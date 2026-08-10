"""Quick sanity check: do the Met + V&A search endpoints return anything sensible
for our seed Uzbek tradition names? Prints counts + a couple of sample records."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from folk_patterns.util import RateLimitedClient
from folk_patterns.museums import met, va


TERMS = ["suzani", "ikat", "adras", "atlas silk", "Bukhara embroidery", "chapan", "muqarnas", "Turkmen carpet"]


def main() -> None:
    with RateLimitedClient(min_interval_s=0.4) as c:
        print("=== Met Open Access ===")
        for t in TERMS:
            ids = met.search_ids(c, t)
            print(f"  {t!r:30} -> {len(ids)} objectIDs")
            if ids:
                obj = met.fetch_object(c, ids[0])
                print(
                    f"      first: {obj.get('title', '?')[:50]!r}  "
                    f"culture={obj.get('culture')!r}  country={obj.get('country')!r}  "
                    f"has_image={bool(obj.get('primaryImage'))}"
                )
        print()
        print("=== Victoria & Albert ===")
        for t in TERMS:
            recs = va.search(c, t, page_size=15, max_pages=1)
            print(f"  {t!r:30} -> {len(recs)} records")
            if recs:
                r = recs[0]
                place = r.get("_primaryPlace")
                print(
                    f"      first: {(r.get('_primaryTitle') or '?')[:50]!r}  "
                    f"place={place!r}  has_img={bool(r.get('_primaryImageId'))}"
                )


if __name__ == "__main__":
    main()
