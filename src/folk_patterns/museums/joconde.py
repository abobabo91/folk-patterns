"""Joconde, the French museums' joint catalogue, through POP (Plateforme ouverte
du patrimoine, Ministry of Culture). Checked 2026-10-07.

    GET https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/joconde.csv
        the whole catalogue, one 1.24 GB "|"-separated CSV (1,059,942 records),
        no key; read as a stream and only the rows of the wanted places kept
    GET https://api.pop.culture.gouv.fr/notices/joconde/<Reference>
        one record as JSON; IMG lists its pictures ("joconde/<ref>/0000647.jpg"),
        served from IMAGE_BASE (the record's COPY field names the rights holder)

There is no people field. The place of making or use
(Lieu_de_creation_utilisation, "France,Corse,Haute Corse,Bastia (lieu de
création)") is the only signal, so it serves a people that is one place:
PLACES. For Corsica that is 85 records, 47 with a picture, most of them from
the Musée d'ethnographie corse in Bastia. Heraldry, epigraphy and
archaeology are left out (KEEP), as are paintings and drawings made in Corsica
by visiting artists (a Matisse-museum landscape): a painting counts when the
Bastia museum holds it or its title names the Corsicans ("Portrait de femme
corse"). The MuCEM's Corsican hunting and household objects mostly have no
picture.
"""
from __future__ import annotations

import csv
import functools
import json
import re
import sys
from pathlib import Path

import httpx

CSV_URL = "https://ministere-culture.s3.sbg.io.cloud.ovh.net/POP/joconde.csv"
API = "https://api.pop.culture.gouv.fr/notices/joconde"
IMAGE_BASE = "https://pop-perf-assets.s3.gra.io.cloud.ovh.net"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (folk-patterns atlas research)"}

# Atlas people (Wikidata id) -> the place word in Lieu_de_creation_utilisation.
PLACES = {"Q509590": "Corse"}

_SKIP_DOMAIN = re.compile(r"héraldique|archéologie|art contemporain")
_SKIP_DENOM = re.compile(r"^(plaque|écu|écusson|blason)\b")
_PICTURE = re.compile(r"peinture|dessin")
_LOCAL_MUSEUM = re.compile(r"ethnographie corse")


def client() -> httpx.Client:
    return httpx.Client(timeout=60, headers=_UA, follow_redirects=True)


def _place(row: dict) -> str:
    return " ".join(row.get(k) or "" for k in ("Lieu_de_creation_utilisation", "Ecole_pays", "Geographie_historique"))


@functools.lru_cache(maxsize=2)
def download(path: Path) -> tuple[dict, ...]:
    """The records made or used in any of PLACES, streamed once out of the CSV
    into `path` (gitignored); about 10 minutes."""
    if not path.exists():
        csv.field_size_limit(sys.maxsize // 2)
        words = set(PLACES.values())
        tmp = path.with_suffix(".part")
        with httpx.stream("GET", CSV_URL, timeout=600, headers=_UA) as r, tmp.open("w", encoding="utf-8") as f:
            r.raise_for_status()
            for row in csv.DictReader(r.iter_lines(), delimiter="|"):
                if any(w in _place(row) for w in words):
                    f.write(json.dumps({k: v for k, v in row.items() if v}, ensure_ascii=False) + "\n")
        tmp.replace(path)
    return tuple(json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip())


def keep(row: dict) -> bool:
    """A record with a picture that is the people's own work (see module doc)."""
    if row.get("Presence_image") != "oui":
        return False
    dom, deno = row.get("Domaine") or "", row.get("Denomination") or ""
    if _SKIP_DOMAIN.search(dom) or _SKIP_DENOM.search(deno):
        return False
    if _PICTURE.search(dom):
        return bool(_LOCAL_MUSEUM.search(row.get("Nom_officiel_musee") or "") or "corse" in (row.get("Titre") or "").lower())
    return True


def records(key: str, path: Path) -> list[dict]:
    word = PLACES.get(key)
    return [r for r in download(path) if word and word in _place(r) and keep(r)] if word else []


def art_form(row: dict) -> str:
    """Paintings and sculpture by the museum's domain; "" leaves it to the lexicon."""
    dom = row.get("Domaine") or ""
    if _PICTURE.search(dom):
        return "painting-mss"
    if dom.startswith("sculpture"):
        return "sculpture"
    return ""


def image_url(cl: httpx.Client, ref: str) -> str:
    r = cl.get(f"{API}/{ref}")
    if r.status_code != 200:
        return ""
    img = (r.json().get("IMG") or [""])[0]
    return f"{IMAGE_BASE}/{img}" if img else ""


def object_url(ref: str) -> str:
    return f"https://www.pop.culture.gouv.fr/notice/joconde/{ref}"
