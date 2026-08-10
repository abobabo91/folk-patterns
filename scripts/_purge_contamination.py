"""Purge visually-confirmed contamination in the library.

Two contamination classes:
  A. Europeana ethnonym-substring false positives:
     - San: Italian/Spanish saint-name architecture
     - Chin: China maps, Boer women hats
     Filter: reject records whose title/description contains anti-topic tokens
     that would never appear in a legitimate San/Chin object.

  B. commons_arch broad-category noise:
     - Basilan / Zamboanga City catch every naval ship + tourist photo
     - Nukus catches Chevrolets, air pollution reports
     - Rondavels in South Africa catches game reserves + Afrikaans resorts
     - Mbuti people catches botanical specimens
     Filter: reject records whose title contains clearly non-cultural tokens.
"""
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"

# (A) Europeana anti-topic patterns per ethnicity slug
EUROPEANA_REJECT_PATTERNS = {
    "san": [
        re.compile(r"San (Marco|Marc|Giovanni|Lorenzo|Martin|Sebasti|Isidro|Gimignano|Zeno|Zanipol|Michele|Francesco|Salvatore|Matteo|Gennaro|Zanobi|Simeone|Luca|Juan|Miguel|Antonio|Pedro|Pablo|Nicol|Cristob|Andr|Bernardin)", re.I),
        re.compile(r"\b(Iglesia|Kirche|Cathedral|Baptister|Basilica|Duomo|Scuola|Convent|Palazzo|Monaste|Chapel|Escorial|Verrocchio|Donatello|Cimabue|Goya|Guardi|Rusconi)\b", re.I),
        re.compile(r"\b(Venedig|Florenz|Antequera|Neapel|Rom|Venice|Florence|Naples|Rome|Ecija)\b"),
    ],
    "chin": [
        re.compile(r"\b(Imperio Chino|Chinese Empire|Peking|Beijing|Ming dynasty|Qing dynasty|Han dynasty|Voortrekker|Boer)\b", re.I),
        re.compile(r"\bChina y (Japon|Jap[oó]n)\b", re.I),
    ],
}

# (B) commons_arch noise patterns — token that appears in title means NOT cultural material
COMMONS_REJECT_PATTERNS = [
    re.compile(r"\bBRP [A-Z][a-z]+\b"),                                # Navy ships (BRP Ivatan, BRP Malvar)
    re.compile(r"\b(Chevrolet|Toyota|Ford|Nissan|Honda|Hyundai) taxi", re.I),
    re.compile(r"\bAir pollution\b", re.I),
    re.compile(r"\bJardin botanique|botanical (garden|specimen)\b", re.I),
    re.compile(r"\bGlyphaea|Serratula|Iris ensata|Londesia\b"),         # Latin plant binomials
    re.compile(r"\bgame reserve\b|\brest ?camp\b", re.I),
    re.compile(r"\bATKVHartenbos\b"),                                    # Afrikaans resort
    re.compile(r"\bAmbassadors? visit\b", re.I),
    re.compile(r"^Atlas pittoresque pl \d", re.I),                       # generic atlas plates
    re.compile(r"^Bay \d+\.jpg", re.I),                                  # numeric Karakalpak bay photos with no cultural context
    re.compile(r"^Doslıq|^Dosliq|^Erik gu'li", re.I),                    # Nukus urban geography
    re.compile(r"^\d{8} khiva\d+", re.I),                                # Khiva tourism photo-runs (Karakalpak bucket)
    re.compile(r"^Between [A-Z][a-z]+ and\b"),                           # "Between Nukus and Urgench"
]

# (B) commons_arch narrow rejects — some categories are just too broad to trust
COMMONS_CATEGORY_REJECT = {
    ("yakan", "Basilan"),
    ("yakan", "Zamboanga City"),
    ("karakalpak", "Nukus"),
    ("xhosa", "Rondavels in South Africa"),
    ("t-boli", "Lake Sebu"),
}

purged_records = 0
purged_images = 0
touched_files = set()

for meta in LIB.rglob("metadata.json"):
    parts = meta.relative_to(LIB).parts
    if len(parts) < 5: continue
    ethn = parts[2]
    try: recs = json.loads(meta.read_text(encoding="utf-8"))
    except: recs = []
    if not recs: continue

    keep = []
    for rec in recs:
        src = (rec.get("source") or {}).get("museum") or ""
        title = ((rec.get("physical") or {}).get("title") or "")
        desc = ((rec.get("physical") or {}).get("summary") or "")
        hay = title + " " + desc

        drop = False

        # (A) Europeana ethnonym-substring false positives
        if src == "europeana":
            patterns = EUROPEANA_REJECT_PATTERNS.get(ethn, [])
            for pat in patterns:
                if pat.search(hay):
                    drop = True
                    break

        # (B) commons_arch broad-category noise
        if not drop and src == "commons_arch":
            raw = rec.get("raw") or {}
            cat = raw.get("source_category", "")
            if (ethn, cat) in COMMONS_CATEGORY_REJECT:
                drop = True
            if not drop:
                for pat in COMMONS_REJECT_PATTERNS:
                    if pat.search(title):
                        drop = True
                        break

        if drop:
            purged_records += 1
            for img in rec.get("images") or []:
                lp = img.get("local_path") or ""
                fname = Path(lp).name
                if fname:
                    p = meta.parent / "images" / fname
                    if p.exists():
                        p.unlink()
                        purged_images += 1
        else:
            keep.append(rec)

    if len(keep) != len(recs):
        meta.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
        touched_files.add(meta)

# Cleanup empty images dirs
for meta in touched_files:
    imgs = meta.parent / "images"
    if imgs.exists() and not any(imgs.iterdir()):
        imgs.rmdir()

print(f"Contaminated records purged: {purged_records}")
print(f"Image files deleted:         {purged_images}")
print(f"Metadata files modified:     {len(touched_files)}")
