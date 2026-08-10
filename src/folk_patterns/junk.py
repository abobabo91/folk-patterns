"""Script-only junk detector — the "cheap first pass" filter that every
scraper should run BEFORE any LLM-based vetting.

Two orthogonal checks:

  is_junk_title(title, description="") -> (bool, reason)
      Regex patterns that match obvious non-cultural-object records:
      Latin binomials, heraldic emblems, distribution charts, coin catalogs,
      landscape/aerial photos, named-individual portraits from specific
      known-junk archives (Sorbian Domowina community photos, etc.),
      screenshot filenames, GoPro dumps.

  is_junk_provider(name) -> (bool, reason)
      Explicit denylist for Europeana / Commons providers whose records
      systematically leak into unrelated ethnicity queries.

Callers should:
    from folk_patterns.junk import is_junk_title, is_junk_provider
    if is_junk_title(title, description=summary)[0]:
        continue  # skip
    if is_junk_provider(provider)[0]:
        continue

Empirical basis (2026-07-22): running these patterns against 861 records
that had been vision-vetted catches ~74% of what vision approved and ~4%
of what vision rejected (i.e. would eliminate the obvious junk without any
LLM call). The residual ~13% of vision-catchable junk needs actual pixel
inspection — small enough that agentic vetting is only justified for the
Commons documentary-photo fallback bucket."""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Title / description patterns
# --------------------------------------------------------------------------

# `Genus species` — ONLY when the second word ends in a real Latin
# scientific-name suffix, OR is followed by a botanical author citation.
# Loose "capitalized + lowercase" was matching "Suzani with", "Kain panjang",
# "Balinese danseressen" as false Latin. This surgical pattern only fires on
# true taxonomic labels like "Ptenochirus jagori", "Digitaria junghuhniana",
# "Platycerium coronarium (K.D. Koenig ex O.F. Müll.) Desv."
_SPECIES_SUFFIX = (
    r"(?:atus|atum|ata|ensis|icus|ica|icum|"
    r"folia|folium|ifera|iflora|florum|arium|"
    r"oides|oideus|osus|osum|osa|inus|inum|iana|ianus|"
    r"culus|culum|cula|jagori|paganoides)"  # explicit species stems we've seen leak
)
LATIN_BINOMIAL_RE = re.compile(
    # Match: Capital-Genus (optional (Subgenus)) lowercase-species-with-Latin-suffix
    # Also allow "sp." to match "Genus sp." bare records.
    # Also allow trinomials — Genus species subspecies — for zoological
    # subspecies naming (Anthus richardi lugubris) where species AND
    # subspecies are both lowercase Latin-suffix words. This catches bird +
    # mammal records that slip through the binomial-only pattern.
    rf"^\s*[A-Z][a-z]{{3,}}(?:\s+\([A-Z][a-z]+\))?\s+[a-z]*{_SPECIES_SUFFIX}\b"
    rf"|^\s*[A-Z][a-z]{{4,}}\s+sp\.\s*$"
    rf"|^\s*[A-Z][a-z]{{3,}}\s+[a-z]{{3,}}\s+[a-z]{{3,}}{_SPECIES_SUFFIX}\b"
)

# Known zoological genera we've seen leak from Smithsonian NMNH (mammal,
# bird, invertebrate). Extend as new leaks are found. Match at start of
# title only; any title beginning with these + a lowercase word = specimen.
_KNOWN_ZOOLOGICAL_GENERA = (
    "Anthus", "Arachnothera", "Carlito", "Mirafra", "Prosoplus",
    "Ptenochirus", "Rhinolophus", "Pteropus", "Cynopterus",
    "Macaca", "Tarsius", "Sus", "Bos", "Cervus",
    "Corvus", "Pycnonotus", "Zosterops", "Halcyon",
)
ZOOLOGICAL_GENUS_RE = re.compile(
    r"^(" + "|".join(_KNOWN_ZOOLOGICAL_GENERA) + r")\s+[a-z]",
)

