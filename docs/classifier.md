# Art form + pattern density classifier

Deterministic, rule-based, no AI. Runs at scrape time (each downloaded object) and again by `scripts/build_index.py`. Source: `src/folk_patterns/classify.py`.

## Art form buckets

Order matters — checks are tried top to bottom, first match wins.

| Bucket           | Positive terms (partial list)                          | Notes |
|------------------|---------------------------------------------------------|-------|
| `architectural`  | tile, mosaic, muqarnas, wall panel, revetment           | Architectural fixtures — checked first because "tile" appears in ceramic wording too |
| `wallpaper`      | wallpaper, wall covering                                | Flat printed surface design |
| `garment`        | robe, kurta, chapan, coat, dress, doppa, kalpak, elechek| Garment types beat generic "silk / cotton" |
| `textile`        | suzani, ikat, carpet, rug, textile, embroider, panel, cover, hanging, chuval, bag face, torba, shyrdak, felt | Flat textiles + tribal weaving vocab |
| `jewelry`        | jewelry, ornament, ring, pendant, pectoral, tumar       | |
| `metalwork`      | sword, weapon, axe, dagger, knife, hilt                 | Weapons + utilitarian metal |
| `painting-mss`   | codex, manuscript, folio, painting, drawing, print      | 2D representational works |
| `ceramic`        | ceramic, stonepaste, bowl, plate, jar (unless tile)     | Vessel ceramics, not architectural |
| `sculpture`      | sculpture, figurine, statue, plaque, relief             | 3D representational works |
| `unclassified`   | –                                                        | Fallback |

## Pattern density (0–3)

Roughly: how much of the image is legible surface pattern?

| Score | Meaning | Applied when |
|-------|---------|-------------|
| **3** | Pure surface pattern — the whole visible surface IS the pattern | Textile / wallpaper / architectural mosaics + muqarnas + tile |
| **2** | Strongly patterned object | Garment (pattern is dominant but on a 3D form); painted/decorated ceramic; architectural non-mosaic |
| **1** | Some ornament | Jewelry; metalwork if "engraved / chased / inlaid / gilded"; illuminated / decorated manuscripts |
| **0** | Figurative or plain | Manuscript with figures; sculpture; plain vessels; portrait paintings; landscape drawings |

## Failure modes to know

- **Ambiguous tradition names.** "Atlas" matches the Greek Titan on V&A and a silk fabric on Uzbek entries — the classifier can't tell them apart from the object_type alone. Filtered downstream at the place-routing layer.
- **Sketchbook drawings of patterns** classify as `painting-mss` even when the content is architectural. A 19th c. sketchbook study of muqarnas from Cairo will land in painting-mss, which is technically correct as a *medium*. If you want it in architectural, sort by `object_type == 'Sketchbook' && title contains 'muqarnas'` explicitly.
- **Ceramic-vs-architectural collision.** "Tile" wins over "ceramic" because architectural is checked first. That's on purpose — an architectural tile fragment is more useful as a pattern surface than as pottery.
- **Bag face / chuval / torba** — Turkmen tribal weaving vocabulary that reads as textile but was originally missing from the classifier. Added 2026-07-18 after seeing 44 items land in `unclassified`. If you see a wave of unclassified for a new region, dump the top object_types and add the vocab.

## When to extend

The classifier is a live document. To add vocabulary:

1. Add the term to the relevant `_TERMS` list in `classify.py`.
2. Re-run `scripts/build_index.py` — it re-reads the metadata and re-buckets.
3. If moving between art_form buckets, that's a re-organization of the on-disk `library/` structure — either re-scrape or move files by script.
