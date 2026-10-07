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
    python scripts/world_peoples.py ethno-objects [--only ...]      # REM, Kunstkamera, Berlin: objects whose ethnic field names the people
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
import contextlib
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
            "village museum", "astra national museum",
            # English labels Europeana gives these providers; checked 2026-10-06:
            # Museo de América held 245 of 388 Shipibo records, Museon 136 Toraja.
            "museum of america", "museo de am", "museon", "world museum vienna")


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
    # The screen step's stubs too (they were never in peoples.json): the 857
    # weakest cultures had no Europeana search until 2026-10-06.
    screened = json.loads((OUT / "screened.json").read_text(encoding="utf-8")) if (OUT / "screened.json").exists() else {}
    seen = {r["key"] for r in pe}
    pe += [{"key": k, "label": v.get("label") or k} for k, v in screened.items() if v.get("verdict") == "keep" and k not in seen]
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


ETHNO_CAP, ETHNO_PHOTO_CAP = 300, 60   # objects / field photographs kept per people per museum
# Sound and film carriers: their picture is a label or a box. Shellac records
# (269) and cassettes (39) were in the first 250 peoples' harvest, 2026-10-06.
_SMB_SKIP_TYPE = re.compile(r"tonband|tonbänd|schallplatte|schellack|platte|kassette|walze|tonträger|film|video|audio|^dat$|^cd$", re.I)


_NOT_PEOPLE = re.compile(r"period|dynasty|era|phase|horizon|culture \(archaeolog|archaeolog|période|dynastie|эпох|период|династ", re.I)


def _ethno_match(title: str, names: list[str]) -> bool:
    """A museum's people term is this people: the name itself, or the name
    plus a subgroup ("марийцы горные", "Mapuche-Huilliche")."""
    t = _fold(title).strip()
    if "?" in t:   # the museum's own doubt ("литовцы (?)")
        return False
    if _NOT_PEOPLE.search(t):   # "Edo (Japanese period)" is not the Edo of Nigeria (Peabody, 2026-10-06)
        return False
    return any(t == n or t.startswith(n + " ") or t.startswith(n + "-") or t.startswith(n + " (") for n in names)


def _kamis_people(cl: httpx.Client, site: str, names: list[str]) -> list[dict]:
    from folk_patterns.museums import kamis
    folded = [_fold(n) for n in names]
    ids: dict[str, str] = {}
    for n in names:
        for e in kamis.facets(cl, site, n).get("ethnos", []):
            if _ethno_match(e["title"], folded):
                ids[e["value"]] = e["title"]
        time.sleep(0.2)
    if not ids:
        return []
    funds = kamis.facets(cl, site, "", {"ethnos": list(ids)}).get("fund", [])
    photo = [f["value"] for f in funds if "фото" in f["title"].casefold()]
    other = [f["value"] for f in funds if f["value"] not in photo]
    out = []
    for fund_ids, cap, is_photo in ((other, ETHNO_CAP, False), (photo, ETHNO_PHOTO_CAP, True)):
        if not fund_ids:
            continue
        for d in kamis.records(cl, site, {"ethnos": list(ids), "fund": fund_ids}, cap):
            title = d.get("title") or ""
            out.append({"source": site, "id": str(d["id"]),
                        "name": "фотография" if is_photo else kamis.object_name(title),
                        "item": {"title": title, "image_url": kamis.image_url(site, d["image"]),
                                 "ethnos": sorted(set(ids.values()))}})
    return out


def _smb_people(cl: httpx.Client, names: list[str]) -> list[dict]:
    from folk_patterns.museums import smb
    folded = [_fold(n) for n in names]
    hits: dict[str, dict] = {}
    for n in names:
        start = 0
        while start < 600:
            j = smb.search(cl, f'"{n}"', start)
            arts = j.get("artworks") or []
            for a in arts:
                if a.get("sammlung") in smb.ETHNO_COLLECTIONS and not _SMB_SKIP_TYPE.search(a.get("objekttyp") or ""):
                    hits.setdefault(a["id"], a)
            start += len(arts)
            if not arts or start >= j.get("total", 0):
                break
            time.sleep(0.2)
    out = []
    for oid, a in hits.items():
        if len(out) >= ETHNO_CAP:
            break
        try:
            d = smb.detail(cl, oid)
        except httpx.HTTPError:
            continue
        peoples = smb.ethnie(d)
        img = smb.image_url(d)
        if not img or not any(_ethno_match(p, folded) for p in peoples):
            continue
        out.append({"source": "smb", "id": oid, "name": (a.get("objekttyp") or "")[:120],
                    "item": {"title": str(d.get("title") or a.get("titel") or "").strip('"') or a.get("objekttyp") or "", "image_url": img,
                             "ethnos": peoples}})
        time.sleep(0.1)
    return out


def _prm_people(cl: httpx.Client, names: list[str]) -> list[dict]:
    from folk_patterns.museums import prm
    folded = [_fold(n) for n in names]
    groups = list(dict.fromkeys(g for n in names for g in prm.groups(cl, n) if _ethno_match(g, folded)))
    out: list[dict] = []
    for g in groups:
        for it in prm.records(cl, g, ETHNO_CAP + ETHNO_PHOTO_CAP - len(out)):
            photo = it.get("collection") == "Photograph"
            if photo and sum(o["name"] == "photograph" for o in out) >= ETHNO_PHOTO_CAP:
                continue
            out.append({"source": "prm", "id": it["id"],
                        "name": "photograph" if photo else (it.get("recordSubtitle") or "")[:120],
                        "item": {"title": it.get("recordSubtitle") or "", "image_url": prm.image_url(it), "ethnos": [g]}})
    return out


