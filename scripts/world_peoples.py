"""World list of peoples with museum evidence — which cultures the atlas could
add, per continent, before anything is scraped.

    python scripts/world_peoples.py wikidata     # -> data/world/wikidata.json
    python scripts/world_peoples.py bm           # BM "Ethnic group" hits per name (needs BM_CDP_URL)
    python scripts/world_peoples.py aliases      # Wikidata English aliases of the names BM found 0 for
    python scripts/world_peoples.py bm --aliases # retry those under their aliases
    python scripts/world_peoples.py europeana    # hits at ethnographic providers per name
    python scripts/world_peoples.py local        # Met + Cleveland pool rows whose people field names it
    python scripts/world_peoples.py europeana-objects [--only ...]  # ethnographic Europeana objects naming the people + its country
    python scripts/world_peoples.py classify     # Wikipedia summary + Haiku: a people? where?
    python scripts/world_peoples.py harvest      # BM object names per people (<= 500), for category breadth
    python scripts/world_peoples.py cleanup      # Haiku: atlas match, duplicates, sub-groups
    python scripts/world_peoples.py report       # -> data/world/peoples.json + docs/world-peoples.md
    python scripts/world_peoples.py candidates   # -> data/world/candidates.jsonl: objects per category, one culture per object
    python scripts/world_peoples.py pick --only Haida Tiv   # vetted, ranked objects per category -> data/world/picks/ (needs BM_CDP_URL)
    python scripts/world_peoples.py pick --only ... --shard 0/3   # + 1/3, 2/3 in two more processes

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
    for _ in range(3):
        r = httpx.post("https://query.wikidata.org/sparql", data={"query": q, "format": "json"}, headers=UA, timeout=300)
        if r.status_code == 200:
            return [{k: v["value"] for k, v in b.items()} for b in r.json()["results"]["bindings"]]
        print(f"  wikidata {r.status_code}, retrying", flush=True)
        time.sleep(15)
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
            "etnolog", "ethnolog", "folk", "rahva", "etnografisk", "open air museum", "skansen", "mucem")


def cmd_europeana() -> None:
    from folk_patterns.museums.europeana import _get_key
    key = _get_key()
    done = _done("europeana")
    todo = [(k, l) for k, l in _names() if k not in done]
    print(f"europeana: {len(todo)} names to count ({len(done)} cached)", flush=True)
    with httpx.Client(timeout=60) as cl, open(OUT / "counts_europeana.jsonl", "a", encoding="utf-8") as f:
        for i, (k, l) in enumerate(todo):
            hits, provs = {}, {}
            for v in variants(l):
                try:
                    j = cl.get("https://api.europeana.eu/record/v2/search.json", params={
                        "wskey": key, "query": f'"{v}"', "rows": 0, "media": "true", "reusability": "open,permission",
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
                if hits[v]:
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


def cmd_local() -> None:
    """Met and Cleveland rows already in data/pool (harvest_pool.py) whose
    people / culture field names the people, as a whole word or phrase:
    "Asmat people", "Africa, West Africa, Burkina Faso, Bwa". Free, no requests.
    -> data/world/local_objects.jsonl, one line per key with the matched objects."""
    pool = REPO / "data" / "pool"
    rows = []
    for src in ("met", "cleveland"):
        for l in (pool / f"{src}.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(l)
            except ValueError:
                continue
            if r.get("people") and _people_text(r):
                rows.append(r)
    # index word n-grams (1-3) of the folded people text -> row positions
    index: dict[str, set[int]] = {}
    for i, r in enumerate(rows):
        w = re.findall(r"[^\W_]+(?:['’][^\W_]+)?", _fold(_people_text(r)).replace("-", " "))
        for n in (1, 2, 3):
            for j in range(len(w) - n + 1):
                index.setdefault(" ".join(w[j:j + n]), set()).add(i)
    al_p = OUT / "aliases.json"
    al = json.loads(al_p.read_text(encoding="utf-8")) if al_p.exists() else {}
    with open(OUT / "local_objects.jsonl", "w", encoding="utf-8") as f:
        hit = 0
        for k, l in _names():
            names = list(dict.fromkeys(v for x in [l] + al.get(k, []) for v in variants(x)))
            found: set[int] = set()
            for v in names:
                key = " ".join(re.findall(r"[^\W_]+(?:['’][^\W_]+)?", _fold(v).replace("-", " ")))
                if len(key) >= 3:
                    found |= index.get(key, set())
            objs = [{"source": rows[i]["source"], "id": rows[i]["id"], "name": rows[i].get("object_name") or rows[i].get("title"),
                     "people": rows[i]["people"]} for i in sorted(found)]
            f.write(json.dumps({"key": k, "label": l, "objects": objs}, ensure_ascii=False) + "\n")
            hit += bool(objs)
    print(f"{len(rows)} Met + Cleveland rows with a people field; {hit} of {len(_names())} names match at least one")


EU_LANGS = ["en", "sv", "nl", "de", "es", "fr", "cs", "da", "nb", "fi", "it", "pt", "pl", "hu"]
EU_MAX = 500   # items fetched per people
_EU_GEO_ONLY_NAMES = {"Q640090"}  # Kongo: 500 sampled hits name the country, not the people
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
    100 Inuit records say "inuit"). A provider whose name holds a double quote is
    skipped: it breaks the query (it emptied Maya). Sámi stays thin: the records
    say "samer", "samisk", "saame", and "Sami" is a Finnish first name.
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
    -> data/world/eu_objects.jsonl (gitignored); the last line per key wins."""
    from folk_patterns.museums.europeana import _get_key
    key = _get_key()
    pe = [r for r in json.loads((OUT / "peoples.json").read_text(encoding="utf-8")) if r.get("listed")]
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
            names = list(dict.fromkeys(v for x in [r["label"], r.get("atlas") or ""] if x for v in variants(x)))
            nrx = re.compile(r"(?<![\w-])(" + "|".join(re.escape(n) if len(n) <= 4 else "(?i:" + re.escape(n) + ")"
                                                        for n in names) + r")(?![\w-])")
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
                         if any(g in x["label"].lower() for g in _EU_GOOD) and '"' not in x["label"]]
                cursor = "*"
                while provs and cursor and fetched < EU_MAX:
                    q = f'"{n}" AND (' + " OR ".join(f'DATA_PROVIDER:"{pv}"' for pv in provs) + ")"
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
                        identity = " | ".join(str(v) for k in ("title", "dcCreator", "dcDescription", "dcSubject")
                                              for v in (it.get(k) or []))
                        geography = " | ".join(str(v) for k in ("edmPlaceLabel", "dcCoverage", "dcSpatial")
                                               for v in (it.get(k) or []))
                        geo = re.sub(r"\b(new|nieuw|nya|neu|nouvelle|nueva|nuova|nova)[ -]guin\w*", "", _fold(identity + " | " + geography))
                        if it["id"] in objs or not nrx.search(identity) or not (crx and crx.search(geo)):
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