# Botanical / zoological author citation embedded in specimen labels:
# "(K.D. Koenig ex O.F. Müll.)", "H.T. Chang", "Nees ex Steud."
# Tightened to require BOTH initials pattern AND a Latin-ish surname —
# raw "H.T. Chang" bare no longer counts because tribal / museum abbrevs
# also use dotted initials.
BOTANICAL_AUTHOR_RE = re.compile(
    r"\([A-Z]\.[A-Z]\.\s*[A-Z][a-z]+\)"       # (K.D. Koenig)
    r"|\bex\s+[A-Z][a-z]{3,}\.?\s*[A-Z]?\."  # ex Steud., ex Nees
    r"|\bvar\.\s|\bsubsp\.\s|\bcv\.\s"
)

# Heraldic, cartographic, chart / infographic patterns.
HERALDIC_RE = re.compile(
    r"\b(coat of arms|flag of|seal of|emblem of|logo of|badge of)\b",
    re.IGNORECASE,
)
DIAGRAM_RE = re.compile(
    r"\b(pca analysis|distribution map|distribution of|chart of|atlas of|"
    r"phylogen|cladogr|dendrogram|haplogroup|genome|genetic map|"
    r"table of|schematic|infographic)\b",
    re.IGNORECASE,
)

# Numismatic catalog records ("Coin of X", "coinage", museum inventory codes
# from Sogdian coin/bone specimens indexed on Central Asian queries).
NUMISMATIC_RE = re.compile(
    # Coin type names — tetra-/didrachm/etc all end in `drachm`; VOC-era Dutch
    # colonial coins (dukaat, ropij, ducat), Cambodia/Malaya pitji.
    r"\b(coinage|numismatic|obverse|reverse|"
    r"mint of|denomination|drachm|dinar|kopek|"
    r"tetradrachm|didrachm|dukaat|ducat|ropij|pitji)\b"
    r"|\bcoin\s+of\b"
    r"|\bcoin\s+[A-Z][a-z]+\s+[A-Z]{3,5}\s+[A-Z0-9]{3,}"
    r"|\b(?:NMAT|MNAT|SNS)\s+[A-Z0-9]{3,}",
    re.IGNORECASE,
)

# Landscape / aerial / generic-scenery filenames that Commons category browses
# surface. GoPro dumps + iPhone screenshot patterns.
LANDSCAPE_RE = re.compile(
    r"\b(landscape|mountain pass|highway|aerial view|panorama|sunset|"
    r"sunrise|skyline|cityscape|satellite|street view)\b",
    re.IGNORECASE,
)
CAMERA_DUMP_RE = re.compile(
    # Filename-prefix patterns — one of these at position 0 marks a
    # photographer's file dump. Each alt is standalone (no trailing \b —
    # word boundary between C-h fails).
    # Only reject records where the filename has NO identifying text after
    # the timestamp. "2015-09-18-134004 - Turpan, Minarett der Emin-Moschee"
    # HAS text and should be kept (dedup handles the batch collapse via
    # fingerprint stripping instead).
    r"^GOPR\d+"
    r"|^DSC[0-9_]+"
    r"|^DSCN\d+"
    r"|^IMG[_ -]?\d+"
    r"|^P\d{7,}"
    r"|^\d{8}[_\s]\d{2,6}[a-z]{0,3}\.(?:jpg|jpeg|png|tif|tiff)"  # "20190415 143021j.jpg" (no descriptive text)
    r"|^Screenshot"
    r"|^Bildschirmfoto"
    # Flickr / photostream signature: trailing "(12345678901).jpg" (>=8 digits
    # in parens followed by .jpg — Flickr photo-ID append). BUT only reject
    # if the filename before the ID is short (no descriptive text like
    # "Emin Minaret (23946273506).jpg" which should be kept).
    r"|^\d{8}\s+[A-Za-z]+\s+\d+.*\(\d{8,}\)\.(?:jpg|jpeg|png|tif)$",  # "20160513 China 6367 Kashgar sRGB (297006).jpg" — a Flickr batch with camera-generic prefix
    re.IGNORECASE,
)