def _maa_people(cl: httpx.Client, names: list[str]) -> list[dict]:
    from folk_patterns.museums import maa
    folded = [_fold(n) for n in names]
    ids = list(dict.fromkeys(i for n in names for i in maa.search(cl, n)))
    out: list[dict] = []
    for oid in ids:
        if len(out) >= ETHNO_CAP:
            break
        try:
            d = maa.detail(cl, oid)
        except httpx.HTTPError:
            continue
        parts = [x.strip() for x in re.split(r"[;,]", d["culture"]) if x.strip()]
        if d["image_url"] and any(_ethno_match(x, folded) for x in parts):
            out.append({"source": "maa", "id": oid, "name": d["title"][:120],
                        "item": {"title": d["title"], "image_url": d["image_url"], "ethnos": parts}})
        time.sleep(1.0)   # MAA answered 503 to every other request at ~3/s (2026-10-06)
    return out


def _qb_people(cl: httpx.Client, names: list[str]) -> list[dict]:
    from folk_patterns.museums import quaibranly as qb
    folded = [_fold(n) for n in names]
    out: list[dict] = []
    seen: set[str] = set()
    for photos, cap in ((False, ETHNO_CAP), (True, ETHNO_PHOTO_CAP)):
        kept = 0
        for n in names:
            if kept >= cap:
                break
            for rec in qb.records(cl, n, cap - kept, photos=photos):
                num, img = qb.number(rec), qb.image_url(rec)
                if num in seen or not img or not any(_ethno_match(p, folded) for p in qb.populations(rec)):
                    continue
                seen.add(num)
                kept += 1
                out.append({"source": "quaibranly", "id": num,
                            "name": "photographie" if photos else (qb.title(rec) or str(rec.get("Classification") or ""))[:120],
                            # the museum's own category, mapped to an art form without an LLM
                            "museum_class": "Photographie" if photos else str(rec.get("Classification") or ""),
                            "item": {"title": qb.title(rec), "image_url": img, "ethnos": qb.populations(rec)}})
    return out


def _peabody_people(cl: httpx.Client, names: list[str]) -> list[dict]:
    from folk_patterns.museums import peabody
    folded = [_fold(n) for n in names]
    flts = dict((f, label) for n in names for label, f in peabody.cultures(cl, n) if _ethno_match(label, folded))
    out: list[dict] = []
    seen: set[str] = set()
    for dept, cap in (("Ethnographic", ETHNO_CAP), ("Photographic", ETHNO_PHOTO_CAP)):
        kept = 0
        for f in flts:
            for it in peabody.records(cl, f, cap - kept, dept):
                if it["id"] in seen:
                    continue
                seen.add(it["id"])
                kept += 1
                out.append({"source": "peabody", "id": it["id"],
                            "name": "photograph" if dept == "Photographic" else (it["title"] or it["classification"])[:120],
                            "item": {"title": it["title"], "image_url": peabody.image_url(it["image"]),
                                     "classification": it["classification"], "ethnos": [flts[f]]}})
            if kept >= cap:
                break
    return out


def _museudoindio_people(names: list[str]) -> list[dict]:
    """Museu do Índio: records whose "Povo" is the people. The whole collection
    is cached locally (museudoindio.download), so this makes no request."""
    from folk_patterns.museums import museudoindio as mi
    folded = [_fold(n) for n in names]
    out: list[dict] = []
    for row in mi.download(OUT / "museudoindio_items.jsonl"):
        if len(out) >= ETHNO_CAP:
            break
        povo = mi.peoples(row)
        if row["image"] and any(_ethno_match(p, folded) for p in povo):
            out.append({"source": "museudoindio", "id": row["id"], "name": row["title"][:120],
                        "museum_class": row["categoria"],
                        "item": {"title": row["title"], "image_url": row["image"], "ethnos": povo}})
    return out


def _ntm_people(cl: httpx.Client, key: str) -> list[dict]:
    """National Taiwan Museum: indigenous records whose quoted title or "used by"
    phrase names the people (ntm.NAMES, keyed by Wikidata id). One record page
    per object is read for its picture."""
    from folk_patterns.museums import ntm
    names = ntm.NAMES.get(key)
    if not names:
        return []
    out: list[dict] = []
    for rec in ntm.catalog(OUT / "ntm_catalog.json"):
        if len(out) >= ETHNO_CAP:
            break
        if not ntm.matches(rec, names):
            continue
        img = ntm.image_url(cl, rec)
        time.sleep(0.3)
        if img:
            out.append({"source": "ntm", "id": ntm.object_id(rec), "name": (rec.get("MainTitle") or "").strip()[:120],
                        "item": {"title": (rec.get("MainTitle") or "").strip(), "image_url": img,
                                 "ethnos": [ntm.attribution(rec)]}})
    return out


def neprajz_singulars(name: str) -> list[str]:
    from folk_patterns.museums.neprajz import singulars
    return singulars(name)


def _neprajz_people(cl: httpx.Client, key: str, names: list[str]) -> list[dict]:
    """Néprajzi Múzeum, Budapest: records under a people term (search_ethnicity_hu_ss)
    that is the people, matched with its Hungarian names and their singulars."""
    from folk_patterns.museums import neprajz
    folded = [_fold(n) for n in names]
    terms = [g for g in neprajz.groups() if g in names or _ethno_match(g, folded)]
    terms = [g for g in terms if g not in _NM_NOT.get(key, ())
             and (not _NM_NOT_WORDS.search(g) or any(_NM_NOT_WORDS.search(n) for n in names))]
    if not terms:
        return []
    out: list[dict] = []
    for photos, cap in ((False, ETHNO_CAP), (True, ETHNO_PHOTO_CAP)):
        for d in neprajz.records(cl, terms, cap, photos):
            title = d.get("list_title_hu_s") or ""
            out.append({"source": "neprajz", "id": str(d["oid"]), "name": "fénykép" if photos else title[:120],
                        "art_form": "photo" if photos else neprajz.art_form(d),   # the museum's collection, not the lexicon
                        "item": {"title": title, "image_url": neprajz.image_url(d),
                                 "ethnos": sorted(set(d.get("search_ethnicity_hu_ss") or []))}})
    return out


ETHNO_SOURCES = ("kamis", "smb", "prm", "maa", "quaibranly", "peabody", "museudoindio", "ntm", "neprajz")


