"""World list of peoples with museum evidence — which cultures the atlas could
add, per continent, before anything is scraped.

    python scripts/world_peoples.py wikidata     # -> data/world/wikidata.json
    python scripts/world_peoples.py bm           # BM "Ethnic group" hits per name (needs BM_CDP_URL)
    python scripts/world_peoples.py aliases      # Wikidata English aliases of the names BM found 0 for
    python scripts/world_peoples.py bm --aliases # retry those under their aliases
    python scripts/world_peoples.py labels       # Wikidata labels + aliases in 19 languages
    python scripts/world_peoples.py europeana    # hits at ethnographic providers per name
    python scripts/world_peoples.py europeana --multilingual  # sum cached multilingual names
    python scripts/world_peoples.py local        # Met + Cleveland pool rows whose people field names it
    python scripts/world_peoples.py europeana-objects [--only ...]  # ethnographic Europeana objects naming the people + its country
    python scripts/world_peoples.py classify     # Wikipedia summary + Haiku: a people? where?
    python scripts/world_peoples.py classify --backend codex --all-min-sitelinks 20
    python scripts/world_peoples.py harvest      # BM object names per people (<= 500), for category breadth
    python scripts/world_peoples.py cleanup      # Haiku: atlas match, duplicates, sub-groups
    python scripts/world_peoples.py report       # -> data/world/peoples.json + docs/world-peoples.md
    python scripts/world_peoples.py candidates   # -> data/world/candidates.jsonl: objects per category, one culture per object
    python scripts/world_peoples.py gaps          # -> docs/gaps.md: source and site coverage gaps
    python scripts/world_peoples.py pick --only Haida Tiv   # vetted, ranked objects per category -> data/world/picks/ (needs BM_CDP_URL)
    python scripts/world_peoples.py pick --only ... --shard 0/3   # + 1/3, 2/3 in two more processes
    python scripts/world_peoples.py pick --cached-only --export-batch p001 --only ...   # cloud batch of what needs judging
    python scripts/world_peoples.py pick-import --batch p001   # cloud verdicts -> pick cache; then pick --no-judge
    python scripts/world_peoples.py coverage [--only ...]   # per candidate: kept, judged, dropped by rule (why), never reached

The universe is Wikidata: every item that is an instance of "ethnic group"
(Q41710) or "indigenous people" (Q103817), or of any of their ~2,600
subclasses, and that has an English Wikipedia article. Only names with
articles in 5+ languages are counted. Two known gaps: the subclass tree also
holds dioceses, church bodies and ancient tribes, which the keyword filter
below does not fully remove (the museum count does: they have no objects),
and some peoples have no P31 at all (Kuba, T'boli), so the atlas's own
cultures are always added.

Counts are cached per name in data/world/counts_<source>.jsonl, so every step
resumes.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
OUT = REPO / "data" / "world"
UA = {"User-Agent": "folk-patterns/0.1 (https://github.com/abobabo91/folk-patterns)"}
MIN_SITELINKS = 5

# Labels that are not peoples: institutions the subclass tree drags in, and
# diaspora / religious sub-groups of a people already on the list.
_INSTITUTION = re.compile(
    r"\b(Diocese|Archdiocese|Archbishopric|Eparchy|Church|Order|University|Universidad|Instituto|School|College|"
    r"Sisters|Daughters|Brothers|Congregation|Friary|Abbey|Monastery|Council|Conference|Catholic|Orthodox|"
    r"Presbyterian|Baptists|titular|see|Prefecture|Vicariate|Francophonie|Reservation|Rancheria|Band of|"
    r"Tribe of|Pueblo of|Community|Society|Association|History|List|Governing Body)\b", re.I)
_DIASPORA = re.compile(
    r"\b(Americans?|Canadians?|Australians?|Britons?|Brazilians?|Argentines?|Mexicans?|Chileans?|New Zealanders|"
    r"in the|in [A-Z]\w+|of [A-Z]\w+ia\b|diaspora|expatriates|immigrants?|descent|Muslims?|Christians?|Sikhs?|Jews)\b")


def _sparql(q: str) -> list[dict]:
    for attempt in range(3):
        r = httpx.post("https://query.wikidata.org/sparql", data={"query": q, "format": "json"}, headers=UA, timeout=300)
        if r.status_code == 200:
            return [{k: v["value"] for k, v in b.items()} for b in r.json()["results"]["bindings"]]
        if r.status_code == 429 or 500 <= r.status_code < 600:
            print(f"  wikidata {r.status_code}, retrying", flush=True)
            time.sleep(5 * (2 ** attempt))
            continue
        raise SystemExit(f"wikidata query failed: HTTP {r.status_code}")
    raise SystemExit("wikidata query failed 3 times")


def cmd_wikidata() -> None:
    # one query per 40 types: the single transitive query times out (504)
    types = [t["c"].split("/")[-1] for t in _sparql(
        "SELECT DISTINCT ?c WHERE { VALUES ?root { wd:Q41710 wd:Q103817 } ?c wdt:P279* ?root }")] + ["Q83828"]
    rows: dict[str, dict] = {}
    for i in range(0, len(types), 40):
        vals = " ".join("wd:" + t for t in types[i:i + 40])
        for x in _sparql(f"""SELECT ?g ?gLabel ?article ?sitelinks (SAMPLE(?cLabel) AS ?country)
            (SAMPLE(?lat) AS ?lat) (SAMPLE(?lon) AS ?lon) WHERE {{
              VALUES ?t {{ {vals} }} ?g wdt:P31 ?t .
              ?article schema:about ?g ; schema:isPartOf <https://en.wikipedia.org/> .
              ?g wikibase:sitelinks ?sitelinks .
              OPTIONAL {{ ?g wdt:P17 ?c . ?c rdfs:label ?cLabel FILTER(lang(?cLabel) = "en") }}
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
            }} GROUP BY ?g ?gLabel ?article ?sitelinks"""):
            rows[x["g"].split("/")[-1]] = {"qid": x["g"].split("/")[-1], "label": x["gLabel"],
                                           "article": x["article"], "sitelinks": int(x["sitelinks"]),
                                           "country": x.get("country")}
        print(f"types {i + 40}/{len(types)}: {len(rows)} items", flush=True)
    keep = [r for r in rows.values()
            if not re.match(r"^Q\d+$", r["label"]) and not _INSTITUTION.search(r["label"]) and not _DIASPORA.search(r["label"])]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "wikidata.json").write_text(json.dumps(sorted(keep, key=lambda r: -r["sitelinks"]), ensure_ascii=False, indent=0),
                                       encoding="utf-8")
    print(f"{len(rows)} items, {len(keep)} after the label filter, "
          f"{sum(r['sitelinks'] >= MIN_SITELINKS for r in keep)} with {MIN_SITELINKS}+ sitelinks")


def cmd_aliases() -> None:
    """English alternative names for every item the BM found nothing for: the BM
    search is exact and accent-sensitive ("Otomi" 0, "Otomí" 86; "Ashanti" 0,
    "Asante" 2,785)."""
    bm = _counts("bm")
    zero = [k for k, d in bm.items() if not k.startswith("atlas:") and not any(d["hits"].values())]
    got: dict[str, list[str]] = {}
    for i in range(0, len(zero), 200):
        vals = " ".join("wd:" + k for k in zero[i:i + 200])
        for x in _sparql(f'SELECT ?g ?alt WHERE {{ VALUES ?g {{ {vals} }} ?g skos:altLabel ?alt FILTER(lang(?alt) = "en") }}'):
            got.setdefault(x["g"].split("/")[-1], []).append(x["alt"])
        print(f"  {min(i + 200, len(zero))}/{len(zero)}: {len(got)} with aliases", flush=True)
    (OUT / "aliases.json").write_text(json.dumps(got, ensure_ascii=False, indent=0), encoding="utf-8")


def _atlas_names() -> list[str]:
    return sorted({json.loads(Path(f).read_text(encoding="utf-8"))["ethnicity"].split(" (")[0]
                   for f in glob.glob(str(REPO / "data" / "ethnicities" / "*.json"))})


def _names() -> list[tuple[str, str]]:
    """(key, label) to count: Wikidata items with enough sitelinks, plus the atlas."""
    wd = json.loads((OUT / "wikidata.json").read_text(encoding="utf-8"))
    out = [(r["qid"], r["label"]) for r in wd if r["sitelinks"] >= MIN_SITELINKS]
    return out + [("atlas:" + n, n) for n in _atlas_names()]


def variants(label: str) -> list[str]:
    """BM and Europeana name a people in the singular, without "people":
    "Nagas" -> Naga, "Hopi people" -> Hopi, "Hungarians" -> Hungarian."""
    l = re.sub(r"\s*\(.*\)$", "", label)
    l = re.sub(r"\s+(people|peoples|tribe|tribes)$", "", l, flags=re.I).replace("ʼ", "'")
    v = [l]
    if l.endswith("s") and not l.endswith("ss") and len(l) > 4:
        v.append(l[:-1])
    return list(dict.fromkeys(v))


LABEL_LANGS = ["en", "de", "fr", "sv", "fi", "et", "hu", "ru", "pl", "nl", "es", "it", "cs", "da", "nb", "pt", "ro", "uk", "tr"]


def _labels_path() -> Path:
    return OUT / "labels.json"


def _ml_variants(qid: str, label: str) -> list[str]:
    """English variants followed by cached Wikidata labels and aliases.

    The source strings are deliberately kept in their original scripts. In
    particular, Russian Cyrillic labels are useful to Europeana and do not
    need a guessed Latin transliteration.
    """
    try:
        cached = json.loads(_labels_path().read_text(encoding="utf-8")) if _labels_path().exists() else {}
    except (OSError, json.JSONDecodeError):
        cached = {}
    out: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = str(value or "").strip()
        if len(value) < 4 or value.casefold() in seen:
            return
        seen.add(value.casefold())
        out.append(value)

    for value in variants(label):
        add(value)
    # Wikidata aliases include common words and other peoples' names ("Santa"
    # for Dongxiang, "Congo" for Americo-Liberian, "Wind" for Kaw, "Lera" for
    # Hutu; sampled 2026-10-04, they inflated counts to 4,000-14,000). A foreign
    # name is kept only when the consonant skeleton of the English name appears
    # in it, so tjuvasjer, tšuvassit, csuvasok and чуваши stay for Chuvash.
    roots = {_skeleton(v)[:3] for v in variants(label)}
    names = {_fold(v) for v in variants(label)}
    for lang_values in (cached.get(qid) or {}).values():
        for value in lang_values or []:
            for v in variants(str(value)):
                sk = _skeleton(v)
                # A root under 2 consonants ("Hutu" -> "t") matches almost
                # anything; then the folded English name itself must appear.
                if v.casefold() in _ML_COMMON_WORDS:
                    continue
                if any((len(r) >= 2 and r in sk) or (len(r) < 2 and any(n in _fold(v) for n in names)) for r in roots):
                    add(v)
    return out


# Aliases that pass the skeleton test but are common words in the museums'
# languages. "Sandal" (an alias of the Santal) kept 406 Indian sandals at
# the Stockholm and Gothenburg museums on 2026-10-04.
_ML_COMMON_WORDS = {"sandal"}


_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                 "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))
_SKEL_SUBS = [("tsch", "c"), ("tch", "c"), ("tsh", "c"), ("tj", "c"), ("ts", "c"), ("cs", "c"), ("ch", "c"), ("cz", "c"),
              ("sch", "s"), ("sh", "s"), ("sj", "s"), ("sz", "s"), ("zh", "s"), ("kh", "k"), ("ph", "f"), ("th", "t"),
              ("w", "v"), ("q", "k"), ("x", "k"), ("d", "t"), ("b", "p"), ("g", "k"), ("z", "s"), ("j", ""), ("y", ""), ("h", "")]


def _skeleton(s: str) -> str:
    """Consonants of a name, with common transliteration spellings merged."""
    t = _fold(s)
    t = "".join(_CYR.get(c, c) for c in t)
    t = re.sub(r"[^a-z]", "", t)
    for a_, b_ in _SKEL_SUBS:
        t = t.replace(a_, b_)
    t = re.sub(r"[aeiou]", "", t)
    return re.sub(r"(.)+", r"", t)


def cmd_labels() -> None:
    """Cache multilingual Wikidata labels and aliases in 200-item batches."""
    try:
        cache = json.loads(_labels_path().read_text(encoding="utf-8")) if _labels_path().exists() else {}
    except (OSError, json.JSONDecodeError):
        cache = {}
    rows = json.loads((OUT / "wikidata.json").read_text(encoding="utf-8"))
    qids = [r["qid"] for r in rows]
    todo = [qid for qid in qids if qid not in cache]
    print(f"labels: {len(todo)} qids to fetch ({len(qids) - len(todo)} cached)", flush=True)
    langs = ", ".join(f'"{lang}"' for lang in LABEL_LANGS)
    for i in range(0, len(todo), 200):
        batch = todo[i:i + 200]
        vals = " ".join("wd:" + qid for qid in batch)
        result = _sparql(f'''SELECT ?g ?lang ?value WHERE {{
            VALUES ?g {{ {vals} }}
            {{ ?g rdfs:label ?value }} UNION {{ ?g skos:altLabel ?value }}
            FILTER(lang(?value) IN ({langs}))
            BIND(lang(?value) AS ?lang)
        }}''')
        for qid in batch:
            cache[qid] = {lang: [] for lang in LABEL_LANGS}
        for row in result:
            qid, lang, value = row.get("g", "").split("/")[-1], row.get("lang", ""), row.get("value", "")
            if qid in cache and lang in LABEL_LANGS and value not in cache[qid][lang]:
                cache[qid][lang].append(value)
        _labels_path().parent.mkdir(parents=True, exist_ok=True)
        _labels_path().write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"  {min(i + 200, len(todo))}/{len(todo)} qids cached", flush=True)


def _done(src: str) -> set[str]:
    p = OUT / f"counts_{src}.jsonl"
    return {json.loads(l)["key"] for l in p.read_text(encoding="utf-8").splitlines() if l.strip()} if p.exists() else set()


def cmd_bm(use_aliases: bool = False) -> None:
    from folk_patterns.museums import british_museum as bm
    c = bm._client()
    done = _done("bm")

    def count(name: str) -> int:
        # first page only: 100 ids a page, so ">= 100" is all the threshold needs
        r = c.get(bm.SEARCH_URL, params={"ethnic_name": name, "page": 0, "image": "true"})
        if r.status_code != 200:   # a Cloudflare 403 page has no ids: never record it as 0
            raise RuntimeError(f"BM answered {r.status_code}")
        t = r.text
        ids = set(bm._OBJECT_LINK_RE.findall(t))
        more = re.search(r"page=[1-9]", t.replace("&amp;", "&"))
        return 100 if more else len(ids)

    def one_names(k: str, names: list[str]) -> dict:
        hits = {}
        for v in names:
            hits[v] = count(v)
            time.sleep(0.3)
            if hits[v]:
                break
        return {"key": k, "label": names[0] if names else "", "hits": hits}

    def one(kl: tuple[str, str]) -> dict:
        k, l = kl
        hits = {}
        for v in variants(l):
            hits[v] = count(v)
            time.sleep(0.3)
            if hits[v]:
                break
        return {"key": k, "label": l, "hits": hits}

    from concurrent.futures import ThreadPoolExecutor
    if use_aliases:   # second pass: only names the first found nothing for, tried under their aliases
        al = json.loads((OUT / "aliases.json").read_text(encoding="utf-8"))
        done = _done("bm_alias")
        tried = {k: set(d["hits"]) for k, d in _counts("bm").items()}
        todo = [(k, [a for a in dict.fromkeys(v for x in al[k] for v in variants(x)) if a not in tried.get(k, ())][:6])
                for k in al if k not in done]
        out_p, fn = OUT / "counts_bm_alias.jsonl", lambda kv: one_names(kv[0], kv[1])
    else:
        todo = [(k, l) for k, l in _names() if k not in done]
        out_p, fn = OUT / "counts_bm.jsonl", one
    print(f"bm: {len(todo)} names to count ({len(done)} cached)", flush=True)
    with open(out_p, "a", encoding="utf-8") as f, ThreadPoolExecutor(3) as ex:
        for i, d in enumerate(ex.map(fn, todo)):
            if not d["hits"]:
                d["hits"] = {"": 0}
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
            f.flush()
            if i % 50 == 0 or max(d["hits"].values()) >= 30:
                print(f"  {i}/{len(todo)} {d['label']}: {d['hits']}", flush=True)


# Ethnographic / folk-life providers. Europeana's text search matches any
# record that mentions the name, so a count only means something at these.
_EU_GOOD = ("world culture", "wereldculturen", "world cultures", "ethnograph", "etnograf", "néprajz", "neprajz",
            "náprstek", "naprstek", "anthropolog", "weltmuseum", "rautenstrauch", "quai branly", "volkenkunde",
            "tropenmuseum", "asia and pacific", "finnish heritage", "volkskunde", "národopis", "narodopis",
            "etnolog", "ethnolog", "folk", "rahva", "etnografisk", "open air museum", "skansen", "mucem",
            "village museum", "astra national museum")


def cmd_europeana(multilingual: bool = False) -> None:
    from folk_patterns.museums.europeana import _get_key
    key = _get_key()
    source = "europeana_ml" if multilingual else "europeana"
    done = _done(source)
    english = _counts("europeana") if multilingual else {}
    # Multilingual names cost ~20 queries each: only for items the classifier
    # called a living people (religions, ancient tribes and institutions skipped).
    living = ({k for k, d in json.loads((OUT / "classified.json").read_text(encoding="utf-8")).items() if d.get("people")}
              if multilingual and (OUT / "classified.json").exists() else None)
    todo = [(k, l) for k, l in _names()
            if k not in done and (living is None or k in living)
            and (not multilingual or max((english.get(k) or {}).get("hits", {}).values() or [0]) < 30)]
    label = "europeana multilingual" if multilingual else "europeana"
    print(f"{label}: {len(todo)} names to count ({len(done)} cached)", flush=True)
    out_path = OUT / f"counts_{source}.jsonl"
    with httpx.Client(timeout=60) as cl, open(out_path, "a", encoding="utf-8") as f:
        for i, (k, l) in enumerate(todo):
            hits, provs = {}, {}
            if multilingual:
                # One OR query per 12 names: one request instead of ~20 per people,
                # and an object named twice is counted once.
                vs = [v.replace('"', "") for v in _ml_variants(k, l)]
                names = [" OR ".join(f'"{v}"' for v in vs[n:n + 12]) for n in range(0, len(vs), 12)]
            else:
                names = [f'"{v}"' for v in variants(l)]
            for v in names:
                try:
                    j = cl.get("https://api.europeana.eu/record/v2/search.json", params={
                        "wskey": key, "query": v, "rows": 0, "media": "true", "reusability": "open,permission",
                        "qf": "TYPE:IMAGE", "profile": "facets", "facet": "DATA_PROVIDER",
                        "f.DATA_PROVIDER.facet.limit": 100}).json()
                except (httpx.HTTPError, ValueError) as e:
                    print(f"  ! {v}: {e}", flush=True)
                    time.sleep(5)
                    continue
                fields = {x["name"]: x["fields"] for x in j.get("facets", [])}
                good = {x["label"]: x["count"] for x in fields.get("DATA_PROVIDER", [])
                        if any(g in x["label"].lower() for g in _EU_GOOD)}
                hits[v] = sum(good.values())
                provs[v] = sorted(good.items(), key=lambda x: -x[1])[:5]
                time.sleep(0.2)
                if hits[v] and not multilingual:
                    break
            f.write(json.dumps({"key": k, "label": l, "hits": hits, "providers": provs}, ensure_ascii=False) + "\n")
            f.flush()
            if i % 100 == 0:
                print(f"  {i}/{len(todo)} {l}: {hits}", flush=True)


def _counts(src: str) -> dict[str, dict]:
    p = OUT / f"counts_{src}.jsonl"
    if not p.exists():
        return {}
    return {d["key"]: d for d in (json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip())}


def _fold(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower()


# Parts of a Met culture field that name a period, court or market, not a
# people: "Japan, Edo period", "Mughal India, court of Akbar", "for the Japanese market".
_NOT_PEOPLE = re.compile(r"period|dynasty|court of|reigned|kingdom|empire|market|made for|style of", re.I)


def _people_text(r: dict) -> str:
    """The part of a pool row's people field that can name a people.
    Cleveland writes a place path and then the maker ("Africa, Central Africa,
    Democratic Republic of the Congo, Kuba-style maker"): only the maker counts,
    or the alias "Congo" matches every object from the DRC."""
    parts = [x.strip() for x in r["people"].split(",")]
    if r["source"] == "cleveland":
        if _NOT_PEOPLE.search(parts[-1]):   # Asian rows end in a period: "Japan, Edo period (1615–1868)"
            return ""
        m = re.sub(r"\b(possibly|probably|unknown|workshop|-?style|maker|artist|people|peoples)\b", " ", parts[-1], flags=re.I)
        return m if len(parts) > 1 and m.strip() else ""
    return ", ".join(x for x in parts if not _NOT_PEOPLE.search(x))


# South Asian peoples named after a region: the Met and Cleveland file their
# objects by place ("Western India, Gujarat, Kachchh"), never by people, so the
# name match finds none. Ordered most specific first; an object goes to the first
# people whose place it names, so Kachchh embroidery is Kutchi, not Gujarati, and
# "Punjab Hills" painting is Pahari, not Punjabi, and "Assam, Naga Hills"
# swords are Naga, not Assamese.
_PLACE_PEOPLES = [
    ("Q3303599", ["Kachchh", "Kutch"]), ("Q1130145", ["Naga Hills", "Naga"]),
    ("Q1530167", ["Punjab Hills", "Kangra", "Chamba", "Guler", "Basohli", "Mandi"]),
    ("Q1282294", ["Gujarat"]), ("Q854323", ["Punjab"]), ("Q402913", ["Bengal"]),
    ("Q1196649", ["Kashmir"]), ("Q1258074", ["Sindh"]), ("Q4387218", ["Rajasthan"]),
    ("Q1983634", ["Orissa", "Odisha"]), ("Q1287940", ["Assam"]), ("Q1265028", ["Maharashtra"]),
    ("Q1267987", ["Kerala"]), ("Q418708", ["Andhra"]), ("Q118281", ["Karnataka", "Mysore"]),
    ("Q173491", ["Tamil Nadu"]), ("Q201501", ["Baluchistan", "Balochistan"]), ("Q21652255", ["Ladakh"]),
]
# Place matches also hold court and temple art (Jain manuscripts of 1475,
# 4th-century Kashmir sculpture, Rajput album folios; sampled 2026-10-04), so
# they keep only objects from 1750 on that are not folios or sculpture.
_PLACE_DROP = re.compile(r"folio|sculpture|manuscript|page from|leaf from", re.I)


def _year(date: str) -> int | None:
    d = str(date or "")
    if m := re.search(r"\b(\d{4})", d):
        return int(m.group(1))
    if m := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\b", d):
        return (int(m.group(1)) - 1) * 100
    return None


def _place_matches(rows: list[dict]) -> dict[str, list[int]]:
    """key -> pool row positions whose people/place text names one of its places."""
    out: dict[str, list[int]] = {k: [] for k, _ in _PLACE_PEOPLES}
    pats = [(k, re.compile(r"\b(" + "|".join(re.escape(p) for p in ps) + r")\b")) for k, ps in _PLACE_PEOPLES]
    for i, r in enumerate(rows):
        text = f"{r.get('people') or ''} | {r.get('place') or ''}"
        y = _year(r.get("date"))
        if y is None or y < 1750 or _PLACE_DROP.search(f"{r.get('object_name') or ''} {r.get('title') or ''}"):
            continue
        for k, rx in pats:
            if rx.search(text):
                out[k].append(i)
                break
    return out


def cmd_local() -> None:
    """Met and Cleveland rows already in data/pool (harvest_pool.py) whose
    people / culture field names the people, as a whole word or phrase:
    "Asmat people", "Africa, West Africa, Burkina Faso, Bwa". Free, no requests.
    South Asian peoples named after a region also take the rows that name their
    place (_PLACE_PEOPLES), from 1750 on and without folios or sculpture.
    -> data/world/local_objects.jsonl, one line per key with the matched objects."""
    pool = REPO / "data" / "pool"
    rows = []
    for src in ("met", "cleveland"):
        for l in (pool / f"{src}.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(l)
            except ValueError:
                continue
            if r.get("people") or r.get("place"):
                rows.append(r)
    # index word n-grams (1-3) of the folded people text -> row positions
    index: dict[str, set[int]] = {}
    for i, r in enumerate(rows):
        w = re.findall(r"[^\W_]+(?:['’][^\W_]+)?", _fold(_people_text(r) if r.get("people") else "").replace("-", " "))
        for n in (1, 2, 3):
            for j in range(len(w) - n + 1):
                index.setdefault(" ".join(w[j:j + n]), set()).add(i)
    al_p = OUT / "aliases.json"
    al = json.loads(al_p.read_text(encoding="utf-8")) if al_p.exists() else {}
    by_place = _place_matches(rows)
    with open(OUT / "local_objects.jsonl", "w", encoding="utf-8") as f:
        hit = 0
        for k, l in _names():
            names = list(dict.fromkeys(v for x in [l] + al.get(k, []) for v in variants(x)))
            found: set[int] = set()
            for v in names:
                key = " ".join(re.findall(r"[^\W_]+(?:['’][^\W_]+)?", _fold(v).replace("-", " ")))
                if len(key) >= 3:
                    found |= index.get(key, set())
            found |= set(by_place.get(k, []))
            objs = [{"source": rows[i]["source"], "id": rows[i]["id"], "name": rows[i].get("object_name") or rows[i].get("title"),
                     "people": rows[i]["people"]} for i in sorted(found)]
            f.write(json.dumps({"key": k, "label": l, "objects": objs}, ensure_ascii=False) + "\n")
            hit += bool(objs)
    print(f"{len(rows)} Met + Cleveland rows with a people field; {hit} of {len(_names())} names match at least one")


EU_LANGS = ["en", "sv", "nl", "de", "es", "fr", "cs", "da", "nb", "fi", "it", "pt", "pl", "hu"]
EU_MAX = 500   # items fetched per people
# Names whose Europeana hits are something else. Kongo: 500 sampled hits name the
# country, not the people. Known but kept (2026-10-04): the 91 Tanka objects are
# Tibetan thangkas, which Stockholm also spells "tanka".
_EU_GEO_ONLY_NAMES = {"Q640090"}
# the fields an object keeps: enough for the judge and for europeana._to_canonical
_EU_FIELDS = ("id", "guid", "title", "dcCreator", "dcDescription", "dcSubject", "year", "dataProvider", "rights", "edmPreview",
              "edmIsShownBy", "edmPlaceLabel", "country", "edmType")


def _country_labels(rows: list[dict]) -> dict[str, set[str]]:
    """key -> the people's countries, folded, in EU_LANGS: every country Wikidata
    links to the item (P17 country, P2341 indigenous to, P27, P495) plus the
    peoples.json country. The ethnographic museums write the origin in their own
    language: "Kamerun", "Centralafrikanska republiken", "Filippinerna"."""
    langs = ", ".join(f'"{l}"' for l in EU_LANGS)
    out: dict[str, set[str]] = {r["key"]: {_fold(r["country"])} if r.get("country") else set() for r in rows}
    qids = [r["key"] for r in rows if r["key"].startswith("Q")]
    for i in range(0, len(qids), 100):
        vals = " ".join("wd:" + k for k in qids[i:i + 100])
        for x in _sparql(f"""SELECT ?g ?lab WHERE {{ VALUES ?g {{ {vals} }} ?g wdt:P17|wdt:P2341|wdt:P27|wdt:P495 ?c .
                              ?c rdfs:label ?lab FILTER(lang(?lab) IN ({langs})) }}"""):
            out[x["g"].split("/")[-1]].add(_fold(x["lab"]))
    en = sorted({r["country"] for r in rows if r.get("country")})
    lab: dict[str, set[str]] = {}
    for i in range(0, len(en), 100):
        vals = " ".join('"%s"@en' % c.replace('"', "") for c in en[i:i + 100])
        for x in _sparql(f"""SELECT ?en ?lab WHERE {{ VALUES ?en {{ {vals} }} ?c rdfs:label ?en ; wdt:P31 wd:Q6256 .
                              ?c rdfs:label ?lab FILTER(lang(?lab) IN ({langs})) }}"""):
            lab.setdefault(x["en"], set()).add(_fold(x["lab"]))
    for r in rows:
        out[r["key"]] |= lab.get(r.get("country"), set())
    return out


def cmd_europeana_objects(only: list[str]) -> None:
    """Europeana objects at ethnographic providers (_EU_GOOD) whose text names
    the people in an identity field AND one of its countries, for every listed
    people. Geography fields can support the country check but cannot establish
    the people's identity: otherwise Kongo place labels admit other peoples.
    The name must appear as a whole word, case-sensitively when it has four
    letters or fewer: the short names are the ones that are common words
    elsewhere ("dan" is Dutch for "than", "mano" Spanish for "hand", "Fur" folds
    to German "für"), while Swedish museums write longer names lower-case (71 of
    100 Inuit records say "inuit"). A provider whose name holds a double quote
    ("Dimitrie Gusti" National Village Museum) is escaped in the query; unescaped
    it broke the query and emptied Maya. Sámi stays thin: the records say
    "samer", "samisk", "saame", and "Sami" is a Finnish first name.
    A museum subject tag (dcSubject, also read from dcSubjectLangAware, where
    the Finnish Heritage Agency keeps "vepsäläiset") that names the people in
    the variant's own case admits an object without the country check; the
    case rule keeps out "votes" and Finnish "friisit" (friezes). Europeana's
    automatic concepts (edmConceptLabel) count as identity but keep the country
    check. Measured 2026-10-04: Vepsians 0 -> 76, Transylvanian Saxons 0 -> 500
    (ASTRA, named only in concepts), Nenets 0 -> 11; Votes 37 -> 1 and Frisians
    16 -> 1 once the case rule was added.
    dcCreator is read with the text: the Stockholm Museum of Ethnography files
    the maker culture there ("Inuit"); without it Inuit kept 21 of 500.
    The country check removes the namesakes: "Toba" is also Lake Toba (Batak
    cloth), "Guinea" also New Guinea (dropped before matching).
    Sampled 2026-09-26 on 19 peoples, reading the kept items: Gbaya 401 kept
    (Gothenburg's Hilberth collection), Igorot 337, Guna 323, Aymara 310,
    Tibetan 223, Navajo 140, Quechua 113 — all naming the right people; Dan 1
    wrong, and 0-1 for Ha, Mano, Sara, Masa, Bara, Lega, Banda, Fur: Europeana
    adds little to the small African peoples. Much of what is kept is weak
    (medicinal plants, seeds, catalogue cards); the pick's QUALITY line drops it.
    Kongo is skipped after 500 sampled records supplied no reliable
    people-specific hits: "Kongo" was only a geographic label.
    -> data/world/eu_objects.jsonl (gitignored); the last line per key wins.
    Listed and unreviewed-only peoples are both searched."""
    from folk_patterns.museums.europeana import _get_key
    key = _get_key()
    pe = [r for r in json.loads((OUT / "peoples.json").read_text(encoding="utf-8"))
          if r.get("listed") or r.get("unvetted_only")]
    want = {s.lower() for s in only}
    if want:
        pe = [r for r in pe if {r["key"].lower(), r["label"].lower(), re.sub(r"\s+peoples?$", "", r["label"].lower()),
                                (r.get("atlas") or "").lower()} & want]
    p = OUT / "eu_objects.jsonl"
    done = {json.loads(l)["key"] for l in p.read_text(encoding="utf-8").splitlines()} if p.exists() and not want else set()
    todo = [r for r in pe if r["key"] not in done]
    print(f"europeana-objects: {len(todo)} peoples ({len(done)} cached); country labels ...", flush=True)
    countries = _country_labels(todo)
    base = {"wskey": key, "media": "true", "reusability": "open,permission", "qf": "TYPE:IMAGE"}
    with httpx.Client(timeout=60, headers=UA) as cl, open(p, "a", encoding="utf-8") as f:
        for i, r in enumerate(todo):
            if r["key"] in _EU_GEO_ONLY_NAMES:
                f.write(json.dumps({"key": r["key"], "label": r["label"], "objects": []}, ensure_ascii=False) + "\n")
                f.flush()
                print(f"  {i + 1}/{len(todo)} {r['label']}: 0 kept (geographic-name collision)", flush=True)
                continue
            names = list(dict.fromkeys(v for x in [r["label"], r.get("atlas") or ""] if x for v in _ml_variants(r["key"], x)))
            if not names:
                f.write(json.dumps({"key": r["key"], "label": r["label"], "objects": []}, ensure_ascii=False) + "\n")
                f.flush()
                continue
            nrx = re.compile(r"(?<![\w-])(" + "|".join(re.escape(n) if len(n) <= 4 else "(?i:" + re.escape(n) + ")"
                                                        for n in names) + r")(?![\w-])")
            # Tags are matched in each variant's own case: subject vocabularies write
            # peoples as the language does ("Vepsians", "vepsäläiset") and common
            # nouns lower-case ("votes", Finnish "friisit" = friezes).
            trx = re.compile(r"(?<![\w-])(" + "|".join(re.escape(n) for n in names if len(n) > 4) + r")(?![\w-])")                 if any(len(n) > 4 for n in names) else None
            cs = countries.get(r["key"]) or set()
            crx = re.compile(r"\b(" + "|".join(re.escape(c) for c in sorted(cs, key=len, reverse=True)) + r")\b") if cs else None
            objs: dict[str, dict] = {}
            fetched = 0
            for n in names:
                try:
                    fj = cl.get("https://api.europeana.eu/record/v2/search.json", params={
                        **base, "query": f'"{n}"', "rows": 0, "profile": "facets", "facet": "DATA_PROVIDER",
                        "f.DATA_PROVIDER.facet.limit": 300}).json()
                except (httpx.HTTPError, ValueError) as e:
                    print(f"  ! {n}: {type(e).__name__}", flush=True)
                    continue
                provs = [x["label"] for fc in fj.get("facets", []) for x in fc["fields"]
                         if any(g in x["label"].lower() for g in _EU_GOOD)]
                cursor = "*"
                while provs and cursor and fetched < EU_MAX:
                    q = f'"{n}" AND (' + " OR ".join('DATA_PROVIDER:"' + pv.replace('"', '\\"') + '"' for pv in provs) + ")"
                    try:
                        j = cl.get("https://api.europeana.eu/record/v2/search.json",
                                   params={**base, "query": q, "rows": 100, "cursor": cursor, "profile": "rich"}).json()
                    except (httpx.HTTPError, ValueError) as e:
                        print(f"  ! {n}: {type(e).__name__}", flush=True)
                        break
                    if not j.get("success", True):
                        print(f"  ! {n}: {str(j.get('error'))[:120]}", flush=True)
                        break
                    items = j.get("items") or []
                    fetched += len(items)
                    for it in items:
                        identity = " | ".join(str(v) for k in ("title", "dcCreator", "dcDescription", "dcSubject", "edmConceptLabel")
                                              for v in (it.get(k) or []))
                        geography = " | ".join(str(v) for k in ("edmPlaceLabel", "dcCoverage", "dcSpatial")
                                               for v in (it.get(k) or []))
                        geo = re.sub(r"\b(new|nieuw|nya|neu|nouvelle|nueva|nuova|nova)[ -]guin\w*", "", _fold(identity + " | " + geography))
                        # A museum subject tag naming the people is its own attribution, so
                        # it needs no country: the Finnish Heritage Agency tags Vepsian cloth
                        # "vepsäläiset" with no place. Europeana's automatic concepts
                        # (edmConceptLabel) only count as identity and keep the country
                        # check: they name the Transylvanian Saxons at ASTRA, but also tag
                        # every sandal "Sandal", a Wikidata alias of the Santal.
                        tags = " | ".join([str(v) for v in (it.get("dcSubject") or [])]
                                         + [v for vs in (it.get("dcSubjectLangAware") or {}).values() for v in vs])
                        tagged = bool(trx and trx.search(tags))
                        if it["id"] in objs or not (nrx.search(identity) or tagged):
                            continue
                        if not tagged and not (crx and crx.search(geo)):
                            continue
                        if not (it.get("edmIsShownBy") or it.get("edmPreview")):
                            continue
                        slim = {k: it[k] for k in _EU_FIELDS if it.get(k)}
                        if slim.get("dcDescription"):
                            slim["dcDescription"] = [" ".join(slim["dcDescription"])[:600]]
                        objs[it["id"]] = {"source": "europeana", "id": it["id"], "name": (it.get("title") or [""])[0][:120],
                                          "item": slim}
                    cursor = j.get("nextCursor") if items else None
                    time.sleep(0.2)
            f.write(json.dumps({"key": r["key"], "label": r["label"], "objects": list(objs.values())}, ensure_ascii=False) + "\n")
            f.flush()
            print(f"  {i + 1}/{len(todo)} {r['label']}: {len(objs)} kept of {fetched}", flush=True)


@__import__("functools").lru_cache(maxsize=1)
def _eu_index() -> dict[str, dict]:
    p = OUT / "eu_objects.jsonl"
    if not p.exists():
        return {}
    return {o["id"]: o["item"] for l in p.read_text(encoding="utf-8").splitlines() for o in json.loads(l)["objects"]}


def _bm_name(k: str) -> str | None:
    """The spelling the BM answered to (first pass or alias pass)."""
    for d in (_counts("bm").get(k), _counts("bm_alias").get(k)):
        for v, n in ((d or {}).get("hits") or {}).items():
            if n:
                return v
    return None


def cmd_harvest(pages: int, threshold: int = 30, refill: bool = False) -> None:
    """Object names of every classified people from the BM list pages, up to
    `pages` x 100 per people — enough to count its categories, no images.
    -> data/world/bm_objects.jsonl (gitignored)."""
    sys.path.insert(0, str(REPO / "scripts"))
    from harvest_pool import _bm_teasers
    from folk_patterns.museums.british_museum import _client, SEARCH_URL
    from concurrent.futures import ThreadPoolExecutor
    cls = json.loads((OUT / "classified.json").read_text(encoding="utf-8"))
    out_p = OUT / "bm_objects.jsonl"
    done = {json.loads(l)["key"] for l in out_p.read_text(encoding="utf-8").splitlines()} if out_p.exists() else set()
    strong = {r["key"] for r in _rows() if r["bm"] >= threshold}   # Europeana-only hits are mostly word collisions
    todo = [(k, _bm_name(k)) for k, d in cls.items() if d.get("people") and k in strong and k not in done]
    todo = [(k, n) for k, n in todo if n]
    if refill:   # re-fetch, in full, every people an earlier run stopped at its page cap (the last line per key wins)
        last = {d["key"]: d for d in (json.loads(l) for l in out_p.read_text(encoding="utf-8").splitlines())}
        todo = [(k, d["bm_name"]) for k, d in last.items() if len(d["objects"]) >= 500 and len(d["objects"]) % 100 == 0]
    print(f"harvest: {len(todo)} peoples ({len(done)} cached)", flush=True)
    c = _client()

    def one(kn: tuple[str, str]) -> dict:
        k, n = kn
        objs = []
        for page in range(pages):
            r = c.get(SEARCH_URL, params={"ethnic_name": n, "image": "true", "page": page})
            if r.status_code != 200:
                raise RuntimeError(f"BM answered {r.status_code} for {n}")
            ts = _bm_teasers(r.text)
            objs += [{"id": t["id"], "name": t["title"], "date": t["meta"].get("Production date")} for t in ts]
            time.sleep(0.3)
            if len(ts) < 100:
                break
        return {"key": k, "bm_name": n, "objects": objs}

    with open(out_p, "a", encoding="utf-8") as f, ThreadPoolExecutor(3) as ex:
        for i, d in enumerate(ex.map(one, todo)):
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
            f.flush()
            if i % 25 == 0:
                print(f"  {i}/{len(todo)} {d['bm_name']}: {len(d['objects'])}", flush=True)


CLEANUP_PROMPT = """Below is a list of peoples (LIST, one per line: id | name | country | region), and the
cultures an atlas already has (ATLAS). For each line of BATCH, answer:

- atlas: the ATLAS name that is the same people, or "" (e.g. "Asante people" -> "Ashanti",
  "Amhara people" -> "Amhara", "Kazakhs" -> "Kazakh"; match the people, not just the country).
- same_as: the id of another LIST entry that is the same people under another name
  (Boer / Afrikaners, Lokono / Arawak), or "". Point to the better-known name.
- part_of: the id of another LIST entry, or an ATLAS name, of which this is a sub-group — a clan,
  iwi, lineage, sub-tribe, or local branch (Ngāti Kahungunu -> Māori, Thembu -> Xhosa, Aro -> Igbo),
  or "". Only when it is widely described as part of that people, not merely related or neighbouring.

Reply with a JSON array only, one object per BATCH line, in order:
[{{"id": "...", "atlas": "", "same_as": "", "part_of": ""}}]

ATLAS
{atlas}

LIST
{all}

BATCH
{batch}
"""


CLEANUP_MODEL = "claude-sonnet-5"   # Haiku got sub-groups wrong: Fante -> Ashanti, Nandi -> Maasai, Nguni -> Xhosa


def _json_objects(text: str) -> list[dict]:
    """Every {...} object in a reply, parsed one by one: one bad line must not lose the batch."""
    out = []
    for m in re.finditer(r"\{[^{}]*\}", text or ""):
        try:
            out.append(json.loads(m.group(0)))
        except ValueError:
            pass
    return out


def cmd_cleanup(min_cats: int, limit: int = 0) -> None:
    """Atlas match, duplicates and sub-groups for every listed people.
    The model sees the whole list, so it can point at another entry.
    -> data/world/cleanup.json"""
    import os, shutil, subprocess, tempfile
    pe = [r for r in json.loads((OUT / "peoples.json").read_text(encoding="utf-8"))
          if r["tier"] == "bm" and (r.get("cats3") or 0) >= min_cats]
    cache_p = OUT / "cleanup.json"
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    line = lambda r: f"{r['key']} | {r['label']} | {r.get('country') or ''} | {r.get('region') or ''}"
    allt = "\n".join(line(r) for r in pe)
    atlas = "\n".join(_atlas_names())
    todo = [r for r in pe if r["key"] not in cache][:limit or None]
    print(f"cleanup: {len(todo)} of {len(pe)} to check", flush=True)
    mcp = Path(tempfile.gettempdir()) / "empty_mcp.json"
    mcp.write_text('{"mcpServers":{}}', encoding="utf-8")
    raw = open(OUT / "cleanup_raw.jsonl", "a", encoding="utf-8")
    for i in range(0, len(todo), 80):
        batch = todo[i:i + 80]
        res = subprocess.run([shutil.which("claude") or "claude", "--print", "--model", CLEANUP_MODEL, "--effort", "low",
                              "--output-format", "json", "--tools", "", "--mcp-config", str(mcp), "--strict-mcp-config"],
                             input=CLEANUP_PROMPT.format(atlas=atlas, all=allt, batch="\n".join(line(r) for r in batch)),
                             capture_output=True, text=True, encoding="utf-8", timeout=900,
                             env={**os.environ, "MAX_THINKING_TOKENS": "0"})
        ev = json.loads(res.stdout)
        raw.write(json.dumps({"batch": i, "cost_usd": ev.get("total_cost_usd"), "result": ev.get("result")}, ensure_ascii=False) + "\n")
        raw.flush()
        got = _json_objects(ev.get("result"))
        keys = {r["key"] for r in batch}
        for d in got:
            if d.get("id") in keys:
                cache[d["id"]] = d
        cache_p.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"  batch {i}: {len(got)}/{len(batch)}, ${ev.get('total_cost_usd') or 0:.3f}", flush=True)


CLASSIFY_MODEL = "claude-haiku-4-5-20251001"
CLASSIFY_PROMPT = """For each entry below (a Wikidata "ethnic group" item with the first lines of its
English Wikipedia article), decide from the text:

- people: true only if it is a living or historically recent people / ethnic group with its own
  material culture (dress, crafts, objects). National peoples count (Germans, French, Hungarians
  have folk art), and so do regional peoples inside them (Transylvanian Saxons, Catalans).
  false for: a religion, church or institution; a caste or clan; a diaspora group; a people
  extinct before 1700 (Aztec, Medes, Romans); an umbrella grouping of many peoples ("Bantu
  peoples", "Slavs", "Melanesians", "Indigenous peoples of the Americas"); a racial or
  mixed-descent category ("Negro", "Coloured", "Creole").
- continent: one of Africa, Europe, Asia, Americas, Oceania.
- region: a short sub-region, e.g. "West Africa", "Central Asia", "Andes", "Melanesia".
- country: the main country of its homeland.

Use only the text given. Reply with a JSON array only, one object per entry, same order:
[{{"key": "...", "people": true, "continent": "...", "region": "...", "country": "..."}}]

Entries:
{entries}
"""

# Codex structured output needs an object at the top level, not an array.
CLASSIFY_SCHEMA = {
    "type": "object",
    "required": ["entries"],
    "additionalProperties": False,
    "properties": {"entries": {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["key", "people", "continent", "region", "country"],
        "properties": {
            "key": {"type": "string"},
            "people": {"type": "boolean"},
            "continent": {"type": "string"},
            "region": {"type": "string"},
            "country": {"type": "string"},
        },
        "additionalProperties": False,
    },
    }},
}


def _summary(cl: httpx.Client, article: str) -> str:
    title = article.rsplit("/", 1)[-1]
    for wait in (0, 5, 20):   # parallel fetches get 429s; an empty text makes Haiku answer "not a people"
        time.sleep(wait)
        try:
            r = cl.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}")
            if r.status_code == 200:
                return (r.json().get("extract") or "")[:600]
        except (httpx.HTTPError, ValueError):
            pass
    return ""


def _reply_array(text: str) -> list[dict]:
    try:
        value = json.loads(text or "")
        return value if isinstance(value, list) else []
    except (TypeError, ValueError):
        m = re.search(r"\[.*\]", text or "", re.S)
        try:
            value = json.loads(m.group(0)) if m else []
            return value if isinstance(value, list) else []
        except (TypeError, ValueError):
            return []


def cmd_classify(threshold: int, backend: str = "claude", all_min_sitelinks: int = 0) -> None:
    """Wikipedia summary + Claude/Codex for names that need classification.
    ``--all-min-sitelinks`` includes every sufficiently linked Wikidata item,
    even when it has no museum evidence, for the gap report."""
    import subprocess, tempfile
    cache_p = OUT / "classified.json"
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    wd = {r["qid"]: r for r in json.loads((OUT / "wikidata.json").read_text(encoding="utf-8"))}
    if all_min_sitelinks:
        rows = [dict(r, key=r["qid"]) for r in sorted(wd.values(), key=lambda x: -x.get("sitelinks", 0))
                if r.get("sitelinks", 0) >= all_min_sitelinks and r["qid"] not in cache]
    else:
        rows = [r for r in _rows() if (_museums(r) >= threshold or r["europeana"] >= 30) and r["key"] not in cache]
    print(f"classify: {len(rows)} names ({len(cache)} cached), backend={backend}", flush=True)
    with httpx.Client(timeout=30, headers=UA, follow_redirects=True) as cl:
        sums = [_summary(cl, r["article"]) if r.get("article") else "" for r in rows]
    print(f"  {sum(1 for x in sums if not x)} of {len(sums)} without article text", flush=True)
    with open(OUT / "classify_raw.jsonl", "a", encoding="utf-8") as raw:
        for i in range(0, len(rows), 60):
            batch = [(r, s) for r, s in zip(rows[i:i + 60], sums[i:i + 60])]
            entries = "\n".join(f'- key: {r["key"]} | name: {r["label"]} | wikidata country: {r.get("country") or "-"} | '
                                f'text: {s or "(no article text)"}' for r, s in batch)
            prompt = CLASSIFY_PROMPT.format(entries=entries)
            if backend == "codex":
                from folk_patterns.codex_cli import ask
                reply = ask(prompt, schema=CLASSIFY_SCHEMA, timeout=600)
                parsed = json.loads(reply) if isinstance(reply, str) else reply
                if isinstance(parsed, dict):
                    parsed = parsed.get("entries", [])
                result = json.dumps(parsed, ensure_ascii=False)
                ev = {"batch": i, "backend": backend, "cost_usd": 0, "total_cost_usd": 0, "result": result}
            else:
                mcp = Path(tempfile.gettempdir()) / "empty_mcp.json"
                mcp.write_text('{"mcpServers":{}}', encoding="utf-8")
                res = subprocess.run([__import__("shutil").which("claude") or "claude", "--print", "--model", CLASSIFY_MODEL, "--output-format", "json", "--tools", "",
                                      "--mcp-config", str(mcp), "--strict-mcp-config"],
                                     input=prompt, capture_output=True, text=True,
                                     encoding="utf-8", timeout=600, env={**__import__("os").environ, "MAX_THINKING_TOKENS": "0"})
                ev = json.loads(res.stdout)
                ev = {"batch": i, "backend": backend, "cost_usd": ev.get("total_cost_usd"),
                      "total_cost_usd": ev.get("total_cost_usd"), "result": ev.get("result")}
            raw.write(json.dumps(ev, ensure_ascii=False) + "\n")
            raw.flush()
            got = _reply_array(ev.get("result"))
            for d in got:
                if d.get("key") in {r["key"] for r, _ in batch}:
                    cache[d["key"]] = d
            cache_p.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
            print(f"  batch {i}: {len(got)}/{len(batch)} classified, ${ev.get('cost_usd') or 0:.3f}", flush=True)


def _local() -> dict[str, list[dict]]:
    p = OUT / "local_objects.jsonl"
    if not p.exists():
        return {}
    return {d["key"]: d["objects"] for d in (json.loads(l) for l in p.read_text(encoding="utf-8").splitlines())}


def _museums(r: dict) -> int:
    """Image objects in the museums with a people field: BM (first-page count) + Met + Cleveland."""
    return r["bm"] + r.get("local", 0)


def _europeana_count(key: str, english: dict[str, dict], multilingual: dict[str, dict]) -> int:
    en = max((english.get(key) or {}).get("hits", {}).values() or [0])
    ml = sum((multilingual.get(key) or {}).get("hits", {}).values())
    return max(en, ml)


def _rows() -> list[dict]:
    wd = {r["qid"]: r for r in json.loads((OUT / "wikidata.json").read_text(encoding="utf-8"))}
    bm, bma, eu, eu_ml = _counts("bm"), _counts("bm_alias"), _counts("europeana"), _counts("europeana_ml")
    loc = _local()
    atlas = set(_atlas_names())
    rows = []
    for k, l in _names():
        b = max(list((bm.get(k) or {}).get("hits", {0: 0}).values()) + list((bma.get(k) or {}).get("hits", {0: 0}).values()))
        e = _europeana_count(k, eu, eu_ml)
        w = wd.get(k, {})
        rows.append({"key": k, "label": l, "country": w.get("country"), "sitelinks": w.get("sitelinks"),
                     "article": w.get("article"), "bm": b, "local": len(loc.get(k, [])), "europeana": e,
                     "in_atlas": k.startswith("atlas:") or any(v in atlas for v in variants(l))})
    return rows


def _queue_skips() -> dict[str, str]:
    path = OUT / "onboard_queue.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(r.get("key")): str(r.get("skip")) for r in data.get("skipped", []) if r.get("key") and r.get("skip")}


def _evidence(r: dict) -> int:
    return int(r.get("bm") or 0) + int(r.get("local") or 0) + int(r.get("europeana") or 0)


# One record is enough to show a people unreviewed: the site gets a stub only
# when a candidate also survives europeana-objects' name and country checks and
# resolves to an image. At 6 the floor left out 24 European and 17 South Asian
# living peoples with 1-5 records (counted 2026-10-04).
UNVETTED_MIN_EVIDENCE = 1


def _unvetted_only(r: dict, queue_skips: dict[str, str] | None = None) -> bool:
    """Whether a classified, source-backed people can be shown text-only."""
    skip = (queue_skips or {}).get(str(r.get("key")), "")
    return (not r.get("listed")) and r.get("people") is True and _evidence(r) >= UNVETTED_MIN_EVIDENCE and (not skip or skip == "nation")


def _site_region(continent: str, region: str) -> str:
    """Map the classifier's free-text geography to one of the eleven site regions."""
    text = f"{continent} {region}".casefold()
    if "caucasus" in text:
        return "caucasus"
    if any(x in text for x in ("middle east", "north africa", "mena", "west asia", "southwest asia", "anatolia",
                               "levant", "mesopotamia", "arabia", "iran")):
        return "middle-east-north-africa"
    if "europe" in text:
        return "europe"
    if "central asia" in text:
        return "central-asia"
    if "south asia" in text:
        return "south-asia"
    if "south east asia" in text or "southeast asia" in text:
        return "southeast-asia"
    if "east asia" in text:
        return "east-asia"
    if "north america" in text or "arctic" in text:
        return "north-america"
    if "america" in text or "andes" in text or "caribbean" in text:
        return "latin-america"
    if "oceania" in text or "melanesia" in text or "micronesia" in text or "polynesia" in text:
        return "oceania"
    if "africa" in text:
        return "sub-saharan-africa"
    continent = continent.casefold()
    return {
        "europe": "europe",
        "asia": "east-asia",
        "americas": "latin-america",
        "north america": "north-america",
        "oceania": "oceania",
        "africa": "sub-saharan-africa",
    }.get(continent, "sub-saharan-africa")


# photo counts: the vetting keeps documentary photographs of dress, craft and daily life
# (docs/vetting.md); only unclassified does not.
_CATS = ["textile", "garment", "jewelry", "ceramic", "metalwork", "arms", "masks-ritual", "sculpture",
         "instruments", "household", "architectural", "painting-mss", "photo"]


# Kinds the Haiku mapping left unclassified that have an obvious category
# (top unclassified BM names, 2026-09-26). Samples ("vegetal remains", "dye
# sample") and money stay unclassified: they are not material culture to show.
_KIND_FIX = [
    (re.compile(r"divination|charm|amulet|ceremonial staff|religious/ritual|shrine|fetish", re.I), "masks-ritual"),
    (re.compile(r"adinkra|stamp|stencil|^pattern", re.I), "textile"),
    (re.compile(r"model building|model house|miniature", re.I), "sculpture"),   # a model is not a building
    (re.compile(r"house-post", re.I), "architectural"),
    (re.compile(r"mancala|doll|toy|walking-stick|game", re.I), "household"),
    # 2026-10-04, from the object names the mapping still left unclassified.
    # Names that say nothing ("artefact", empty, "Föremål") stay unclassified.
    (re.compile(r"\bmodell?\b|pappersfigur|paper figure", re.I), "sculpture"),   # before canoe/gun: a model canoe is a model
    (re.compile(r"plaque|tusk", re.I), "sculpture"),
    (re.compile(r"\bbaton\b|\bwand\b|\bstaff\b|soul-catcher|ceremonial object|offering|\bcross\b|processionskors|mask-mould", re.I), "masks-ritual"),
    (re.compile(r"\bgun\b|\barco\b|sashimono", re.I), "arms"),
    (re.compile(r"forowa", re.I), "metalwork"),
    (re.compile(r"ahuayo|sl[äa]nd|weaving equipment", re.I), "textile"),
    (re.compile(r"snow-shoe|\bplume\b|\broach\b|bandolera", re.I), "garment"),
    (re.compile(r"rattle|mungiga", re.I), "instruments"),
    (re.compile(r"tablet; document|bildskriftsh|kalender", re.I), "painting-mss"),
    (re.compile(r"canoe|\bboat\b|kayak|\bdice\b|gaming-piece|maika-piece|puzzle|playing-card|cat's cradle|quoit|football|\bboll\b|"
                r"swagger-stick|decorated egg|betel|snuff-container|pounder|karott|decoy|maniokpress|horse-bridle|furnishing", re.I), "household"),
]


# Peoples whose old court art the judge files as "archaeological" although the
# tradition is still practised. Edo: the Benin brass-casters' guild (Igun
# Street, Benin City) still works, so the 16th-century plaques are kept
# (decided 2026-10-04).
_LIVING_COURT = {"Q1287326"}


def _kinds() -> dict:
    p = REPO / "data" / "pool" / "kinds.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _art_form(kinds: dict, name: str | None) -> str:
    n = (name or "").strip()[:120]
    af = (kinds.get(n) or {}).get("art_form", "unclassified")
    if af == "unclassified":
        for rx, fix in _KIND_FIX:
            if rx.search(n):
                return fix
    return af


def cmd_candidates() -> None:
    """Every listed people's objects, grouped by category — the pool the
    5-per-category pick works from. One culture per object: an object the BM
    tags with several peoples (Nguni + Zulu, Akan + Asante) goes to the most
    specific one, the people with the fewest objects; the umbrella keeps the
    rest. -> data/world/candidates.jsonl (gitignored)."""
    pe = [r for r in json.loads((OUT / "peoples.json").read_text(encoding="utf-8"))
          if r.get("listed") or r.get("unvetted_only")]
    bm = {}
    for l in (OUT / "bm_objects.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(l)
        bm[d["key"]] = d["objects"]   # the last line per key wins (harvest --refill)
    loc, kinds = _local(), _kinds()
    eu = {}
    if (OUT / "eu_objects.jsonl").exists():
        for l in (OUT / "eu_objects.jsonl").read_text(encoding="utf-8").splitlines():
            d = json.loads(l)
            eu[d["key"]] = d["objects"]   # the last line per key wins (--only reruns)
    pool = {r["key"]: [dict(o, source="bm") for o in bm.get(r["key"], [])] + loc.get(r["key"], [])
            + [{k: v for k, v in o.items() if k != "item"} for o in eu.get(r["key"], [])] for r in pe}
    size = {k: len(v) for k, v in pool.items()}
    owner: dict[tuple, str] = {}
    for k, objs in pool.items():
        for o in objs:
            ref = (o.get("source"), o["id"])
            if ref not in owner or size[k] < size[owner[ref]]:
                owner[ref] = k
    with open(OUT / "candidates.jsonl", "w", encoding="utf-8") as f:
        moved = 0
        for r in pe:
            cats: dict[str, list] = {}
            for o in pool[r["key"]]:
                if owner[(o.get("source"), o["id"])] != r["key"]:
                    moved += 1
                    continue
                cats.setdefault(_art_form(kinds, o.get("name")), []).append(
                    {"source": o.get("source"), "id": o["id"], "name": o.get("name"),
                     "kind": (kinds.get((o.get("name") or "").strip()[:120]) or {}).get("kind")})
            f.write(json.dumps({"key": r["key"], "label": r["label"], "continent": r.get("continent"),
                                "region": r.get("region"), "country": r.get("country"), "in_atlas": r["in_atlas"],
                                "unvetted_only": bool(r.get("unvetted_only")),
                                "atlas": r.get("atlas"), "bm_name": r.get("bm_name"),
                                "counts": {c: len(v) for c, v in sorted(cats.items(), key=lambda x: -len(x[1]))},
                                "objects": cats}, ensure_ascii=False) + "\n")
    print(f"{len(pe)} peoples, {len(owner)} distinct objects; {moved} shared objects left to a more specific people")


PICK_MAX, PICK_TRIES = 5, 10


def _pick_order(objs: list[dict]) -> list[dict]:
    """Round-robin over kinds, commonest kind first, so the first five tried
    are five different things (bowl, cup, jar...) rather than five bowls."""
    by: dict[str, list] = {}
    for o in objs:
        by.setdefault(o.get("kind") or o.get("name") or "?", []).append(o)
    queues = sorted(by.values(), key=len, reverse=True)
    out = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


class _Blocked(Exception):
    """The British Museum answered 403: Cloudflare cookies expired."""


def _detail(o: dict, bm_client, http: httpx.Client) -> dict | None:
    """{title, description, place, image_url} of one candidate, from its museum.
    Raises _Blocked on a BM 403, which fetch_detail alone would report as None."""
    if o["source"] == "bm":
        from folk_patterns.museums.british_museum import fetch_detail, DETAIL_URL
        d = fetch_detail(bm_client, o["id"])
        if not d and bm_client.get(DETAIL_URL.format(uid=o["id"])).status_code == 403:
            raise _Blocked(o["id"])
        return d and dict(d, place="")
    if o["source"] == "europeana":
        it = _eu_index().get(o["id"])
        makers = list(dict.fromkeys(it.get("dcCreator") or [])) if it else []
        return it and {"title": (it.get("title") or [""])[0], "image_url": (it.get("edmIsShownBy") or it["edmPreview"])[0],
                       "fallback_url": (it.get("edmPreview") or [None])[0],
                       "description": ("Museum maker/creator: " + ", ".join(makers) + ". " if makers else "")
                       + " ".join(it.get("dcDescription") or []),
                       # edmPlaceLabel is one {"def": name} per language: the first Latin-script one
                       "place": next((p for p in ((x.get("def") if isinstance(x, dict) else x) for x in it.get("edmPlaceLabel") or [])
                                      if p and p.isascii()), "")}
    if o["source"] == "met":
        r = http.get(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{o['id']}")
        if r.status_code not in (200, 404):   # a block, not a missing object: let the caller retry
            raise httpx.HTTPStatusError(f"Met {r.status_code}", request=r.request, response=r)
        j = r.json() if r.status_code == 200 else {}
        return j.get("primaryImageSmall") and {"title": j.get("title") or j.get("objectName"), "image_url": j["primaryImageSmall"],
                                               "description": " · ".join(filter(None, [j.get("culture"), j.get("medium"), j.get("objectDate")])),
                                               "place": j.get("country") or ""}
    r = http.get(f"https://openaccess-api.clevelandart.org/api/artworks/{o['id']}")
    j = (r.json() or {}).get("data") or {} if r.status_code == 200 else {}
    img = ((j.get("images") or {}).get("web") or {}).get("url")
    return img and {"title": j.get("title"), "image_url": img, "place": "",
                    "description": " · ".join(filter(None, [j.get("culture") and ", ".join(j["culture"]), j.get("technique"), j.get("creation_date")]))}


PICK_QUALITY_MIN = 3
PICK_QUALITY = """
QUALITY — add one more line, QUALITY: <1-5>: how well this one picture would show a general viewer what this people makes, as one of the five chosen for its category.
5 a showpiece: distinctive of this people, well made, whole, clearly photographed (a Kuba raffia cloth, an Akan kuduo, a Haida crest pole)
4 a good, characteristic example of a type this people is known for
3 a sound but ordinary object that many neighbouring peoples make the same way (a plain spear, a gourd, a comb)
2 a fragment, a part, raw material, a toy, a plain tool or a dull repeat
1 barely worth showing, or a catalogue card, drawing of an object, or a picture of something else

ATTRIBUTION: This list assigns an object to a specific people. If the museum
only says "X-style", "X or Y", or "X or X-influenced neighbours", BELONGS is NO
unless the record provides independent evidence for a maker from X. A country
or findspot alone is not enough. Read non-English museum notes for such caveats.
"""


def _quality(reply: str) -> int:
    m = re.search(r"QUALITY:\s*([1-5])", reply or "", re.I)
    return int(m.group(1)) if m else 0


# Umbrella peoples whose BM facet returns records made by named member groups.
# Mangyan 2026-09-28: all 376 candidates were Hanunóo or Buid, and the pick kept 0.
# Luyia 2026-09-28: 12 of its first drops were "Bukusu" and 1 "Maragoli". Tiriki is
# left out: it is queued as a people of its own.
_BM_SUBGROUPS = {"mangyan": {"hanunóo", "hanunoo", "buid", "buhid", "iraya", "alangan",
                             "tadyawan", "tau-buid", "bangon", "ratagnon"},
                 "luyia": {"bukusu", "maragoli", "logoli", "idakho", "isukha", "kabras", "marama",
                           "wanga", "nyala", "tachoni", "samia", "nyole", "banyore", "marachi",
                           "kisa", "tsotso", "khayo"},
                 # 2026-09-29 coverage replay: "multiple peoples" drops whose second
                 # BM group is a spelling, a subgroup or a parent of the same people
                 # (docs/vetting.md -> "Pick coverage").
                 "chorote": {"choroti"},
                 "kalabari": {"ijo"},
                 "mohawk": {"iroquois"},
                 "konyak": {"naga"},
                 "fante": {"akan"},
                 "merina": {"malagasy"},
                 "hopi": {"moqui", "moki"},
                 "innu": {"montagnais"},
                 "arhuaco": {"bintukua"},
                 "nyamwezi": {"unyamwezi", "uniamezi"},
                 "inuit": {"labrador inuit", "canadian inuit", "iglulik", "inglulik", "iglulingmiut", "itivimiut"},
                 "tlingit": {"tlinkit", "chilkat", "sitka", "stikeen", "yakutat", "yuketat"},
                 "nuu-chah-nulth": {"hesquiaht", "moachat", "ahousaht", "clayoquot", "toquaht", "tseshaht"},
                 "kwakwaka'wakw": {"koskimo"},
                 "dinka": {"tuich", "agar"},
                 "moru": {"moru miza"},
                 "lahu": {"lahu na", "lahu shi", "lahu nyi"},
                 "shona": {"karanga", "korekore"},
                 "banyankole": {"bahima"},
                 "naga": {"angami", "ao", "chang", "zemi", "kalyo-kengyu"},
                 # 2026-10-02, the p003 peoples left under 5 objects
                 "kiga": {"bachiga"},
                 "jola": {"flup"},
                 "rizeigat": {"rizayqat"}}

# BM production groups that name a region or a language family, not a people,
# with the peoples each one covers: beside one of those it is ignored; beside
# anyone else it stays a second group (Chukchi are not Eskimo-Aleut, Luo not
# Bantu). A region alone names no maker.
_NWC = {"haida", "tlingit", "kwakwaka'wakw", "nuu-chah-nulth", "tsimshian"}
_BM_UMBRELLAS = {"northwest coast": _NWC, "northwest coast peoples": _NWC,
                 "southwest": {"hopi", "navajo"}, "puebloan": {"hopi"},
                 "northeast": {"micmac", "mohawk", "innu", "winnebago"},
                 "plains": {"winnebago", "pawnee", "osage", "crow", "lakota", "cheyenne"},
                 "southeast": {"cherokee", "choctaw"},
                 "arctic": {"inuit", "yupik", "inupiat", "chukchi"},
                 "arctic peoples": {"inuit", "yupik", "inupiat", "chukchi"},
                 "eskimo-aleut": {"inuit", "yupik", "inupiat", "cup'ig"},
                 "algonquian": {"micmac", "innu"}, "cariban": {"akawaio"}, "chuncho": {"campa"},
                 "east asian": {"shan"}, "aboriginal australian": {"tiwi"}, "dayak": {"kelabit"}}


def _umbrella(g: str, expected: str) -> bool:
    return expected in _BM_UMBRELLAS.get(re.sub(r"\s*\(.*$", "", g), set())


def _norm_group(g: str) -> str:
    return re.sub(r"\s+(?:people|peoples)$", "", g, flags=re.I).casefold()


def _source_exclusion(o: dict, d: dict, expected_bm_group: str = "") -> str:
    """Source labels that cannot establish an authentic maker attribution, or
    that mark human remains, which the atlas does not show."""
    expected = _norm_group(expected_bm_group) if expected_bm_group else ""
    allowed = {expected} | _BM_SUBGROUPS.get(expected, set())
    named = {_norm_group(g) for g in d.get("production_ethnic_groups") or []}
    groups = {g for g in named if not _umbrella(g, expected)}
    if o["source"] == "bm" and named and not groups:
        return f"museum names only a region or language family ({'; '.join(sorted(named))})"
    if o["source"] == "bm" and "(?)" in (d.get("production_ethnic_attribution") or ""):
        return f"museum marks production ethnic group uncertain ({d['production_ethnic_attribution']})"
    # an umbrella and its own member group ("Luyia; Bukusu") name one people
    if o["source"] == "bm" and len(groups) > 1 and not groups <= allowed:
        return f"museum attributes production to multiple peoples ({'; '.join(sorted(groups))})"
    if o["source"] == "bm" and expected and groups:
        if not groups & allowed:
            return f"museum attributes production to {', '.join(sorted(groups))}, not {expected}"
    if o["source"] == "cleveland" and re.search(r"\b[\w-]+-style maker\b", d.get("description") or "", re.I):
        return "style-only maker attribution"
    if o["source"] == "bm" and re.search(r"\b(?:fake|forgery)\b", d.get("title") or "", re.I):
        return "museum labels object a fake or forgery"
    # Tiv beaded skull and Anga preserved head, 2026-09-28: the pick kept both
    if o["source"] == "bm" and re.search(r"\bhuman remains\b", d.get("title") or "", re.I):
        return "museum classes object as human remains"
    return ""


def _choose(objs: list[dict]) -> list[dict]:
    """Every kept object of one category, best first (the first PICK_MAX are
    the featured ones): quality first, a good image before a
    weak one, and the best of each kind before a second of any kind, so five
    gold-weights never crowd out the one kuduo."""
    objs = sorted((o for o in objs if o["quality"] >= PICK_QUALITY_MIN),
                  key=lambda o: (-o["quality"], o["image"] != "good"))
    out, kinds = [], set()
    for o in objs:
        k = re.sub(r"[^a-z]", "", (o.get("kind") or o.get("name") or "").lower())
        if k not in kinds:
            out.append(o)
            kinds.add(k)
    out += [o for o in objs if o not in out]
    return out


def cmd_pick(only: list[str], shard: str = "", cached_only: bool = False, no_judge: bool = False,
             export_batch: str = "") -> None:
    """Up to PICK_MAX objects per category per people, fewer when fewer pass.
    Up to PICK_TRIES candidates per category, in _pick_order, are shown to the
    library's judge (scripts/vet_judge.py) with one line added: a QUALITY
    score 1-5 (PICK_QUALITY). Kept: BELONGS YES, IMAGE good or weak, ERA not
    modern or archaeological, QUALITY >= PICK_QUALITY_MIN. The judge often
    re-files (wooden bowls the kind list calls ceramic go to household), so
    every kept object is collected first and assigned to the judge's category
    afterwards, then _choose ranks each category by quality with one of each
    kind first. The file keeps every kept object in that order ("ranked"),
    the first PICK_MAX flagged "featured"; q1-q2 and every other verdict stay
    only in raw.jsonl. Objects already in the library are skipped. Judge replies are
    cached in picks/raw.jsonl, so a rerun only pays for new objects.
    -> data/world/picks/<key>.json.
    Measured 2026-09-26, before the QUALITY line, on 15 peoples (Rukai, Tiv,
    Oromo, Afar, Haida and 10 African): 473 judged, 371 kept, ~$0.009 per call,
    ~4.5 s each. The judge passed ~90% of what it saw, so without a score the
    first five to pass won: spinning tops as Ambundu sculpture, raw eggshell as
    Sukuma jewelry, four plain Akan gold-weights beside the one kuduo. Haida
    ceramic ends at 0 correctly: all 10 tried were wooden or argillite dishes
    (the Haida made no pottery).
    With the QUALITY line, same day, the 10 African peoples: 421 judged, 222
    picks, $3.80, ~5 min for the richest. Scores q1 17, q2 76, q3 165, q4 114,
    q5 7; the q1-q2 drops are what the earlier run wrongly kept (spinning
    tops, drum pegs, sinew, catalogue cards, raw eggshell).
    Every candidate that ends without a verdict goes to picks/ledger.jsonl with
    why; `coverage` reports it. --cached-only makes no judge call and writes no
    pick file: it replays the cached verdicts and records the other outcomes,
    and a candidate that would need a new verdict is logged as awaiting_judge.
    --no-judge does the same but writes the pick file: the way to rebuild picks
    after verdicts came from elsewhere (the cloud, docs/cloud-vetting.md).
    --export-batch NAME (with --cached-only) also writes every awaiting_judge
    candidate to data/vet_batches/NAME.jsonl for cloud_vet_batch.py; its
    verdicts come back through `pick-import NAME`."""
    import os
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "src"))
    from vet_judge import judge, build_record, answered_by
    from vet_images import parse_reply
    from folk_patterns.museums.british_museum import _client, _in_library
    judge_name = "pick:claude-sonnet-5"   # replaced per call by the backend that answered
    want = {s.lower() for s in only}
    exclusions = {(x["key"], x["source"], x["id"])
                  for x in json.loads((OUT / "pick_exclusions.json").read_text(encoding="utf-8"))}
    override_path = OUT / "pick_overrides.json"
    overrides = {(x["key"], x["source"], x["id"]): x
                 for x in json.loads(override_path.read_text(encoding="utf-8"))} if override_path.exists() else {}
    rows = [json.loads(l) for l in (OUT / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if not want or {r["key"].lower(), r["label"].lower(), re.sub(r"\s+peoples?$", "", r["label"].lower()),
                                             (r.get("atlas") or "").lower()} & want]
    if shard:   # "i/n": this process takes every n-th people, with its own raw log
        i, n = map(int, shard.split("/"))
        rows = rows[i::n]
    print(f"{len(rows)} peoples: {', '.join(r['label'] for r in rows)}", flush=True)
    (OUT / "picks").mkdir(exist_ok=True)
    raw = OUT / "picks" / (f"raw-{shard.split('/')[0]}.jsonl" if shard else "raw.jsonl")
    ledger = OUT / "picks" / (f"ledger-{shard.split('/')[0]}.jsonl" if shard else "ledger.jsonl")

    def note(key: str, cat: str, o: dict, status: str, reason: str = "") -> None:
        """Every candidate that ends without a judge verdict, with why (see `coverage`)."""
        with open(ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "category": cat, "source": o["source"], "id": o["id"], "status": status,
                                "reason": reason, "at": time.strftime("%Y-%m-%d")}, ensure_ascii=False) + "\n")
    seen = {}
    for l in (l for p in sorted((OUT / "picks").glob("raw*.jsonl")) for l in p.read_text(encoding="utf-8").splitlines()):
        x = json.loads(l)
        if "QUALITY:" in (x.get("reply") or ""):
            seen[(x["key"], x["source"], x["id"])] = x
    bm_client = _client() if os.environ.get("BM_CDP_URL") else None
    http = httpx.Client(timeout=45, follow_redirects=True, headers=UA)
    batch = REPO / "data" / "vet_batches" / f"{export_batch}.jsonl" if export_batch else None
    if batch:
        batch.write_text("", encoding="utf-8")
    for r in rows:
        name = r.get("atlas") or re.sub(r"\s+(people|peoples)$", "", r["label"])
        accepted: list[dict] = []
        # A rerun skips objects already in the library and may not reach the rest
        # (new candidates take the tries first): earlier kept objects this run does
        # not re-judge stay in the pick file, so it keeps listing everything kept.
        pf = OUT / "picks" / f"{r['key']}.json"
        previous = [o for v in json.loads(pf.read_text(encoding="utf-8"))["ranked"].values() for o in v] if pf.exists() else []
        evaluated: set = set()
        tried = cached = cost = 0
        t0 = time.time()
        for cat, objs in r["objects"].items():
            if cat == "unclassified":
                continue
            n = 0
            for o in _pick_order(objs):
                if n >= PICK_TRIES:
                    break
                if (r["key"], o["source"], o["id"]) in exclusions:
                    continue
                if any(a["source"] == o["source"] and a["id"] == o["id"] for a in accepted):
                    continue
                if o["source"] == "bm" and bm_client is None:
                    continue
                if o["source"] == "bm" and _in_library(o["id"]):
                    note(r["key"], cat, o, "in_library")
                    continue
                n += 1
                hit = seen.get((r["key"], o["source"], o["id"]))
                if hit:
                    d, reply, err = hit["detail"], hit["reply"], ""
                    if o["source"] == "bm" and "production_ethnic_attribution" not in d:
                        # Earlier cached judgments saw only the facet value and
                        # missed qualifiers such as "Made by: Luba (?)".
                        current = _detail(o, bm_client, http)
                        if not current:
                            print(f"  {cat:13s} {o['id']:22s} attribution unavailable", flush=True)
                            note(r["key"], cat, o, "fetch_failed", "attribution unavailable")
                            continue
                        d = {**d, **current}
                    cached += 1
                else:
                    def fetch():
                        d = _detail(o, bm_client, http)
                        if not d:
                            return d, None
                        if o["source"] == "europeana":
                            try:
                                img = http.get(d["image_url"], timeout=12)
                            except httpx.RequestError:
                                img = None
                            if (img is None or img.status_code != 200 or
                                    not img.headers.get("content-type", "").startswith("image/")) and d.get("fallback_url"):
                                img = http.get(d["fallback_url"])
                                d["image_url"] = d["fallback_url"]
                        else:
                            img = (bm_client if o["source"] == "bm" else http).get(d["image_url"])
                        if img is not None and o["source"] == "bm" and img.status_code == 403:
                            raise _Blocked(o["id"])
                        return d, img
                    try:
                        try:
                            d, img = fetch()
                        except _Blocked:   # cookies expired: fetch fresh ones from Chrome, once
                            print("  British Museum 403 - refreshing cookies", flush=True)
                            bm_client = _client()
                            try:
                                d, img = fetch()
                            except _Blocked:
                                sys.exit(f"British Museum still 403 after a cookie refresh; {name} not written. "
                                         "Rerun: judged objects come from the cache.")
                    except Exception as e:   # one museum timing out must not end the run
                        print(f"  {cat:13s} {o['id']:22s} fetch failed: {type(e).__name__}", flush=True)
                        note(r["key"], cat, o, "fetch_failed", type(e).__name__)
                        continue
                    if not d:
                        print(f"  {cat:13s} {o['id']:22s} no image", flush=True)
                        note(r["key"], cat, o, "no_image")
                        continue
                    if img is None:
                        print(f"  {cat:13s} {o['id']:22s} image fetch failed", flush=True)
                        note(r["key"], cat, o, "fetch_failed", "image")
                        continue
                    if img.status_code != 200 or (o["source"] == "europeana" and not img.headers.get("content-type", "").startswith("image/")):
                        print(f"  {cat:13s} {o['id']:22s} image {img.status_code}", flush=True)
                        note(r["key"], cat, o, "fetch_failed", f"image HTTP {img.status_code}")
                        continue
                    source_exclusion = _source_exclusion(o, d, r.get("bm_name") or name)
                    if source_exclusion:
                        # No judge call, so no try used: Tlingit 2026-09-27 lost 64 of its
                        # 95 tries to BM "multiple peoples" / "uncertain" flags.
                        print(f"  {cat:13s} {o['id']:22s} drop: {source_exclusion}", flush=True)
                        note(r["key"], cat, o, "source_rule", source_exclusion)
                        evaluated.add((o["source"], o["id"]))
                        n -= 1
                        continue
                    if cached_only or no_judge:
                        note(r["key"], cat, o, "awaiting_judge")
                        if batch:
                            description = (d.get("description") or "") + (
                                " Museum production ethnic group: " + d["production_ethnic_attribution"]
                                if d.get("production_ethnic_attribution") else "")
                            bkey = hashlib.sha1(f"{r['key']}|{o['source']}|{o['id']}".encode()).hexdigest()[:16]
                            with open(batch, "a", encoding="utf-8") as f:
                                f.write(json.dumps({
                                    "id": f"{r['key']}|{o['source']}|{o['id']}", "key": bkey,
                                    "prompt": build_record(name, r.get("country") or "", cat, d.get("title") or o.get("name") or "",
                                                           description, d.get("place") or ""),
                                    "extra": PICK_QUALITY, "urls": [d["image_url"]], "image_path": f"work/img/{bkey}.jpg",
                                    "meta": {"key": r["key"], "category": cat, "object": o, "detail": d}},
                                    ensure_ascii=False) + "\n")
                        continue
                    ev: dict = {}
                    description = d.get("description") or ""
                    if d.get("production_ethnic_attribution"):
                        description += " Museum production ethnic group: " + d["production_ethnic_attribution"]
                    reply, err = judge(build_record(name, r.get("country") or "", cat, d.get("title") or o.get("name") or "",
                                                    description, d.get("place") or ""),
                                       img.content, on_attempt=lambda a, s, res, e: ev.update(res or {}), extra=PICK_QUALITY)
                    if err and any(word in err.lower() for word in ("hit your limit", "usage limit", "quota", "rate limit")):
                        raise SystemExit(f"Subscription limit reached while picking {name}: {err}. "
                                         "Rerun after the reset; completed verdicts are cached.")
                    judge_name = f"pick:{answered_by()}"
                    tried += 1
                    cost += ev.get("total_cost_usd") or 0
                    with open(raw, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"key": r["key"], "category": cat, **o, "detail": d, "reply": reply, "error": err,
                                            "judge": judge_name,
                                            "cost_usd": ev.get("total_cost_usd")}, ensure_ascii=False) + "\n")
                source_exclusion = _source_exclusion(o, d, r.get("bm_name") or name)
                if source_exclusion:
                    # uses no try, as on a fresh fetch above: Maya 2026-09-28 fell from
                    # 26 kept to 16 on a cached rerun when these drops counted
                    print(f"  {cat:13s} {o['id']:22s} drop: {source_exclusion}", flush=True)
                    note(r["key"], cat, o, "source_rule", source_exclusion)
                    evaluated.add((o["source"], o["id"]))
                    n -= 1
                    continue
                belongs, af, reason, conf, image, era = parse_reply(reply) if reply else (None, None, err, "", "", "")
                q = _quality(reply)
                evaluated.add((o["source"], o["id"]))
                # A museum photograph the judge files as a spear, bowl or mask is a
                # picture of an object: the object record is the gallery item, and
                # the print is not a documentary photo of people. Asmat 2026-09-27:
                # 17 BM "photographic print" records (EA_Oc-B142-*) re-filed this way;
                # Tlingit: BM postcards of totem poles (EA_Am-B59-*) filed as sculpture.
                if re.match(r"photographic print|photograph\b|postcard", d.get("title") or "", re.I) and af not in (None, "photo"):
                    print(f"  {cat:13s} {o['id']:22s} drop: photograph of an object ({af})", flush=True)
                    note(r["key"], cat, o, "photo_of_object", af)
                    continue
                if era == "archaeological" and r["key"] in _LIVING_COURT:
                    era = "traditional"
                ok = belongs and era not in ("modern", "archaeological") and image in ("good", "weak") and q >= PICK_QUALITY_MIN
                if belongs and era not in ("modern", "archaeological") and image in ("good", "weak"):
                    accepted.append({**o, "title": d.get("title"), "image_url": d["image_url"],
                                     "judge": (hit.get("judge") if hit else judge_name) or "pick:claude-sonnet-5",
                                     "art_form": af if af and af != "unclassified" else cat,
                                     "image": image, "era": era, "quality": q, "confidence": conf, "reason": reason})
                print(f"  {cat:13s} {o['id']:22s} {'KEEP' if ok else 'drop'} q{q} {af or '-':13s} {image:6s} {era:14s} "
                      f"{'(cached) ' if hit else ''}{reason[:80]}", flush=True)
        for o in previous:
            if ((o["source"], o["id"]) not in evaluated and (r["key"], o["source"], o["id"]) not in exclusions
                    and not any(a["source"] == o["source"] and a["id"] == o["id"] for a in accepted)):
                accepted.append({k: v for k, v in o.items() if k != "featured"})
        by: dict[str, list] = {}
        for a in accepted:
            override = overrides.get((r["key"], a["source"], a["id"]))
            if override and override.get("art_form"):
                a["art_form"] = override["art_form"]
                a["judge"] += "+manual-category"
            by.setdefault(a["art_form"], []).append(a)
        ranked = {c: v for c, v in ((c, _choose(v)) for c, v in by.items()) if v}
        for v in ranked.values():
            for i, o in enumerate(v):
                o["featured"] = i < PICK_MAX
        picks = {c: v[:PICK_MAX] for c, v in ranked.items()}
        if cached_only:
            print(f"{name}: outcomes recorded (cached-only, pick file unchanged)", flush=True)
            continue
        (OUT / "picks" / f"{r['key']}.json").write_text(json.dumps(
            {"key": r["key"], "label": r["label"], "name": name, "judged": tried, "cached": cached, "cost_usd": round(cost, 3),
             "ranked": ranked}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{name}: {sum(map(len, ranked.values()))} kept, {sum(map(len, picks.values()))} featured in {len(picks)} categories "
              f"({', '.join(f'{c} {len(v)}' for c, v in picks.items())}); {tried} judged, {cached} cached, ${cost:.2f}, {time.time() - t0:.0f}s", flush=True)


def cmd_pick_import(name: str) -> None:
    """Cloud verdicts for a pick batch (data/vet_verdicts/NAME.jsonl, from
    cloud_vet_batch.py collect) -> picks/raw-cloud.jsonl, the pick's judge cache.
    Then `pick --no-judge` rebuilds the pick files from the cache."""
    rows = [json.loads(l) for l in (REPO / "data" / "vet_verdicts" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    n = 0
    with open(OUT / "picks" / "raw-cloud.jsonl", "a", encoding="utf-8") as f:
        for v in rows:
            if not v.get("reply") or "QUALITY:" not in v["reply"]:
                continue
            m = v["meta"]
            f.write(json.dumps({"key": m["key"], "category": m["category"], **m["object"], "detail": m["detail"],
                                "reply": v["reply"], "error": "", "judge": "pick:cloud-claude-sonnet-5",
                                "batch": name}, ensure_ascii=False) + "\n")
            n += 1
    print(f"{name}: {n} of {len(rows)} verdicts added to the pick cache "
          f"({len(rows) - n} without a QUALITY reply: download failures or unparsed)")


def cmd_coverage(only: list[str]) -> None:
    """What the pick has looked at, per candidate, for every people with a pick file.
    Joins candidates.jsonl, the judge log (picks/raw*.jsonl), the outcome ledger
    (picks/ledger*.jsonl, every candidate that ended without a verdict and why)
    and pick_exclusions.json. Statuses: kept, judged-drop (with the failing
    field), review-excluded, the ledger's no_image / fetch_failed / source_rule /
    photo_of_object / in_library, and not_reached (never tried: the PICK_TRIES
    cap, or a BM candidate in a run without BM_CDP_URL). raw and candidates are
    gitignored and live on one machine, so the per-candidate result is written
    to data/world/pick_coverage.jsonl, which is committed."""
    sys.path.insert(0, str(REPO / "scripts"))
    from vet_images import parse_reply
    want = {s.lower() for s in only}
    judged: dict = {}
    for p in sorted((OUT / "picks").glob("raw*.jsonl")):
        for l in p.read_text(encoding="utf-8").splitlines():
            x = json.loads(l)
            if "QUALITY:" in (x.get("reply") or ""):
                judged[(x["key"], x["source"], x["id"])] = x["reply"]
    ledger: dict = {}
    for p in sorted((OUT / "picks").glob("ledger*.jsonl")):
        for l in p.read_text(encoding="utf-8").splitlines():
            x = json.loads(l)
            ledger[(x["key"], x["source"], x["id"])] = x   # the latest outcome wins
    excluded = {(x["key"], x["source"], x["id"]): x.get("reason", "")
                for x in json.loads((OUT / "pick_exclusions.json").read_text(encoding="utf-8"))}
    out, rows = [], []
    for l in (OUT / "candidates.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(l)
        pf = OUT / "picks" / f"{r['key']}.json"
        if not pf.exists() or (want and not {r["key"].lower(), r["label"].lower(), re.sub(r"\s+peoples?$", "", r["label"].lower()),
                                             (r.get("atlas") or "").lower()} & want):
            continue
        kept = {(o["source"], o["id"]) for v in json.loads(pf.read_text(encoding="utf-8"))["ranked"].values() for o in v}
        counts: dict[str, int] = {}
        for cat, objs in r["objects"].items():
            for o in objs:
                k = (r["key"], o["source"], o["id"])
                reason = ""
                if (o["source"], o["id"]) in kept:
                    status = "kept"
                elif k in excluded:
                    status, reason = "review_excluded", excluded[k]
                elif k in ledger and (ledger[k]["status"] == "photo_of_object" or k not in judged):
                    status, reason = ledger[k]["status"], ledger[k].get("reason", "")
                elif k in judged:
                    belongs, _, why, _, image, era = parse_reply(judged[k])
                    status = "judged_drop"
                    reason = ("not this people" if not belongs else f"era {era}" if era in ("modern", "archaeological")
                              else f"image {image}" if image not in ("good", "weak") else f"quality {_quality(judged[k])}")
                elif cat == "unclassified":
                    status = "unclassified"
                else:
                    status = "not_reached"
                counts[status] = counts.get(status, 0) + 1
                out.append({"key": r["key"], "category": cat, "source": o["source"], "id": o["id"],
                            "status": status, "reason": reason})
        rows.append((r.get("atlas") or r["label"], sum(counts.values()), counts))
    if not want:
        (OUT / "pick_coverage.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8")
    cols = ["kept", "judged_drop", "review_excluded", "source_rule", "photo_of_object", "no_image", "fetch_failed",
            "in_library", "awaiting_judge", "not_reached", "unclassified"]
    print(f"{'people':28s} {'cand':>5s} " + " ".join(f"{c[:9]:>9s}" for c in cols))
    for name, total, c in sorted(rows, key=lambda x: x[0]):
        print(f"{name[:28]:28s} {total:5d} " + " ".join(f"{c.get(k, 0):9d}" for k in cols))
    tot = {k: sum(c.get(k, 0) for _, _, c in rows) for k in cols}
    print(f"{'total (' + str(len(rows)) + ' peoples)':28s} {sum(t for _, t, _ in rows):5d} " + " ".join(f"{tot[k]:9d}" for k in cols))


def _atlas_bm_names() -> set[str]:
    """The BM spellings our own census resolved the atlas cultures to (Asante, Kuba, Herero...)."""
    p = REPO / "data" / "bm_ethnic_census.json"
    return {n for r in json.loads(p.read_text(encoding="utf-8")) for n, c in r["facet"].items() if c} if p.exists() else set()


def cmd_report(threshold: int, min_cats: int = 1) -> None:
    cls_p = OUT / "classified.json"
    cls = json.loads(cls_p.read_text(encoding="utf-8")) if cls_p.exists() else {}
    kinds_p = REPO / "data" / "pool" / "kinds.json"
    kinds = json.loads(kinds_p.read_text(encoding="utf-8")) if kinds_p.exists() else {}
    objs_p = OUT / "bm_objects.jsonl"
    objs = {d["key"]: d for d in (json.loads(l) for l in objs_p.read_text(encoding="utf-8").splitlines())} if objs_p.exists() else {}
    atlas_bm = _atlas_bm_names()
    loc = _local()
    queue_skips = _queue_skips()
    rows = [r for r in _rows() if not r["key"].startswith("atlas:") and _evidence(r) >= UNVETTED_MIN_EVIDENCE]
    for r in rows:
        r.update({k: v for k, v in (cls.get(r["key"]) or {}).items() if k != "key"})
        o = objs.get(r["key"])
        r["bm_name"] = _bm_name(r["key"])
        r["in_atlas"] = r["in_atlas"] or (r["bm_name"] in atlas_bm)
        r["tier"] = "bm" if _museums(r) >= threshold else "europeana-only"
        sample = ((o or {}).get("objects") or []) + loc.get(r["key"], [])
        if sample:
            cnt: dict[str, int] = {}
            for x in sample:
                af = _art_form(kinds, x.get("name"))
                cnt[af] = cnt.get(af, 0) + 1
            r["sampled"] = len(sample)
            r["categories"] = dict(sorted(cnt.items(), key=lambda x: -x[1]))
            r["breadth"] = sum(1 for c in _CATS if cnt.get(c, 0) >= 5)
            r["cats3"] = sum(1 for c in _CATS if cnt.get(c, 0) >= 3)   # the list rule: 2+ categories with 3+ objects
            r["photo_share"] = round(cnt.get("photo", 0) / max(1, len(sample)), 2)
    keep = [r for r in rows if r.get("people")]
    # one row per BM name: "Arahuacos (Arawak)" and "Lokono" both resolve to BM "Arawak"
    # one row per BM name (or, with no BM hit, per identical set of Met/Cleveland objects)
    def dk(r: dict) -> str:
        return r["bm_name"] or "local:" + ",".join(sorted(o["id"] for o in loc.get(r["key"], [])))
    best: dict[str, dict] = {}
    for r in keep:
        if r["tier"] != "bm":
            continue
        b = best.get(dk(r))
        if b is None or (r.get("sitelinks") or 0) > (b.get("sitelinks") or 0):
            best[dk(r)] = r
    for r in keep:
        if r["tier"] == "bm" and best[dk(r)] is not r:
            best[dk(r)].setdefault("also", []).append(r["label"])
    keep = [r for r in keep if r["tier"] != "bm" or best[dk(r)] is r]
    # curated merges (clans / iwi / bands into their people) and the Sonnet atlas matches,
    # minus the ones data/world/merges.json rejects
    mp = OUT / "merges.json"
    merges = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
    atlas_not = merges.get("_atlas_not", {})
    cp = OUT / "cleanup.json"
    clean = json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else {}
    by_label = {r["label"]: r for r in keep}
    for child, parent in merges.items():
        if child.startswith("_") or child not in by_label or parent not in by_label:
            continue
        by_label[parent].setdefault("includes", []).append(child)
        by_label[child]["merged_into"] = parent
    for r in keep:
        a = (clean.get(r["key"]) or {}).get("atlas")
        if a and r["label"] not in atlas_not:
            r["in_atlas"], r["atlas"] = True, a
        elif r["label"] in atlas_not:
            r["in_atlas"] = False
    keep = [r for r in keep if not r.get("merged_into")]
    for r in keep:
        r["listed"] = r["tier"] == "bm" and (r.get("cats3") or 0) >= min_cats
        r["evidence"] = _evidence(r)
        r["unvetted_only"] = _unvetted_only(r, queue_skips)
    keep.sort(key=lambda r: (r.get("continent") or "?", not r["listed"], -(r.get("breadth") or 0), -(r.get("cats3") or 0),
                             -(r.get("sampled") or 0)))
    (OUT / "peoples.json").write_text(json.dumps(keep, ensure_ascii=False, indent=0), encoding="utf-8")
    _write_doc(keep, threshold)
    by: dict[str, list] = {}
    for r in keep:
        by.setdefault(r.get("continent") or "?", []).append(r)
    print(f"{len(rows)} names with {threshold}+ image objects in one source; {len(keep)} are peoples, "
          f"{sum(r['tier'] == 'bm' for r in keep)} of them with {threshold}+ in the BM "
          f"({sum(r['in_atlas'] for r in keep)} already in the atlas)")
    lst = [r for r in keep if r["listed"]]
    print(f"LIST ({min_cats}+ categories with 3+ objects): {len(lst)}, new {sum(not r['in_atlas'] for r in lst)}, "
             f"in atlas {sum(r['in_atlas'] for r in lst)} ({len({r.get('atlas') for r in lst if r.get('atlas')})} distinct atlas cultures)")
    print(f"UNREVIEWED-ONLY (people, evidence >= {UNVETTED_MIN_EVIDENCE}): {sum(r['unvetted_only'] for r in keep)}", flush=True)
    for c, rs in sorted(by.items()):
        l = [r for r in rs if r["listed"]]
        print(f"  {c:9s} listed {len(l):3d} (new {sum(not r['in_atlas'] for r in l):3d}, breadth>=6 {sum((r.get('breadth') or 0) >= 6 for r in l):3d})"
              f"   not listed {sum(not r['listed'] for r in rs):3d}   unreviewed-only {sum(r['unvetted_only'] for r in rs):3d}")


SCREEN_PROMPT = """You check entries before they are added as cultures to a world atlas of folk
culture. Each entry is a Wikidata item already classified as a people. Using the
Wikipedia summary and your own knowledge, give one verdict per entry:

- keep: a living people or ethnic group (or a distinct regional people inside a
  nation, such as Catalans or Cornish) with a community today.
- extinct: no community identifies as this people today; it died out or was fully
  assimilated (Westo, Slovincians). A people with present-day descendant
  communities that still carry the name is keep.
- duplicate: the same people as one of the names already on the map for that
  country, or as another entry in this list, under another name, spelling or a
  historical exonym (Arnauts = Albanians). Give that name in duplicate_of.
  When two entries (or an entry and another candidate below) are the same
  people, keep the one with more Wikipedia language editions (sl=) and mark
  only the other as duplicate. A
  distinct subgroup of a people on the map (Hoklo inside Han Chinese,
  Carinthian Slovenes beside Slovenes) is keep.
- not_people: a government, tribal nation as a political body, band, reserve,
  organisation, religious community without its own ethnicity, caste, clan or
  confederation of peoples. When the people it governs is already on the map or
  in the list, use duplicate instead and name it.

Reply with JSON only: {{"entries": [{{"key": "...", "verdict": "...", "duplicate_of": "", "reason": "..."}}]}},
one object per entry, same order; reason is at most 15 words.

Country: {country}
Names already on the map for this country: {on_map}
Other candidates from this country, judged in other batches: {candidates}

Entries:
{entries}
"""

SCREEN_SCHEMA = {
    "type": "object", "required": ["entries"], "additionalProperties": False,
    "properties": {"entries": {"type": "array", "items": {
        "type": "object", "required": ["key", "verdict", "duplicate_of", "reason"], "additionalProperties": False,
        "properties": {"key": {"type": "string"},
                       "verdict": {"type": "string", "enum": ["keep", "extinct", "duplicate", "not_people"]},
                       "duplicate_of": {"type": "string"}, "reason": {"type": "string"}}}}},
}


def _missing_peoples() -> list[str]:
    """Classified living peoples with no culture on the map yet (neither a
    vetted atlas culture nor an unreviewed stub)."""
    cl = json.loads((OUT / "classified.json").read_text(encoding="utf-8"))
    wd = {r["qid"] for r in json.loads((OUT / "wikidata.json").read_text(encoding="utf-8"))}
    atlas = {r["key"] for r in _rows() if r.get("in_atlas")}
    stubs_p = REPO / "data" / "unvetted" / "stubs.json"
    stubs = {s["people_key"] for s in json.loads(stubs_p.read_text(encoding="utf-8"))} if stubs_p.exists() else set()
    return [k for k, r in cl.items() if r.get("people") is True and k in wd and k not in atlas and k not in stubs]


def cmd_screen(only: list[str], limit: int = 0, workers: int = 3) -> None:
    """Sort the living peoples not yet on the map into keep / extinct /
    duplicate / not_people before they get map points and writeups.
    -> data/world/screened.json (cache), screen_raw.jsonl (every reply)."""
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor
    from folk_patterns.codex_cli import ask
    cache_p, raw_p = OUT / "screened.json", OUT / "screen_raw.jsonl"
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    cl = json.loads((OUT / "classified.json").read_text(encoding="utf-8"))
    wd = {r["qid"]: r for r in json.loads((OUT / "wikidata.json").read_text(encoding="utf-8"))}
    missing = _missing_peoples()
    keys = list(dict.fromkeys(only)) or [k for k in missing if k not in cache]
    if limit:
        keys = keys[:limit]
    on_map: dict[str, set[str]] = defaultdict(set)
    for path in (REPO / "data" / "ethnicities").glob("*.json"):
        s = json.loads(path.read_text(encoding="utf-8"))
        on_map[str(s.get("country") or "")].add(str(s.get("ethnicity") or ""))
    missing_by_country: dict[str, list[str]] = defaultdict(list)
    for k in missing:
        missing_by_country[str(cl[k].get("country") or "")].append(k)
    by_country: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        by_country[str(cl[k].get("country") or "")].append(k)
    batches = [(c, ks[i:i + 30]) for c, ks in sorted(by_country.items()) for i in range(0, len(ks), 30)]
    print(f"screen: {len(keys)} peoples in {len(batches)} batches ({len(cache)} cached)", flush=True)
    with httpx.Client(timeout=30, headers=UA, follow_redirects=True) as http:
        def one(batch: tuple[str, list[str]]) -> list[dict]:
            country, ks = batch
            entries = "\n".join(
                f'- key: {k} | name: {wd[k]["label"]} | sl={wd[k].get("sitelinks", 0)} | region: {cl[k].get("region") or "-"} | '
                f'text: {(_summary(http, wd[k]["article"]) if wd[k].get("article") else "") or "(no article text)"}'
                for k in ks)
            others = sorted(n for n in on_map.get(country, set())
                            if n) or ["(none)"]
            rest = [f'{wd[k]["label"]} (sl={wd[k].get("sitelinks", 0)})' for k in missing_by_country.get(country, [])
                    if k not in ks]
            prompt = SCREEN_PROMPT.format(country=country or "-", on_map=", ".join(others),
                                          candidates=", ".join(rest) or "(none)", entries=entries)
            reply = ask(prompt, schema=SCREEN_SCHEMA, timeout=900)
            got = (json.loads(reply) if isinstance(reply, str) else reply).get("entries", [])
            with open(raw_p, "a", encoding="utf-8") as raw:
                raw.write(json.dumps({"country": country, "keys": ks, "reply": got}, ensure_ascii=False) + "\n")
            return [g for g in got if g.get("key") in ks]
        with ThreadPoolExecutor(workers) as ex:
            for n, got in enumerate(ex.map(one, batches), 1):
                for g in got:
                    cache[g["key"]] = {"label": wd[g["key"]]["label"], "country": cl[g["key"]].get("country"),
                                       **{x: g[x] for x in ("verdict", "duplicate_of", "reason")}}
                    print(f'  {g["verdict"]:10} {wd[g["key"]]["label"][:40]:40} {g["duplicate_of"][:25]:25} {g["reason"]}', flush=True)
                cache_p.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    from collections import Counter
    print(Counter(v["verdict"] for v in cache.values()))


def cmd_gaps() -> None:
    """Write source/site coverage gaps from the classified world list."""
    classified = json.loads((OUT / "classified.json").read_text(encoding="utf-8")) if (OUT / "classified.json").exists() else {}
    wikidata = json.loads((OUT / "wikidata.json").read_text(encoding="utf-8")) if (OUT / "wikidata.json").exists() else []
    evidence_rows = json.loads((OUT / "peoples.json").read_text(encoding="utf-8")) if (OUT / "peoples.json").exists() else []
    evidence = {str(r.get("key")): int(r.get("evidence") or _evidence(r)) for r in evidence_rows}
    wd = {r["qid"]: r for r in wikidata}
    from collections import Counter, defaultdict

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for key, result in classified.items():
        if result.get("people") is not True or key not in wd:
            continue
        continent = str(result.get("continent") or "")
        region = _site_region(continent, str(result.get("region") or ""))
        country = str(result.get("country") or wd[key].get("country") or "Unknown")
        groups[(region, country)].append({"key": key, "label": wd[key].get("label") or key,
                                          "sitelinks": int(wd[key].get("sitelinks") or 0),
                                          "evidence": evidence.get(key, 0)})

    site_vetted: Counter[tuple[str, str]] = Counter()
    site_unvetted: Counter[tuple[str, str]] = Counter()
    for path in (REPO / "data" / "ethnicities").glob("*.json"):
        try:
            shard = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pair = (str(shard.get("region") or ""), str(shard.get("country") or "Unknown"))
        if shard.get("unvetted_only"):
            site_unvetted[pair] += 1
        else:
            site_vetted[pair] += 1

    def row_counts(items: list[dict], pair: tuple[str, str]) -> tuple[int, int, int, int]:
        return (len(items), sum(bool(x["evidence"]) for x in items), site_vetted[pair], site_unvetted[pair])

    pairs = sorted(set(groups) | set(site_vetted) | set(site_unvetted))
    region_rows: dict[str, list[dict]] = defaultdict(list)
    for pair in pairs:
        region_rows[pair[0]].extend(groups.get(pair, []))
    lines = ["# World peoples coverage gaps", "",
             "Generated by `python scripts/world_peoples.py gaps` — do not edit by hand.", "",
             "Living peoples are classified Wikidata items with `people: true`. Source evidence is any row in "
             "`peoples.json` with a positive BM, Met/Cleveland, or multilingual Europeana count. Site counts "
             "come from the current ethnicity shards; unreviewed-only shards are separated from vetted cultures.", "",
             "## By region", "", "| site region | living peoples | with source evidence | vetted cultures | unreviewed-only cultures |",
             "|---|---:|---:|---:|---:|"]
    for region in sorted(region_rows):
        items = region_rows[region]
        region_pairs = [pair for pair in pairs if pair[0] == region]
        lines.append(f"| {region} | {len(items)} | {sum(bool(x['evidence']) for x in items)} | "
                     f"{sum(site_vetted[p] for p in region_pairs)} | {sum(site_unvetted[p] for p in region_pairs)} |")

    lines += ["", "## By country", "", "| site region | country | living peoples | with source evidence | vetted cultures | unreviewed-only cultures |",
              "|---|---|---:|---:|---:|---:|"]
    for pair in pairs:
        counts = row_counts(groups.get(pair, []), pair)
        lines.append(f"| {pair[0]} | {pair[1]} | {counts[0]} | {counts[1]} | {counts[2]} | {counts[3]} |")

    lines += ["", "## Visible gaps", "",
              "Peoples below have at least 20 Wikipedia sitelinks, are classified as people, and have no "
              "evidence in the current museum-source report.", ""]
    gaps = []
    for pair in sorted(groups):
        missing = sorted((x for x in groups[pair] if x["sitelinks"] >= 20 and not x["evidence"]),
                         key=lambda x: (-x["sitelinks"], x["label"]))
        if not missing:
            continue
        lines += [f"### {pair[1]} ({pair[0]})", ""]
        for item in missing:
            lines.append(f"- {item['label']} ({item['key']}, {item['sitelinks']} sitelinks)")
            gaps.append(item)
        lines.append("")
    if not gaps:
        lines.append("None found.")
    docs = REPO / "docs" / "gaps.md"
    docs.parent.mkdir(parents=True, exist_ok=True)
    docs.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"gaps: {sum(len(v) for v in groups.values())} living peoples, "
          f"{sum(bool(x['evidence']) for v in groups.values() for x in v)} with evidence, "
          f"{sum(site_vetted.values())} vetted cultures, {sum(site_unvetted.values())} unreviewed-only cultures, "
          f"{len(gaps)} visible gaps", flush=True)


def _write_doc(keep: list[dict], threshold: int) -> None:
    lines = ["# World peoples with museum evidence", "",
             "Generated by `python scripts/world_peoples.py report` — do not edit by hand.", "",
             "A people is **listed** when its objects fill at least one of the 13 categories with 3+ objects (column "
             "**cat. 3+**). It is counted at all when the museums with a people field hold "
             f"{threshold}+ image objects under its "
             "name: the British Museum \"Ethnic group\" and the Met and Cleveland culture fields (the Met: public "
             "domain, 1700 or later). **Met+Cle** is that count. The Met's European entries (French, German) come "
             "from its costume and arms departments, not folk collections. The V&A names places, not peoples, so it "
             "cannot be counted per people. Smithsonian anthropology has no open images. **Breadth** is how many of "
             "the 13 categories (photo included, unclassified not) have 5+ objects in a sample of up to 500 BM "
             "objects plus every Met/Cleveland match. Categories come from each object's "
             "BM name via `normalize_kinds.py` (Haiku). **BM** is capped at 500 by the sample. **Eur.** counts "
             "records at ethnographic providers in Europeana that mention the name. It is a text match, so it "
             "is only a hint. The **evidence** total is BM + Met/Cleveland + the multilingual Europeana maximum. "
             "A classified people with at least one evidence record but no listed pick is **unreviewed-only**: "
             "its text-matched museum candidates are shown separately and never counted as vetted objects. Peoples "
             "only Europeana finds are listed separately, because most of those are "
             "word collisions (\"Iron\" for Ossetians, \"Bali\", \"Dan\"). Clans, iwi and bands are merged into their "
             "people (\"incl.\") by the curated `data/world/merges.json`. Its note lists the model suggestions that were "
             "rejected: Sonnet folded distinct peoples into umbrella groups (Hopi into Puebloan, Vezo into Merina).", ""]
    for cont in sorted({r.get("continent") or "?" for r in keep}):
        rs = [r for r in keep if (r.get("continent") or "?") == cont and r["listed"]]
        below = [r for r in keep if (r.get("continent") or "?") == cont and not r["listed"]]
        lines += [f"## {cont} — {len(rs)}", "", "| people | country | region | in atlas | BM | Met+Cle | breadth | cat. 3+ | photo | top categories | Eur. |",
                  "|---|---|---|:-:|--:|--:|--:|--:|--:|---|--:|"]
        for r in rs:
            top = ", ".join(f"{k} {v}" for k, v in list((r.get("categories") or {}).items())[:4])
            also = f" (also {', '.join(r['also'])})" if r.get("also") else ""
            also += f" (incl. {', '.join(r['includes'])})" if r.get("includes") else ""
            lines.append(f"| [{r['label']}]({r.get('article') or ''}){also} | {r.get('country') or ''} | {r.get('region') or ''} | "
                         f"{'✓' if r['in_atlas'] else ''} | {r.get('sampled', 0) - r.get('local', 0)} | {r.get('local', 0)} | {r.get('breadth', '')} | {r.get('cats3', '')} | "
                         f"{int(100 * (r.get('photo_share') or 0))}% | {top} | {r['europeana']} |")
        lines += ["", f"Below the bar ({len(below)}): " + ", ".join(
            f"{r['label']} ({r.get('sampled', 0)} obj, photo {int(100 * (r.get('photo_share') or 0))}%)" for r in below), ""]
    eo = [r for r in keep if r["tier"] != "bm"]
    lines += [f"## Europeana only — {len(eo)}, to check", "",
              ", ".join(f"{r['label']} ({r['europeana']})" for r in sorted(eo, key=lambda r: -r["europeana"])), ""]
    (REPO / "docs" / "world-peoples.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["wikidata", "bm", "aliases", "labels", "europeana", "europeana-objects", "local", "classify", "harvest", "cleanup", "report", "candidates", "gaps", "pick", "coverage", "pick-import", "screen"])
    ap.add_argument("--only", nargs="*", default=[], help="pick/europeana-objects: peoples by Wikidata key, label or atlas name; screen: Wikidata keys")
    ap.add_argument("--shard", default="", help="pick: i/n, this process takes every n-th people (run n processes)")
    ap.add_argument("--cached-only", action="store_true", help="pick: no judge calls, no pick file written; record outcomes only")
    ap.add_argument("--export-batch", default="", help="pick --cached-only: write awaiting_judge candidates as a cloud batch")
    ap.add_argument("--batch", default="", help="pick-import: the batch name")
    ap.add_argument("--tries", type=int, default=0, help="pick: candidates shown to the judge per category (default 10); earlier verdicts come from the cache")
    ap.add_argument("--no-judge", action="store_true", help="pick: no judge calls; write the pick file from cached verdicts")
    ap.add_argument("--limit", type=int, default=0, help="cleanup/screen: only the first N (a test batch)")
    ap.add_argument("--min-cats", type=int, default=1, help="cleanup/report: categories with 3+ objects a listed people needs")
    ap.add_argument("--pages", type=int, default=5, help="harvest: BM list pages (100 objects each) per people")
    ap.add_argument("--refill", action="store_true", help="harvest: re-fetch in full the peoples that hit the page cap")
    ap.add_argument("--aliases", action="store_true", help="bm: second pass over aliases.json")
    ap.add_argument("--threshold", type=int, default=6)  # 6: the museum-evidence floor the list was built with; 30 drops 239 listed peoples
    ap.add_argument("--multilingual", action="store_true", help="europeana: query cached Wikidata names in 19 languages")
    ap.add_argument("--backend", choices=["claude", "codex"], default="claude", help="classify: local subscription backend")
    ap.add_argument("--all-min-sitelinks", type=int, default=0, help="classify: include every Wikidata item at this sitelink threshold")
    a = ap.parse_args()
    PICK_TRIES = a.tries or PICK_TRIES
    {"wikidata": cmd_wikidata, "bm": lambda: cmd_bm(a.aliases), "aliases": cmd_aliases, "labels": cmd_labels,
     "europeana": lambda: cmd_europeana(a.multilingual), "local": cmd_local,
     "europeana-objects": lambda: cmd_europeana_objects(a.only),
     "classify": lambda: cmd_classify(a.threshold, a.backend, a.all_min_sitelinks),
     "harvest": lambda: cmd_harvest(a.pages, a.threshold, a.refill), "cleanup": lambda: cmd_cleanup(a.min_cats, a.limit),
     "candidates": cmd_candidates, "gaps": cmd_gaps, "screen": lambda: cmd_screen(a.only, a.limit),
     "pick": lambda: cmd_pick(a.only, a.shard, a.cached_only, a.no_judge, a.export_batch),
     "coverage": lambda: cmd_coverage(a.only), "pick-import": lambda: cmd_pick_import(a.batch)}.get(a.step, lambda: cmd_report(a.threshold, a.min_cats))()