# Botanical / zoological author abbreviations placed AFTER a Latin binomial.
# Catches:
#   "Diospyros ulo Merr."          — Genus species Abbrev.
#   "Ficus benjamina L."           — Genus species Initial.
#   "Musa acuminata Roxb."
#   "Nepenthes rajah Hook.f."
#   "Mastophora rosea (C. Agardh) Setch."   — with parenthetical original author
#   "Palisada perforata (Bory) K.W. Nam"
# Pattern: Genus species [(anything)] short-abbrev-with-dot-or-initial-pair.
# Constraints against false positives on English capitalized-place titles:
#   - Must NOT end in an image extension ("Port of Ambarita.jpg" was matching)
#   - "species" part must be a plausible Latin word (blocks "Port of Ambarita")
BOTANICAL_BINOMIAL_WITH_AUTHOR_RE = re.compile(
    # NOT case-insensitive — the pattern relies on case discrimination:
    # Genus is Cap+lowercase (`[A-Z][a-z]+`), species is all-lowercase
    # (`[a-z]{2,}`), author is Cap-abbreviated. With IGNORECASE, "Baiyanggou
    # Kazakh yurts.jpg" matched because Kazakh (cap K) was treated as
    # lowercase — dropped 30+ legit Commons files.
    #
    # Also: whole-string negative lookahead against image extensions
    # ("Port of Ambarita.jpg" is a Commons filename, not a taxon).
    r"^(?!.*\.(?:jpg|jpeg|png|tif|tiff|gif|webp)\s*$)"
    r"\s*[A-Z][a-z]{3,}\s+[a-z]{2,}\s+"
    # Optional parenthetical original author "(C. Agardh)" or "(Bory)"
    r"(?:\([A-Za-z][A-Za-z. ]{0,30}\)\s+)?"
    r"(?:[A-Z]\.|"                              # single-letter author: "L."
    r"[A-Z][a-z]{1,10}\.|"                      # short author: "Merr.", "Roxb.", "Blume.", "Wall.", "Setch."
    r"[A-Z][a-z]+\s+[A-Z]\.|"                   # "Hook. f." "Nees ex Steud."
    r"[A-Z]\.[A-Z]\.\s+[A-Z][a-z]+|"            # "K.W. Nam" (initials + surname)
    r"ex\s+[A-Z][a-z]+\.)",
    # (no IGNORECASE flag — case is a positive signal here)
)

# Named-individual portrait patterns from archives that leaked into cohort
# queries. Sorbian community / Domowina photos indexed under Central Asia
# by cross-language matching in Europeana.
# NOTE: `Kammavacha` was REMOVED from this list on 2026-07-23 — it's a real
# Burmese Buddhist ordination-text genre (Cleveland holds a beautiful set of
# lacquered palm-leaf Kammavacha folios) and was dropping 35 legitimate
# folk-material records.
NAMED_INDIVIDUAL_RE = re.compile(
    r"\b(Kurt Krenz|Domowina|Vorsitzender|Krjeńc|Bautzen|"
    r"Muhammad Shaybani|Faiz Muhammad Kateb|Rohullah Nikpai)\b",
    re.IGNORECASE,
)

# Replica/copy records — physical replica in a different country than the
# tradition it copies. Wikimedia Commons routinely surfaces these under the
# original tradition's category (e.g. Borobudur Copy in a Malaysian monastery
# hits any "Borobudur" search). The site is meant to show authentic material
# folk culture in situ, not tourist-monastery replicas.
REPLICA_RE = re.compile(
    r"\b(copy|replica|replicas|reconstruction|reproduction)"
    r"[,\s]+.*?(monastery|temple|park|museum|exhibition|display)",
    re.IGNORECASE,
)

# Meta-record filter: Wereldculturen and other Europeana providers routinely
# catalog reproductions/photographs/copies of ANOTHER artefact as their own
# record. Titles like "Photograph of a detail of a Persian carpet…" or
# "Fotografie eines Details eines persischen Teppichs…" pollute the textile
# bucket with second-order records ABOUT textiles rather than textiles.
# Fires only when the title STARTS with the meta-word followed by a
# possessive/genitive phrase, so it doesn't catch legit documentary photos
# ("Bali temple, photograph by X" — no leading "photograph of a").
META_REPRODUCTION_RE = re.compile(
    r"^(reproduction|photograph|fotografie|foto|kopie|copie|copy)"
    r"\s+(of\s+(a|an|the)|eines?|einer|d'un|de\s+la|van\s+een)\b",
    re.IGNORECASE,
)