def cmd_ethno_objects(only: list[str], workers: int = 6, sources: tuple[str, ...] = ("kamis", "smb")) -> None:
    """Objects from three ethnographic museums whose records name the people in
    a controlled ethnic field: the Russian Museum of Ethnography and the
    Kunstkamera (KAMIS `ethnos`, matched with the people's Cyrillic Wikidata
    names) and the Berlin Ethnological Museum (SMB `Ethnie`, matched with its
    English and German names). Assignment is by that text field alone, no
    picture is judged. Up to ETHNO_CAP objects and ETHNO_PHOTO_CAP field
    photographs per people per museum.
    -> data/world/ethno_objects_<museums>.jsonl (gitignored), one file per museum
    set: parallel runs appending to one file corrupted a line on 2026-10-06.
    The last line per key wins."""
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import urllib3
    from folk_patterns.museums import kamis, maa, prm, smb
    from folk_patterns.museums import quaibranly as qb
    urllib3.disable_warnings()
    labels = json.loads(_labels_path().read_text(encoding="utf-8")) if _labels_path().exists() else {}
    pe = [r for r in json.loads((OUT / "peoples.json").read_text(encoding="utf-8")) if r.get("listed") or r.get("unvetted_only")]
    screened = json.loads((OUT / "screened.json").read_text(encoding="utf-8")) if (OUT / "screened.json").exists() else {}
    seen = {r["key"] for r in pe}
    pe += [{"key": k, "label": v.get("label") or k} for k, v in screened.items() if v.get("verdict") == "keep" and k not in seen]
    want = {s.lower() for s in only}
    if want:
        pe = [r for r in pe if {r["key"].lower(), r["label"].lower()} & want]
    p = OUT / f"ethno_objects_{'-'.join(sources)}.jsonl"
    # A line covers the museums in its "sources"; a run for other museums
    # adds lines beside it instead of replacing it.
    done = {d["key"] for d in _ethno_lines()   # the shared pre-split file counts too
            if tuple(d.get("sources") or ("kamis", "smb")) == tuple(sources)} if not want else set()
    todo = [r for r in pe if r["key"] not in done]
    print(f"ethno-objects {'+'.join(sources)}: {len(todo)} peoples ({len(done)} cached)", flush=True)
    lock = threading.Lock()
    n_done = [0]

    def one(r: dict) -> None:
        # Every Russian and German Wikidata name, without _ml_variants' skeleton
        # test (it drops "литовцы" for Lithuanians): a name only counts when it
        # equals a museum's own people term, so a loose alias finds nothing.
        lab = labels.get(r["key"]) or {}
        cyr = list(dict.fromkeys(v.strip() for v in lab.get("ru") or [] if len(v.strip()) >= 4 and "(" not in v))
        lat = list(dict.fromkeys([*variants(r["label"]), *(v.strip() for v in lab.get("de") or [] if len(v.strip()) >= 4 and "(" not in v)]))
        objs: list[dict] = []
        errs = []
        jobs = []
        if "kamis" in sources and cyr:
            jobs += [(site, kamis.client, lambda c, site=site: _kamis_people(c, site, cyr)) for site in kamis.SITES]
        if "smb" in sources and lat:
            jobs.append(("smb", smb.client, lambda c: _smb_people(c, lat)))
        if "prm" in sources:
            jobs.append(("prm", prm.client, lambda c: _prm_people(c, lat)))
        if "maa" in sources:
            jobs.append(("maa", maa.client, lambda c: _maa_people(c, lat)))
        if "quaibranly" in sources:
            # its thesaurus is French and singular ("Kurde"); variants() adds the singular
            fr = list(dict.fromkeys([*variants(r["label"]), *(x for v in lab.get("fr") or [] if len(v.strip()) >= 4 and "(" not in v
                                                              for x in variants(v.strip()))]))
            jobs.append(("quaibranly", qb.client, lambda c: _qb_people(c, fr)))
        if "peabody" in sources:
            from folk_patterns.museums import peabody
            jobs.append(("peabody", peabody.client, lambda c: _peabody_people(c, lat)))
        country = r.get("country") or (screened.get(r["key"]) or {}).get("country") or ""
        if "museudoindio" in sources and country in _MI_COUNTRIES:
            # its Povo terms are Portuguese singulars ("Kayabí"); add the Portuguese
            # Wikidata names, their singulars, and the hand-read aliases
            pt = list(dict.fromkeys([*lat, *(x for v in lab.get("pt") or [] if len(v.strip()) >= 3 and "(" not in v
                                                for x in (v.strip(), re.sub(r"(?<=[^s])s$", "", v.strip())))]))
            pt += _MI_ALIASES.get(r["key"], [])
            jobs.append(("museudoindio", contextlib.nullcontext, lambda c: _museudoindio_people(pt)))
        if "neprajz" in sources:
            # Hungarian names only: the museum's terms are Hungarian ("sokác"), and
            # English ones would collide ("bari"); plus the hand-read aliases
            hu = list(dict.fromkeys(x for v in lab.get("hu") or [] if len(v.strip()) >= 3 and "(" not in v
                                    for x in neprajz_singulars(v.strip())))
            hu += _NM_ALIASES.get(r["key"], [])
            if hu:
                from folk_patterns.museums import neprajz
                jobs.append(("neprajz", neprajz.client, lambda c: _neprajz_people(c, r["key"], hu)))
        if "ntm" in sources:
            from folk_patterns.museums import ntm
            jobs.append(("ntm", ntm.client, lambda c: _ntm_people(c, r["key"])))
        for name, mk, fn in jobs:
            try:
                with mk() as c:
                    objs += fn(c)
            except Exception as e:   # one museum's bad answer must not stop the run; left uncached
                errs.append(f"{name} {type(e).__name__} {str(e)[:80]}")
        with lock:
            n_done[0] += 1
            if errs:   # left uncached so a rerun retries it
                print(f"  {n_done[0]}/{len(todo)} {r['label']}: ! {', '.join(errs)}", flush=True)
                return
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"key": r["key"], "label": r["label"], "sources": list(sources), "names": cyr + lat,
                                    "objects": objs},
                                   ensure_ascii=False) + "\n")
            by = {}
            for o in objs:
                by[o["source"]] = by.get(o["source"], 0) + 1
            print(f"  {n_done[0]}/{len(todo)} {r['label']}: {len(objs)} {by or ''}  names={cyr + lat}", flush=True)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, todo))


