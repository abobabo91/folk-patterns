# Museum sources — quirks and workarounds

Everything measured against live endpoints on 2026-07-18 while wiring this project.
This is the authoritative copy — there is no cross-project duplicate.

## Currently wired

| Museum | Base URL | Key? | Depth | Strength |
|---|---|---|---|---|
| **Victoria & Albert** | `api.vam.ac.uk/v2` | no | search + `/museumobject/{id}` deep-fetch (60+ fields) | Largest ethnographic textile archive; strong Silk Road + South Asia |
| **Met Museum** | `collectionapi.metmuseum.org/public/collection/v1` | no | search + `/objects/{id}` full (55+ fields) | CC0 images, Islamic Art department is Central Asian gold |
| **Rijksmuseum** | `data.rijksmuseum.nl/search/collection` | no (new API is keyless) | search + Linked Art `/id.rijksmuseum.nl/<id>` resolve | Dutch colonial reach → Indonesia (Sumatra 1861, Java 1645, batik 40), Persian holdings (164). **Zero Central Asian.** |

## Wired but not yet used at scale

| Museum | Base URL | Key | Reach for it when |
|---|---|---|---|
| Cooper Hewitt | `collection.cooperhewitt.org/api` | yes | pure design / pattern focus, proper full-text search |
| Smithsonian OA | `api.si.edu/openaccess/api/v1.0` | yes (api.data.gov) | Indigenous Americas or African American folk art — filter NMAI / NMAAHC via `unit_code` |
| Europeana | `api.europeana.eu/record/v2` | yes | breadth over EU-adjacent cultures (Balkans, Baltic, Sami); aggregates 4000+ institutions incl. Tropenmuseum |

All keyed, all free, ~2 min signup each. When scaling beyond the current region.

## Picking a source for a query

- **Know the country / culture, want everything they hold** → Met + V&A country queries + client-side technique filter.
- **Know a specific niche craft name** (suzani, adras, sabuku, adire) → V&A + Rijksmuseum + Cooper Hewitt full-text. Skip the Met at this layer — niche vocabulary lands in its fallback bucket.
- **EU-adjacent breadth** → Europeana.
- **Indigenous Americas / African American folk art** → Smithsonian OA.
- **SE Asia / Indonesian batik** → Rijksmuseum first (Dutch colonial holdings are enormous), Tropenmuseum via Europeana second.

## Rate limits

None of these APIs publish a hard rate limit on their open-access endpoints. 0.5 s between requests
is polite and has never drawn a 429 on any of them. For a big backfill use 1–2 s — a few hours instead
of one, and it stays under the radar.

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

**`/objects/<id>` can return non-JSON.** Specific IDs occasionally answer with an HTML error page.
Wrap the `.json()` call in try/except and skip the ID — one bad object must not kill a whole scrape.

**Sanity-check against the V&A, not the Met.** V&A returns 0 for a nonsense term and correct counts
for real ones, so it behaves like a real search index. When testing a new museum-API pattern, verify
your expectations there first, then adapt for the Met's fallback behaviour.

## Rijksmuseum

New API (data.rijksmuseum.nl) is **keyless**. Returns Linked Art JSON.

**Search:** `GET /search/collection?description=<term>&imageAvailable=true` → array of LOD IDs (`https://id.rijksmuseum.nl/<n>`).

**Resolve:** `GET https://id.rijksmuseum.nl/<n>` with `Accept: application/ld+json` → full Linked Art record. Fields are nested (`identified_by[type=Name].content` for title, `produced_by.part[].took_place_at[]._label` for place).

**Coverage:** zero Central Asian holdings. Dutch colonial reach was to Indonesia — this is a game-changer for a SE Asia region (Sumatra 1861 records, Java 1645, batik 40, sarong 17, ikat 10, Perzië 164).

## What not to trust

- **Museums' own country / culture attributions.** Widely inconsistent. V&A uses "Central Asia" for anything Silk Road; Met uses "Iran or Central Asia" for a lot; both mislabel Uzbek as "Turkestan" in pre-1920 records. Use these as *hints* to route into a `_regional` bucket, not as ground truth.
- **Museum date fields.** "mid 19th century" gets parsed to (1825, 1875) but some entries say "1800s" (whole century), "early Timurid" (400-year range), or just `null`. Always check `date_earliest` and `date_latest` before using them as filter bounds.
