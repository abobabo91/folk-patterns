"""Object kind and art form from a museum's object name, by keyword, no LLM.

The ethnographic museums added on 2026-10-06 (KAMIS, SMB, Pitt Rivers, MAA,
quai Branly) name their objects with a plain noun in their own language:
"Flèche", "Ложка", "Tabakpfeifenkopf", "Bamboo arrow with pointed tip. ...".
Those names were ~38,000 the kinds cache did not hold, ~150 LLM batches, so
the common nouns are mapped here instead and only what is left goes to the
LLM pass in normalize_kinds.py.

How a name is read:
- it is cut into segments at ". " and " : " (Pitt Rivers and MAA lead with
  the noun, sometimes after a local name: "Bidang. Skirt of woven cotton");
- in each segment, in order, the PRIORITY rules win outright (a photograph, a
  model, money), then the keyword that starts EARLIEST, longest first at the
  same place, so "Couteau de jet" is a knife and "Ножны" a sheath, not a knife;
- a vessel takes its art form from the material named, if any.

Art forms follow the majorities of the cached kinds (data/pool/kinds.json,
counted 2026-10-06): knife -> arms, bag/pipe/calabash -> household, doll ->
sculpture, gold weight -> metalwork, mat -> textile.
"""
from __future__ import annotations

import re
import unicodedata

# (pattern, kind, art_form); patterns are searched case-insensitively.
# German compounds put the noun last ("Holzlöffel"), so German stems carry no
# leading word boundary; English and French ones do.
PRIORITY = [
    (r"photograph|фотограф|фотоотпечат|негатив|\bpositiv\b|\bdia\b|\bnegativ\b|fototafel|foto-?postkarte|"
     r"carte de visite|carte postale|\bpostcard|lantern slide|glass plate|\bphoto\b", "photograph", "photo"),
    (r"\bmod[eè]le\b|\bmaquette|\bmodel\b|\bmodell\b|модель|макет|\bminiature\b", "model", "sculpture"),
    (r"\bmoulage|\bcast of\b|слепок", "cast", "sculpture"),
    (r"banknote|\bcoin\b|\bmonnaie|монета|банкнот|\bmünze|geldschein", "money", "unclassified"),
    (r"[ée]chantillon v[ée]g[ée]tal|\bseed sample|plant specimen", "sample", "unclassified"),
    (r"образец[^.]{0,40}(ткан|бархат|шелк|шёлк|сукн|парч)", "cloth sample", "textile"),
]