@__import__("functools").lru_cache(maxsize=1)
def _ethno_index() -> dict[tuple[str, str], dict]:
    return {(o["source"], o["id"]): o["item"] for d in _ethno_lines() for o in d["objects"]}


def _ethno_lines() -> list[dict]:
    """Every parseable line of the ethno-objects files, oldest file first.
    The shared ethno_objects.jsonl, written before the per-set files, always
    reads first: its quai Branly lines predate museum_class."""
    out = []
    for f in sorted(OUT.glob("ethno_objects*.jsonl"), key=lambda f: (f.name != "ethno_objects.jsonl", f.stat().st_mtime)):
        for l in f.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(l))
            except json.JSONDecodeError:
                continue
    return out


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


# Wikidata labels that name a different people than the item is: Q1983600's
# English label read "Siberians" while its article is Siberian Tatars, so its
# objects landed on the Siberians (Siberiaks, Q4418370) point (2026-10-07).
_LABEL_FIX = {"Q1983600": "Siberian Tatars"}


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
        l = _LABEL_FIX.get(k, l)
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


# quai Branly's own object class, for names no kind covers ("Sans titre",
# "Malgache" under Arts graphiques). Counted 2026-10-06: 13,081 of 24,071
# objects are "(non renseigné)"; Denrée alimentaire, Objet archéologique and
# Monnaies stay unclassified, Restes humains are dropped before this.
_QB_CLASS = {"Photographie": "photo", "Textile ou vêtement": "textile", "Sculpture": "sculpture",
             "Instrument de musique": "instruments", "Arts graphiques": "painting-mss", "Peinture": "painting-mss",
             "Manuscrit": "painting-mss", "Maquette ou modèle": "sculpture", "Moulage": "sculpture"}

from folk_patterns.museums.museudoindio import CLASS as _MI_CLASS   # noqa: E402  Museu do Índio's "Categoria"

# Museu do Índio "Povo" terms that no English or Portuguese Wikidata name of
# the people reaches, read by hand from the museum's 187 terms (2026-10-07).
_MI_ALIASES: dict[str, list[str]] = {
    "Q4001119": ["Txicão"],                 # Ikpeng: their older name
    "Q5363631": ["Tikuna"],                 # Ticuna
    "Q1882676": ["Manchineri"],             # Machinere
    "Q2549328": ["Pacaa Nova"],             # Wari'
    "Q1099072": ["Urubu"],                  # Ka'apor (Urubu-Ka'apor)
    "Q1114291": ["Salumã"],                 # Enawenê-Nawê: their older name
    "Q10375164": ["Suruí"],                 # Paiter Suruí; "Suruí do Tocantins" is the Aikewara
    "Q1028240": ["A'Ukre", "Gorotire", "Kubenkrankégn", "Menkrangnotí", "Txukahamãe", "Xikrin"],  # Kayapó subgroups
    "Q34188": ["Waiká", "Guaharibo", "Xamatari"],                                               # Yanomami subgroups
}
# The museum holds Brazil and its neighbours only; other continents' peoples
# share its names: the Wodaabe are also "Bororo", Madagascar's Bara spell like
# the Bará of the Vaupés (both matched on 2026-10-07 before this).
_MI_COUNTRIES = {"Brazil", "Paraguay", "Bolivia", "Peru", "Colombia", "Venezuela", "Guyana", "Suriname",
                 "French Guiana", "Argentina", "Ecuador"}
