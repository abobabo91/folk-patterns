"""World list of peoples with museum evidence — which cultures the atlas could
add, per continent, before anything is scraped.

    python scripts/world_peoples.py wikidata     # -> data/world/wikidata.json
    python scripts/world_peoples.py bm           # BM "Ethnic group" hits per name (needs BM_CDP_URL)
    python scripts/world_peoples.py aliases      # Wikidata English aliases of the names BM found 0 for
    python scripts/world_peoples.py bm --aliases # retry those under their aliases
    python scripts/world_peoples.py europeana    # hits at ethnographic providers per name
    python scripts/world_peoples.py local        # Met + Cleveland pool rows whose people field names it
    python scripts/world_peoples.py classify     # Wikipedia summary + Haiku: a people? where?
    python scripts/world_peoples.py harvest      # BM object names per people (<= 500), for category breadth
    python scripts/world_peoples.py cleanup      # Haiku: atlas match, duplicates, sub-groups
    python scripts/world_peoples.py report       # -> data/world/peoples.json

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
        m = re.sub(r"(possibly|probably|unknown|workshop|-?style|maker|artist|people|peoples)", " ", parts[-1], flags=re.I)
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


def _bm_name(k: str) -> str | None:
    """The spelling the BM answered to (first pass or alias pass)."""
    for d in (_counts("bm").get(k), _counts("bm_alias").get(k)):
        for v, n in ((d or {}).get("hits") or {}).items():
            if n:
                return v
    return None


def cmd_harvest(pages: int, threshold: int = 30) -> None:
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


_CATS = ["textile", "garment", "jewelry", "ceramic", "metalwork", "arms", "masks-ritual", "sculpture",
         "instruments", "household", "architectural", "painting-mss"]   # photo / unclassified do not count


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
                af = (kinds.get((x.get("name") or "").strip()[:120]) or {}).get("art_form", "unclassified")
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
             "A people is **listed** when its objects fill at least one of the 12 categories with 3+ objects (column "
             "**cat. 3+**). It is counted at all when the museums with a people field hold "
             f"{threshold}+ image objects under its "
             "name: the British Museum \"Ethnic group\" and the Met and Cleveland culture fields (the Met: public "
             "domain, 1700 or later). **Met+Cle** is that count. The Met's European entries (French, German) come "
             "from its costume and arms departments, not folk collections. The V&A names places, not peoples, so it "
             "cannot be counted per people. Smithsonian anthropology has no open images. **Breadth** is how many of "
             "the 12 object categories (photo and unclassified excluded) have 5+ objects in a sample of up to 500 BM "
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
    ap.add_argument("step", choices=["wikidata", "bm", "aliases", "europeana", "local", "classify", "harvest", "cleanup", "report"])
    ap.add_argument("--limit", type=int, default=0, help="cleanup: only the first N (a test batch)")
    ap.add_argument("--min-cats", type=int, default=1, help="cleanup/report: categories with 3+ objects a listed people needs")
    ap.add_argument("--pages", type=int, default=5, help="harvest: BM list pages (100 objects each) per people")
    ap.add_argument("--aliases", action="store_true", help="bm: second pass over aliases.json")
    ap.add_argument("--threshold", type=int, default=30)
    a = ap.parse_args()
    {"wikidata": cmd_wikidata, "bm": lambda: cmd_bm(a.aliases), "aliases": cmd_aliases, "europeana": cmd_europeana, "local": cmd_local,
     "classify": lambda: cmd_classify(a.threshold), "harvest": lambda: cmd_harvest(a.pages, a.threshold), "cleanup": lambda: cmd_cleanup(a.min_cats, a.limit)}.get(a.step, lambda: cmd_report(a.threshold, a.min_cats))()
