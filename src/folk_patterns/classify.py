"""Two-dimensional classifier over museum metadata:
  - art_form:        which category of folk art (textile / garment / ceramic / ...)
  - pattern_density: 0-3 heuristic of how "pattern-heavy" this object is

Rule-based, using classification / object_type / medium / material_technique
fields already populated by the source museum. No AI, no image inspection.

art_form values:
  textile           carpet, suzani, ikat, embroidery, cover, panel, hanging
  garment           chapan, robe, kurta, coat, dress, doppa, kalpak
  ceramic           bowls, plates, dishes, jars (not architectural)
  architectural     tile, muqarnas, wall panel, mosaic
  jewelry           rings, pendants, pectorals, ornaments, bracelets
  metalwork         bronze, brass, weapons, tools
  painting-mss      manuscripts, folios, drawings, calligraphy, sketchbooks
  sculpture         figurines, statues, plaster reliefs
  wallpaper         wall covering, printed textile design
  unclassified      no signal

pattern_density values:
  3   pure surface pattern: ikat length, wallpaper, tile mosaic, carpet field
  2   strongly patterned: robe with embroidery, painted plate, architectural tile
  1   ornament present: jewelry with engraving, decorated bowl
  0   figurative / plain: portraits, sculptures, plain vessels
"""
from __future__ import annotations

