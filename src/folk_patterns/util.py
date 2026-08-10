"""Shared helpers: rate-limited HTTP, path building, image download + hash."""
from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any

import httpx
from slugify import slugify
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
LIBRARY_DIR = REPO_ROOT / "library"
RAW_DIR = DATA_DIR / "raw"


def library_path(region: str, country: str, ethnicity: str, art_form: str = "", tradition: str = "") -> Path:
    """New layout: region/country/ethnicity/art_form/tradition/. art_form and
    tradition are optional so callers can build partial paths."""
    p = LIBRARY_DIR / slugify(region) / slugify(country) / slugify(ethnicity)
    if art_form:
        p = p / slugify(art_form)
    if tradition:
        p = p / slugify(tradition)
    return p


def raw_path(museum: str, query_key: str) -> Path:
    d = RAW_DIR / slugify(museum)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{slugify(query_key)}.json"


class TransientError(Exception):
    """Retryable: 5xx, 403 (Met's soft-block), empty body, JSON decode fail."""


class RateLimitedClient:
    """httpx.Client wrapper that sleeps between requests + retries transients.
    Museum APIs are polite but flaky under load — Met in particular has been
    seen returning 403 and empty-200 bodies during sustained pulls."""

    def __init__(self, min_interval_s: float = 0.5, timeout: float = 30.0):
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "folk-patterns/0.1 (research atlas; https://github.com/abobabo91; contact: elekesabo@gmail.com)"},
        )
        self.min_interval_s = min_interval_s
        self._last_request_ts = 0.0

    def _wait(self):
        wait = self.min_interval_s - (time.time() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.15))  # small jitter

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1.5, min=2, max=20),
        retry=retry_if_exception_type(TransientError),
        reraise=True,
    )
    def get(self, url: str, **kw) -> httpx.Response:
        self._wait()
        r = self.client.get(url, **kw)
        self._last_request_ts = time.time()
        # Treat Met's soft-block symptoms as transient.
        if r.status_code == 403 or r.status_code >= 500:
            raise TransientError(f"HTTP {r.status_code} on {url}")
        if r.status_code >= 400:
            r.raise_for_status()
        return r

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1.5, min=2, max=20),
        retry=retry_if_exception_type(TransientError),
        reraise=True,
    )
    def get_json(self, url: str, **kw) -> dict:
        r = self.get(url, **kw)
        try:
            return r.json()
        except Exception as e:
            raise TransientError(f"non-JSON body on {url}: {e}") from e

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


_IMAGE_MAGICS = (
    b"\xff\xd8\xff",           # JPEG
    b"\x89PNG\r\n\x1a\n",       # PNG
    b"GIF8",                    # GIF
)


def _looks_like_image(data: bytes) -> bool:
    if any(data.startswith(m) for m in _IMAGE_MAGICS):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


def download_image(client: RateLimitedClient, url: str, dst: Path) -> tuple[str, int]:
    """Download an image. Returns (sha256, byte_count). Skips if dst exists.

    Validates magic bytes — some Europeana media URLs return MP3 or PDF
    content with `image/jpeg` Content-Type, and the pipeline used to save
    those unchecked. Raises ValueError on non-image content so the caller
    can log_reject the record and move on."""
    if dst.exists():
        b = dst.read_bytes()
        return hashlib.sha256(b).hexdigest(), len(b)
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = client.get(url)
    if not _looks_like_image(r.content[:16]):
        raise ValueError(f"non-image response ({r.content[:8].hex()}) from {url}")
    dst.write_bytes(r.content)
    return hashlib.sha256(r.content).hexdigest(), len(r.content)


def _record_key(record: dict) -> str | tuple:
    """Canonical records have top-level id="<source>-<id>". Legacy records
    used flat source+object_id fields."""
    if "id" in record and isinstance(record["id"], str):
        return record["id"]
    return (record.get("source"), record.get("object_id"))


def append_metadata(dir_: Path, record: dict[str, Any]) -> None:
    """Append a metadata record to dir/metadata.json (array of records)."""
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / "metadata.json"
    if path.exists():
        arr = json.loads(path.read_text(encoding="utf-8"))
    else:
        arr = []
    key = _record_key(record)
    arr = [r for r in arr if _record_key(r) != key]
    arr.append(record)
    path.write_text(json.dumps(arr, indent=2, ensure_ascii=False), encoding="utf-8")