# Néprajzi Múzeum people terms that no Hungarian Wikidata name of the people
# reaches, read by hand from the museum's 484 terms (2026-10-07).
_NM_ALIASES: dict[str, list[str]] = {
    "Q178419": ["mohácsi sokác", "Dráva-menti sokác"],
    "Q510403": ["Dráva-menti horvát"],
    "Q1760969": ["oláh cigány"],
    "Q832474": ["vend"],
    "Q699958": ["sváb"],
    "Q498700": ["gorál"],
    "Q855178": ["moldvai csángó", "gyimesi csángó", "hétfalusi csángó"],
    "Q171336": ["tót", "pilisi szlovák"],
    "Q47246": ["mordvin-erza"],
    "Q1943269": ["mordvin-moksa"],
    "Q203319": ["zürjén"],
    "Q80040": ["kazah"],
    "Q101828": ["ajnu"],
    "Q690126": ["lív"],
    "Q483569": ["belorusz"],
    "Q191730": ["tunguz"],
    "Q486316": ["szaha"],
    "Q476030": ["hanti, osztják", "keleti osztják"],
    "Q60046": ["nyivh"],
    "Q810714": ["batak"],
    "Q504685": ["bamana (bambara)", "bambara"],
    "Q1295544": ["fang", "pangve", "pongve"],
    "Q640090": ["kongo"],
    "Q805841": ["luba"],
    "Q2088223": ["lega", "warega", "rega"],
    "Q793575": ["zande"],
    "Q1602764": ["mangbetu"],
    "Q810544": ["szongé"],
    "Q811078": ["teke"],
    "Q2576790": ["punu"],
    "Q48885": ["makonde"],
    "Q1143929": ["kamba"],
    "Q1453190": ["turkana"],
    "Q170088": ["herero"],
    "Q577576": ["szoto"],
    "Q1262400": ["mende"],
    "Q930128": ["kpelle"],
    "Q1804699": ["dan törzs"],
    "Q1266038": ["szenufo"],
    "Q1165955": ["moszi"],
    "Q961201": ["lobi"],
    "Q811460": ["baule"],
    "Q415693": ["akan"],
    "Q12257903": ["fon"],
    "Q806017": ["bamileke"],
    "Q1262591": ["duala"],
    "Q239577": ["ibibio"],
    "Q244157": ["ibo"],
    "Q1478209": ["ogoni"],
    "Q192647": ["hutu"],
    "Q193092": ["tuszi"],
    "Q1474755": ["szukuma"],
    "Q1262850": ["nyamvézi"],
    "Q147725": ["szaramo"],
    "Q920233": ["csagga"],
    "Q210332": ["haida"],
    "Q536129": ["tlingit"],
    "Q1929613": ["pomo"],
    "Q331789": ["crow"],
    "Q1937531": ["papagó"],
    "Q1162132": ["huichol"],
    "Q429921": ["tarahumara"],
    "Q1132647": ["otomi"],
    "Q623215": ["mixtec"],
    "Q147401": ["zatopec"],
    "Q826591": ["nahua"],
    "Q1130354": ["huastek", "uaszték"],
    "Q45009": ["tarasco", "taraszka"],
    "Q1355029": ["lakandon"],
    "Q134936": ["kecsua"],
    "Q36411": ["shipibo"],
    "Q948636": ["yagua"],
    "Q1969828": ["uitoto"],
    "Q1179410": ["piaroa"],
    "Q589718": ["yukpa"],
    "Q891077": ["goajiro"],
    "Q1261048": ["guarauno"],
    "Q5363631": ["tikuna"],
    "Q2299416": ["tiriyó"],
    "Q2299931": ["wayana"],
    "Q1431998": ["wayampi"],
    "Q1853257": ["waiwai"],
    "Q1028240": ["kayapo", "kajapó, sikrin csoport"],
    "Q3509829": ["krahó"],
    "Q2630736": ["kuikuro"],
    "Q2520155": ["kalapalo"],
    "Q1722872": ["kamayurá"],
    "Q2530880": ["mehinacu"],
    "Q1099056": ["waura"],
    "Q978982": ["savante"],
    "Q176203": ["serente"],
    "Q1099041": ["tapirape"],
    "Q1728924": ["karazsa"],
    "Q1476741": ["nambikuara"],
    "Q432324": ["patasó"],
    "Q1099072": ["urubú-kaapor"],
    "Q34188": ["yanoama", "janoama", "janomami", "waika", "guaharibo", "guaica", "shamatari"],
    "Q545219": ["kreen-akarore"],
    "Q1115972": ["kajabi"],
    "Q617636": ["apalai"],
    "Q3621312": ["araweté", "araveti"],
    "Q2522489": ["chamacoco"],
    "Q1092569": ["cinta larga"],
    "Q2259699": ["mawé"],
    "Q894756": ["botokudo"],
    "Q774436": ["botokudo (sokleng)"],
    "Q1542227": ["toba"],
    "Q1284276": ["mataco"],
    "Q3388372": ["pilagá"],
    "Q3485276": ["siriono"],
    "Q1289028": ["guahibo"],
    "Q750479": ["lahu"],
    "Q417628": ["akha"],
    "Q476550": ["hani"],
    "Q857626": ["liszu"],
    "Q461282": ["ji", "déli ji (niszu)", "északi ji (noszu)"],
    "Q217815": ["nahszi"],
    "Q72805": ["muong"],
    "Q1347290": ["bahnar"],
    "Q2467559": ["gia-rai"],
    "Q383946": ["lao"],
    "Q842323": ["halha"],
    "Q1628371": ["darhat"],
    "Q1355221": ["sakalava"],
    "Q643103": ["aszmat"],
    "Q172717": ["avar"],
    "Q1193813": ["kalanga"],
    "Q973254": ["iban dajak"],
    "Q1743773": ["kissi"],
    "Q1859406": ["loma"],
    "Q2002234": ["vei"],
    "Q2275739": ["mambila"],
    "Q888796": ["bobo"],
    "Q1018432": ["bwa"],
    "Q4351953": ["marka"],
    "Q819186": ["mandingo"],
    "Q1226906": ["diola"],
    "Q799815": ["baga"],
    "Q1537493": ["kru"],
    "Q1534501": ["gola"],
    "Q377119": ["pende"],
    "Q2749225": ["jaka"],
    "Q1717132": ["jombe"],
    "Q1465429": ["olcsa"],
    "Q1290677": ["meru"],
    "Q612976": ["pare"],
    "Q1276078": ["teita"],
    "Q1276229": ["murszi"],
    "Q1171957": ["dasszanecs"],
    "Q1134682": ["silluk"],
    "Q1427215": ["tsogho"],
}
# Terms a Hungarian name reaches by the prefix rule but that are another group:
# "vend" is the Hungarian name of the Rába Slovenes, not the Sorbs; "sváb" in
# a Hungarian museum is the Danube Swabians; "bena lulua" is the Luluwa of the
# Kasai, not the Bena of Tanzania. A term naming Roma or Jews ("oláh cigány",
# "román cigány", "magyar zsidó") never goes to the nation it starts with.
_NM_NOT = {"Q146521": {"vend"}, "Q1970302": {"sváb"}, "Q1115893": {"bena lulua"}}
_NM_NOT_WORDS = re.compile(r"cigány|zsidó")
# Left out on purpose, ambiguous: "kuba" (Kuba of the Kasai, but the name
# search reaches the Kuban Cossacks), "tonga" (Zambia or Polynesia), "bororo"
# (Brazil, or the Wodaabe), "afgán", "tatár", "szász", "sziú", "arab".

# Left out on purpose: "Maku" (several peoples: Hup, Dâw, Nadëb, Yuhup) and
# "Karipuna" (the Karipuna of Amapá and of Rondônia are two peoples).


# Peoples whose old court art the judge files as "archaeological" although the
# tradition is still practised. Edo: the Benin brass-casters' guild (Igun
# Street, Benin City) still works, so the 16th-century plaques are kept
# (decided 2026-10-04).
_LIVING_COURT = {"Q1287326"}


