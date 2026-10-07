"""National Taiwan Museum (國立臺灣博物館): its open catalogue on the Ministry
of Culture's collections site, https://collections.culture.tw. Checked 2026-10-07.

    GET /getMetadataList.aspx?format=OpenData&DN=ntm
        the whole catalogue, one 97 MB JSON list (133,203 records), Open
        Government Data License 1.0; fields MainTitle, Type, Description,
        OriginalUrl ... ImageUrl is empty in every record
    GET <OriginalUrl>   (/Object.aspx?SYSUID=13&RNO=...)
        the record page; <img id="imgLarge" src="/ShowGalImage.aspx?...">
        is the picture, 400 x 300. The src carries an encrypted token that
        differs on every page load; it redirects (302) to /ShowGalImage?...,
        which answers without cookie or Referer. A token was still valid 10
        minutes later; how long it stays valid is not known.

The indigenous objects are Type "人類學\\原住民類" (7,393). The catalogue has no
people field: the description names the people in the object's quoted full
title ("本件國立臺灣博物館藏「鄒族單鏃鐵箭」"), or says which people used it
("為平埔族群（噶瑪蘭族）使用物件"). Only those two phrases, or a title that
starts with the people, count: the rest of a description also compares peoples
("雅美族的木雕人像其型態一般較排灣族木雕人像簡單"), and about half the records
name no people at all and are left out.
"""
from __future__ import annotations

import functools
import json
import re
import time
from pathlib import Path

import httpx

BASE = "https://collections.culture.tw"
CATALOG_URL = f"{BASE}/getMetadataList.aspx?format=OpenData&DN=ntm"
INDIGENOUS = "人類學\\原住民類"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (folk-patterns atlas research)"}

# Atlas people (Wikidata id) -> the museum's names. The museum writes the
# official ethnonyms; Yami also appears as 達悟 and "雅美/達悟族".
NAMES = {
    "Q701676": ["泰雅族"], "Q715238": ["排灣族"], "Q333484": ["賽夏族"], "Q715994": ["魯凱族"],
    "Q205006": ["阿美族"], "Q706500": ["卑南族"], "Q701704": ["布農族"], "Q706528": ["雅美", "達悟"],
    "Q333624": ["西拉雅族"], "Q619481": ["鄒族"], "Q707464": ["賽德克族"], "Q714393": ["邵族"],
    "Q710817": ["撒奇萊雅族"], "Q718347": ["噶瑪蘭族"], "Q709963": ["太魯閣族"], "Q15939915": ["卡那卡那富族"],
    "Q697899": ["洪雅族", "洪安雅族"], "Q17370580": ["拉阿魯哇族", "沙阿魯阿族"], "Q619826": ["巴布薩族", "貓霧捒族"],
    "Q716841": ["凱達格蘭族"], "Q3814071": ["噶哈巫族"], "Q15912275": ["大武壠族", "大滿族"],
}

_QUOTED = re.compile(r"館藏?「([^」]{1,40})」")
_USED_BY = re.compile(r"為([^，。；\s]{1,20}?族群?(?:（[^）]{1,10}）)?)使用")
_IMG = re.compile(r'id="imgLarge"\s+src="([^"]+)"')


def client() -> httpx.Client:
    return httpx.Client(timeout=120, headers=_UA, follow_redirects=True)


@functools.lru_cache(maxsize=2)
def catalog(path: Path) -> list[dict]:
    """The indigenous records of the catalogue, downloaded once into `path`."""
    if not path.exists():
        with client() as cl:
            r = cl.get(CATALOG_URL)
            r.raise_for_status()
            path.write_bytes(r.content)
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    return [r for r in rows if (r.get("Type") or "").startswith(INDIGENOUS)]


def attribution(rec: dict) -> str:
    """The phrase that names the people: the quoted title, the "used by" phrase,
    or the title itself; "" when none of them names a people (…族)."""
    d = rec.get("Description") or ""
    for m in (_QUOTED.search(d), _USED_BY.search(d)):
        if m and "族" in m.group(1):
            return m.group(1)
    t = rec.get("MainTitle") or ""
    return t if "族" in t[:8] else ""


def matches(rec: dict, names: list[str]) -> bool:
    """The record is this people's: its attribution names it and no other people
    of NAMES ("雅美/達悟族" is one people under two names)."""
    a = attribution(rec)
    if not a or not any(n in a for n in names):
        return False
    others = {n for ns in NAMES.values() if not set(ns) & set(names) for n in ns}
    return not any(o in a for o in others)


def image_url(cl: httpx.Client, rec: dict) -> str:
    """The record page's large picture, as its redirect target; "" without one."""
    for wait in (5, 30, 0):
        r = cl.get(rec["OriginalUrl"])
        if r.status_code < 500 or not wait:
            break
        time.sleep(wait)
    if r.status_code != 200:
        return ""
    m = _IMG.search(r.text)
    if not m:
        return ""
    src = m.group(1).replace("&amp;", "&")
    src = src if src.startswith("http") else BASE + "/" + src.lstrip("/")
    h = cl.get(src, follow_redirects=False)   # HEAD answers 404
    loc = h.headers.get("location", "")
    if h.status_code in (301, 302) and loc:
        return loc if loc.startswith("http") else BASE + "/" + loc.lstrip("/")
    return src if h.status_code == 200 else ""


def object_id(rec: dict) -> str:
    return rec["Identifier"]


def object_url(oid: str) -> str:
    """The record page; RNO is the identifier in base64 without its "=" padding."""
    import base64
    return f"{BASE}/Object.aspx?SYSUID=13&RNO={base64.b64encode(oid.encode()).decode().rstrip('=')}"
