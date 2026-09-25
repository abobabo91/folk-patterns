"""Give every pool row (data/pool/<source>.jsonl, from harvest_pool.py) an atlas
culture, a confidence and a normalised object kind, so a culture's gallery can
be picked across kinds before any image is fetched.

    python scripts/assign_pool.py            # -> data/pool/assigned.jsonl
    python scripts/assign_pool.py --show Yoruba Kazakh Persian

status, most to least certain:
  named      the source's own people tag is this culture (BM "Made by", Met /
             Cleveland culture field)
  split      a people name several atlas cultures share (Kazakh, Kurd, Uzbek,
             Lao, Turkmen), resolved by the row's place
  text       Europeana: the name occurs in an ethnographic museum's record
  check      a broad name or a known collision — Miao (not only Hmong), Herero
             (not only Himba), "Iranian", Europeana "Kongo" (Swedish for Congo),
             Europeana "Lao"; or a shared name the place did not resolve
  candidate  place only (V&A; Met / Cleveland rows without a people tag) — the
             vetter decides the people
A row can be candidate for several cultures of one country; it is written once
per culture. flags: held (already in the library), photo, pre1500.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from folk_patterns.util import DATA_DIR, LIBRARY_DIR  # noqa: E402

POOL = DATA_DIR / "pool"

# Place words per atlas country — the country as sources spell it, old names,
# and the regions sources use instead of the country.
COUNTRY_PLACES = {
    "Afghanistan": ["afghanistan", "afghan", "herat", "kabul", "mazar", "balkh"],
    "Botswana": ["botswana", "bechuanaland", "kalahari"],
    "Cambodia": ["cambodia", "khmer", "angkor", "phnom penh"],
    "Central African Republic": ["central african republic", "ituri", "ubangi"],
    "China (Xinjiang)": ["xinjiang", "sinkiang", "kashgar", "khotan", "turfan", "yarkand", "ili", "east turkestan", "chinese turkestan"],
    "Democratic Republic of the Congo": ["congo", "zaire", "kongo", "kasai", "kinshasa", "katanga", "bas-congo", "lower congo"],
    "Djibouti / Eritrea": ["djibouti", "eritrea", "danakil", "afar"],
    "Egypt": ["egypt", "cairo", "nubia", "aswan", "luxor", "upper egypt", "fustat", "alexandria"],
    "Ethiopia": ["ethiopia", "abyssinia", "tigray", "tigre", "harar", "gondar", "shewa", "shoa", "addis"],
    "Gabon": ["gabon", "gaboon", "ogowe", "ogooue"],
    "Ghana": ["ghana", "gold coast", "kumasi", "asante", "ashanti"],
    "Indonesia": ["indonesia", "java", "sumatra", "bali", "sulawesi", "celebes", "borneo", "kalimantan", "lombok", "dutch east indies", "netherlands indies", "nias", "flores", "sumba"],
    "Iran": ["iran", "persia", "isfahan", "tehran", "tabriz", "kashan", "shiraz", "kerman", "qazvin", "mashhad", "fars", "khorasan", "azerbaijan (iran)"],
    "Kazakhstan": ["kazakhstan", "kazakh", "almaty", "semirechye"],
    "Kenya": ["kenya", "british east africa", "nairobi", "mombasa"],
    "Kyrgyzstan": ["kyrgyzstan", "kirghiz", "kyrgyz", "bishkek", "osh"],
    "Laos": ["laos", "lao pdr", "luang prabang", "vientiane"],
    "Malaysia": ["malaysia", "malaya", "malay peninsula", "sarawak", "sabah", "north borneo", "perak", "kelantan", "terengganu", "penang", "malacca", "johor", "selangor", "pahang"],
    "Morocco": ["morocco", "fez", "fes", "marrakesh", "marrakech", "rabat", "tangier", "meknes", "atlas", "rif", "sous", "tetouan", "sale"],
    "Myanmar": ["myanmar", "burma", "mandalay", "rangoon", "yangon", "shan", "pagan", "toungoo", "moulmein", "chin hills", "arakan", "rakhine"],
    "Namibia": ["namibia", "south west africa", "kaokoland", "damaraland", "ovamboland"],
    "Nigeria": ["nigeria", "lagos", "ibadan", "oyo", "ife", "abeokuta", "benin city", "onitsha", "yorubaland", "igboland", "niger delta"],
    "Philippines": ["philippines", "philippine", "luzon", "mindanao", "sulu", "manila", "basilan", "visayas"],
    "Senegal": ["senegal", "dakar", "saint-louis", "senegambia", "gambia"],
    "Somalia": ["somalia", "somaliland", "mogadishu", "berbera"],
    "South Africa": ["south africa", "natal", "zululand", "transvaal", "cape province", "eastern cape", "orange free state", "kwazulu", "lesotho", "basutoland", "cape colony"],
    "Tajikistan": ["tajikistan", "pamir", "badakhshan", "dushanbe"],
    "Tanzania": ["tanzania", "zanzibar", "tanganyika", "dar es salaam", "pemba", "kilwa"],
    "Thailand": ["thailand", "siam", "bangkok", "chiang mai", "isan", "isaan", "north-east thailand", "northeast thailand"],
    "Tunisia": ["tunisia", "tunis", "kairouan", "djerba", "sfax", "nabeul"],
    "Turkey": ["turkey", "anatolia", "istanbul", "constantinople", "ottoman", "iznik", "bursa", "kutahya", "smyrna", "izmir", "konya"],
    "Turkmenistan": ["turkmenistan", "merv", "ashgabat", "turkmen"],
    "Uzbekistan": ["uzbekistan", "bukhara", "samarkand", "khiva", "tashkent", "fergana", "ferghana", "karakalpak", "nurata", "shahrisabz"],
    "Vietnam": ["vietnam", "viet nam", "annam", "tonkin", "cochin china", "cochinchina", "hanoi", "hue", "saigon", "champa"],
}

# BM facet names that cover more than one atlas people, or a wider one.
_CHECK_NAMES = {"Miao": "Miao covers peoples besides the Hmong", "Herero": "Herero covers peoples besides the Himba",
                "Iranian": "Iranian is a nationality, not a people"}
_EU_CHECK = {"Kongo": "Swedish 'Kongo' = Congo", "Lao": "only ~30 of Europeana's Lao are Lao objects"}

_KIND_SYNONYMS = {
    "necklet": "necklace", "neck-ring": "necklace", "neck ring": "necklace", "cloth": "textile",
    "textiles": "textile", "fabric": "textile", "textile fragment": "textile", "fragment": "fragment",
    "figurine": "figure", "statuette": "figure", "sculpture": "figure", "carving": "figure",
    "ear-ring": "earring", "ear ring": "earring", "ear-ornament": "earring", "ear ornament": "earring",
    "arm-ring": "armlet", "arm ring": "armlet", "bangle": "bracelet", "finger-ring": "ring",
    "smoking-pipe": "pipe", "tobacco pipe": "pipe", "snuff-box": "snuff container", "snuff-bottle": "snuff container",
    "head-rest": "headrest", "neck-rest": "headrest", "drinking-cup": "cup", "beaker": "cup",
    "masquerade-mask": "mask", "helmet mask": "mask", "face mask": "mask", "dance mask": "mask",
    "sword-sheath": "sheath", "scabbard": "sheath", "spear-head": "spear", "spearhead": "spear",
    "arrow-head": "arrow", "arrowhead": "arrow", "hanging": "hanging", "wall hanging": "hanging",
    "photographic print": "photo", "glass negative": "photo", "negative": "photo", "photograph": "photo",
    "print": "print", "lantern slide": "photo", "postcard": "photo", "digital photograph (colour)": "photo",
    "wrapped garment": "wrapper", "skirt cloth": "skirt", "skirt-cloth": "skirt", "waist-cloth": "wrapper",
    "vessel": "vessel", "jar": "jar", "pot": "pot", "bowl": "bowl", "dish": "dish", "plate": "dish",
}
_PHOTO_KINDS = {"photo", "print", "drawing", "watercolour", "watercolour drawing", "painting", "sketch", "design"}


def fold(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(ch)).lower()


def kind(name: str | None) -> str:
    """'mask; masquerade-costume' -> 'mask'; 'Necklets' -> 'necklace'."""
    k = fold(name or "").split(";")[0].split(",")[0].strip(" .")
    k = re.sub(r"\s*\(.*?\)\s*", " ", k).strip()
    k = re.sub(r"^(fragment of|part of|pair of|set of|model of|a |an )\s*", "", k).strip()
    if k in _KIND_SYNONYMS:
        return _KIND_SYNONYMS[k]
    if k.endswith("s") and not k.endswith(("ss", "us", "is")) and k[:-1] in _KIND_SYNONYMS.values():
        k = k[:-1]
    return _KIND_SYNONYMS.get(k, k) or "?"


def year(date: str | None) -> int | None:
    d = fold(str(date or ""))
    m = re.search(r"(\d{1,2})(st|nd|rd|th)\s*(c\b|century|c\.|thc)", d) or re.search(r"(\d{1,2})thc", d)
    if m:
        y = (int(m.group(1)) - 1) * 100 + 50
    else:
        m = re.search(r"\b(\d{3,4})\b", d)
        if not m:
            return None
        y = int(m.group(1))
    return -y if re.search(r"\bb\.?c\.?e?\b|\bbc\b", d) else y


def _atlas() -> list[dict]:
    return [json.loads(Path(f).read_text(encoding="utf-8")) for f in sorted(glob.glob(str(DATA_DIR / "ethnicities" / "*.json")))]


def _place_countries(text: str) -> set[str]:
    t = " " + fold(text) + " "
    return {c for c, toks in COUNTRY_PLACES.items() if any(re.search(r"\b" + re.escape(x) + r"\b", t) for x in toks)}


def _held() -> set[str]:
    out = set()
    for mp in LIBRARY_DIR.glob("*/*/*/*/*/metadata.json"):
        for r in json.loads(mp.read_text(encoding="utf-8")):
            s = r.get("source") or {}
            out.add(f"{s.get('museum')}|{s.get('object_id')}")
    return out


_HELD_MUSEUM = {"bm": "british_museum", "va": "va", "europeana": "europeana", "met": "met",
                "cleveland": "cleveland", "si": "smithsonian"}


def _rows(source: str) -> list[dict]:
    p = POOL / f"{source}.jsonl"
    out, seen = [], set()
    if not p.exists():
        return out
    for l in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(l)
        except json.JSONDecodeError:
            continue
        if r["id"] in seen:          # Europeana ran "Kurdish" once per Kurdish culture
            continue
        seen.add(r["id"])
        out.append(r)
    return out


def assign() -> list[dict]:
    atlas = _atlas()
    by_country = collections.defaultdict(list)
    for d in atlas:
        by_country[d["country"]].append(d)
    name_to = collections.defaultdict(list)          # people name -> atlas cultures
    for d in atlas:
        name_to[fold(d["ethnicity"].split(" (")[0])].append(d)
    bm_census = json.loads((DATA_DIR / "bm_ethnic_census.json").read_text(encoding="utf-8"))
    facet_to = collections.defaultdict(list)
    for r in bm_census:
        d = next(x for x in atlas if x["ethnicity"] == r["ethnicity"])
        for n, c in r["facet"].items():
            if c and d not in facet_to[n]:
                facet_to[n].append(d)
    held = _held()
    out = []

    def emit(r, d, status, why=None):
        k = kind(r.get("object_name") or r.get("title"))
        y = year(r.get("date"))
        flags = []
        if f"{_HELD_MUSEUM[r['source']]}|{r['id']}" in held:
            flags.append("held")
        if k in _PHOTO_KINDS:
            flags.append("photo")
        if y is not None and y < 1500:
            flags.append("pre1500")
        out.append(dict(key=d["key"], ethnicity=d["ethnicity"], country=d["country"], status=status, why=why,
                        kind=k, year=y, flags=flags, **{f: r.get(f) for f in (
                            "source", "id", "object_name", "title", "date", "place", "people", "provider", "image", "url")}))

    def by_place(r, cands, status_if_one="split"):
        """Keep the candidates whose country the row's place names."""
        cs = _place_countries(" ".join(str(r.get(f) or "") for f in ("place", "people", "title")))
        hit = [d for d in cands if d["country"] in cs]
        return hit

    # British Museum — facet name is the people
    for r in _rows("bm"):
        ds = facet_to.get(r["query"], [])
        if not ds:
            continue
        if r["query"] in _CHECK_NAMES:
            for d in ds:
                emit(r, d, "check", _CHECK_NAMES[r["query"]])
        elif len(ds) == 1:
            emit(r, ds[0], "named")
        else:
            hit = by_place(r, ds)
            if len(hit) == 1:
                emit(r, hit[0], "split")
            else:
                for d in ds:
                    emit(r, d, "check", f"{r['query']} is shared by {len(ds)} cultures; place did not decide")

    # Europeana — the name occurs in an ethnographic provider's record
    for r in _rows("europeana"):
        ds = name_to.get(fold(r["query"]), [])
        if len(ds) > 1:
            hit = by_place(r, ds)
            ds = hit or ds
        for d in ds:
            if r["query"] in _EU_CHECK:
                emit(r, d, "check", _EU_CHECK[r["query"]])
            else:
                emit(r, d, "text" if len(ds) == 1 else "check", None if len(ds) == 1 else "shared name")

    # V&A — place only
    for r in _rows("va"):
        cs = _place_countries(" ".join(str(r.get(f) or "") for f in ("place", "query")))
        for c in cs:
            for d in by_country[c]:
                emit(r, d, "candidate")

    # Met and Cleveland — people tag when the culture field names one, else place
    alias = {d["key"]: [fold(d["ethnicity"].split(" (")[0])] for d in atlas}
    extra = {"Ashanti": ["asante", "akan"], "Bamar": ["burmese", "burman"], "Hmong": ["miao"], "Uyghur": ["uighur"],
             "Kyrgyz": ["kirghiz"], "Persian": ["persian", "iranian"], "Turkish": ["ottoman", "turkish"],
             "Fulani": ["fulbe", "peul"], "Filipino": ["filipino"], "Kinh": ["vietnamese"], "Thai": ["thai", "siamese"],
             "Khmer": ["khmer", "cambodian"], "Lao": ["laotian"], "Egyptian": ["egyptian"], "Malay": ["malay"]}
    for d in atlas:
        alias[d["key"]] += extra.get(d["ethnicity"], [])
    for src in ("met", "cleveland"):
        for r in _rows(src):
            ppl = fold(r.get("people") or "")
            named = [d for d in atlas if any(re.search(r"\b" + re.escape(a) + r"\b", ppl) for a in alias[d["key"]])]
            if len(named) > 1:
                named = by_place(r, named) or named
            if named:
                for d in named:
                    emit(r, d, "named" if len(named) == 1 else "check", None if len(named) == 1 else "culture field names several")
                continue
            cs = _place_countries(" ".join(str(r.get(f) or "") for f in ("place", "people")))
            for c in cs:
                for d in by_country[c]:
                    emit(r, d, "candidate")

    # Smithsonian — only rows with an open-access image are worth anything
    for r in _rows("si"):
        if not r.get("image"):
            continue
        for d in name_to.get(fold(r["query"]), []):
            if fold(r["query"]) in fold(r.get("people") or ""):
                emit(r, d, "named")
    return out


def show(rows: list[dict], names: list[str]) -> None:
    for n in names:
        rs = [r for r in rows if r["ethnicity"].lower().startswith(n.lower()) and "held" not in r["flags"]]
        st = collections.Counter(r["status"] for r in rs)
        src = collections.Counter(r["source"] for r in rs)
        objs = [r for r in rs if "photo" not in r["flags"] and "pre1500" not in r["flags"]]
        kinds = collections.Counter(r["kind"] for r in objs if r["status"] in ("named", "split", "text"))
        print(f"\n=== {n}: {len(rs)} rows  status {dict(st)}  sources {dict(src)}")
        print(f"    confident objects (no photos, not pre-1500): {sum(kinds.values())} in {len(kinds)} kinds")
        print("    ", kinds.most_common(30))


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", nargs="*")
    a = ap.parse_args()
    rows = assign()
    with open(POOL / "assigned.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    st = collections.Counter(r["status"] for r in rows)
    print(f"{len(rows)} assignments, {len({(r['source'], r['id']) for r in rows})} distinct objects, status {dict(st)}")
    if a.show:
        show(rows, a.show)