def _kinds() -> dict:
    p = REPO / "data" / "pool" / "kinds.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _kind(kinds: dict, name: str | None) -> dict:
    """The cached LLM reading of an object name, else the keyword lexicon's
    (kind_lexicon.py: the ethnographic museums' plain nouns, no LLM)."""
    from folk_patterns.kind_lexicon import classify
    n = (name or "").strip()[:120]
    c = kinds.get(n)
    if c and c.get("art_form") != "unclassified":
        return c
    return classify(n) or c or {}


def _art_form(kinds: dict, name: str | None) -> str:
    n = (name or "").strip()[:120]
    af = _kind(kinds, n).get("art_form", "unclassified")
    if af == "unclassified":
        for rx, fix in _KIND_FIX:
            if rx.search(n):
                return fix
    return af


# A BM object id starts with its department: E_Af (Africa), E_Am (Americas),
# E_As (Asia, the Middle East included), E_Oc (Oceania), E_Eu. The BM's
# ethnic_name term is one spelling for several peoples, so the department is
# the cheap check that the object is of this people's continent. Counted
# 2026-10-06 over the candidates: Lodha (India) had 234 Lozi (Zambia) objects,
# Kotas (India) 25 Kota (Gabon) reliquary figures, Teke (Congo) 45 Turkmen
# Teke ones, Catawba 74 and Nara (Eritrea) 35 from other continents, and
# French, Spaniards and Dutch colonial-era pieces. Indonesia, the Philippines
# and Malaysia sit across As and Oc (Nuaulu: 303 in As), North Africa and the
# Middle East across Af and As (Bedouin), and Russia, Turkey and the Caucasus
# across As and Eu (Nenets, Kalmyks), so those keep both.
_BM_DEPT = {"Africa": {"Af"}, "Americas": {"Am"}, "Asia": {"As"}, "Oceania": {"Oc"}, "Europe": {"Eu"}}
_BM_DEPT_BOTH = {
    **{c: {"As", "Oc"} for c in ("Indonesia", "Philippines", "Malaysia", "East Timor", "Timor-Leste", "Brunei", "Papua New Guinea")},
    **{c: {"Af", "As"} for c in ("Egypt", "Libya", "Tunisia", "Algeria", "Morocco", "Sudan", "Western Sahara", "Mauritania")},
    **{c: {"As", "Eu"} for c in ("Russia", "Turkey", "Georgia", "Armenia", "Azerbaijan", "Kazakhstan", "Cyprus")},
}


# BM spellings from the alias pass that name another people, checked by hand
# (2026-10-06). The cached harvest keeps the objects when an alias is later
# removed from aliases.json (Nzema came back with 5 Zimba objects), so they are
# refused here. Sihasapa are the "Blackfoot Sioux", a Lakota band; the BM's
# Blackfoot are the Blackfoot Confederacy. "Lozi" is "Lozi (Indic people)"
# with its qualifier stripped; the BM's Lozi are the Zambian people.
_BM_NAME_WRONG = {("Q1722812", "Zimba"), ("Q1680351", "Blackfoot"), ("Q6666357", "Lozi")}


def _bm_dept_ok(obj_id: str, people: dict) -> bool:
    m = re.match(r"[A-Z]+_(Af|Am|As|Oc|Eu)(?=\d|-|_|[A-Z])", str(obj_id))
    if not m:
        return True   # registration numbers without a department prefix are not judged
    allowed = _BM_DEPT_BOTH.get(str(people.get("country") or "")) or _BM_DEPT.get(str(people.get("continent") or ""))
    return not allowed or m.group(1) in allowed