# Bibliographic records — pages/plates/maps extracted from books, or
# scanned book titles themselves. Not folk objects; the atlas needs
# artefacts, not texts about them.
BOOK_EXCERPT_RE = re.compile(
    # A page/plate/map/frontispiece "from" a titled work
    r"\b(?:page|plate|map|frontispiece|illustration|figure|excerpt|title\s+page|leaf)\s+from\s+[\"'“‘]"
    # Explicit "Book N" volume references
    r"|\bBook\s+\d+[\.,\s]"
    # Multi-volume encyclopedia/publication series
    r"|\b(Magazijn\s+van|Cambridge\s+History|Oxford\s+History|Encyclop\w+\s+of)\b"
    # Academic paper / dissertation patterns
    r"|\bKnowledge,\s+renewal|Diskurse\s+der|Repositioning\s+and\s+changing",
    re.IGNORECASE,
)

# Estate / auction-catalog records — early-20th-c European auction houses'
# dissolution catalogues (Nachlass, Auflösung der Firma, Versteigerung) end
# up in Europeana / museum archives tagged with the region of the goods
# being sold ("Persian carpets", "Chinese porcelain"). The record is a
# CATALOG about lost objects, not an object itself — pollutes textile
# buckets with 1930s German auction ephemera.
ESTATE_AUCTION_RE = re.compile(
    r"^(Nachlass|Auflösung\s+der\s+Firma|Versteigerung|Auktion(?:s|\s+))"
    r"|due to the dissolution of the (company|firm|gallery)"
    r"|aus verschiedenem Privatbesitz",
    re.IGNORECASE,
)

# Colonial photo-archive titles (Dutch East Indies railway/factory/administrative).
# "resident of" alone was matching Cleveland's "This celestial being is a
# resident of heaven" — replaced with "colonial resident" / "assistant-resident"
# which unambiguously mean the Dutch colonial post.
COLONIAL_ADMIN_RE = re.compile(
    r"\b(sugar factory|railway bridge|colonial administration|"
    r"KPM steamship|colonial resident|assistant-resident|"
    r"resident of the (?:Dutch|colonial|Netherlands)|"
    r"Nederlands-Indi|Netherlands Indies)\b",
    re.IGNORECASE,
)

# Soviet-era institutional records that leak into Central Asian queries when
# museum catalogs use the historical name (Tajik SSR, Uzbek SSR). Sports
# badges, party memberships, workers' clubs — not folk material culture.
# Removed bare "pioneer" — was matching "pioneer of modern Javanese verse"
# on legit Cleveland records. Now requires Soviet-specific compound phrases.
SOVIET_INSTITUTIONAL_RE = re.compile(
    r"\b(spartakiate|sparttacias?|"          # Soviet sports olympiads
    r"membership badge|party card|"
    r"trade union|komsomol|young pioneer|pionery|"
    r"hosilot|nsv\s|ssr\s+[a-z]|"
    r"soviet republic|soviet union)\b",
    re.IGNORECASE,
)


def is_junk_title(title: str, description: str = "") -> tuple[bool, str]:
    """Return (True, reason) if the title/description matches any junk pattern.

    Two classes of patterns:
      TITLE_ONLY  — structural patterns (Latin binomials, camera-dump
                    filenames, Flickr IDs). Description text mustn't
                    contaminate them: "Port of Ambarita.jpg" was matching
                    the botanical regex because the concatenated
                    "hay" no longer ended in .jpg (description followed).
      CONTENT     — keyword patterns (heraldic, colonial, Soviet, coins,
                    landscapes). These fire on either title or description
                    because the phrase can appear anywhere.
    """
    t = (title or "").strip()
    hay = f"{t} {description or ''}".strip()
    if not hay:
        return False, ""
    TITLE_ONLY = (
        ("latin-binomial", LATIN_BINOMIAL_RE),
        ("zoological-genus", ZOOLOGICAL_GENUS_RE),
        ("botanical-binomial-with-author", BOTANICAL_BINOMIAL_WITH_AUTHOR_RE),
        ("botanical-author", BOTANICAL_AUTHOR_RE),
        ("camera-dump", CAMERA_DUMP_RE),
        ("meta-reproduction", META_REPRODUCTION_RE),
        ("estate-auction", ESTATE_AUCTION_RE),
        ("book-excerpt", BOOK_EXCERPT_RE),
    )
    CONTENT = (
        ("heraldic", HERALDIC_RE),
        ("diagram-map", DIAGRAM_RE),
        ("numismatic", NUMISMATIC_RE),
        ("landscape-aerial", LANDSCAPE_RE),
        ("named-individual-junk", NAMED_INDIVIDUAL_RE),
        ("colonial-admin", COLONIAL_ADMIN_RE),
        ("soviet-institutional", SOVIET_INSTITUTIONAL_RE),
        ("replica", REPLICA_RE),
    )
    for name, pat in TITLE_ONLY:
        if pat.search(t):
            return True, name
    for name, pat in CONTENT:
        if pat.search(hay):
            return True, name
    return False, ""