GARMENT_TERMS = [
    "robe", "kurta", "coat", "chapan", "dress", "doppa", "tubeteika",
    "kalpak", "kaftan", "gown", "costume", "duppi", "saukele",
    "elechek", "kurte", "keteni", "sari", "boot", "hat", "shoe",
    "outer coat", "kimono", "belt", "wedding cap", "cap",
    # sv: jacka=jacket, huvudbonad=headwear, halsduk=scarf
    "jacka", "huvudbonad", "halsduk",
    # Footwear + hosiery variants (Ladies stocking boots, bootstocking, overshoes, galosh)
    "stocking", "bootstocking", "overshoe", "galosh", "magshi", "itik",
    "slipper", "sandal",
    # SE Asia specific
    "sarong", "longyi", "sinh", "ao dai", "kebaya", "kain", "songket",
    "batik dress", "barong tagalog", "jusi", "malong", "tapis",
    # Multilingual (Wereldculturen tags in Swedish/Dutch, Berlin in German)
    # sv: klänning=dress, hatt=hat, mantel=cloak, byxor=trousers, tröja=sweater
    "klänning", "mantel", "byxor", "tröja", "skor",
    # de: Kleid, Mantel, Hemd, Hose, Mütze, Trachten
    "kleid", "hemd", "hose", "mütze", "trachten", "gewand",
    # nl: jurk, mantel, hemd, broek, kleding
    "jurk", "kleding",
    # et: kübar=hat, saapad=boots
    "kübar", "saapad",
    # ro: rochie=dress, pantofi=shoes, cizme=boots
    "rochie", "cizme",
]
ARCHITECTURAL_TERMS = [
    "tile", "mosaic", "muqarnas", "wall panel", "ceramic-architectural",
    "architectural fragment", "spandrel", "revetment",
    # Buildings + monumental architecture (Commons category names)
    "mosque", "mosques", "madrasah", "madrasa", "mausoleum", "minaret",
    "temple", "pagoda", "stupa", "shrine", "chapel", "cathedral",
    "monastery", "palace", "kraton", "fort", "fortress", "citadel",
    "gate", "gateway", "pura", "wat", "shwedagon", "borobudur",
    # SE Asia named building types
    "tongkonan", "rumah gadang", "longhouse", "bahay", "nipa hut",
    # Berlin architectural drawings (specific, unambiguous multi-word terms)
    "pfahlbau", "flusswohnung", "wohnhaus", "hauswand", "architektur",
]
WALLPAPER_TERMS = ["wallpaper", "wall covering"]
TEXTILE_TERMS = [
    "suzani", "ikat", "carpet", "rug", "textile", "silk", "cotton", "wool",
    "fabric", "cloth", "embroider", "cover", "blanket", "ribbon", "panel", "hanging",
    "length", "sash", "band", "cushion", "chakan", "quilt", "tapestry",
    "termeh", "garnhärva", "garn",  # Persian velvet, sv: yarn skein / yarn
    "kilim", "felt", "shyrdak", "ala kiyiz", "tush kiyiz", "syrmak",
    "koshma", "keteni", "chid", "kürte",
    # Turkmen tribal weaving vocab
    "bag face", "chuval", "torba", "mafrash", "spindle bag", "asmalyk",
    "ok bash", "tent band", "germech", "napramach", "juval",
    # SE Asia textile vocab
    "pua kumbu", "ulos", "batik", "songket", "tenun", "kalaga", "acheik",
    "sampot", "prāphum", "sinh", "praewa", "mudmee", "chintz",
    "t'nalak", "tnalak", "inabel", "piña", "pina",
    # Multilingual textile terms
    # sv: matta=rug, filt=felt, väska=bag, tyg=fabric, duk=cloth, kudde=cushion,
    #     sittdyna=cushion, dyna=cushion, väggväska=wall bag, filtmatta=felt rug
    "matta", "filt", "väska", "tyg", "duk", "kudde", "sittdyna", "dyna",
    "väggväska", "filtmatta", "brokad", "väv", "vävnad",
    # de: Teppich=carpet, Filz=felt, Stoff=fabric, Tuch=cloth, Kissen=cushion,
    #     Decke=blanket, Sack=bag, Wandbehang=wall hanging
    "teppich", "stoff", "tuch", "kissen", "decke", "sack", "wandbehang",
    # nl: kleed=rug, doek=cloth, mand=basket-textile hybrid, wandkleed
    "kleed", "wandkleed",
    # et: seinavaip=wall-hanging, tekk=blanket, käterätik=towel
    "seinavaip", "tekk", "vaip",
    # ro: covor=carpet, ștergar=towel, țesătură=weaving
    "covor", "ștergar", "țesătură",
    # Swedish variants seen for suzani/tush-kiyiz
    "suzanne", "tush-kiyiz", "väggprydnad", "väggdekoration",
]
CERAMIC_TERMS = [
    "ceramic", "stonepaste", "terracotta", "earthenware", "porcelain",
    "faience", "bowl", "plate", "dish", "jar", "cup", "vessel",
    "vase", "pottery", "beaker", "jug",
    # Multilingual
    # sv: skål=bowl, tallrik=plate, kruka=jar, kanna=jug, kopp=cup, korg=basket
    "skål", "tallrik", "kruka", "kanna", "kopp", "korg", "keramik",
    # de: Schale=bowl, Teller=plate, Krug=jug, Tasse=cup, Topf=pot, Geschirr=dishware
    "schale", "teller", "krug", "tasse", "topf", "geschirr",
    # nl: kom, schaal, kruik
    "kom", "schaal", "kruik",
]
JEWELRY_TERMS = [
    "jewelry", "jewellery", "ornament", "ring", "pendant", "pectoral",
    "bracelet", "earring", "necklace", "tumar", "bilezik", "asyk",
    "gulyaka", "seal ring", "amulet",
    # sv: smycke, halsband, örhänge, armband
    "smycke", "halsband", "örhänge", "armband",
    # de: Schmuck, Kette, Ohrring, Armband
    "schmuck", "kette", "ohrring",
]
METALWORK_TERMS = [
    "sword", "weapon", "axe", "dagger", "shield", "helmet", "spearhead",
    "arrowhead", "knife", "hilt", "scabbard",
    # Bare metal-material terms — a bronze fitting, brass bowl, iron mount
    # is metalwork even when its shape isn't a weapon. Previously missing,
    # dropping "Pair of naga finials" (Metalwork/bronze) into 'unclassified'.
    "bronze", "brass", "metalwork", "metalware", "iron mount", "iron fitting",
    "cuirass", "armor", "armour", "mirror", "finial",
    # SE Asia
    "kris", "keris", "parang", "mandau", "kampilan",
    # sv: kniv=knife, svärd=sword, yxa=axe, kvarn=mortar (grinding tool),
    #     rivbräda=grater
    "kniv", "svärd", "yxa", "kvarn", "rivbräda",
    # de: Messer=knife, Schwert=sword
    "messer", "schwert",
]
PAINTING_MSS_TERMS = [
    "codex", "codices", "manuscript", "folio", "painting", "drawing",
    "calligraphy", "sketchbook", "book", "portrait", "miniature",
    "print", "engraving", "watercolor", "watercolour", "ink on paper",
    "ink on parchment", "album", "album leaf", "diary", "travelogue", "journal",
    # Photographs are visual records of culture; treat them as painting-mss
    # bucket (2D visual media) since we don't have a dedicated `photo` art_form
    # in the classifier's target set. Multilingual photo terms:
    "fotografi", "photograph", "photo",     # sv / en
    "fotografie", "foto",                    # cs / de
    "valokuva",                              # fi
    # Náprstek Museum (Prague) Bambuti photograph collection — Czech captions
    # start with a descriptor + subject. "dvě/žena/muž/stará/stojící ..." are
    # descriptive photo captions, not object types. Route to painting-mss.
    "žena", "muž", "dvě", "stará", "stojící",  # cs
    # Finnish Heritage Agency Himba photos: "Himba-mies/nainen ja keihäs"
    "himba-mies", "himba-nainen",  # fi
]

