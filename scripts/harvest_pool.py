"""Harvest metadata only — no images, no vetting — for everything each source
holds on the atlas cultures, so a culture's gallery can be picked for variety
before anything is downloaded (docs/source-census.md says how big the pools
are; a random slice of one is mostly repeats: 58 of 60 BM Zulu records were
photographic prints).

    python scripts/harvest_pool.py bm          # BM "Ethnic group" facet list pages (needs BM_CDP_URL)
    python scripts/harvest_pool.py si          # Smithsonian, culture-tagged NMNH Anthropology
    python scripts/harvest_pool.py europeana   # ethnographic providers only
    python scripts/harvest_pool.py va          # V&A by place id (no people named — assignment later)
    python scripts/harvest_pool.py cleveland   # from the department dump
    python scripts/harvest_pool.py met         # from the open-access CSV in .cache/
    python scripts/harvest_pool.py report      # rows per source / per query

One row per source object in data/pool/<source>.jsonl:
  {source, id, query, people, object_name, title, material, date, place,
   provider, image, url}
`query` is what was searched (a BM facet name, a culture name, a V&A place);
`people` is the source's own people tag when it has one. Which atlas culture a
row belongs to is decided later, not here — shared names (Kazakh, Kurd, Uzbek)
and place-only sources (V&A) need the place.
Resumable: a query already present in the file is skipped.
"""
from __future__ import annotations

import argparse
import collections
import glob
import html
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import DATA_DIR  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
POOL = DATA_DIR / "pool"


def _atlas() -> list[dict]:
    return [json.loads(Path(f).read_text(encoding="utf-8")) for f in sorted(glob.glob(str(DATA_DIR / "ethnicities" / "*.json")))]


def _rows(p: Path) -> list[dict]:
    """Rows of a pool file; a line cut off by a killed run is skipped."""
    out = []
    for l in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            pass
    return out


def _done(source: str) -> set[str]:
    p = POOL / f"{source}.jsonl"
    return {r["query"] for r in _rows(p)} if p.exists() else set()


def _writer(source: str):
    POOL.mkdir(parents=True, exist_ok=True)
    return open(POOL / f"{source}.jsonl", "a", encoding="utf-8")


def _row(source, id, query, **kw) -> str:
    base = dict(source=source, id=str(id), query=query, people=None, object_name=None, title=None,
                material=None, date=None, place=None, provider=None, image=None, url=None)
    base.update(kw)
    return json.dumps(base, ensure_ascii=False) + "\n"


def _get(url, **params):
    for a in range(4):
        try:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            print(f"  ! {r.status_code} {url}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {e}", flush=True)
        time.sleep(3 * (a + 1))
    return None


# ---------------------------------------------------------------- British Museum
# Facet names from data/bm_ethnic_census.json with hits, minus the mappings
# read and rejected there: Twa is not Mbuti, Arab is not Moroccan/Tunisian
# Arab, Akan is broader than Asante.
_BM_REJECT = {"Twa", "Arab", "Akan"}
_TEASER = re.compile(r'<article class="teaser teaser--collection.*?</article>', re.S)
_T_LINK = re.compile(r'href="/collection/object/([^"]+)"')
_T_TITLE = re.compile(r'<h2 class="teaser__title">(.*?)</h2>', re.S)
_T_META = re.compile(r'<dt class="visually-hidden">([^<]+)</dt>\s*<dd>(.*?)</dd>', re.S)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).replace(" ;", ";").strip(" |\xa0")


def _bm_teasers(page_html: str) -> list[dict]:
    out = []
    for block in _TEASER.findall(page_html):
        link = _T_LINK.search(block)
        if not link:
            continue
        title = _text(_T_TITLE.search(block).group(1)) if _T_TITLE.search(block) else ""
        meta = {}
        for k, v in _T_META.findall(block):
            v = re.sub(r"^\s*x\d+\s*", "", v)            # BM thesaurus id before the term
            meta[k.strip()] = _text(v).replace("\xa0", " ").strip(" |")
        out.append(dict(id=link.group(1), title=title, meta=meta))
    return out


def harvest_bm() -> None:
    import os
    from folk_patterns.museums.british_museum import _client, SEARCH_URL
    if not os.environ.get("BM_CDP_URL"):
        sys.exit("set BM_CDP_URL to a Chrome that passed the BM Cloudflare check (e.g. http://127.0.0.1:9226)")
    names = []
    for r in json.loads((DATA_DIR / "bm_ethnic_census.json").read_text(encoding="utf-8")):
        for n, c in r["facet"].items():
            if c and n not in _BM_REJECT and n not in names:
                names.append(n)
    done = _done("bm")
    client = _client()
    with _writer("bm") as f:
        for n in names:
            if n in done:
                continue
            got = 0
            for page in range(0, 200):
                r = client.get(SEARCH_URL, params={"ethnic_name": n, "image": "true", "page": page})
                if r.status_code != 200:
                    print(f"  ! {n} page {page}: {r.status_code}", flush=True)
                    break
                ts = _bm_teasers(r.text)
                if not ts:
                    break
                for t in ts:
                    m = t["meta"]
                    f.write(_row("bm", t["id"], n, people=m.get("Ethnic group"), object_name=t["title"],
                                 title=m.get("Title") or t["title"], date=m.get("Production date"),
                                 place=m.get("Production place") or m.get("Findspot"),
                                 url=f"https://www.britishmuseum.org/collection/object/{t['id']}"))
                got += len(ts)
                f.flush()
                time.sleep(0.5)
            print(f"bm {n}: {got}", flush=True)