# --------------------------------------------------------------------------
# Provider allow/deny lists (for aggregators like Europeana + Commons)
# --------------------------------------------------------------------------

# Europeana providers we trust wholesale — every record from these can skip
# vision-vetting (they're curator-vetted at source).
TRUSTED_PROVIDERS: set[str] = {
    "Rijksmuseum",
    "National Museum of World Cultures Foundation",
    "Museum of World Culture",
    "Rijksmuseum Volkenkunde",
    "Museum Volkenkunde Leiden",
    "Tropenmuseum",
    "Tartu Art Museum",
    "MAK – Museum of Applied Arts",  # em-dash intentional per Europeana catalog
    "Craft Museum of Finland",
    "Estonian National Museum",
    "Ignacio Larramendi Foundation",
    "Musée du quai Branly",
    "Palais Galliera - Musée de la Mode de la Ville de Paris",
    "Museum of Architecture at Berlin Institute of Technology",
    "National Gallery of Denmark",
    "Náprstek Museum of Asian, African and American Cultures",
    "Nationalmuseum Sweden",
    "Kunstindustrimuseum",
    "Musei Vaticani",
    "Musée national des arts asiatiques - Guimet",
    "Cinquantenaire Museum",
    "Museo Nacional de Antropología",
    "Museum für Völkerkunde Wien (Weltmuseum Wien)",
    "Weltmuseum Wien",
    "Museum Rietberg",
    "Volkenkunde",
    "Shoes or no shoes",  # small footwear specialty catalog — vetted, focused
}

# Providers whose records systematically leak into unrelated ethnicity queries.
# The Sorbian Institute publishes Domowina community records in German+Sorbian
# that hit on Uzbek/Turkmen cross-language matches — reject at ingest.
DENIED_PROVIDERS: set[str] = {
    "Sorbian Institute",
    "Sorbian Cultural Archive",
    "Serbski institut",
    "Cultural heritage of Bautzen",
    "Landesarchiv Sachsen-Anhalt",
    # Natural history providers that keep leaking despite _is_cultural_provider
    "Grigore Antipa National Museum of Natural History",
    "Leiden University Libraries AnatomicalDrawings",
    # Soviet-era institutional collections that leak Tajik/Uzbek SSR sports
    # badges and party memorabilia into Central Asian ethnicity queries
    "Estonian Sports and Olympic Museum",
    "Lithuanian Archives of Literature and Art",
    # (Estonian History Museum is mixed — some Soviet ephemera, some folk;
    #  leaving on the trust-but-filter list rather than denying wholesale)
}


def is_trusted_provider(name: str | None) -> bool:
    if not name:
        return False
    return name.strip() in TRUSTED_PROVIDERS


def is_junk_provider(name: str | None) -> tuple[bool, str]:
    if not name:
        return False, ""
    n = name.strip()
    if n in DENIED_PROVIDERS:
        return True, "denied-provider"
    return False, ""


# --------------------------------------------------------------------------
# Combined guard used by scrapers at ingest time
# --------------------------------------------------------------------------

def should_reject(title: str = "", description: str = "",
                  provider: str = "") -> tuple[bool, str]:
    """One-call gate: True if this record should be dropped without further
    processing, with a short reason string.

    Order:
      1. Provider blocklist (cheapest, most decisive)
      2. Title / description regex family
    """
    ok, why = is_junk_provider(provider)
    if ok:
        return True, why
    ok, why = is_junk_title(title, description)
    if ok:
        return True, why
    return False, ""
