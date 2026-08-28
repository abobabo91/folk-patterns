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

## Europeana

**`country` / `made_in_place` is the HOLDING institution, not the origin.** This is the single most dangerous field in the whole pipeline. A Sarawak Iban `pua kumbu` carries `country: ["Sweden"]` because Gothenburg's Museum of World Culture owns it; a Thai Isan silk shawl reads `Sweden` for the same reason. The object's real origin is buried in `dcDescription`, usually in the holding museum's own language — the Iban cloth's description reads *"Ovanligt lång pua kumbu med krokodilmotiv 'baya' … Varpikat, växtfärgad"* followed by `Sarawak, Östmalaysia | Malaysia | Sea Dayaks | Iban`.

Never let any filter treat that field as evidence of origin. Measured 2026-08-27: feeding it to the vision vetter as "made in" with instructions to reject on geographic contradiction sent Europeana's reject rate to **86%**, discarding canonical material (Iban pua kumbu, Batak ulos, Minangkabau songket, Kuba cloth). Labelling the same field as the holding museum and telling the judge a European holding country is never grounds for rejection brought it to **14%** on the identical sample. Ethnographic collections of the whole world sit in European museums; that is the normal case, not an anomaly.

Corollary for `dcDescription`: read several entries, not just the first. Europeana splits origin across list items (`"Sarazm"`, `"Malaysia"`, `"Iban"`), so `desc[0]` alone usually loses the provenance.

## Ethnonym word-match collisions found in the library

Every one of these was sitting in the atlas as a real record. They pass the junk regexes because nothing about the title looks like junk — only looking at the image plus the museum's own description catches them. Verified 2026-08-28 by the vision vetter across 100 sampled records.

| filed under | actually is |
|---|---|
| San | Venetian engravings — `San` is Italian for *Saint* (San Salvatore, Visentini album); also "after a Female Figure by Tintoretto" |
| Iban | Spanish documents — `iban` is Spanish for *"they were going"* (an 1821 Galician political pamphlet) |
| Fang | Chinese porcelain — `fang ding` is a Chinese vessel type (Jingdezhen fahua censer) |
| Chin | a Santee **Dakota** studio portrait from St Paul, USA |
| Maasai | Japanese ukiyo-e kabuki prints (two separate records) |
| Khmer | a 1778 Spanish royal decree on Vizcaya taxation |
| Thai | a European etching after Parmigianino |
| Persian | Rubens' costume book; a Baroque chalk drawing of "the Persian Sibyl" |
| Cham | the French cartoonist Cham |

Neighbouring-group misfiles are a *different* class and just as common — the museum's own record names the right people and we filed it wrong: `"Shan weft-ikat cloth"` under Bamar, `"Sierra Leone Kusaibi type"` under Wolof, `"Afghan war kilim"` under both Kurdish (Iranian) and Azeri (Iranian), a Khmer temple site under Lao Isan, a Herero hut engraving under Himba. Rule that works: if the museum's description **names** a different people, it is mis-filed; if the group is merely unverifiable, keep it.

## What not to trust

- **Museums' own country / culture attributions.** Widely inconsistent. V&A uses "Central Asia" for anything Silk Road; Met uses "Iran or Central Asia" for a lot; both mislabel Uzbek as "Turkestan" in pre-1920 records. Use these as *hints* to route into a `_regional` bucket, not as ground truth.
- **British Museum's `title` is its classification**, not a description — 486 of 1,322 BM records (37%) are titled `print` / `drawing` / `album` / `photographic print` / `book-illustration`. That department is mostly out of scope (Ephesus ruin watercolours, named-sultan portraits, European book plates) but ~162 of them are genuine costume documentation (`"Folio 21 from an album showing Turkish costume … çengi dancer"`). Do NOT drop the department wholesale — it needs per-record judgement.
- **V&A `title` is null on 94% of records** while `classification` carries the real name ("Kurta", "Ikat length"). Read classification as the fallback title.
- **Museum date fields.** "mid 19th century" gets parsed to (1825, 1875) but some entries say "1800s" (whole century), "early Timurid" (400-year range), or just `null`. Always check `date_earliest` and `date_latest` before using them as filter bounds.
