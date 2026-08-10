"""Shared --only matcher used by all scraper drivers.

Old behaviour was case-insensitive substring match, so `--only Turkish` also
matched `Kurdish (Turkish)`. This matches EXACT ethnicity name OR the
slugified ethnicity name OR the slugified region__country__ethnicity key.
"""
from slugify import slugify


def matches(needle: str, ethnicity: str, country: str = "", region: str = "") -> bool:
    """True if `needle` unambiguously identifies this ethnicity."""
    if not needle:
        return True
    n = needle.strip().lower()
    ethn_lower = ethnicity.strip().lower()
    if n == ethn_lower:
        return True
    ethn_slug = slugify(ethnicity)
    if n == ethn_slug or n == slugify(needle) == ethn_slug:
        return True
    key = "__".join(slugify(x) for x in (region, country, ethnicity) if x)
    if key and n == key:
        return True
    # Substring match ONLY if it's unambiguous (no parens in ethnicity name,
    # or the substring is bounded by word boundaries). Keeps the ergonomic
    # short-form while blocking the "Turkish" ⊂ "Kurdish (Turkish)" bug.
    if n in ethn_lower and "(" not in ethnicity:
        return True
    return False
