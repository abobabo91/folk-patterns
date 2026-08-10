"""Second-round semantic purge.

Newly discovered contamination classes from visual review:
  1. Audio archive records with waveform placeholder images (CNRS timeside).
  2. Historical maps mis-attributed as ethnic-culture artifacts (18th c. French
     nautical charts, 19th c. Abyssinia atlas, "Imperio Chino y Japon").
  3. Estonian public sculpture mis-attributed to Bamar (Myanmar) — one-off,
     Haapsalu / Juhan Raudsepp signature.
  4. Colonial-era Oromo "Map of Abissinia" already caught by (2).
"""
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "library"

# URL fragments signalling audio archives
AUDIO_URL_TOKENS = ("crem.cnrs", "crem_cnrs", "/sounds/", "data_sounds", "timeside", "archives_sound", "archives_items")
# Title patterns signalling maps rather than ethnographic artifacts
MAP_TITLE = re.compile(r"^(Carte|Mapa|Carta|Map|Kaart|Atlas)\b", re.I)
MAP_KEYWORD = re.compile(r"\b(Imperio Chino|Cochinchine|Abissinia|Abyssinia)\b", re.I)
# Estonia / Baltic misroute
ESTONIA_KEYWORDS = re.compile(
    r"\b(Estonia|Eesti|Haapsalu|Tartu|Tallinn|Juhan Raudsepp|Saaremaa|Poiss kalaga|Haapsalus)\b",
    re.I,
)
# Providers that repeatedly misroute to specific ethnicities via language
# collision. Same list as in europeana._HOSTILE_PROVIDER_BY_ETHNICITY.
HOSTILE_PROVIDER_BY_ETHN = {
    "bamar": [re.compile(r"Tartu Art Museum|Estonian History Museum|Estonian National Museum|Art Museum of Estonia", re.I)],
}
# Generic non-artifact titles (museum inventories, Surinamese-Dutch, academic).
GENERIC_NONARTIFACT_TITLE = re.compile(
    r"^\s*("
    r"f[oö]rteckning|register"
    r"|Model van een|Surinaams|Suriname|Kreools|Creoolse"
    r"|How [A-Z].*learnt|Colaboraci[oó]n art"
    r"|karamellipaperi|makeisk|Reklamma|Namn[aä]"
    r")",
    re.I,
)
# Academic repository providers
ACADEMIC_PROVIDER = re.compile(r"SSOAR|GESIS|Leibniz Institute for the Social|Polytechnic University|Open Access Repository", re.I)

purged = 0
imgs_deleted = 0
touched = set()

for meta in LIB.rglob("metadata.json"):
    try: recs = json.loads(meta.read_text(encoding="utf-8"))
    except: continue
    if not recs: continue
    keep = []
    for rec in recs:
        drop = False
        if (rec.get("source") or {}).get("museum") == "europeana":
            title = ((rec.get("physical") or {}).get("title") or "")
            desc = ((rec.get("physical") or {}).get("summary") or "")
            provider = (rec.get("location") or {}).get("current_museum") or ""
            hay = title + " " + desc
            ethn = meta.relative_to(LIB).parts[2] if len(meta.relative_to(LIB).parts) >= 3 else ""
            # audio: check any URL-shaped field on the record
            for img in rec.get("images") or []:
                lp = (img.get("local_path") or "").lower()
                url = (img.get("url") or "").lower()
                if any(t in lp or t in url for t in AUDIO_URL_TOKENS):
                    drop = True; break
            if not drop:
                raw = rec.get("raw") or {}
                shown = raw.get("edmIsShownBy") or ""
                if isinstance(shown, list): shown = shown[0] if shown else ""
                if any(t in shown.lower() for t in AUDIO_URL_TOKENS):
                    drop = True
            if not drop and (MAP_TITLE.match(title) or MAP_KEYWORD.search(hay)):
                drop = True
            if not drop and ESTONIA_KEYWORDS.search(hay):
                drop = True
            if not drop:
                for hp in HOSTILE_PROVIDER_BY_ETHN.get(ethn, []):
                    if hp.search(provider):
                        drop = True; break
            if not drop and GENERIC_NONARTIFACT_TITLE.match(title):
                drop = True
            if not drop and ACADEMIC_PROVIDER.search(provider):
                drop = True

        if drop:
            purged += 1
            for img in rec.get("images") or []:
                lp = img.get("local_path") or ""
                fname = Path(lp).name
                if fname:
                    p = meta.parent / "images" / fname
                    if p.exists():
                        p.unlink()
                        imgs_deleted += 1
        else:
            keep.append(rec)
    if len(keep) != len(recs):
        meta.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
        touched.add(meta)

# Prune empty images/ dirs
for meta in touched:
    imgs = meta.parent / "images"
    if imgs.exists() and not any(imgs.iterdir()):
        imgs.rmdir()

print(f"Semantic purge v2:")
print(f"  Records removed:  {purged}")
print(f"  Images deleted:   {imgs_deleted}")
print(f"  Files touched:    {len(touched)}")
