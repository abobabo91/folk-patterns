"""Third-round semantic purge — findings from 200-image visual audit.

Contamination classes discovered:
  1. BM Cham author collision: French cartoonist "Cham" (Amédée de Noé)
     humorous illustrated books filed under Vietnamese Cham.
  2. commons_arch broad-category noise: Sotho/Rondavels catches Duisburg Zoo
     food stand; Cham/Po Klong Garai has an Angkor Thom Bayon relief.
  3. Rijks 'hol' tradition collision: Dutch numismatic descriptions containing
     "hol" (hollow) get routed to Southeast Asian _regional buckets.
  4. Wider Estonian provider catches: Estonian War Museum (not in v2 list).
  5. Kunstpalast Düsseldorf ukiyo-e for Maasai.
  6. Chin Dutch archive "am.-eng.-chin." (Chinese soldier abbreviations).
  7. Khmer/Heidelberg auction catalog (should have been caught by library
     token but predates the filter).
"""
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"

# (1) BM Cham author collision
BM_CHAM_REJECT = re.compile(
    r"\bpar CHAM\b|\bAm[eé]d[eé]e.*No[eé]|de No[eé], Am[eé]d"
    r"|\bcomic book\b|\bcaricaturist\b|\bcours de g[eé]om[eé]trie\b",
    re.I,
)

# (2) commons_arch broad categories — entire (ethnicity, category) buckets to purge
COMMONS_CATEGORY_REJECT = {
    ("sotho", "Rondavels in South Africa"),
    ("filipino", "Paoay Church"),  # colonial Spanish church, not Filipino folk
}
# commons_arch specific-file rejects by title fragment
COMMONS_TITLE_REJECT_BY_ETHN = {
    "cham": re.compile(r"\bAngkor Thom|Angkor Wat|Bayon\b", re.I),
    "sotho": re.compile(r"\bZoo Duisburg|Verkaufsh[uü]tte\b", re.I),
}

# (3) Rijks 'hol' tradition path prefix
RIJKS_HOL_PATH_FRAGMENTS = ("/unclassified/hol/",)

# (4) Broader Estonian provider catch for Bamar (already in europeana.py, but
# repeat here in canonical form to purge existing data). Any provider mention.
BAMAR_ESTONIAN = re.compile(
    r"\b(estonia|estonian|eesti|tartu|haapsalu|tallinn|p[aä]rnu)\b", re.I,
)

# (5) Maasai Kunstpalast
MAASAI_KUNSTPALAST = re.compile(r"kunstpalast", re.I)

# (6) Chin Dutch archive shorthand
CHIN_DUTCH_ARCHIVE = re.compile(
    r"\bchin\.[- ]|\ballied soldiers|geallieerde soldaten",
    re.I,
)

# (7) Any-ethnicity: Heidelberg / university library auction catalogs
LIBRARY_AUCTION_CATALOG = re.compile(
    r"heidelberg university library|drouot|art d'extr[eê]me-orient|"
    r"catalogue des ventes|auction catalog",
    re.I,
)


def _record_key(rec, meta_path):
    ethn = meta_path.relative_to(LIB).parts[2] if len(meta_path.relative_to(LIB).parts) >= 3 else ""
    src = (rec.get("source") or {}).get("museum") or ""
    title = (rec.get("physical") or {}).get("title") or ""
    desc = (rec.get("physical") or {}).get("summary") or ""
    provider = (rec.get("location") or {}).get("current_museum") or ""
    raw = rec.get("raw") or {}
    cat = raw.get("source_category", "")
    return {
        "ethn": ethn, "src": src, "title": title, "desc": desc,
        "provider": provider, "cat": cat,
        "path": str(meta_path.relative_to(LIB)),
    }


purged = 0
imgs_deleted = 0
touched = set()

for meta in LIB.rglob("metadata.json"):
    try: recs = json.loads(meta.read_text(encoding="utf-8"))
    except: continue
    keep = []
    for rec in recs:
        k = _record_key(rec, meta)
        drop = False
        reasons = []

        # (1) BM Cham
        if k["src"] == "british_museum" and k["ethn"] == "cham":
            if BM_CHAM_REJECT.search(k["title"] + " " + k["desc"]):
                drop = True; reasons.append("bm-cham-author")

        # (2) commons_arch categories + titles
        if not drop and k["src"] == "commons_arch":
            if (k["ethn"], k["cat"]) in COMMONS_CATEGORY_REJECT:
                drop = True; reasons.append("commons-broad-category")
            pat = COMMONS_TITLE_REJECT_BY_ETHN.get(k["ethn"])
            if not drop and pat and pat.search(k["title"]):
                drop = True; reasons.append("commons-wrong-topic")

        # (3) Rijks hol path
        if not drop and k["src"] == "rijks":
            if any(f in k["path"] for f in RIJKS_HOL_PATH_FRAGMENTS):
                drop = True; reasons.append("rijks-hol-collision")

        # (4) Bamar Estonian widened
        if not drop and k["src"] == "europeana" and k["ethn"] == "bamar":
            if BAMAR_ESTONIAN.search(k["provider"]):
                drop = True; reasons.append("bamar-estonian-widened")

        # (5) Maasai Kunstpalast
        if not drop and k["src"] == "europeana" and k["ethn"] == "maasai":
            if MAASAI_KUNSTPALAST.search(k["provider"]):
                drop = True; reasons.append("maasai-kunstpalast")

        # (6) Chin Dutch shorthand
        if not drop and k["src"] == "europeana" and k["ethn"] == "chin":
            if CHIN_DUTCH_ARCHIVE.search(k["title"] + " " + k["desc"]):
                drop = True; reasons.append("chin-dutch-shorthand")

        # (7) Auction / library catalogs
        if not drop and k["src"] == "europeana":
            if LIBRARY_AUCTION_CATALOG.search(k["title"] + " " + k["desc"] + " " + k["provider"]):
                drop = True; reasons.append("auction-catalog")

        if drop:
            purged += 1
            for img in rec.get("images") or []:
                fname = Path(img.get("local_path") or "").name
                if fname:
                    p = meta.parent / "images" / fname
                    if p.exists():
                        p.unlink(); imgs_deleted += 1
        else:
            keep.append(rec)
    if len(keep) != len(recs):
        meta.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
        touched.add(meta)

for meta in touched:
    imgs = meta.parent / "images"
    if imgs.exists() and not any(imgs.iterdir()):
        imgs.rmdir()

print(f"Semantic purge v3:")
print(f"  Records removed: {purged}")
print(f"  Images deleted:  {imgs_deleted}")
print(f"  Files touched:   {len(touched)}")
