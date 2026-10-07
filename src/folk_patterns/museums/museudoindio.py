"""Museu do Índio (FUNAI, Rio de Janeiro): its Tainacan collection,
https://tainacan.museudoindio.gov.br. Checked 2026-10-07.

    GET /wp-json/tainacan/v2/collection/471/items?perpage=96&paged=N
        &fetch_only=title,thumbnail,url,id&fetch_only_meta=<metadatum ids>
        96 rows a page at most (perpage=100 returns 96), ~3 s a page;
        X-WP-Total gives the count (20,965 published items)

Every record names its people in the "Povo" metadatum (187 values: "Krahô",
"Guarani Mbiá", "Txicão"), the museum's own field, so an object is assigned by
that text alone. The museum's "Categoria" (ten values: "Cerâmica", "Armas",
"Trançados"...) gives the art form; its object names are Portuguese, which the
keyword lexicon does not read. The newest acquisitions (2022) have no image yet.
The whole collection is small, so it is downloaded once into
data/world/museudoindio_items.jsonl and matched locally.
"""
from __future__ import annotations

import functools
import json
import time
from pathlib import Path

import httpx

BASE = "https://tainacan.museudoindio.gov.br/wp-json/tainacan/v2"
COLLECTION = 471
POVO, CATEGORIA, AUTOID = 1020138, 544, 574
_UA = {"User-Agent": "Mozilla/5.0 (folk-patterns atlas research; https://folk-patterns.vercel.app)"}

# Categoria -> atlas art form
CLASS = {
    "Adornos de Materiais Ecléticos, Indumentária e Toucador": "jewelry",
    "Adornos Plumários": "jewelry",
    "Armas": "arms",
    "Cerâmica": "ceramic",
    "Cordões e Tecidos": "textile",
    "Instrumentos musicais e de sinalização": "instruments",
    "Objetos rituais, mágicos e lúdicos": "masks-ritual",
    "Trançados": "household",     # basketry: the lexicon files baskets under household
    "Utensílios e implementos de materiais ecléticos": "household",
    "Etnobotânica": "unclassified",
}


def client() -> httpx.Client:
    return httpx.Client(timeout=120, headers=_UA, follow_redirects=True)


def _image(thumb) -> str:
    if not isinstance(thumb, dict):
        return ""
    for size in ("tainacan-medium-full", "medium_large", "large", "full"):
        v = thumb.get(size)
        if v and v[0]:
            return v[0]
    return ""


def _row(it: dict) -> dict:
    meta = {m.get("name"): (m.get("value_as_string") or "").strip() for m in (it.get("metadata") or {}).values()}
    return {"id": str(it["id"]), "title": it.get("title") or "", "url": it.get("url") or "",
            "image": _image(it.get("thumbnail")), "povo": meta.get("Povo", ""),
            "categoria": meta.get("Categoria", ""), "autoid": meta.get("Autoidentificação", "")}


@functools.lru_cache(maxsize=2)
def download(path: Path) -> list[dict]:
    """Every published item, cached in `path` (one JSON row per item)."""
    if path.exists():
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows: list[dict] = []
    with client() as cl:
        page = 1
        while True:
            for wait in (10, 60, 0):
                r = cl.get(f"{BASE}/collection/{COLLECTION}/items", params={
                    "perpage": 96, "paged": page, "order": "ASC", "orderby": "date",
                    "fetch_only": "title,thumbnail,url,id", "fetch_only_meta": f"{POVO},{CATEGORIA},{AUTOID}"})
                if r.status_code < 500 or not wait:
                    break
                time.sleep(wait)
            r.raise_for_status()
            items = r.json().get("items") or []
            rows += [_row(it) for it in items]
            if page % 20 == 0:
                print(f"  museudoindio: page {page}, {len(rows)} items", flush=True)
            if len(items) < 96:
                break
            page += 1
            time.sleep(0.5)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)
    return rows


def peoples(row: dict) -> list[str]:
    """The record's people terms: "Povo", split at "|" like the other multi-values."""
    return [p.strip() for p in row.get("povo", "").split("|") if p.strip()]


def object_url(oid: str) -> str:
    return f"https://tainacan.museudoindio.gov.br/?p={oid}"