def cmd_classify(threshold: int) -> None:
    """Wikipedia summary + Haiku for every name that passes the threshold:
    is it a people, and where. Cached in data/world/classified.json."""
    import subprocess, tempfile
    cache_p = OUT / "classified.json"
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    wd = {r["qid"]: r for r in json.loads((OUT / "wikidata.json").read_text(encoding="utf-8"))}
    rows = [r for r in _rows() if (_museums(r) >= threshold or r["europeana"] >= 30) and r["key"] not in cache]
    print(f"classify: {len(rows)} names ({len(cache)} cached)", flush=True)
    with httpx.Client(timeout=30, headers=UA, follow_redirects=True) as cl:
        sums = [_summary(cl, r["article"]) if r.get("article") else "" for r in rows]
    print(f"  {sum(1 for x in sums if not x)} of {len(sums)} without article text", flush=True)
    mcp = Path(tempfile.gettempdir()) / "empty_mcp.json"
    mcp.write_text('{"mcpServers":{}}', encoding="utf-8")
    raw = open(OUT / "classify_raw.jsonl", "a", encoding="utf-8")
    for i in range(0, len(rows), 60):
        batch = [(r, s) for r, s in zip(rows[i:i + 60], sums[i:i + 60])]
        entries = "\n".join(f'- key: {r["key"]} | name: {r["label"]} | wikidata country: {r.get("country") or "-"} | '
                            f'text: {s or "(no article text)"}' for r, s in batch)
        res = subprocess.run([__import__("shutil").which("claude") or "claude", "--print", "--model", CLASSIFY_MODEL, "--output-format", "json", "--tools", "",
                              "--mcp-config", str(mcp), "--strict-mcp-config"],
                             input=CLASSIFY_PROMPT.format(entries=entries), capture_output=True, text=True,
                             encoding="utf-8", timeout=600, env={**__import__("os").environ, "MAX_THINKING_TOKENS": "0"})
        ev = json.loads(res.stdout)
        raw.write(json.dumps({"batch": i, "cost_usd": ev.get("total_cost_usd"), "result": ev.get("result")},
                             ensure_ascii=False) + "\n")
        raw.flush()
        m = re.search(r"\[.*\]", ev.get("result") or "", re.S)
        got = json.loads(m.group(0)) if m else []
        for d in got:
            if d.get("key") in {r["key"] for r, _ in batch}:
                cache[d["key"]] = d
        cache_p.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"  batch {i}: {len(got)}/{len(batch)} classified, ${ev.get('total_cost_usd') or 0:.3f}", flush=True)


