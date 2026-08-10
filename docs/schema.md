# Canonical record schema

Every object stored under `library/<region>/<country>/<ethnicity>/<art_form>/<tradition>/metadata.json` follows this shape. Defined in `src/folk_patterns/schema.py`.

```jsonc
{
  "id": "va-O360718",                         // "<source>-<object_id>", globally unique
  "source": {
    "museum": "va",                            // va | met | rijks | cooper | smithsonian | europeana
    "museum_name": "Victoria and Albert Museum",
    "object_id": "O360718",
    "object_url": "https://collections.vam.ac.uk/item/O360718",
    "iiif_manifest": null,
    "credit_line": "Bequeathed by Miss D B Simpson",
    "rights": "public-domain",                 // public-domain | public-domain-non-commercial | unknown | <verbatim>
    "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
    "accession_number": "T.113-1977",
    "accession_year": 1977
  },
  "cultural": {
    "region": "central-asia",
    "country": "Uzbekistan",
    "ethnicity": "Uzbek",
    "tradition": "suzani",
    "art_form": "textile",                     // see classify.py
    "pattern_density": 3                       // 0=figurative/plain, 3=pure surface pattern
  },
  "physical": {
    "title": "suzani",
    "titles_alt": [],
    "date_text": "mid 19th century",
    "date_earliest": 1825,                     // parsed year, may be null
    "date_latest": 1875,
    "period": null,                            // e.g. "Timurid", from Met
    "dynasty": null,
    "materials": ["silk", "cotton"],
    "techniques": ["embroidery", "chain stitch"],
    "medium_raw": "silk on cotton, embroidered",  // untouched from museum
    "classification": "Suzani",                // museum's own type name
    "styles": [],
    "categories": ["Textiles", "Embroidery", "Household objects"],
    "physical_description": null,
    "summary": null,
    "historical_context": null,
    "dimensions": [
      { "dimension": "Width",  "value": "149", "unit": "cm", "part": null },
      { "dimension": "Length", "value": "224", "unit": "cm", "part": null }
    ],
    "dimensions_note": null,
    "marks_inscriptions": null
  },
  "location": {
    "made_in_place": "Bukhara",
    "made_in_place_alt": [],
    "current_gallery": "007",                  // gallery / room / display number
    "current_museum": "Victoria and Albert Museum, London",
    "on_display": null
  },
  "attribution": {
    "makers": [                                // may be empty
      { "name": "…", "role": "designer", "dates": "1830 – 1890", "urls": ["https://…wikidata"] }
    ],
    "acquisition_history": [],                 // provenance chain if published
    "excavation": null                         // Met's excavation field
  },
  "linked_data": {
    "wikidata_url": null,
    "aat_urls": [],                            // Getty Art & Architecture Thesaurus
    "wikipedia": null,
    "other_urls": []
  },
  "images": [
    {
      "url": "https://framemark.vam.ac.uk/collections/2018LB2714/full/1000,/0/default.jpg",
      "iiif_id": "2018LB2714",
      "iiif_base": "https://framemark.vam.ac.uk/collections/2018LB2714/full/{size}/0/default.jpg",
      "role": "primary",                       // "primary" | "alt"
      "sha256": "1fba02226deb…",
      "bytes": 551205,
      "local_path": "central-asia/uzbekistan/uzbek/textile/suzani/images/va_O360718.jpg"
    }
    // …additional views if the museum published them
  ],
  "map": {
    "lat": null,                               // for future object-level geocoding
    "lon": null,
    "geocoded_from": null
  },
  "processed_at": "2026-07-18T14:09:33+00:00",
  "raw": {
    "search": { /* the search snippet the record was discovered from */ },
    "deep":   { /* the /museumobject/{id} full record (V&A) or full /objects/{id} (Met) */ }
  }
}
```

## Invariants

- Every top-level section (`source`, `cultural`, `physical`, `location`, `attribution`, `linked_data`, `images`, `map`) is always present. No `if k in rec` needed downstream.
- Every field is either its typed value or `null` / `[]` / `{}`.
- `id` is stable and idempotent — re-scraping never duplicates.
- `raw` always contains the untouched museum response(s). We never re-scrape to recover a field we dropped.

## When to extend the schema

Only add fields that a downstream consumer actually needs. When you add a field, do it in `_empty_record()` (so every record still has it) + fill it in each source's transformer. Don't add source-specific fields at top level — put them under `raw` and expose the derived value through a canonical field.