def cmd_candidates() -> None:
    """Every listed people's objects, grouped by category — the pool the
    5-per-category pick works from. One culture per object: an object the BM
    tags with several peoples (Nguni + Zulu, Akan + Asante) goes to the most
    specific one, the people with the fewest objects; the umbrella keeps the
    rest. -> data/world/candidates.jsonl (gitignored)."""
    pe = [r for r in json.loads((OUT / "peoples.json").read_text(encoding="utf-8"))
          if r.get("listed") or r.get("unvetted_only")]
    # Peoples the screen step kept are on the map as stubs but were never in
    # peoples.json; without this their harvested BM objects (Kota: 25) never
    # became candidates and 857 stubs showed none.
    screened = json.loads((OUT / "screened.json").read_text(encoding="utf-8")) if (OUT / "screened.json").exists() else {}
    cls = json.loads((OUT / "classified.json").read_text(encoding="utf-8"))
    seen = {r["key"] for r in pe}
    # classified.json is corrected by hand (Nzema: Guinea-Bissau -> Ghana);
    # peoples.json keeps the country of the report run that wrote it.
    pe = [dict(r, country=(cls.get(r["key"]) or {}).get("country") or r.get("country")) for r in pe]
    for k, v in screened.items():
        if v.get("verdict") == "keep" and k not in seen and (cls.get(k) or {}).get("people"):
            c = cls[k]
            pe.append({"key": k, "label": v.get("label") or k, "continent": c.get("continent"),
                       "region": c.get("region"), "country": c.get("country") or v.get("country"),
                       "in_atlas": False, "unvetted_only": True, "bm_name": _bm_name(k)})
    bm = {}
    for l in (OUT / "bm_objects.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(l)
        bm[d["key"]] = [] if (d["key"], d.get("bm_name")) in _BM_NAME_WRONG else d["objects"]   # the last line per key wins (harvest --refill)
    loc, kinds = _local(), _kinds()
    eu = {}
    if (OUT / "eu_objects.jsonl").exists():
        for l in (OUT / "eu_objects.jsonl").read_text(encoding="utf-8").splitlines():
            d = json.loads(l)
            eu[d["key"]] = d["objects"]   # the last line per key wins (--only reruns)
    ethno = {}
    for d in _ethno_lines():
        # the last line per key and museum set wins
        ethno.setdefault(d["key"], {})[tuple(d.get("sources") or ("kamis", "smb"))] = d["objects"]
    from folk_patterns.museums import kamis
    for objs in (o for v in ethno.values() for o in v.values()):
        for o in objs:   # names cut before kamis.object_name read artists' initials
            if o["source"] in kamis.SITES and o.get("name") != "фотография":
                o["name"] = kamis.object_name((o.get("item") or {}).get("title") or o.get("name") or "")
    ethno = {k: list({(o["source"], o["id"]): o for objs in v.values() for o in objs   # runs may overlap
                      if not (o["source"] == "smb" and _SMB_SKIP_TYPE.search(o.get("name") or ""))
                      and o.get("museum_class") != "Restes humains"}.values())
             for k, v in ethno.items()}
    # Met/Cleveland culture texts local-audit judged to name a place or another
    # people for this one ("India (Rajasthan, Kota)" for the Kotas).
    lv_p = OUT / "local_verdicts.json"
    lv = json.loads(lv_p.read_text(encoding="utf-8")) if lv_p.exists() else {}
    # A region-named people takes its region's rows on purpose (_PLACE_PEOPLES:
    # Kalighat paintings are Bengali, Kutch embroidery Kutchi), so a drop that
    # only says "Bengal is a place" does not count for it. The judge read 211
    # pairs as drops; this keeps the ones naming the people's own places.
    own = {k: [p.casefold() for p in places] for k, places in _PLACE_PEOPLES}

    def dropped(k: str, people: str) -> bool:
        if (lv.get(_local_pair_id(k, people)) or {}).get("verdict") != "drop":
            return False
        return not any(p in people.casefold() for p in own.get(k, []))
    loc = {k: [o for o in objs if not dropped(k, str(o.get("people")))] for k, objs in loc.items()}
    pool = {r["key"]: [dict(o, source="bm") for o in bm.get(r["key"], []) if _bm_dept_ok(o["id"], r)] + loc.get(r["key"], [])
            + [{k: v for k, v in o.items() if k != "item"} for o in eu.get(r["key"], []) + ethno.get(r["key"], [])] for r in pe}
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
                af = o.get("art_form") or _art_form(kinds, o.get("name"))   # a source may set its own (neprajz)
                if af == "unclassified":   # the museum's own class: quai Branly's, Peabody's ("Headrest")
                    mc = o.get("museum_class") or (o.get("item") or {}).get("classification") or ""
                    af = _QB_CLASS.get(mc) or _MI_CLASS.get(mc) or _art_form(kinds, mc)
                cats.setdefault(af, []).append(
                    {"source": o.get("source"), "id": o["id"], "name": o.get("name"),
                     "kind": _kind(kinds, o.get("name")).get("kind")})
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
    if o["source"] in ("rem", "kunstkamera", "smb", "prm", "maa", "quaibranly", "peabody", "museudoindio", "ntm", "neprajz"):
        it = _ethno_index().get((o["source"], o["id"]))
        return it and {"title": it.get("title") or o.get("name") or "", "image_url": it["image_url"],
                       "description": "Museum's people term: " + ", ".join(it.get("ethnos") or []), "place": ""}
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


LOCAL_AUDIT_PROMPT = """A world atlas of folk culture attributes museum objects to peoples. The Met and
Cleveland objects below were matched because a word of the museum's culture
field equals a name of the people, so the match is text only. The culture field
mixes geography and peoples: "Africa, West Africa, Burkina Faso, Bwa" names the
Bwa people, but "India (Rajasthan, Kota)" names the city of Kota, not the Kota
people, and "Italian, Milan" names a city.

For each entry decide whether the culture field attributes the object to THIS
people (the one named, in the country given):
- keep: the field names this people as maker or culture, alone or as the most
  specific term ("Quechua", "Slovak", "German, Augsburg" for Germans, "Fang-Betsi"
  for Fang). A nation's own adjective counts ("Danish" for Danes).
- drop: the matched word is a place, city, kingdom, school or dynasty and not
  this people; or it names a different people with the same or a similar name;
  or the people is named only for a part that the field gives to another
  people ("hilt Turkish; blade Iranian" stays keep for both peoples named).
- uncertain: the field hedges between peoples ("Persian or Turkish", "possibly
  Italian", "Mongolian or Tibetan").

Answer for every entry, with its exact id.

{entries}"""

LOCAL_AUDIT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["entries"],
    "properties": {"entries": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "verdict", "reason"],
        "properties": {"id": {"type": "integer"}, "verdict": {"type": "string", "enum": ["keep", "drop", "uncertain"]},
                       "reason": {"type": "string"}}}}},
}


def _local_pair_id(key: str, people: str) -> str:
    return f"{key}|{people}"