def _local() -> dict[str, list[dict]]:
    p = OUT / "local_objects.jsonl"
    if not p.exists():
        return {}
    return {d["key"]: d["objects"] for d in (json.loads(l) for l in p.read_text(encoding="utf-8").splitlines())}


def _museums(r: dict) -> int:
    """Image objects in the museums with a people field: BM (first-page count) + Met + Cleveland."""
    return r["bm"] + r.get("local", 0)


def _rows() -> list[dict]:
    wd = {r["qid"]: r for r in json.loads((OUT / "wikidata.json").read_text(encoding="utf-8"))}
    bm, bma, eu = _counts("bm"), _counts("bm_alias"), _counts("europeana")
    loc = _local()
    atlas = set(_atlas_names())
    rows = []
    for k, l in _names():
        b = max(list((bm.get(k) or {}).get("hits", {0: 0}).values()) + list((bma.get(k) or {}).get("hits", {0: 0}).values()))
        e = max((eu.get(k) or {}).get("hits", {0: 0}).values() or [0])
        w = wd.get(k, {})
        rows.append({"key": k, "label": l, "country": w.get("country"), "sitelinks": w.get("sitelinks"),
                     "article": w.get("article"), "bm": b, "local": len(loc.get(k, [])), "europeana": e,
                     "in_atlas": k.startswith("atlas:") or any(v in atlas for v in variants(l))})
    return rows


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
]


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
    pe = [r for r in json.loads((OUT / "peoples.json").read_text(encoding="utf-8")) if r.get("listed")]
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
                           "kisa", "tsotso", "khayo"}}


def _norm_group(g: str) -> str:
    return re.sub(r"\s+(?:people|peoples)$", "", g, flags=re.I).casefold()