KEYWORDS = [
    # ritual, masks
    (r"\bmasque|maske|\bmask\b|\bmasks\b|маск", "mask", "masks-ritual"),
    (r"крест|писанк|яйцо пасхальн|\bинау|фетиш|gebetsstock|opferstab|federzepter|kraftfigur|ahnenfigur|\bobjet rituel|\baccessoire de (danse|culte)|\bobjet cultuel|предмет культа|\bex-voto|\bchapelet|\brosary|\bgâteaux d.offrande|\bchandelier rituel|\btalisman|\bobjet magique|\bfer cultuel|\bcroix\b|\bcross\b|\brécade|\bherminette rituelle|\bchasse-mouches|\bfly.?whisk|\bmohara|\bamulet|amulett|амулет|оберег|\bcharm\b|\bfétiche|fetisch|\bfetish", "amulet", "masks-ritual"),
    (r"\bbrûle-parfum|\bencensoir|\bcenser|\bincense burner|\bidol|идол|\bautel|\bshrine|altar\b", "idol", "masks-ritual"),
    (r"бубен|\btambour de chaman|schamanentrommel", "shaman drum", "instruments"),
    (r"\bstaff\b|\bbâton\b|\bstab\b|\bzepter|\bsceptre|\bscepter|жезл|посох", "staff", "masks-ritual"),
    # sculpture
    (r"\bmarionnette|\bpuppet|\bmaternité|\bnetsuke|\btête (anthropomorphe|humaine)|\bstatuette|\bstatue|\bfigurine|\bfigure\b|figur\b|plastik\b|skulptur|\bsculpture|скульптур|статуэтк|фигурк|фигура|изображение будды|"
     r"\bpoupée|\bdoll\b|кукла|puppe|\btête commémorative|\bbuste|\bbust\b|\bplaque|клык", "figure", "sculpture"),
    # arms
    (r"\bfl[èe]che|\barrow|стрел|наконечник стрел|pfeil(?!e)|\bcarquois|\bquiver|колчан|köcher", "arrow", "arms"),
    (r"\barbalète|\bcrossbow|armbrust|арбалет|\barc\b|\bbow\b|\bbogen\b|\bлук\b|^лук", "bow", "arms"),
    (r"\bcasse-tête|\bmassue|\bclub\b|\bkeule|палица|дубин", "club", "arms"),
    (r"\bbouclier|\bshield|schild\b|\bщит", "shield", "arms"),
    (r"\bjavelot|\blance\b|\bsagaie|\bspear|\bspeer|копь[её]|копье|\bjavelin", "spear", "arms"),
    (r"\bépée|\bsabre|\bsword|schwert|säbel|меч\b|сабл|шашк", "sword", "arms"),
    (r"\bpoignard|\bdagger|dolch|кинжал|\bkriss?\b", "dagger", "arms"),
    (r"\bcouteau|\bknife|messer|\bнож\b|^нож|ножик", "knife", "arms"),
    (r"ножны|\bfourreau|\bsheath|\bscabbard|scheide", "sheath", "arms"),
    (r"\bhache|\baxe\b|axt\b|beil\b|топор", "axe", "arms"),
    (r"\bsarbacane|blowpipe|blasrohr|\bdart\b", "blowpipe", "arms"),
    (r"\bfusil|\bgun\b|\brifle|gewehr|ружь", "gun", "arms"),
    # instruments
    (r"\bhautbois|\boboe\b|\bclarinette|\bclarinet|\bsistre|\bsistrum|\bsonnailles|\bgrelot|\bclaquettes?|\bclapper|\borgue à bouche|\bmouth organ|\brhombe|\bbull.?roarer|\btambour|\bdrum\b|trommel|барабан", "drum", "instruments"),
    (r"\bhochet|\brattle|rassel|погремуш|трещотк", "rattle", "instruments"),
    (r"\bsifflet|\bwhistle|pfeife\b(?<!tabakpfeife)|свист", "whistle", "instruments"),
    (r"\bflûte|\bflute|flöte|флейт|дудк|свирел", "flute", "instruments"),
    (r"\bcloche|\bclochette|\bbell\b|glocke|колокол|бубенец", "bell", "instruments"),
    (r"\bluth|\blute\b|laute\b|\bharpe|\bharp\b|harfe|\bvièle|\bfiddle|geige|\bsanza|lamellophon|zither|leier\b|lamella?phone|\bcithare|"
     r"\bguitar|гитар|домбр|балалайк|гусли|кобыз|скрипк|варган|jew.s harp|maultrommel|guimbarde|\btrompe\b|\btrumpet|trompete|"
     r"\bhorn\b(?=.*instrument)|\bxylophon|\bgong\b|instrument de musique|musical instrument|\bwoodwind|\bpercussion|\bstringed|\b(idio|aero|chordo|membrano)phone|musikinstrument|музыкальн", "musical instrument", "instruments"),
    # jewelry
    (r"\bneck ?band|\bperles\b|\btorques?\b|\bcollier|\bnecklace|halskette|halsschmuck|ожерель|бусы|\bbeads?\b|perle\b|perlen", "necklace", "jewelry"),
    (r"\bpendentif|\bpendant|anhänger|подвес", "pendant", "jewelry"),
    (r"пряжк|\bbuckle|schnalle|\bhalsband|\bbracelet|\bbangle|\barmlet|armband|armreif|браслет|\bbrassard", "bracelet", "jewelry"),
    (r"armring|\banneau\b|\bbague\b|\bring\b|fingerring|ohrring|кольц|перстен", "ring", "jewelry"),
    (r"\bboucles? d.oreilles?|ornements? d.oreilles?|\bearring|\bear ornament|ohrschmuck|серьг|серёжк", "earring", "jewelry"),
    (r"\bpointe-démêloir|\bépingle|\bhairpin|\bhair pin|haarnadel|шпильк|заколк", "hairpin", "jewelry"),
    (r"\bbijou|\bpendeloque|\bjambelet|anneau de cheville|\banklet|\bornement|\bornament|\bparure|schmuck|украшени|\bpectoral|нагрудн|\blabret|\bnose ornament", "ornament", "jewelry"),
    (r"\bfibule|\bbrooch|\bbroche|fibel|brosche|фибул|брошь|застежк", "brooch", "jewelry"),
    # garment
    (r"\bceinture|\bbelt\b|gürtel|\bgurt\b|пояс|кушак", "belt", "garment"),
    (r"\bjupon|\bpagne|\bloincloth|lendentuch|\bsarong|\bskirt|\bjupe|(?<=[a-zäöü])rock\b|^rock\b|юбк", "skirt", "garment"),
    (r"\bcouvre-chef|\bchapeau|\bhat\b|\bhut\b|\bmütze|шляп|шапк|\bcap\b|\bbonnet", "hat", "garment"),
    (r"\bhead covering|убор головной|haube\b|\bcoiffure|\bhampe de coiffure|\bcalotte|\bbandeau|\bornements? de (tête|cheveux)|\bhead ornament|\bcoiffe|\bheaddress|kopfschmuck|kopfputz|головн[оы]й убор|\bdiadème|\bcouronne|\bcrown|krone|венец|кокошник", "headdress", "garment"),
    (r"чулок|чулк|носок|носк[иа]|schurz|korsett|камлейк|\bjambières|\bbande molletière|\bleggings?\b|\bpantalon|\bcaleçon|\btrousers|\bhose\b|штаны|шаровар", "trousers", "garment"),
    (r"\bpenis.?sheath|\bcache-fesses|\bcache-sexe|\bétui pénien|\bpenis sheath", "loincloth", "garment"),
    (r"\bboubou|\bplastron|\bcarré d.épaule|\bbaldric|\btunique|\btunic|\bchemise|\bshirt|hemd|рубах|рубаш|\bblouse|\bcorsage|bluse", "shirt", "garment"),
    (r"\bgilet|\bmanteau|\bcoat\b|mantel|\bcape\b|\bponcho|халат|кафтан|шуб|\bjacket|\bveste|jacke|куртк|\bkimono", "coat", "garment"),
    (r"\brobe\b|\bdress\b|kleid|платье|сарафан|\bcostume|\bkostüm|костюм|\bvêtement|kleidung|одежд|\bgarment", "dress", "garment"),
    (r"\bchaussettes?|\bsocks?\b|\braquettes? à neige|\bsnow.?shoe|\bsandale|\bsandal|\bshoe|\bchaussure|\bbotte|\bboot|\bmocassin|\bmoccasin|schuh|stiefel|обув|сапог|туфл|лапо?т|башмак|чувяк", "footwear", "garment"),
    (r"\bkragen|\bcollar\b|\bcol\b|воротник", "collar", "garment"),
    (r"\bgant|\bglove|handschuh|рукавиц|перчатк|\bmitten", "glove", "garment"),
    (r"\bplatok|платок|шаль|\bchâle|\bshawl|\bscarf|\bécharpe|\bfoulard|tuch\b|\bturban|\bvoile\b|\bveil", "shawl", "garment"),
    (r"\bapron|\btablier|schürze|передник|фартук", "apron", "garment"),
    # textile
    (r"скатерт|половик|кошма|\bтапа\b|лента плетен|шнур|schnur\b|matte\b|\bnappe\b|\bruban|\bgalon\b|\bcordelette|\bsangle|\bétoffe d.écorce|\bbark.?cloth|\bnapperon|\bcoussin|\bcushion|\bhamac|\bhammock|\bcorde\b|\brope\b|\bfilet\b(?! de pêche)|\bcroix de laine|\btissu|\bétoffe|\bcloth\b|\bfabric|\btextile|gewebe|stoff\b|ткан|\bnatte|\bmat\b|\bmatte\b|циновк|\bcouverture|\bblanket|"
     r"decke\b|одеял|покрывал|\btenture|\btapis|\bcarpet|\brug\b|teppich|ковер|ковёр|паас|набойк|бархат|\bvelvet|\bikat|\bbatik|"
     r"\bbroderie|\bembroider|stickerei|вышивк|полотенц|\btowel|\bserviette|тесьм|\bkilim|\bpanel of woven", "cloth", "textile"),
    (r"\bnavette|\bfusaïole|\bquenouille|\bshuttle|\bspindle.?whorl|\bfuseau|\bspindle|spindel|веретен|\bmétier à tisser|\bloom\b|webstuhl|ткацк|прялк|\bwhirl", "spinning tool", "textile"),
    # ceramic / vessels (material decides below)
    (r"\bcafetière|\bthéière|\bteapot|чайник|\btimbale|\bpoterie|\bpottery|\bmarmite|\bgargoulette|\bécuelle|\bcreuset|\bjarre|\bpot\b|topf\b|töpfe|горш|\bjar\b|vase\b|\bvase|ваза|\bjug\b|\bcruche|krug|кувшин|\bpichet|\bbouteille|\bbottle|flasche|бутыл|"
     r"\brécipient|\bvessel|gefäß|сосуд|\btesson|\bsherd|\bshard|scherbe|черепок", "vessel", "ceramic"),
    (r"\bcoupelle|\bbol\b|\bbowl|schüssel|schale\b|\bcoupe\b|миск|чаш|пиал|\bcup\b|\btasse\b|becher|чашк|\bgobelet|кружк|\bplat\b|"
     r"\bdish|\bplate|teller|\bassiette|блюд|тарелк|поднос|\btray|\bplateau\b|tablett", "bowl", "ceramic"),
    # metalwork
    (r"poids à peser l.or|gold.?weight|goldgewicht", "gold weight", "metalwork"),
    # household
    (r"\bcuill[eè]re?|\bspoon|löffel|ложк|\blouche|\bladle|kelle|половник|черпак|ковш", "spoon", "household"),
    (r"\bcalebasse|\bcalabash|\bgourd|kalebasse|калебас|тыкв", "calabash", "household"),
    (r"\bvannerie|\bpanier|\bcorbeille|\bbasket|korb|корзин|\bhotte\b|туес|туяс|лукошк", "basket", "household"),
    (r"\bsac\b|\bsacoche|\bbag\b|\bpouch|\bpurse|tasche|beutel|сумк|сумочк|кисет|мешоч|мешок|\bbourse", "bag", "household"),
    (r"\bcontainer|behälter|контейнер|\bétui\b|\bboîte|\bbox\b|kiste|schachtel|dose\b|коробк|короб|шкатулк|ларец|сундук|\bcoffret|\bcoffre\b|\bchest\b|\bcase\b|футляр|\bétui", "box", "household"),
    (r"\bpipe\b|\bpipe à|tabakpfeif|pfeifenkopf|трубк|\bchibouque|\bhookah|\bnarguilé|кальян", "pipe", "household"),
    (r"\bblague à tabac|\btabatière|snuff|schnupftabak|табакерк|флакон табачн|\bflacon", "snuff container", "household"),
    (r"\bpeigne|\bcomb\b|\bkamm\b|kämme|гребен|гребн|расческ", "comb", "household"),
    (r"\btabouret|\bstool|hocker|\bsiège|\bseat\b|\bchaise|\bchair\b|stuhl|табурет|стул|скамь|\bbanc\b|\bbench|\bbed\b|bettfuß|\blit\b|кровать", "stool", "household"),
    (r"\bhead ?rest|\bneck ?rest|kopfstütze|\bappui-?nuque|\bappui-?tête|\bheadrest|\bneckrest|nackenstütze|подголовник", "headrest", "household"),
    (r"\béventail|\bfan\b|fächer|веер|опахал", "fan", "household"),
    (r"\bcerf-volant|\bkite\b|\bballe\b|\bball\b|для игры|\bjouet|\btoy\b|spielzeug|игрушк|\bjeu\b|\bgame\b|spiel\b|домино|фишк|шашк|\bdice\b|\bdés\b|würfel|карт[ыа] игральн|toupie", "toy", "household"),
    (r"\bracloir|\bracleur|\bgrattoir|\bscraper|schaber|скребок", "scraper", "household"),
    (r"\bcouvercle|\blid\b|deckel\b|крышк", "lid", "household"),
    (r"\bлыж|\bведро|трепало|мутовк|корытц|солонк|столик|\bшило\b|сверло|люльк|\bciseau\b|\bcouperet|\blissoir|\bpolissoir|\boutil\b|\bpinceau|\bbrush\b|\bbalance\b|\bbêche|\bforet\b|\bdrill\b|\bcravache|огниво|\bberceau|\bcradle|колыбел|\bmangle\b|\btoggle\b|\bherminette|\badze|\bhoue\b|\bhoe\b|\bfaucille|\bsickle|\bmarteau|\bhammer|\bpoinçon|\bawl\b|\bburin\b|\baiguille|\bneedle|"
     r"\bspatul[ea]s?\b|\bvan\b|\bwinnow|\btamis|\bsieve|\bpassoire|\bbalai|\bbroom|\bbattoir|\bbeater|\bfouet|\bwhip|\bbriquet|"
     r"\bfire.?stick|\brasoir|\brazor|\bfourchette|\bfork\b|\bbrasero|\bserrure|\boutre|\bwaterskin|\binstrument de pierre|\bpiquet de tente", "tool", "household"),
    (r"\bcrochet|\bhook\b|haken|крюк", "hook", "household"),
    (r"\bmortier|\bpilon|\bmortar|\bpestle|mörser|\bступ[аке]\b|\bпест\b", "mortar", "household"),
    (r"\bpirogue|\bcanoe|\bboat\b|\bbateau|boot\b|\bkayak|лодк|\bpagaie|\bpaddle|paddel|весло", "boat", "household"),
    (r"пороховниц|грузил|наконечник гарпун|\bhameçon|\bfish.?hook|\bharpon|\bharpoon|\bnasse|\bfish.?trap|\bpiège|\btrap\b|\bfilet de pêche|\bcalibreur de mailles|"
     r"\bfronde|\bsling\b|\bpropulseur|\bspear.?thrower|\bcanne à pêche|\bfishing.?rod|\bpoire à poudre|\bpowder.?flask", "hunting gear", "household"),
    (r"\bharnais|\bbridle|\bsaddle|\bselle\b|sattel|zaumzeug|седл|узд|стрем", "horse gear", "household"),
    (r"\bcanne-siège|\bcanne\b|\bwalking.stick|spazierstock|трость", "walking stick", "household"),
    (r"\bbougie|\bcandle|\blight holder|\brush.?light|\blampe|\blamp\b|leuchter|светильник|лампа|\bcandlestick|подсвечник", "lamp", "household"),
    (r"\bmiroir|\bmirror|spiegel|зеркал", "mirror", "household"),
    (r"\bclé\b|\bkey\b|schlüssel|ключ\b|\bcadenas|\block\b|замок", "key", "household"),
    (r"\bpoids\b|\bweight\b|gewicht", "weight", "household"),
    # painting, manuscripts
    (r"\bdessin|\bdrawing|zeichnung|aquarell|\baquarelle|\bwatercolou?r|рисун|акварел|\bsketch|\bcroquis|эскиз|набросок", "drawing", "painting-mss"),
    (r"\bpierre peinte|\bimage populaire|\bpeinture|\bpainting|gemälde|malerei|картин|живопис|\btableau\b|\bicône|\bicon\b|ikone|икона|\bthangka|\btriptyque", "painting", "painting-mss"),
    (r"\bmanuscrit|\bmanuscript|handschrift|рукопис|\blivre\b|\bbook\b|\bbuch\b|книг|\bgravure|\bprint\b|\bestampe|\blithograph|"
     r"\bengraving|гравюр|лубок|holzschnitt|\bwoodcut|\bscroll\b|\brouleau", "manuscript", "painting-mss"),
    # architecture
    (r"\bpoteau|\bpost\b|\bpillar|\bpilier|pfosten|\bpanneau sculpté|\blintel|\blinteau|\bdoor\b|\bporte\b|\btür\b|дверь|наличник|\bshutter", "house part", "architectural"),
]

