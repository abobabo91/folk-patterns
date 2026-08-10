"""Walk library/<region>/<country>/<ethnicity>/<art_form>/<tradition>/
and emit index.html grouped by (country -> ethnicity -> art_form -> tradition).

Filter bar at top: toggle art_form categories on/off and toggle 'pattern-first'
(hides pattern_density=0 items).

Usage:
    python scripts/build_gallery.py central-asia
"""
from __future__ import annotations

import argparse
import html
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import LIBRARY_DIR


CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; margin: 0; background: #0e0f12; color: #e6e6e6; }
header { position: sticky; top: 0; background: #14161a; padding: 12px 24px; border-bottom: 1px solid #232629; z-index: 10; }
header h1 { margin: 0 0 8px 0; font-size: 17px; font-weight: 500; letter-spacing: 0.3px; }
.filters { display: flex; flex-wrap: wrap; gap: 6px; font-size: 12px; align-items: center; }
.filters .group-label { color: #6a6d72; margin-right: 4px; text-transform: uppercase; font-size: 10px; letter-spacing: 0.6px; }
.filters button { background: #1a1c20; color: #cdd0d5; border: 1px solid #2c2f34; padding: 5px 10px; border-radius: 14px; cursor: pointer; font-size: 12px; }
.filters button.active { background: #2c4d7a; border-color: #4a75b8; color: #fff; }
.filters button:hover { background: #24272c; }
.filters .divider { color: #2c2f34; margin: 0 4px; }
main { padding: 24px; max-width: 1500px; margin: 0 auto; }
section.country { margin: 40px 0 8px; padding-top: 20px; border-top: 1px solid #2c2f34; }
section.country > h2 { font-size: 22px; margin: 0 0 4px 0; color: #fff; }
section.country > h2 .country-count { font-weight: 400; color: #8a8d92; font-size: 15px; margin-left: 8px; }
section.ethnicity { margin: 24px 0; }
section.ethnicity > h3 { font-size: 13px; font-weight: 600; color: #a8c5ff; text-transform: uppercase; letter-spacing: 0.6px; margin: 8px 0 4px 0; }
section.artform { margin: 8px 0 16px 0; }
section.artform > h4 { font-size: 12px; font-weight: 500; color: #cdd0d5; margin: 8px 0 6px 0; text-transform: uppercase; letter-spacing: 0.5px; }
section.artform > h4 .af-count { color: #6a6d72; font-weight: 400; margin-left: 6px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
.tile { background: #1a1c20; border: 1px solid #232629; border-radius: 6px; overflow: hidden; position: relative; }
.tile a { text-decoration: none; color: inherit; display: block; }
.tile img { width: 100%; height: 200px; object-fit: cover; display: block; background: #0e0f12; }
.tile .caption { padding: 6px 8px; font-size: 11px; color: #a8abaf; line-height: 1.35; min-height: 44px; }
.tile .caption .trad { color: #e6e6e6; margin-bottom: 2px; }
.tile .caption .meta { color: #6a6d72; }
.tile .badge-src { position: absolute; top: 6px; right: 6px; background: rgba(0,0,0,0.65); color: #fff; font-size: 9px; padding: 2px 6px; border-radius: 3px; text-transform: uppercase; letter-spacing: 0.4px; }
.tile .badge-pd { position: absolute; top: 6px; left: 6px; background: rgba(76,110,180,0.85); color: #fff; font-size: 9px; padding: 2px 6px; border-radius: 3px; text-transform: uppercase; letter-spacing: 0.4px; }
.tile.pd-0 .badge-pd { background: rgba(90,90,90,0.85); }
.tile.pd-1 .badge-pd { background: rgba(80,110,140,0.85); }
.tile.pd-2 .badge-pd { background: rgba(60,130,180,0.85); }
.tile.pd-3 .badge-pd { background: rgba(80,180,80,0.9); }
footer { padding: 32px 24px; color: #6a6d72; font-size: 11px; border-top: 1px solid #232629; margin-top: 40px; }
"""

JS = """
const state = {
  hiddenAF: new Set(),
  patternsOnly: false,
};
function toggleAF(af) {
  if (state.hiddenAF.has(af)) state.hiddenAF.delete(af); else state.hiddenAF.add(af);
  document.querySelectorAll(`button[data-af='${af}']`).forEach(b => b.classList.toggle('active'));
  applyFilters();
}
function togglePatternsOnly() {
  state.patternsOnly = !state.patternsOnly;
  document.querySelector('button[data-role=patterns-only]').classList.toggle('active');
  applyFilters();
}
function applyFilters() {
  document.querySelectorAll('section.artform').forEach(s => {
    const af = s.dataset.af;
    s.style.display = state.hiddenAF.has(af) ? 'none' : '';
  });
  document.querySelectorAll('.tile').forEach(t => {
    const pd = parseInt(t.dataset.pd || '0');
    t.style.display = (state.patternsOnly && pd < 2) ? 'none' : '';
  });
}
"""


ART_FORM_ORDER = [
    "textile", "garment", "architectural", "wallpaper", "ceramic",
    "jewelry", "metalwork", "painting-mss", "sculpture", "unclassified",
]
ART_FORM_LABEL = {
    "textile": "Textile",
    "garment": "Garment",
    "architectural": "Architectural",
    "wallpaper": "Wallpaper",
    "ceramic": "Ceramic",
    "jewelry": "Jewelry",
    "metalwork": "Metalwork",
    "painting-mss": "Painting/MSS",
    "sculpture": "Sculpture",
    "unclassified": "Other",
}


def collect_region(region_dir: Path):
    """{country: {ethnicity: {art_form: {tradition: [records]}}}}"""
    tree: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    counts_by_af: dict[str, int] = defaultdict(int)
    for country_dir in sorted(region_dir.iterdir()):
        if not country_dir.is_dir():
            continue
        country = country_dir.name
        for eth_dir in sorted(country_dir.iterdir()):
            if not eth_dir.is_dir():
                continue
            eth = eth_dir.name
            for af_dir in sorted(eth_dir.iterdir()):
                if not af_dir.is_dir():
                    continue
                af = af_dir.name
                for trad_dir in sorted(af_dir.iterdir()):
                    if not trad_dir.is_dir():
                        continue
                    mp = trad_dir / "metadata.json"
                    if not mp.exists():
                        continue
                    recs = json.loads(mp.read_text(encoding="utf-8"))
                    for rec in recs:
                        rec = dict(rec)
                        rec["_country"] = country
                        rec["_ethnicity"] = eth
                        rec["_art_form"] = af
                        rec["_tradition"] = trad_dir.name
                        tree[country][eth][af][trad_dir.name].append(rec)
                        counts_by_af[af] += 1
    return tree, counts_by_af


def render(region: str, tree: dict, counts_by_af: dict) -> str:
    parts: list[str] = []
    parts.append("<!doctype html><html><head><meta charset='utf-8'>")
    parts.append(f"<title>folk-patterns · {html.escape(region)}</title>")
    parts.append(f"<style>{CSS}</style><script>{JS}</script></head><body>")

    total = sum(counts_by_af.values())
    parts.append(f"<header><h1>folk-patterns · {html.escape(region)} · {total} images</h1>")
    parts.append("<div class='filters'>")
    parts.append("<span class='group-label'>show</span>")
    for af in ART_FORM_ORDER:
        if af not in counts_by_af:
            continue
        parts.append(
            f"<button data-af='{af}' class='active' onclick=\"toggleAF('{af}')\">"
            f"{html.escape(ART_FORM_LABEL[af])} · {counts_by_af[af]}</button>"
        )
    parts.append("<span class='divider'>·</span>")
    parts.append("<button data-role='patterns-only' onclick=\"togglePatternsOnly()\">"
                 "Patterns only (density ≥ 2)</button>")
    parts.append("</div></header><main>")

    for country in sorted(tree, key=lambda c: -sum(len(recs) for e in tree[c].values() for af in e.values() for recs in af.values())):
        eths = tree[country]
        c_total = sum(len(recs) for e in eths.values() for af in e.values() for recs in af.values())
        parts.append(
            f"<section class='country'><h2>{html.escape(country)}"
            f"<span class='country-count'>· {c_total} items</span></h2>"
        )
        for eth in eths:
            afs = eths[eth]
            parts.append(f"<section class='ethnicity'><h3>{html.escape(eth)}</h3>")
            for af in ART_FORM_ORDER:
                if af not in afs:
                    continue
                trad_records = afs[af]
                n = sum(len(recs) for recs in trad_records.values())
                parts.append(
                    f"<section class='artform' data-af='{af}'>"
                    f"<h4>{html.escape(ART_FORM_LABEL[af])}"
                    f"<span class='af-count'>· {n}</span></h4><div class='grid'>"
                )
                # flatten traditions into one grid (order by tradition, then within)
                for trad in sorted(trad_records):
                    for r in trad_records[trad]:
                        img_path = r.get("image_path", "").replace("\\", "/")
                        src = r.get("source", "?")
                        obj_url = r.get("object_url") or "#"
                        pd = int(r.get("pattern_density") or 0)
                        date = r.get("date") or r.get("period") or ""
                        place = r.get("place") or r.get("country") or r.get("culture") or ""
                        parts.append(
                            f"<div class='tile pd-{pd}' data-pd='{pd}'>"
                            f"<a href='{html.escape(obj_url)}' target='_blank' rel='noopener'>"
                            f"<img loading='lazy' src='{img_path}' alt=''>"
                            f"<div class='caption'>"
                            f"<div class='trad'>{html.escape(trad)}</div>"
                            f"<div class='meta'>{html.escape(str(date)[:30])} · {html.escape(str(place)[:35])}</div>"
                            f"</div>"
                            f"<div class='badge-src'>{html.escape(src)}</div>"
                            f"<div class='badge-pd'>pd{pd}</div>"
                            f"</a></div>"
                        )
                parts.append("</div></section>")
            parts.append("</section>")
        parts.append("</section>")

    parts.append("<footer>Met + V&amp;A open-access. Rijksmuseum wired but unused for Central Asia (0 hits). Each tile links to source page.</footer>")
    parts.append("</main></body></html>")
    return "".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    args = ap.parse_args()

    region_dir = LIBRARY_DIR / args.region
    tree, counts_by_af = collect_region(region_dir)
    html_str = render(args.region, tree, counts_by_af)
    out_path = region_dir / "index.html"
    out_path.write_text(html_str, encoding="utf-8")
    total = sum(counts_by_af.values())
    print(f"Wrote {out_path}  ({total} images, art-form breakdown: {dict(counts_by_af)})")


if __name__ == "__main__":
    main()