def _source_exclusion(o: dict, d: dict, expected_bm_group: str = "") -> str:
    """Source labels that cannot establish an authentic maker attribution."""
    expected = _norm_group(expected_bm_group) if expected_bm_group else ""
    allowed = {expected} | _BM_SUBGROUPS.get(expected, set())
    groups = {_norm_group(g) for g in d.get("production_ethnic_groups") or []}
    if o["source"] == "bm" and "(?)" in (d.get("production_ethnic_attribution") or ""):
        return "museum marks production ethnic group uncertain"
    # an umbrella and its own member group ("Luyia; Bukusu") name one people
    if o["source"] == "bm" and len(groups) > 1 and not groups <= allowed:
        return "museum attributes production to multiple peoples"
    if o["source"] == "bm" and expected and groups:
        if not groups & allowed:
            return f"museum attributes production to {', '.join(sorted(groups))}, not {expected}"
    if o["source"] == "cleveland" and re.search(r"\b[\w-]+-style maker\b", d.get("description") or "", re.I):
        return "style-only maker attribution"
    if o["source"] == "bm" and re.search(r"\b(?:fake|forgery)\b", d.get("title") or "", re.I):
        return "museum labels object a fake or forgery"
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


def cmd_pick(only: list[str], shard: str = "") -> None:
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
    tops, drum pegs, sinew, catalogue cards, raw eggshell)."""
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
    seen = {}
    for l in (l for p in sorted((OUT / "picks").glob("raw*.jsonl")) for l in p.read_text(encoding="utf-8").splitlines()):
        x = json.loads(l)
        if "QUALITY:" in (x.get("reply") or ""):
            seen[(x["key"], x["source"], x["id"])] = x
    bm_client = _client() if os.environ.get("BM_CDP_URL") else None
    http = httpx.Client(timeout=45, follow_redirects=True, headers=UA)
    for r in rows:
        name = r.get("atlas") or re.sub(r"\s+(people|peoples)$", "", r["label"])
        accepted: list[dict] = []
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
                if o["source"] == "bm" and (bm_client is None or _in_library(o["id"])):
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
                        continue
                    if not d:
                        print(f"  {cat:13s} {o['id']:22s} no image", flush=True)
                        continue
                    if img is None:
                        print(f"  {cat:13s} {o['id']:22s} image fetch failed", flush=True)
                        continue
                    if img.status_code != 200 or (o["source"] == "europeana" and not img.headers.get("content-type", "").startswith("image/")):
                        print(f"  {cat:13s} {o['id']:22s} image {img.status_code}", flush=True)
                        continue
                    source_exclusion = _source_exclusion(o, d, r.get("bm_name") or name)
                    if source_exclusion:
                        # No judge call, so no try used: Tlingit 2026-09-27 lost 64 of its
                        # 95 tries to BM "multiple peoples" / "uncertain" flags.
                        print(f"  {cat:13s} {o['id']:22s} drop: {source_exclusion}", flush=True)
                        n -= 1
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
                    n -= 1
                    continue
                belongs, af, reason, conf, image, era = parse_reply(reply) if reply else (None, None, err, "", "", "")
                q = _quality(reply)
                # A museum photograph the judge files as a spear, bowl or mask is a
                # picture of an object: the object record is the gallery item, and
                # the print is not a documentary photo of people. Asmat 2026-09-27:
                # 17 BM "photographic print" records (EA_Oc-B142-*) re-filed this way;
                # Tlingit: BM postcards of totem poles (EA_Am-B59-*) filed as sculpture.
                if re.match(r"photographic print|photograph\b|postcard", d.get("title") or "", re.I) and af not in (None, "photo"):
                    print(f"  {cat:13s} {o['id']:22s} drop: photograph of an object ({af})", flush=True)
                    continue
                ok = belongs and era not in ("modern", "archaeological") and image in ("good", "weak") and q >= PICK_QUALITY_MIN
                if belongs and era not in ("modern", "archaeological") and image in ("good", "weak"):
                    accepted.append({**o, "title": d.get("title"), "image_url": d["image_url"],
                                     "judge": (hit.get("judge") if hit else judge_name) or "pick:claude-sonnet-5",
                                     "art_form": af if af and af != "unclassified" else cat,
                                     "image": image, "era": era, "quality": q, "confidence": conf, "reason": reason})
                print(f"  {cat:13s} {o['id']:22s} {'KEEP' if ok else 'drop'} q{q} {af or '-':13s} {image:6s} {era:14s} "
                      f"{'(cached) ' if hit else ''}{reason[:80]}", flush=True)
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
        (OUT / "picks" / f"{r['key']}.json").write_text(json.dumps(
            {"key": r["key"], "label": r["label"], "name": name, "judged": tried, "cached": cached, "cost_usd": round(cost, 3),
             "ranked": ranked}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{name}: {sum(map(len, ranked.values()))} kept, {sum(map(len, picks.values()))} featured in {len(picks)} categories "
              f"({', '.join(f'{c} {len(v)}' for c, v in picks.items())}); {tried} judged, {cached} cached, ${cost:.2f}, {time.time() - t0:.0f}s", flush=True)


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
    rows = [r for r in _rows() if not r["key"].startswith("atlas:") and (_museums(r) >= threshold or r["europeana"] >= 30)]
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
    for c, rs in sorted(by.items()):
        l = [r for r in rs if r["listed"]]
        print(f"  {c:9s} listed {len(l):3d} (new {sum(not r['in_atlas'] for r in l):3d}, breadth>=6 {sum((r.get('breadth') or 0) >= 6 for r in l):3d})"
              f"   not listed {sum(r['tier'] == 'bm' and not r['listed'] for r in rs):3d}   Europeana-only {sum(r['tier'] != 'bm' for r in rs)}")


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
             "is only a hint. Peoples only Europeana finds are listed separately, because most of those are "
             "word collisions (\"Iron\" for Ossetians, \"Bali\", \"Dan\"). Clans, iwi and bands are merged into their "
             "people (\"incl.\") by the curated `data/world/merges.json`. Its note lists the model suggestions that were "
             "rejected: Sonnet folded distinct peoples into umbrella groups (Hopi into Puebloan, Vezo into Merina).", ""]
    for cont in sorted({r.get("continent") or "?" for r in keep}):
        rs = [r for r in keep if (r.get("continent") or "?") == cont and r["listed"]]
        below = [r for r in keep if (r.get("continent") or "?") == cont and r["tier"] == "bm" and not r["listed"]]
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
    ap.add_argument("step", choices=["wikidata", "bm", "aliases", "europeana", "europeana-objects", "local", "classify", "harvest", "cleanup", "report", "candidates", "pick"])
    ap.add_argument("--only", nargs="*", default=[], help="pick/europeana-objects: peoples by Wikidata key, label or atlas name")
    ap.add_argument("--shard", default="", help="pick: i/n, this process takes every n-th people (run n processes)")
    ap.add_argument("--limit", type=int, default=0, help="cleanup: only the first N (a test batch)")
    ap.add_argument("--min-cats", type=int, default=1, help="cleanup/report: categories with 3+ objects a listed people needs")
    ap.add_argument("--pages", type=int, default=5, help="harvest: BM list pages (100 objects each) per people")
    ap.add_argument("--refill", action="store_true", help="harvest: re-fetch in full the peoples that hit the page cap")
    ap.add_argument("--aliases", action="store_true", help="bm: second pass over aliases.json")
    ap.add_argument("--threshold", type=int, default=30)
    a = ap.parse_args()
    {"wikidata": cmd_wikidata, "bm": lambda: cmd_bm(a.aliases), "aliases": cmd_aliases, "europeana": cmd_europeana, "local": cmd_local, "europeana-objects": lambda: cmd_europeana_objects(a.only),
     "classify": lambda: cmd_classify(a.threshold), "harvest": lambda: cmd_harvest(a.pages, a.threshold, a.refill), "cleanup": lambda: cmd_cleanup(a.min_cats, a.limit), "candidates": cmd_candidates, "pick": lambda: cmd_pick(a.only, a.shard)}.get(a.step, lambda: cmd_report(a.threshold, a.min_cats))()
