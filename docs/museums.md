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

## Source quality — measured on the full re-vet (2026-09-24)

Every one of the 4,625 library records judged by the current vetter ([vetting.md](vetting.md)). `weak+unusable`, `modern`, `archaeological` are shares of the kept records; `category changed` is how often the vetter's `art_form` differs from the scrape-time classifier's.

| source | records | kept | dropped | weak + unusable | modern | archaeological | category changed |
|---|---|---|---|---|---|---|---|
| British Museum | 1,322 | 873 | **34%** | 2% | 10% | 5% | 27% |
| Wikimedia Commons (arch) | 1,295 | 1,162 | 10% | 5% | 7% | 15% | 22% |
| Europeana | 866 | 642 | **26%** | 4% | 8% | 1% | **57%** |
| Cleveland | 559 | 543 | 3% | 0% | 0% | 34% | 27% |
| V&A | 466 | 429 | 8% | 0% | 0% | 3% | 33% |
| Met | 69 | 66 | 4% | 0% | 0% | 27% | 12% |
| Smithsonian | 33 | 32 | 3% | 0% | 3% | 0% | 6% |
| Rijksmuseum | 15 | 8 | 47% | 0% | 38% | 12% | 100% |

Why each source's drops happen (drop reasons bucketed by keyword, then read):

- **British Museum — ethnonym search against a print room.** 170 of 449 drops are European or outsider art (engravings, drawings, Rubens, fashion sketches), 135 name another people or a distant style, 82 are scans or cards. Almost all of it comes from short ethnonyms that are also words: San 93% dropped (55 of its 64 drops from BM), Chin 86%, Cham 80%, Maasai 74%, Fang 50%. **Fixed in the scraper: it now searches BM's own "Ethnic group" facet** — see [British Museum](#british-museum) below.
- **Europeana — documents, not objects.** 149 of 224 drops are scans, catalogue cards, newspaper pages, book covers or placeholder icons, and it has the worst category accuracy (57% re-categorised). The scans arrive as `type: IMAGE` — the scraper already rejects every other type, and `dcType` is empty on all 866 records — so type cannot filter them. The provider can: Virtual Library of Historical Press gave 0 kept of 32, Palais Galliera (Paris couture sketches) 0 of 9, Bodleian 0 of 8, Digital Memory of Catalonia 0 of 5, Galiciana 0 of 4, Uppsala University 0 of 4, while Museum of World Culture kept 249 of 263 and Náprstek 40 of 40. Those providers are now in `NON_CULTURAL_PROVIDER_TOKENS` (`europeana.py`). Word collisions too: Estonian `kalaga` ("with fish") under Bamar. Treat its category as unknown.
- **Wikimedia Commons (arch)** — low drop rate, but its drops are its own kind: signboards, nature close-ups at a site, souvenirs, a cat in a kasbah, and 42 of its images failed on 429 until the User-Agent carried contact info (see [cloud-vetting.md](cloud-vetting.md)). Most archaeological material comes from here and Cleveland.
- **Cleveland, V&A, Met, Smithsonian — curated, and the vetter agrees**: 3–8% dropped, and those drops are almost all *misfiles*, not junk — Shan cloths under Bamar, Javanese puppets and batik under Balinese, Cham temple sculpture under Kinh, Khmer ware under Thai. The object is good; the ethnicity is wrong. These are the best sources to expand from.
- **Rijksmuseum** — too few records to judge (15); its drops were colonial-exhibition posters and Dutch album covers.

## British Museum

**Search by the "Ethnic group" facet, not by keyword.** `collection/search?ethnic_name=San` returns BM's own ethnic attribution; `keyword=san` searches the whole collection, print room included. Counts on 2026-09-24:

| culture | `ethnic_name=` | `keyword=` |
|---|---:|---:|
| San | 383 | 15,290 |
| Chin | 539 | 7,043 |
| Thai | 53 | 1,531 |
| Maasai | 602 | 1,140 (`Masai` facet: 0) |
| Fang | 167 | 880 |
| Cham | 1 | 373 |
| Yoruba | 2,747 | 3,066 |
| Zulu | 2,049 | 2,464 |

Pages are **0-based** (`page=0` is the first; a run that started at `page=1` silently lost the first 100 of every culture), and `image=true` is the "Image only" toggle — objects without a photo cost a detail fetch and give nothing (San: 304 of 383 have one). The total is not in the HTML; count it from the pager's last-page link.

`british_museum.scrape_ethnicity` walks the facet (up to 40 pages of ~100, image-only) and uses it when it yields 20+ objects, skipping the ethnonym attribution filter since BM already attributed them; otherwise it falls back to keyword search. It skips objects already in the library (`append_metadata` replaces a same-id record, which would wipe its verdict), and shuffles the facet list with a fixed seed before the `--max-total` cap, because BM sorts by object name and a capped run would otherwise take only "adze" and "amulet". A seed entry's `bm_ethnic_name` overrides the facet name ("Asante" for Ashanti).