# ---------------------------------------------------------------- Smithsonian
_SI_NH = (" NOT unit_code:(NMNHBOTANY OR NMNHENTO OR NMNHFISHES OR NMNHHERPETOLOGY OR NMNHINV"
          " OR NMNHMAMMALS OR NMNHBIRDS OR NMNHPALEO OR NMNHMINSCI)")


def harvest_si() -> None:
    from folk_patterns.museums.smithsonian import _get_key
    key = _get_key()
    done = _done("si")
    names = sorted({d["ethnicity"].split(" (")[0] for d in _atlas()})
    with _writer("si") as f:
        for n in names:
            if n in done:
                continue
            start, got = 0, 0
            while True:
                j = _get("https://api.si.edu/openaccess/api/v1.0/search", api_key=key, rows=100, start=start,
                         q=f'"{n}" AND online_media_type:"Images"' + _SI_NH)
                rows = ((j or {}).get("response") or {}).get("rows") or []
                for r in rows:
                    c = r.get("content") or {}
                    ist = c.get("indexedStructured") or {}
                    ft = c.get("freetext") or {}
                    media = ((c.get("descriptiveNonRepeating") or {}).get("online_media") or {}).get("media") or []
                    f.write(_row("si", r.get("id"), n, people="; ".join(ist.get("culture") or []) or None,
                                 object_name="; ".join(ist.get("object_type") or []) or None,
                                 title=r.get("title"),
                                 material="; ".join(x.get("content", "") for x in ft.get("physicalDescription") or [])[:200] or None,
                                 date="; ".join(ist.get("date") or []) or None,
                                 place="; ".join(ist.get("place") or []) or None,
                                 provider=r.get("unitCode"),
                                 image=(media[0].get("thumbnail") if media else None),
                                 url=(c.get("descriptiveNonRepeating") or {}).get("record_link")))
                got += len(rows)
                f.flush()
                total = ((j or {}).get("response") or {}).get("rowCount") or 0
                start += len(rows)
                if not rows or start >= total:
                    break
                time.sleep(0.4)
            print(f"si {n}: {got}", flush=True)


# ---------------------------------------------------------------- Europeana
_EU_GOOD = ('world culture', 'wereldculturen', 'world cultures', 'ethnograph', 'náprstek', 'naprstek',
            'anthropolog', 'weltmuseum', 'rautenstrauch', 'quai branly', 'volkenkunde', 'tropenmuseum',
            'asia and pacific', 'finnish heritage', 'mak ')


def harvest_europeana() -> None:
    from folk_patterns.museums.europeana import _get_key
    key = _get_key()
    done = _done("europeana")
    census = {r["ethnicity"]: r for r in json.loads((DATA_DIR / "europeana_census.json").read_text(encoding="utf-8"))}
    with _writer("europeana") as f:
        for e, c in census.items():
            n = e.split(" (")[0]
            if n in done:
                continue
            provs = [p for p, _ in c["providers"] if any(g in p.lower() for g in _EU_GOOD)]
            if not provs:
                continue
            q = f'"{n}" AND (' + " OR ".join(f'DATA_PROVIDER:"{p}"' for p in provs) + ")"
            cursor, got = "*", 0
            while cursor:
                j = _get("https://api.europeana.eu/record/v2/search.json", wskey=key, query=q, rows=100,
                         cursor=cursor, media="true", reusability="open,permission", qf="TYPE:IMAGE", profile="rich")
                items = (j or {}).get("items") or []
                for it in items:
                    f.write(_row("europeana", it["id"], n,
                                 title=(it.get("title") or [None])[0],
                                 material=" | ".join(it.get("dcDescription") or [])[:600] or None,
                                 date=(it.get("year") or [None])[0],
                                 provider=(it.get("dataProvider") or [None])[0],
                                 image=(it.get("edmPreview") or [None])[0],
                                 url=it.get("guid")))
                got += len(items)
                f.flush()
                cursor = (j or {}).get("nextCursor") if items else None
                time.sleep(0.3)
            print(f"europeana {n}: {got} ({len(provs)} providers)", flush=True)


