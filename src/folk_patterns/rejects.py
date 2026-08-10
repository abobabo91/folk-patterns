"""Structured rejection logger. Every scraper that drops a candidate record
should log why via `log_reject(...)`. This writes one JSON object per line
to `data/rejects.jsonl`, so you can measure filter effectiveness across runs
without piping stdout to a file.

Usage in a scraper:

    from folk_patterns.rejects import log_reject
    ...
    if is_junk:
        log_reject(source="europeana", region=region, country=country,
                   ethnicity=ethnicity, reason="latin-binomial",
                   title=title, extra={"provider": provider})
        continue

Read back:
    import json
    counts = collections.Counter()
    with open("data/rejects.jsonl", encoding="utf-8") as fh:
        for line in fh:
            counts[json.loads(line)["reason"]] += 1
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .util import DATA_DIR

_LOCK = threading.Lock()
_PATH = DATA_DIR / "rejects.jsonl"


def log_reject(
    source: str,
    reason: str,
    region: str | None = None,
    country: str | None = None,
    ethnicity: str | None = None,
    tradition: str | None = None,
    title: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "reason": reason,
        "region": region,
        "country": country,
        "ethnicity": ethnicity,
        "tradition": tradition,
        "title": (title or "")[:200],
    }
    if extra:
        entry.update({f"x_{k}": v for k, v in extra.items()})
    line = json.dumps(entry, ensure_ascii=False)
    with _LOCK:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        with _PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