**Census, 2026-09-25** (`data/bm_ethnic_census.json`: image-bearing objects per culture under BM's best-matching name, against what the library holds): ~19,800 photographed objects we do not have. Largest: Yoruba 2,569, Dayak 2,501, Asante 2,404 (Akan 5,152 is broader — Fante and others), Zulu 1,934, Kuba 1,627, Igbo 1,330, Berber 914, Xhosa 794, Miao 594, Sotho 594, Maasai 540, Chin 515, Somali 506, Iban 497, Fulbe 494, Swahili 433, Turkmen 399. Nothing or next to nothing under any spelling tried: Azeri, Bukharan Jew, Karakalpak, Kinh, Kongo, Pamiri, Sidama, Sundanese, Tigray, Yakan, Cham (1), Hazara (2), Javanese (2), T'boli (2), Balinese (3 with a photo), Khmer (4), Qashqai (4). Mappings deliberately not used: Twa (216) is not Mbuti; Arab (329) spans every Arab country and would need a place filter for Moroccan / Tunisian Arab. Shared names (Kazakh, Kurd, Uzbek, Lao, Turkmen) serve two atlas cultures each and need a place split.

**Sample, 2026-09-25:** 100 new facet records each for Xhosa, Kuba and Dayak (shuffled): **296 of 300 kept**; the 4 drops were right (San figures filed as Xhosa, an Asian basket, a ledger page, a raw cotton sample). Content: Xhosa beadwork, snuff containers and pipes; Kuba boxes, cups, drums, figures, textiles; Dayak baskets, capes, ikat and c. 1900 glass-negative field photographs, which BM displays as positives. Kept although dull: Kuba iron currency rods and pigment samples. The facet has nothing under our names for Qashqai, Sidama, Pamiri, Oromo, Karakalpak, Yakan, Afar, Cham, Hazara, T'boli, Baluch(i), Kurd(ish), Tigray, Lao Isan — those still use keywords.

**Measured result**, 721 facet-scraped records over 13 cultures judged by the vetter on 2026-09-24: 702 kept, 19 dropped (2.6%) — San 59/1 where keyword-era San lost 93%, Chin 59/1 where it lost 86%, Maasai 58/2 where it lost 74%. Per-culture table: [vetting.md](vetting.md#where-this-stands).

**Cloudflare.** Since 2026-09-24 every curl_cffi TLS impersonation (chrome124, chrome131, safari, firefox) gets 403 on search and detail pages. A real Chrome passes, and its `cf_clearance` cookie, sent with that Chrome's exact User-Agent, lets curl_cffi through (200). Set `BM_CDP_URL=http://127.0.0.1:<port>` to a Chrome with remote debugging and `_client()` opens one search page there and copies the cookie. Without it the scraper warns and gets nothing.

**Image host.** `media.britishmuseum.org` serves its leaf certificate without the intermediate; `scripts/certs/extra-intermediates.pem` carries it for the cloud vetter.

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

**Search by place, not by people name.** V&A catalogues by place of origin, so an ethnonym `q` matches titles and pattern names instead. Measured 2026-09-25: `Chin` → 1,144, mostly Chin-Chin prints; `Persian` → 4,521, mostly pattern names ("Persian Sprig") and archive records; `Turkish` → 2,588, mostly European pictures of Turks. Only `Malay` (624, 80% placed in the Malay Peninsula / Malaysia) was real. Per-country place counts are in [source-census.md](source-census.md).

## Met Museum

**For counting, use the open-access CSV, not the API.** `https://media.githubusercontent.com/media/metmuseum/openaccess/master/MetObjects.csv` (318 MB, 484,956 objects, refreshed daily) has `Culture`, `Country`, `Region`, `Department`, `Object End Date` and `Is Public Domain` for every object. The API cannot do this: fetching `/objects/{id}` with 8–12 parallel workers gets HTTP 403 after about 70 requests (measured 2026-09-25), so a department-wide pull of ~70k objects is not feasible. Keep the CSV in `.cache/` (gitignored).

**What the CSV shows for our cultures** (public domain, dated 1700 or later — see [source-census.md](source-census.md)): Persian 90 by culture and 639 with country Iran, Turkish 129 and 376, Javanese 74, Thai 53, Yoruba 28, Kongo 23. Everything else is under 15. The Islamic Art department leaves `Culture` empty and records origin in `Country`; Asian Art writes place into `Culture` ("Indonesia (Central Java)"). Only 6,370 of the Africa/Oceania/Americas department's 12,254 objects are public domain, and most of those are pre-Columbian.

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

## Smithsonian

**NMNH Anthropology has no open-access images.** `"<name>" AND online_media_type:"Images"` with the natural-history units excluded returns mostly `unitCode NMNHANTHRO`, culture-tagged as a Library of Congress heading (`Malays (Asian people)`, `Dayak (Indonesian people)`) — but `online_media` is null on every one of them: 0 of 671 harvested 2026-09-25 carried a media block. The `Images` flag only means a picture exists on collections.si.edu; it is not released. So the ~10,000 culture-tagged Anthropology objects cannot be used. The images that do come through are Cooper Hewitt (`CHNDM`, all 33 we hold), NMAAHC, SAAM and a few NMAfA — a small share of the hits. The fielded query `culture:"Yoruba"` returns 0 although the field says Yoruba; filter on the field client-side.

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