_MATERIAL = [
    (r"terre cuite|\bterre\b|céramique|\bceramic|porcelain|porcelaine|faïence|\bclay\b|earthenware|stoneware|"
                r"\bton\b|tonge|irden|глин|керамич|фарфор|фаянс|терракот", "ceramic"),
    (r"\bmétal|laiton|bronze|cuivre|argent\b|\bfer\b|\bmetal|brass|silver|copper|\biron\b|\bgold\b|pewter|"
                r"messing|silber|kupfer|eisen|bronze|серебр|латун|медн|бронз|желез|металл|оловян", "metalwork"),
    (r"\bbois\b|wooden|\bwood\b|holz|деревян|дерев|\bgourd|calebasse|kalebasse|\bcoco|écorce|\bbark|rinde|берест|кора", "household"),
]

def _fold(s: str) -> str:
    """Without diacritics, on both sides: quai Branly writes capitals bare ("Epingle", "Etui")."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


_PRI = [(re.compile(_fold(p), re.I), k, a) for p, k, a in PRIORITY]
_KW = [(re.compile(_fold(p), re.I), k, a) for p, k, a in KEYWORDS]
_MAT = [(re.compile(_fold(p), re.I), a) for p, a in _MATERIAL]
_SEG = re.compile(r"\.\s+|\s:\s|;\s")


def _segment(s: str) -> tuple[str, str] | None:
    for rx, k, a in _PRI:
        if rx.search(s):
            return k, a
    best = None
    for rx, k, a in _KW:
        m = rx.search(s)
        if m and (best is None or (m.start(), -len(m.group())) < best[0]):
            best = ((m.start(), -len(m.group())), k, a)
    return (best[1], best[2]) if best else None


# Chinese object names (National Taiwan Museum, 2026-10-07) put the head noun
# LAST ("男子羽飾皮帽" is a hat, "鄒族單鏃鐵箭" an arrow), so for them the match
# that ends last wins, the longer one at the same end ("腿布" leg cloth before
# "布" cloth). Built from the title endings of the 3,466 attributed records;
# endings that name no clear kind (棒 stick, 板 board, 片 piece) are left out.
CJK = [
    ("照片|明信片|寫真|相片", "photograph", "photo"),
    ("面具", "mask", "masks-ritual"),
    ("頭飾|額帶|頭巾|髮飾", "headdress", "garment"),
    ("耳飾|頸飾|腕飾|胸飾|腿飾|手飾|飾|項鍊|項圈|手鐲|鐲|戒指|環|珠", "ornament", "jewelry"),
    ("衣|服|裙|褲|帽|兜|披肩|袖|鞋|套|腰帶|帶|腿布|腳布|綁腿|背心", "garment", "garment"),
    ("織布|布|繩|網|蓆|毯|緯板|布夾|經卷|捲布|紡錘|織機|織具", "cloth", "textile"),
    ("刀|劍|箭|槍|矛|弓|盾|鏃|箭袋", "weapon", "arms"),
    ("笛|琴|鈴|鼓|螺|口簧", "musical instrument", "instruments"),
    ("像|偶|雕板|雕刻|人頭", "figure", "sculpture"),
    ("柱|門|楣", "house part", "architectural"),
    ("壺|罐|甕|碗|杯|盆|缽|盤|甑", "vessel", "household"),
    ("袋|籠|籃|筐|簍|匙|勺|盒|斗|杵|臼|桶|箱|笊|筌|枕|梳|架|鍬|籩|匏器|筒", "utensil", "household"),
]
_CJK_RX = [(re.compile(p), k, a) for p, k, a in CJK]
_HAN = re.compile(r"[一-鿿]")


def _cjk(name: str) -> dict | None:
    name = re.sub(r"[（(][^）)]*[）)]\s*$", "", name).strip()   # "背籠（含背帶）": the object, not its part
    best = None
    for rx, k, a in _CJK_RX:
        for m in rx.finditer(name):
            key = (m.end(), len(m.group()))
            if best is None or key > best[0]:
                best = (key, k, a)
    if not best:
        return None
    k, a = best[1], best[2]
    if k == "vessel":
        a = "ceramic" if "陶" in name else ("metalwork" if re.search("銅|鐵|銀|錫", name) else "household")
    return {"kind": k, "art_form": a}


def classify(name: str) -> dict | None:
    """{"kind", "art_form"} for a museum object name, or None when no keyword fits."""
    if _HAN.search(name or ""):
        return _cjk(name.strip())
    name = _fold((name or "").strip())
    for seg in _SEG.split(name)[:3]:
        hit = _segment(seg)
        if hit:
            kind, af = hit
            if kind in ("vessel", "bowl"):
                af = next((a for rx, a in _MAT if rx.search(name)), af)
            return {"kind": kind, "art_form": af}
    return None