# ---------------------------------------------------------------- V&A
# Place ids, not names: "Burma" as a name finds 151, its id x30037 finds 953
# (docs/museums.md → V&A). "China (Xinjiang)" resolves to all of China, so
# sub-places are listed explicitly. The "Gold Coast" place cluster resolved
# to x32021, which is the Coromandel Coast of India (palampores) — not Ghana.
VA_PLACES = {
    "Afghanistan": "x30023", "Cambodia": "x30020", "Central African Republic / DR Congo": "x29585",
    "Egypt": "x29512", "Ethiopia": "x35090", "Ghana": "x30041",
    "Indonesia": "x30044", "Java": "x30310", "Sumatra": "x35701", "Bali": "x32033", "Sulawesi": "x46615",
    "Borneo": "x44401", "Iran": "x30220", "Laos": "x30047", "Malaysia": "x30048", "Morocco": "x30052",
    "Myanmar": "x30037", "Nigeria": "x30055", "Philippines": "x30022", "South Africa": "x30058",
    "Thailand": "x30017", "Tunisia": "x30014", "Turkey": "x29225", "Turkmenistan": "x38876",
    "Uzbekistan": "x30632", "Bukhara": "x35893", "Turkestan": "x36500", "Central Asia": "x35011",
    "Vietnam": "x30019", "Xinjiang": "x29909", "Kurdistan": "x41393", "Baluchistan": "x32933",
    "Nubia": "x40764", "Kenya": "x35155", "Botswana": "x30035", "Namibia": "x39184", "Somalia": "x38287",
}


def harvest_va() -> None:
    done = _done("va")
    with _writer("va") as f:
        for place, pid in VA_PLACES.items():
            if place in done:
                continue
            page, got = 1, 0
            while True:
                j = _get("https://api.vam.ac.uk/v2/objects/search", id_place=pid, images_exist=1,
                         page_size=100, page=page)
                recs = (j or {}).get("records") or []
                for r in recs:
                    img = r.get("_primaryImageId")
                    f.write(_row("va", r["systemNumber"], place, object_name=r.get("objectType"),
                                 title=r.get("_primaryTitle") or None, date=r.get("_primaryDate") or None,
                                 place=r.get("_primaryPlace") or None,
                                 image=f"https://framemark.vam.ac.uk/collections/{img}/full/!400,400/0/default.jpg" if img else None,
                                 url=f"https://collections.vam.ac.uk/item/{r['systemNumber']}/"))
                got += len(recs)
                f.flush()
                pages = ((j or {}).get("info") or {}).get("pages") or 0
                if not recs or page >= pages:
                    break
                page += 1
                time.sleep(0.4)
            print(f"va {place}: {got}", flush=True)


# ---------------------------------------------------------------- Cleveland / Met (bulk files)
def harvest_cleveland() -> None:
    src = REPO / "work" / "sources" / "cleveland_depts.json"
    if "departments" in _done("cleveland"):
        return
    recs = json.loads(src.read_text(encoding="utf-8"))
    with _writer("cleveland") as f:
        for r in recs:
            f.write(_row("cleveland", r["id"], "departments", people="; ".join(r.get("culture") or []) or None,
                         object_name=r.get("type"), title=r.get("title"), material=r.get("technique"),
                         date=r.get("creation_date"), provider=r.get("department"), url=r.get("url")))
    print(f"cleveland: {len(recs)}")


def harvest_met() -> None:
    import pandas as pd
    if "csv" in _done("met"):
        return
    df = pd.read_csv(REPO / ".cache" / "MetObjects.csv", low_memory=False,
                     usecols=["Object ID", "Is Public Domain", "Department", "Object Name", "Title", "Culture",
                              "Country", "Region", "Object Date", "Object End Date", "Medium", "Link Resource"])
    keep = ["Arts of Africa, Oceania, and the Americas", "Islamic Art", "Asian Art", "Musical Instruments",
            "Costume Institute", "Arms and Armor"]
    df = df[df["Is Public Domain"] & df.Department.isin(keep) & (df["Object End Date"] >= 1700)].fillna("")
    with _writer("met") as f:
        for r in df.itertuples(index=False):
            f.write(_row("met", r[0], "csv", people=r.Culture or None, object_name=r[3] or None, title=r.Title or None,
                         material=r.Medium or None, date=r[8] or None,
                         place=", ".join(x for x in (r.Country, r.Region) if x) or None,
                         provider=r.Department, url=r[11] or None))
    print(f"met: {len(df)}")


def report() -> None:
    for p in sorted(POOL.glob("*.jsonl")):
        c = collections.Counter(r["query"] for r in _rows(p))
        print(f"{p.stem:10s} {sum(c.values()):7d} rows  {len(c)} queries  top: {c.most_common(6)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["bm", "si", "europeana", "va", "cleveland", "met", "report"])
    a = ap.parse_args()
    {"bm": harvest_bm, "si": harvest_si, "europeana": harvest_europeana, "va": harvest_va,
     "cleveland": harvest_cleveland, "met": harvest_met, "report": report}[a.source]()