# Coin / numismatic markers — filed under metalwork (they are metal objects
# with cast/struck surface patterns). Word-boundary matched so "drachma"
# and "tetradrachm" both catch.
NUMISMATIC_METAL_TERMS = [
    "drachm", "tetradrachm", "dinar", "dukaat", "ducat", "ropij", "rupee",
    "kopek", "coinage", "pitji", "cent",
    # SE Asia
    "lacquer painting", "lackmalerei", "lack", "dong ho", "hang trong",
    # Multilingual
    # sv: målning, teckning, tryck, bok, handskrift
    "målning", "teckning", "tryck", "bok", "handskrift", "manuskript",
    # de: Gemälde=painting, Zeichnung=drawing, Buch=book, Handschrift=manuscript
    "gemälde", "zeichnung", "buch", "handschrift", "buchmalerei",
    # nl: schilderij, tekening
    "schilderij", "tekening",
]
SCULPTURE_TERMS = [
    "sculpture", "figurine", "figure", "statue", "statuette",
    "stucco-sculpture", "relief", "plaque", "carved",
    "colossal head", "head of a", "bust",  # "Colossal Head of a Deva" Khmer
    # SE Asia carved-wood figures (masks, spirits, ancestor figures)
    "mask", "topeng", "wayang", "puppet", "ancestor figure",
    "wooden figure", "carved figure",
    # sv: skulptur, figur, staty
    "skulptur", "figur", "staty",
    # de: Skulptur, Figur, Statue, Maske
    "maske",
]

# Everyday household objects — Wereldculturen and Berlin catalogs are full of
# these for SE Asia (fans, brooms, combs, water containers, baskets, chairs).
# They aren't strictly "textile / ceramic / etc." but they are folk objects.
# Route them to a distinct "household" art form.
HOUSEHOLD_TERMS = [
    "fan", "broom", "brush", "comb", "chair", "stool", "basket", "bag",
    "water container", "water-container", "grater", "sieve", "colander",
    "spoon", "ladle", "tray", "box", "chest", "cabinet",
    "cutting board", "mortar", "pestle",
    # sv: råttfälla=rat trap, fiskfälla=fish trap, korgflaska=basket flask,
    #     klubba=club/mallet, grytspade=pot spade, huvudbonad=headwear/hat
    "råttfälla", "fiskfälla", "korgflaska", "klubba", "grytspade",
    # sv: solfjäder=fan, borste=brush, kam=comb, luskam=lice comb,
    #     stol=chair, sittdyna=cushion (also textile), säng=bed, kasse=bag,
    #     såll=sieve, kvast=broom, sopkvast=broom, skärbräda=cutting board,
    #     sparbössa=piggy bank, vattenhämtare=water carrier
    "solfjäder", "borste", "kam", "luskam", "stol", "barnstol", "säng",
    "kasse", "såll", "sållset", "kvast", "sopkvast", "skärbräda",
    "sparbössa", "vattenhämtare",
    # de: Fächer=fan, Besen=broom, Bürste=brush, Stuhl=chair, Bank=bench,
    #     Kiste=box, Schrank=cabinet, Prunkschrank=display cabinet
    "fächer", "besen", "bürste", "stuhl", "bank", "kiste", "schrank",
    "prunkschrank",
    # nl: waaier, bezem, borstel, stoel, mand
    "waaier", "bezem", "borstel", "stoel", "mand",
]

import re

# Fields we search across. `summary`/`description` are included because BM
# records have terse titles + rich summaries (`title="cover"`, summary
# "Cover of multi-coloured checked, woven silk cloth"). The reordering
# above (painting-mss / metalwork checked BEFORE textile) contains the risk
# of narrative "covered litter" / "silk binding cords" mentions dragging
# manuscripts/bronzes into textile.
FIELDS = ("classification", "object_type", "medium", "material_technique",
          "title", "objectName", "summary", "description")


def _blob(rec: dict) -> str:
    return " ".join(str(rec.get(k, "") or "") for k in FIELDS).lower()


