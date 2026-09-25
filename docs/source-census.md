# Source census — what each museum holds per culture

Counted 2026-09-25 against every source the scrapers use, for the 71 cultures on the map. The question it answers: how much *more* is out there for each culture, and where. "shown" is the culture's `object_count` in the current build (after vetting and dedup); every other number is objects **not yet in the library**.

## How each column was counted, and how far to trust it

| column | how | trust |
|---|---|---|
| BM | British Museum "Ethnic group" facet, image-bearing, minus BM records already held (`data/bm_ethnic_census.json`) | high — the facet is curated; a 300-record sample kept 296 |
| Cleveland | culture field matches the ethnonym, minus ancient, minus held (`data/cleveland_culture_census.json`) | high, but small; Cleveland mostly catalogues by place |
| Europeana | hits from ethnographic providers only (world-culture / ethnography / anthropology museums), minus held (`data/europeana_census.json`) | medium. **`?` = word collision**: Kongo (Swedish for Congo), San (Spanish place names). Lao is set to the 30 real ones at the Museum of World Culture |
| Met | public-domain objects whose `Culture` names the people, dated 1700 or later, from the open-access CSV (`data/met_census.json`) | high, but tiny for these cultures |
| Smithsonian | `"<name>"` search, image-bearing, natural-history units excluded, scaled by the share of the first 100 hits whose `indexedStructured.culture` names the people (`data/si_commons_census.json`) | **unusable** — almost all NMNH Anthropology, which releases no open-access images (0 of 671 harvested rows had one; [museums.md](museums.md#smithsonian)). The column counts objects, not usable pictures. **`?` = San**: the substring test also hits "San Ildefonso" |
| **named, new** | sum of the columns above | the pool a culture-named scrape can reach |
| V&A by place | V&A objects with images whose place is the culture's country (`work/` scratch census) | **not per culture** — a whole country, ancient included (Egypt 7,517). V&A ethnonym search is not in the sum: "Chin" returns Chin-Chin prints, "Persian" returns wallpaper pattern names, "Turkish" returns European pictures of Turks; only "Malay" (607) was real |
| Commons files | files directly in the culture's curated architecture categories (`commons_arch.ARCH_CATEGORIES`), subcategories not counted | a floor. Photos of buildings, not objects |

Total named, new: **~32,000 usable** against 4,383 shown — BM 24,083, Europeana 5,872, Cleveland 953, Met 487. The Smithsonian's 10,024 are counted in the table but have no open-access images.

## Table

| culture | shown | BM | Cleveland | Europeana | Met | Smithsonian | **named, new** | V&A by place | Commons files |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| Afghan Turkmen | 11 | 398 | 2 |  |  |  | **400** | 146 |  |
| Bukharan Jew | 10 |  | 11 |  |  |  | **11** | 106 | 1 |
| Hazara | 12 |  |  | 7 |  |  | **7** | 146 |  |
| Karakalpak | 9 |  |  |  |  |  | **0** | 106 | 213 |
| Kazakh | 22 | 31 |  | 60 |  |  | **91** | 1 | 182 |
| Kazakh (Xinjiang) | 27 | 28 |  | 57 |  |  | **85** |  | 61 |
| Kyrgyz | 32 | 49 |  | 199 |  | 3 | **251** |  | 177 |
| Pamiri | 5 |  |  |  |  |  | **0** | 1 |  |
| Tajik | 45 |  |  | 1 | 1 |  | **2** | 1 | 169 |
| Turkmen | 89 | 338 |  | 29 | 3 | 62 | **432** | 14 |  |
| Uyghur | 57 | 10 |  | 12 |  |  | **22** |  | 706 |
| Uzbek | 287 | 41 |  |  | 1 |  | **42** | 106 | 1,271 |
| Uzbek (Afghanistan) | 5 | 127 |  | 4 | 1 |  | **132** | 146 |  |
| Azeri (Iranian) | 26 |  | 15 |  |  |  | **15** | 4,679 | 430 |
| Baluchi | 16 | 167 |  |  |  | 15 | **182** | 4,679 | 342 |
| Berber | 32 | 909 |  | 39 | 1 | 120 | **1,069** | 198 | 604 |
| Egyptian | 171 |  | 272 | 110 | 16 | 2,605 | **3,003** | 7,517 | 1,032 |
| Kurdish (Iranian) | 20 | 1 |  | 20 |  |  | **21** | 4,679 | 120 |
| Kurdish (Turkish) | 21 | 19 |  | 26 |  |  | **45** | 2,145 | 157 |
| Moroccan Arab | 93 | 327 | 30 |  |  |  | **357** | 198 | 1,435 |
| Nubian | 38 |  |  |  |  | 34 | **34** | 7,517 | 54 |
| Persian | 257 |  | 520 | 300 | 90 | 86 | **996** | 4,679 | 2,994 |
| Qashqai | 0 | 4 |  | 3 |  |  | **7** | 4,679 |  |
| Tunisian Arab | 57 | 329 | 5 |  |  |  | **334** | 274 | 381 |
| Turkish | 117 | 27 | 46 | 129 | 129 | 8 | **339** | 2,145 | 1,137 |
| Balinese | 110 |  | 1 | 98 | 13 | 37 | **149** | 111 | 858 |
| Bamar | 195 |  |  |  |  |  | **0** | 151 | 435 |
| Batak | 73 | 258 |  | 162 | 1 | 80 | **501** | 111 | 45 |
| Cham | 10 |  |  | 12 |  | 2 | **14** | 425 | 105 |
| Chin | 66 | 395 |  | 221 |  | 9 | **625** | 151 | 41 |
| Dayak | 43 | 2,493 |  | 53 | 1 | 1,634 | **4,181** | 111 | 8 |
| Filipino | 108 | 141 |  |  | 1 | 941 | **1,083** | 56 | 213 |
| Hmong | 72 | 534 |  | 37 |  |  | **571** | 425 | 5 |
| Iban | 80 | 433 |  | 96 | 4 | 1 | **534** | 270 | 13 |
| Javanese | 202 |  |  | 362 | 74 | 147 | **583** | 111 | 261 |
| Khmer | 143 |  |  |  |  | 16 | **16** | 121 | 2,176 |
| Kinh | 77 |  |  | 47 |  |  | **47** | 425 | 826 |
| Lao | 38 | 36 | 1 | 30 |  | 167 | **234** | 88 | 185 |
| Lao Isan | 28 | 44 |  |  |  |  | **44** | 452 | 374 |
| Malay | 50 | 219 |  | 262 |  | 1,664 | **2,752** | 270 | 105 |
| Minangkabau | 24 | 12 |  | 10 | 3 | 3 | **28** | 111 |  |
| Sundanese | 44 |  |  | 5 |  | 99 | **104** | 111 | 292 |
| T'boli | 12 | 2 |  | 3 |  |  | **5** | 56 | 61 |
| Thai | 115 | 36 |  | 96 | 53 | 417 | **602** | 452 | 3,164 |
| Toraja | 59 | 95 |  | 464 | 3 | 2 | **564** | 111 | 22 |
| Yakan | 8 |  |  |  | 1 | 1 | **2** | 56 |  |
| Afar | 10 | 2 |  | 5 |  | 1 | **8** | 2 | 68 |
| Amhara | 68 |  | 1 | 99 | 7 | 16 | **123** | 98 | 75 |
| Ashanti | 77 | 5,092 | 15 | 140 | 3 | 165 | **5,415** | 52 | 72 |
| Chokwe | 49 | 215 |  | 129 | 3 | 14 | **361** | 11 |  |
| Fang | 103 | 41 | 1 | 1,036 | 11 | 198 | **1,287** |  |  |
| Fulani | 29 | 487 |  | 25 | 1 | 8 | **521** | 3 | 143 |
| Himba | 37 | 43 |  |  |  | 159 | **202** | 10 | 121 |
| Igbo | 68 | 1,264 |  | 106 | 3 | 12 | **1,385** | 152 |  |
| Kikuyu | 67 | 279 |  | 37 |  | 30 | **346** | 54 | 82 |
| Kongo | 54 |  | 4 | ? | 23 | 53 | **80** | 11 |  |
| Kuba | 63 | 1,609 | 1 | 608 | 5 | 397 | **2,620** | 11 |  |
| Maasai | 74 | 440 |  | 133 | 1 | 99 | **673** | 54 |  |
| Mbuti | 42 | 216 |  |  |  | 13 | **229** | 978 | 6 |
| Ndebele | 75 | 89 |  | 2 |  | 20 | **111** | 2,483 | 16 |
| Oromo | 8 | 60 |  | 17 |  | 57 | **134** | 98 | 205 |
| San | 58 | 184 | 2 | ? | 2 | ? | **188** | 19 |  |
| Sidama | 4 |  |  |  |  |  | **0** | 98 | 8 |
| Somali | 39 | 494 | 3 | 99 |  | 60 | **656** | 4 | 193 |
| Sotho | 71 | 528 |  |  |  | 14 | **542** | 2,483 | 22 |
| Swahili | 89 | 373 | 2 | 30 |  | 9 | **414** | 8 | 2 |
| Tigray | 21 |  |  | 9 | 1 |  | **10** | 98 | 98 |
| Wolof | 51 |  |  | 14 |  | 15 | **29** | 3 |  |
| Xhosa | 28 | 793 |  |  | 2 | 19 | **814** | 2,483 | 217 |
| Yoruba | 99 | 2,497 | 17 | 122 | 28 | 352 | **3,016** | 152 | 237 |
| Zulu | 83 | 1,874 | 4 | 307 | 1 | 160 | **2,346** | 2,483 | 8 |

## What the table says

- **Nearly all the growth is in one source.** BM (24k) is three quarters of the usable named pool, Europeana (5.9k) most of the rest. Cleveland and the Met add little per culture; the Smithsonian's culture-tagged objects have no open-access images.
- **Nothing named exists** for Bamar, Karakalpak, Pamiri and Sidama, and almost nothing for Tajik, Yakan, T'boli, Hazara, Qashqai, Afar, Tigray, Bukharan Jew, Cham, Azeri, Khmer. For these only place-based sources remain (V&A by place: Burma 151; Met Iran 639 from 1700 on, shared by every Iranian culture) and Commons architecture — both need the vetter to decide the people, since the record does not.
- **The largest pools are African and Bornean**: Ashanti 5.5k (BM counts all Akan), Dayak 4.2k, Yoruba 3.1k, Malay 2.8k, Kuba 2.7k, Zulu 2.4k, Chin 1.8k, Igbo 1.4k, Fang 1.4k, Berber 1.2k, Filipino 1.1k.

## The pool — metadata of everything, harvested

`scripts/harvest_pool.py <source>` writes one metadata-only row per source object to `data/pool/<source>.jsonl` (gitignored, ~52 MB): object name, title, date, place, the source's people tag, provider, thumbnail URL. No images, no vetting. It exists so a culture's gallery can be picked for variety before anything is downloaded. Harvested 2026-09-25:

| source | rows | what a row is |
|---|--:|---|
| British Museum | 22,695 | every image-bearing object under 60 "Ethnic group" facet names (list pages only, 100 per page; needs `BM_CDP_URL`) |
| Europeana | 23,239 | ethnographic providers only; 17,719 of them are the Swedish museums' "Kongo" (= Congo) set |
| V&A | 19,908 | every image-bearing object under 37 place ids; no people named |
| Met | 38,473 | public domain, made 1700 or later, six non-European departments (from the CSV) |
| Cleveland | 9,778 | the department dump |
| Smithsonian | 1,456 | stopped after 9 cultures: NMNH Anthropology rows carry no image |

Which atlas culture a row belongs to is not decided here: shared names (Kazakh, Kurd, Uzbek, Lao, Turkmen), broad names (Miao for Hmong, Herero for Himba) and place-only rows (V&A) need the place, and that is the next step.

What one culture's pool looks like — BM Yoruba, 2,569 objects in 215 kinds: textile / cloth / adire ≈ 690, figure + ibeji ≈ 490, mask ≈ 175, then a long tail (82 kinds occur once: doors, house-posts, mancala boards, bullroarers). A random 72 held before were 54 adire.