def cmd_local_audit(limit: int = 0, workers: int = 3) -> None:
    """Codex judges each distinct (people, Met/Cleveland culture text) pair that
    reached the candidates once: keep / drop / uncertain. `candidates` leaves
    out the objects of a pair judged drop. Measured 2026-10-06: the Kotas of
    India held 15 Met paintings of the Kota school ("India (Rajasthan, Kota)").
    -> data/world/local_verdicts.json (cache), local_audit_raw.jsonl."""
    from concurrent.futures import ThreadPoolExecutor
    from folk_patterns.codex_cli import ask
    cache_p, raw_p = OUT / "local_verdicts.json", OUT / "local_audit_raw.jsonl"
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    cand = {json.loads(l)["key"]: json.loads(l) for l in (OUT / "candidates.jsonl").read_text(encoding="utf-8").splitlines()}
    pairs: dict[str, dict] = {}
    for d in (json.loads(l) for l in (OUT / "local_objects.jsonl").read_text(encoding="utf-8").splitlines()):
        r = cand.get(d["key"])
        if not r:
            continue
        ids = {(o.get("source"), str(o["id"])) for v in r["objects"].values() for o in v}
        for o in d["objects"]:
            # A row with no culture field was matched by its place on purpose
            # (_PLACE_PEOPLES); there is no text to judge.
            if (o["source"], str(o["id"])) in ids and o.get("people"):
                pid = _local_pair_id(d["key"], o["people"])
                p = pairs.setdefault(pid, {"label": r["label"], "country": r.get("country"), "people": o["people"], "n": 0, "names": []})
                p["n"] += 1
                if len(p["names"]) < 3 and o.get("name") not in p["names"]:
                    p["names"].append(o.get("name"))
    todo = [pid for pid in pairs if pid not in cache]
    if limit:
        todo = todo[:limit]
    batches = [todo[i:i + 40] for i in range(0, len(todo), 40)]
    print(f"local-audit: {len(pairs)} pairs, {len(todo)} to judge in {len(batches)} batches", flush=True)

    def one(batch: list[str]) -> list[tuple[str, dict]]:
        entries = "\n".join(f'- id: {i} | people: {pairs[pid]["label"]} ({pairs[pid]["country"] or "-"}) | '
                            f'culture field: "{pairs[pid]["people"]}" | objects: {pairs[pid]["n"]}, e.g. {", ".join(map(str, pairs[pid]["names"]))}'
                            for i, pid in enumerate(batch))
        reply = ask(LOCAL_AUDIT_PROMPT.format(entries=entries), schema=LOCAL_AUDIT_SCHEMA, timeout=900)
        got = (json.loads(reply) if isinstance(reply, str) else reply).get("entries", [])
        with open(raw_p, "a", encoding="utf-8") as raw:
            raw.write(json.dumps({"pairs": batch, "reply": got}, ensure_ascii=False) + "\n")
        return [(batch[g["id"]], g) for g in got if isinstance(g.get("id"), int) and 0 <= g["id"] < len(batch)]

    with ThreadPoolExecutor(workers) as ex:
        for got in ex.map(one, batches):
            for pid, g in got:
                cache[pid] = {"verdict": g["verdict"], "reason": g["reason"], "objects": pairs[pid]["n"]}
                if g["verdict"] != "keep":
                    print(f'  {g["verdict"]:9} {pairs[pid]["label"][:28]:28} "{str(pairs[pid]["people"])[:50]}" ({pairs[pid]["n"]}) {g["reason"][:90]}', flush=True)
            cache_p.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    from collections import Counter
    print(Counter(v["verdict"] for v in cache.values()),
          {k: sum(v["objects"] for v in cache.values() if v["verdict"] == k) for k in ("keep", "drop", "uncertain")})


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
    ap.add_argument("step", choices=["wikidata", "bm", "aliases", "labels", "europeana", "europeana-objects", "ethno-objects", "local", "classify", "harvest", "cleanup", "report", "candidates", "gaps", "pick", "coverage", "pick-import", "screen", "local-audit"])
    ap.add_argument("--only", nargs="*", default=[], help="pick/europeana-objects: peoples by Wikidata key, label or atlas name; screen: Wikidata keys")
    ap.add_argument("--shard", default="", help="pick: i/n, this process takes every n-th people (run n processes)")
    ap.add_argument("--cached-only", action="store_true", help="pick: no judge calls, no pick file written; record outcomes only")
    ap.add_argument("--export-batch", default="", help="pick --cached-only: write awaiting_judge candidates as a cloud batch")
    ap.add_argument("--batch", default="", help="pick-import: the batch name")
    ap.add_argument("--tries", type=int, default=0, help="pick: candidates shown to the judge per category (default 10); earlier verdicts come from the cache")
    ap.add_argument("--no-judge", action="store_true", help="pick: no judge calls; write the pick file from cached verdicts")
    ap.add_argument("--limit", type=int, default=0, help="cleanup/screen: only the first N (a test batch)")
    ap.add_argument("--workers", type=int, default=0, help="ethno-objects: peoples fetched in parallel (default 6)")
    ap.add_argument("--min-cats", type=int, default=1, help="cleanup/report: categories with 3+ objects a listed people needs")
    ap.add_argument("--pages", type=int, default=5, help="harvest: BM list pages (100 objects each) per people")
    ap.add_argument("--refill", action="store_true", help="harvest: re-fetch in full the peoples that hit the page cap")
    ap.add_argument("--aliases", action="store_true", help="bm: second pass over aliases.json")
    ap.add_argument("--threshold", type=int, default=6)  # 6: the museum-evidence floor the list was built with; 30 drops 239 listed peoples
    ap.add_argument("--multilingual", action="store_true", help="europeana: query cached Wikidata names in 19 languages")
    ap.add_argument("--museums", default="kamis,smb", help="ethno-objects: comma list of " + ",".join(ETHNO_SOURCES))
    ap.add_argument("--backend", choices=["claude", "codex"], default="claude", help="classify: local subscription backend")
    ap.add_argument("--all-min-sitelinks", type=int, default=0, help="classify: include every Wikidata item at this sitelink threshold")
    a = ap.parse_args()
    PICK_TRIES = a.tries or PICK_TRIES
    {"wikidata": cmd_wikidata, "bm": lambda: cmd_bm(a.aliases), "aliases": cmd_aliases, "labels": cmd_labels,
     "europeana": lambda: cmd_europeana(a.multilingual), "local": cmd_local,
     "europeana-objects": lambda: cmd_europeana_objects(a.only),
     "ethno-objects": lambda: cmd_ethno_objects(a.only, a.workers or 6, tuple(a.museums.split(","))),
     "classify": lambda: cmd_classify(a.threshold, a.backend, a.all_min_sitelinks),
     "harvest": lambda: cmd_harvest(a.pages, a.threshold, a.refill), "cleanup": lambda: cmd_cleanup(a.min_cats, a.limit),
     "candidates": cmd_candidates, "gaps": cmd_gaps, "screen": lambda: cmd_screen(a.only, a.limit), "local-audit": lambda: cmd_local_audit(a.limit),
     "pick": lambda: cmd_pick(a.only, a.shard, a.cached_only, a.no_judge, a.export_batch),
     "coverage": lambda: cmd_coverage(a.only), "pick-import": lambda: cmd_pick_import(a.batch)}.get(a.step, lambda: cmd_report(a.threshold, a.min_cats))()