def _has_term(blob: str, term: str) -> bool:
    """Match term against blob with word boundaries so 'tile' doesn't match
    inside '(textile)'. Also allow an optional trailing 's' so 'boot' catches
    'boots'. Multi-word terms use plain substring since they're unambiguous."""
    t = term.lower()
    if " " in t or "-" in t:
        return t in blob
    # Single word: require word boundary + optional plural 's'.
    return re.search(r"\b" + re.escape(t) + r"s?\b", blob) is not None


def _any_term(blob: str, terms: list[str]) -> bool:
    return any(_has_term(blob, t) for t in terms)


def classify_art_form(rec: dict) -> str:
    blob = _blob(rec)
    # STRONG SIGNAL: if the museum's own `classification` field explicitly
    # names a category, trust it. Otherwise summary prose about "pleated
    # cloth" (Khmer Vishnu sculpture) or "silk binding cords" (Persian
    # manuscript) drags real sculptures/manuscripts into textile.
    classif = (rec.get("classification") or "").lower().strip()
    if classif:
        # Direct class-name matches. Multi-word keys checked longest-first.
        for canonical, keys in (
            ("painting-mss", ("codices", "manuscripts", "prints", "drawings", "paintings")),
            ("metalwork",    ("metalwork", "metalware", "arms", "arms and armor")),
            ("sculpture",    ("sculpture", "sculpture-figure", "figures")),
            ("ceramic",      ("ceramics", "pottery")),
            ("jewelry",      ("jewelry", "jewellery", "ornament")),
            ("garment",      ("costume", "clothing", "dress")),
            ("textile",      ("textiles", "textile")),
        ):
            if any(k == classif or (" " not in k and k in classif.split()) for k in keys):
                return canonical
    # Fallback: term-list matching, in the priority order below.
    if _any_term(blob, WALLPAPER_TERMS):
        return "wallpaper"
    if _any_term(blob, PAINTING_MSS_TERMS):
        return "painting-mss"
    if _any_term(blob, METALWORK_TERMS):
        return "metalwork"
    if _any_term(blob, JEWELRY_TERMS):
        return "jewelry"
    if _any_term(blob, GARMENT_TERMS):
        return "garment"
    if _any_term(blob, SCULPTURE_TERMS):
        return "sculpture"
    if _any_term(blob, CERAMIC_TERMS):
        return "ceramic"
    if _any_term(blob, TEXTILE_TERMS):
        return "textile"
    if _any_term(blob, ARCHITECTURAL_TERMS):
        return "architectural"
    if _any_term(blob, HOUSEHOLD_TERMS):
        return "household"
    return "unclassified"


def pattern_density(rec: dict, art_form: str) -> int:
    blob = _blob(rec)

    # Textile / wallpaper: the whole surface IS the pattern. But if it's a
    # bag/hat/small textile, it's a fragmentary pattern. Default 3, drop to 2
    # for small items.
    if art_form in ("textile", "wallpaper"):
        return 3

    if art_form == "garment":
        # Garments show pattern but often at a distance / with background
        return 2

    if art_form == "architectural":
        # Muqarnas/mosaic tiles have very dense pattern; some architectural
        # fragments are figurative (stucco-sculpture with figures).
        if "sculpture" in blob or "figure" in blob or "portrait" in blob:
            return 1
        return 3 if _any_term(blob, ("mosaic", "tile", "muqarnas")) else 2

    if art_form == "ceramic":
        # Painted / decorated / glazed ceramics have surface pattern.
        # Plain ceramics don't.
        if _any_term(blob, ("painted", "decorated", "glazed", "polychrome",
                                    "underglaze", "cobalt", "lustre")):
            return 2
        return 1

    if art_form == "jewelry":
        return 1

    if art_form == "metalwork":
        if _any_term(blob, ("engraved", "chased", "damascened",
                                    "niello", "inlaid", "gilded")):
            return 1
        return 0

    if art_form == "painting-mss":
        # Manuscript decoration exists but most manuscripts are figurative.
        # Bump up if title mentions ornament/border/illumination.
        if _any_term(blob, ("ornament", "illuminated", "border",
                                    "decorated pages", "gilt")):
            return 1
        return 0

    if art_form == "sculpture":
        return 0

    return 0


def classify(rec: dict) -> dict:
    """Return {art_form, pattern_density}."""
    af = classify_art_form(rec)
    return {"art_form": af, "pattern_density": pattern_density(rec, af)}
