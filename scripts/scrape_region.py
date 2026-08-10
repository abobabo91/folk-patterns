"""Scrape a region's museums and file everything under
    library/<region>/<country>/<ethnicity>/<art_form>/<tradition>/
       {images/, metadata.json}

Two strategies:
  Met: country-first. Query by country name / cultural umbrella, filter by
       country gate, classify each survivor into an art_form, save under
       <country>/<ethnicity_primary>/<art_form>/general/.
  V&A: tradition-first. Query by tradition name once, route each hit to its
       country by place field, classify into art_form, save under
       <country>/<ethnicity>/<art_form>/<tradition>/.

Idempotent — skip images already on disk.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from folk_patterns.util import RateLimitedClient, DATA_DIR
from folk_patterns.museums import met, va, rijks, smithsonian


def load_seed(region: str) -> dict:
    return json.loads((DATA_DIR / "seed" / f"{region}.json").read_text(encoding="utf-8"))


def collect_traditions(seed: dict) -> dict[str, dict[str, str]]:
    """Return {tradition_name: {country_slug: ethnicity_name}}. If the same
    tradition name appears under multiple countries, all are recorded."""
    out: dict[str, dict[str, str]] = {}
    for country_entry in seed["countries"]:
        country = country_entry["country"]
        for eth in country_entry["ethnicities"]:
            for trad in eth["traditions"]:
                out.setdefault(trad, {})[country] = eth["name"]
    return out


def scrape_met_for_country(client, region, country_entry, max_per_art_form: int) -> dict:
    country = country_entry["country"]
    met_queries = country_entry.get("met_queries") or [country]
    primary_ethnicity = country_entry["ethnicities"][0]["name"]
    # Gate tokens: country name + primary ethnicity + any seed-declared extras.
    # `met_gate_tokens` on the country entry lets you add historical umbrella
    # terms (e.g. "Bactria", "Sogdian") that Met catalogues use for that region.
    # Adding a new country = one seed diff, no code touch.
    gate = list({country, primary_ethnicity})
    gate += country_entry.get("met_gate_tokens") or []
    return met.scrape_country(
        client=client, region=region, country=country,
        ethnicity_primary=primary_ethnicity,
        country_query_terms=met_queries,
        country_gate=gate,
        max_per_art_form=max_per_art_form,
    )


def scrape_tradition_sweep(client, region, seed, max_per_country: int, museum_key: str, museum_mod) -> dict:
    """Generic tradition-first sweep across a museum. Any museum with a
    `scrape_tradition_routed` function fits this shape (V&A, Rijksmuseum,
    Smithsonian, Europeana, ...)."""
    tradition_map = collect_traditions(seed)
    totals: dict[str, dict[str, int]] = {}
    for trad, country_eth_map in tradition_map.items():
        print(f"  [{museum_key}] tradition {trad!r}", flush=True)
        try:
            result = museum_mod.scrape_tradition_routed(
                client=client, region=region,
                tradition_name=trad,
                tradition_ethnicity_by_country=country_eth_map,
                max_per_country=max_per_country,
            )
        except Exception as e:
            print(f"    ! {museum_key} tradition {trad!r} failed: {e}", flush=True)
            continue
        for country, n in result.items():
            totals.setdefault(country, {})[trad] = n
        if result:
            print(f"    -> {result}", flush=True)
    return totals


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    ap.add_argument("--museums", default="met,va")
    ap.add_argument("--max-per-tradition", type=int, default=25)
    ap.add_argument("--only-country", help="Restrict Met to this country substring")
    args = ap.parse_args()

    seed = load_seed(args.region)
    # Canonical region slug lives IN the seed file (hyphen form). Overrides
    # whatever the user typed on the CLI so `cultural.region` is consistent
    # regardless of `python scrape_region.py central_asia` vs `central-asia`.
    args.region = seed.get("region", args.region)
    museums = [m.strip() for m in args.museums.split(",")]
    countries = seed["countries"]
    if args.only_country:
        needle = args.only_country.lower()
        countries = [c for c in countries if needle in c["country"].lower()]

    grand_met: dict[str, dict[str, int]] = {}
    with RateLimitedClient(min_interval_s=0.5) as client:
        if "met" in museums:
            for country_entry in countries:
                country = country_entry["country"]
                print(f"\n=== [MET] {country} ===", flush=True)
                totals = scrape_met_for_country(client, args.region, country_entry, args.max_per_tradition)
                for k, v in totals.items():
                    if v:
                        print(f"    {country}/{k}: {v}", flush=True)
                grand_met[country] = totals

        sweeps: dict[str, dict] = {}
        for m_key, m_mod in [("va", va), ("rijks", rijks), ("si", smithsonian)]:
            if m_key in museums:
                print(f"\n=== [{m_key}] tradition sweep ===", flush=True)
                sweeps[m_key] = scrape_tradition_sweep(client, args.region, seed, args.max_per_tradition, m_key, m_mod)

    print("\n=== GRAND TOTAL ===")
    print("Met by country / art_form:")
    for country, per_af in grand_met.items():
        s = ", ".join(f"{af}={n}" for af, n in per_af.items() if n)
        print(f"  {country:30s}  {s or '(none)'}")
    for m_key, totals in sweeps.items():
        print(f"{m_key} by country / tradition:")
        if not totals:
            print(f"  (no records — check that scrape_tradition_routed returned per-country counts)")
        for country, per_trad in totals.items():
            total = sum(per_trad.values())
            print(f"  {country:30s}  total={total}  ({sum(1 for v in per_trad.values() if v)} traditions with hits)")


if __name__ == "__main__":
    main()
