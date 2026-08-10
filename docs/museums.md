# Museum sources — quirks and workarounds

Sourced from `tools/knowledge base/museum open access apis 2026-07.md`. Kept here for the project-local view.

## Currently wired

| Museum | Base URL | Key? | Depth | Strength |
|---|---|---|---|---|
| **Victoria & Albert** | `api.vam.ac.uk/v2` | no | search + `/museumobject/{id}` deep-fetch (60+ fields) | Largest ethnographic textile archive; strong Silk Road + South Asia |
| **Met Museum** | `collectionapi.metmuseum.org/public/collection/v1` | no | search + `/objects/{id}` full (55+ fields) | CC0 images, Islamic Art department is Central Asian gold |
| **Rijksmuseum** | `data.rijksmuseum.nl/search/collection` | no (new API is keyless) | search + Linked Art `/id.rijksmuseum.nl/<id>` resolve | Dutch colonial reach → Indonesia (Sumatra 1861, Java 1645, batik 40), Persian holdings (164). **Zero Central Asian.** |

## Wired but not yet used at scale

Cooper Hewitt, Smithsonian OA, Europeana. All keyed, all free, ~2 min signup each. When scaling beyond the current region.

## V&A

**Two-step flow:**
1. `GET /v2/objects/search?q=<term>&images=true` → lightweight snippet (10 fields: `_primaryImageId`, `_primaryPlace`, `_primaryTitle`, `objectType`, etc.).
2. `GET /v2/museumobject/<systemNumber>` → deep record with 60+ fields including `dimensions`, `productionDates` (with `earliest`/`latest` parsed years), `placesOfOrigin`, `techniques`, `historicalContext`, `objectHistory`, `bibliographicReferences`, `galleryLocations`, `credits`, and up to N additional image IDs.

**Image URL pattern (IIIF):** `https://framemark.vam.ac.uk/collections/{imageId}/full/{size},/0/default.jpg` — size is width in pixels, e.g. `1000,`.

**Place routing:** every record's `_primaryPlace` field is checked against `places.py::CENTRAL_ASIA::place_to_country`. Records with a rejected place (e.g. "Cairo" from a `muqarnas` query) are dropped. Records with a generic place like "Central Asia" go into the `_regional` bucket.

**Deep-fetch cache:** `data/raw/va-deep/<systemNumber>.json`. Keep this; it's the expensive one.

## Met Museum

**Silent-fallback bug.** `GET /search?q=<term>` returns a ~128-item "highlights" fallback set for any term it doesn't recognize, including nonsense strings. Confirmed 2026-07-18:

```
q=suzani           → total=128, first ID 551786
q=nonsensewordxyz  → total=128, first ID 551786   ← IDENTICAL SET
q=Uzbekistan       → total=135, first ID 329073   ← 7 real extra IDs
```

**Workaround:** query by broad terms the Met understands (country name, culture umbrella, department), NOT niche tradition names. Then subtract the fallback set at read time. See `met.py::_get_fallback_ids`.

**Broad terms that work:** `Uzbekistan`, `Central Asia`, `Timurid`, `Bukhara`, `Samarkand`, `Islamic`, `Ottoman`, `Iznik`. Anything niche/vernacular (`suzani`, `adras`, `chapan`) drops into the fallback set.

**Country-gate filter:** after search, each returned object's `culture` / `country` / `region` fields must contain a per-country whitelist term (see `scrape_region.py::scrape_met_for_country`) — otherwise the "Central Asia" query drags in Persian and Tibetan objects.

**Additional images:** Met records have an `additionalImages[]` array with alt views. All are downloaded.

## Rijksmuseum

New API (data.rijksmuseum.nl) is **keyless**. Returns Linked Art JSON.

**Search:** `GET /search/collection?description=<term>&imageAvailable=true` → array of LOD IDs (`https://id.rijksmuseum.nl/<n>`).

**Resolve:** `GET https://id.rijksmuseum.nl/<n>` with `Accept: application/ld+json` → full Linked Art record. Fields are nested (`identified_by[type=Name].content` for title, `produced_by.part[].took_place_at[]._label` for place).

**Coverage:** zero Central Asian holdings. Dutch colonial reach was to Indonesia — this is a game-changer for a SE Asia region (Sumatra 1861 records, Java 1645, batik 40, sarong 17, ikat 10, Perzië 164).

## What not to trust

- **Museums' own country / culture attributions.** Widely inconsistent. V&A uses "Central Asia" for anything Silk Road; Met uses "Iran or Central Asia" for a lot; both mislabel Uzbek as "Turkestan" in pre-1920 records. Use these as *hints* to route into a `_regional` bucket, not as ground truth.
- **Museum date fields.** "mid 19th century" gets parsed to (1825, 1875) but some entries say "1800s" (whole century), "early Timurid" (400-year range), or just `null`. Always check `date_earliest` and `date_latest` before using them as filter bounds.
