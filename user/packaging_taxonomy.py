"""
Maps packaging keywords and aliases to catalog categories.
"""

from __future__ import annotations

from user.search_normalize import (
    arabic_article_variants,
    arabic_icontains_variants,
    arabic_plural_variants,
    arabic_ta_marbuta_variant,
    fold_text,
    french_icontains_variants,
    french_plural_variants,
    levenshtein_distance,
    strip_arabic_definite_article,
)

# Arabic commercial terms written in Arabic script → Latin search equivalents.
ARABIC_LATIN_TRANSLITERATIONS: dict[str, str] = {
    'دوياك': 'doypack',
    'دوي باك': 'doypack',
    'بليستر': 'blister',
    'بيغ باغ': 'big bag',
    'كرافت': 'kraft',
    'سليف': 'sleeve',
    'باركود': 'barcode',
    'فليكسو': 'flexo',
    'أوفست': 'offset',
    'pet': 'pet',
    'فibc': 'fibc',
}

# Query tokens that indicate packaging intent (not dosage form alone).
PACKAGING_HINT_TERMS: frozenset[str] = frozenset(
    fold_text(term)
    for term in (
        'emballage',
        'packaging',
        'pack',
        'تغليف',
        'التغليف',
        'عبوة',
        'عبوات',
        'conditionnement',
        'conditionnement',
        'blister',
        'بليستر',
        'carton',
        'علبة',
        'علب',
        'صندوق',
        'label',
        'ملصق',
        'etiquette',
        'étiquette',
    )
)

PHARMA_PACKAGING_CONCEPTS: frozenset[str] = frozenset({
    'pharma_packaging',
    'blister_pack',
    'pharma_carton',
    'pharma_label',
})


def _terms(*groups: tuple[str, ...]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        merged.extend(group)
    return merged


# kind: packaging | dosage_form | material | market
CONCEPT_GROUPS: tuple[dict, ...] = (
    {
        'id': 'sacs',
        'kind': 'packaging',
        'terms': _terms(
            (
                'sac', 'sacs', 'sac en papier', 'sac plastique', 'sac kraft',
                'big bag', 'sachet', 'sac de courses', 'sac alimentaire',
                'sac d emballage', 'sac de conditionnement',
            ),
            (
                'كيس', 'أكياس', 'كيس ورقي', 'أكياس ورقية', 'كيس بلاستيك',
                'أكياس بلاستيكية', 'كيس بلاستيكي', 'كيس كرافت', 'أكياس كرافت',
                'كيس تسوق', 'أكياس التسوق', 'كيس غذائي', 'أكياس غذائية',
                'كيس تعبئة', 'أكياس التعبئة', 'كيس تغليف', 'أكياس التغليف',
                'كيس كبير', 'أكياس كبيرة', 'بيغ باغ', 'أكياس بيغ باغ',
                'شوال', 'شوال كبير', 'أكياس منسوجة', 'أكياس غير منسوجة',
            ),
            (
                'bag', 'bags', 'paper bag', 'plastic bag', 'kraft bag',
                'shopping bag', 'food bag', 'packaging bag', 'bulk bag',
                'big bag', 'FIBC', 'non woven bag',
            ),
        ),
    },
    {
        'id': 'etiquettes',
        'kind': 'packaging',
        'terms': _terms(
            (
                'étiquette', 'etiquette', 'étiquettes', 'etiquettes',
                'étiquette autocollante', 'etiquette autocollante',
                'étiquette adhésive', 'étiquette imprimée', 'étiquette thermique',
                'étiquette vierge', 'étiquette produit', 'étiquette pharma',
                'étiquette code-barres', 'etiquette code barres',
            ),
            (
                'ملصق', 'ملصقات', 'ملصق لاصق', 'ملصقات لاصقة',
                'ملصق ذاتي اللصق', 'ملصقات ذاتية اللصق',
                'بطاقة تعريف المنتج', 'بطاقات المنتجات', 'ملصق مطبوع',
                'ملصقات مطبوعة', 'ملصق حراري', 'ملصقات حرارية',
                'ملصق فارغ', 'ملصقات فارغة', 'ملصق دوائي', 'ملصقات دوائية',
                'ملصق الباركود', 'ملصقات الباركود', 'بطاقة المنتج', 'بطاقات المنتج',
            ),
            (
                'label', 'labels', 'sticker', 'adhesive label',
                'self adhesive label', 'printed label', 'thermal label',
                'blank label', 'product label', 'barcode label',
                'pharmaceutical label',
            ),
        ),
    },
    {
        'id': 'bobines',
        'kind': 'packaging',
        'terms': _terms(
            (
                'bobine', 'bobines', 'bobine papier', 'bobine de papier',
                'bobine aluminium', 'bobine alu', 'bobine pvc',
                'bobine film', 'bobine plastique', 'bobine laminée',
                'bobine composite', 'laize', 'mandrin', 'reel', 'reels',
            ),
            (
                'بكرة', 'بكرات', 'بكرة ورق', 'بكرة ألومنيوم', 'بكرة بلاستيك',
                'بكرة فيلم', 'بكرة مركبة',
            ),
            (
                'reel', 'reels', 'paper reel', 'aluminium reel', 'aluminum reel',
                'pvc reel', 'film reel', 'laminated reel', 'composite reel',
                'web width', 'jumbo roll',
            ),
        ),
    },
    {
        'id': 'flexible_packaging',
        'kind': 'packaging',
        'terms': _terms(
            (
                'emballage flexible', 'emballage souple', 'sachet', 'sachet barrière',
                'sachet plat', 'doypack', 'pochette debout', 'sachet debout',
                'sachet zip', 'sachet sous vide', 'film laminé',
            ),
            (
                'تغليف مرن', 'عبوات مرنة', 'تغليف بلاستيكي مرن', 'كيس مرن',
                'أكياس مرنة', 'كيس واقي', 'أكياس حاجزة', 'كيس مسطح',
                'أكياس مسطحة', 'كيس واقف', 'أكياس واقفة', 'دوي باك', 'دوياك',
                'كيس دوي باك', 'كيس بسحاب', 'أكياس بسحاب', 'كيس مفرغ من الهواء',
                'أكياس مفرغة من الهواء', 'فيلم تغليف', 'فيلم متعدد الطبقات',
            ),
            (
                'flexible packaging', 'flexible pack', 'pouch', 'sachet',
                'barrier pouch', 'flat pouch', 'doypack', 'stand-up pouch',
                'zip pouch', 'vacuum pouch', 'laminated film',
            ),
        ),
    },
    {
        'id': 'carton',
        'kind': 'packaging',
        'terms': _terms(
            (
                'carton', 'boîte carton', 'boite carton', 'boîte pliante',
                'caisse carton', 'caisse américaine', 'carton ondulé',
                'boîte pizza', 'boite pizza', 'boîte e-commerce',
                'présentoir carton', 'tube carton', 'mandrin carton',
            ),
            (
                'كرتون', 'كرتون تغليف', 'علبة كرتون', 'علب كرتونية',
                'صندوق كرتوني', 'صناديق كرتونية', 'علبة بيتزا', 'علب البيتزا',
                'صندوق شحن', 'صناديق الشحن', 'كرتون مموج', 'كرتون مضلع',
                'علبة قابلة للطي', 'علب قابلة للطي', 'صندوق أمريكي',
                'صناديق أمريكية', 'حامل عرض كرتوني', 'منصة عرض كرتونية',
                'أنبوب كرتوني', 'قلب كرتوني', 'فاصل كرتوني', 'حشوة كرتونية',
            ),
            (
                'cardboard', 'carton', 'cardboard box', 'folding carton',
                'corrugated box', 'shipping box', 'pizza box', 'ecommerce box',
                'cardboard display', 'cardboard tube', 'cardboard core',
                'cardboard divider',
            ),
        ),
    },
    {
        'id': 'bottles',
        'kind': 'packaging',
        'terms': _terms(
            (
                'bouteille', 'bouteilles', 'flacon', 'flacons', 'bouteille PET',
                'flacon plastique', 'bouteille en verre', 'flacon en verre',
                'pot plastique', 'pot en verre', 'bidon', 'jerricane',
            ),
            (
                'زجاجة', 'زجاجات', 'قارورة', 'قوارير', 'عبوة', 'عبوات',
                'قارورة بلاستيكية', 'زجاجة بلاستيكية', 'قارورة PET',
                'عبوة بلاستيكية', 'قارورة زجاجية', 'زجاجة زجاجية',
                'مرطبان', 'مرطبانات', 'علبة بلاستيكية', 'علب بلاستيكية',
                'علبة زجاجية', 'علب زجاجية', 'جالون', 'جالونات', 'جيريكان',
                'برميل بلاستيكي',
            ),
            (
                'bottle', 'bottles', 'vial', 'vials', 'plastic bottle',
                'PET bottle', 'glass bottle', 'plastic jar', 'glass jar',
                'container', 'jerrycan', 'drum',
            ),
        ),
    },
    {
        'id': 'caps',
        'kind': 'packaging',
        'terms': _terms(
            (
                'bouchon', 'bouchon à vis', 'bouchon flip-top', 'bouchon doseur',
                'bouchon compte-gouttes', 'pompe', 'pulvérisateur', 'opercule',
                'capsule', 'fermeture zip', 'bec verseur',
            ),
            (
                'غطاء', 'أغطية', 'غطاء لولبي', 'أغطية لولبية', 'غطاء قلاب',
                'غطاء جرعات', 'غطاء قطارة', 'مضخة', 'مضخة توزيع', 'بخاخ', 'رشاش',
                'قطارة', 'غطاء ألمنيوم', 'غطاء معدني', 'سدادة', 'سدادات',
                'غطاء حراري', 'إغلاق بسحاب', 'فوهة صب',
            ),
            (
                'cap', 'screw cap', 'flip-top cap', 'dosing cap', 'dropper cap',
                'pump dispenser', 'spray pump', 'dropper', 'aluminum cap',
                'foil seal', 'zip closure', 'spout',
            ),
        ),
    },
    {
        'id': 'tubes',
        'kind': 'packaging',
        'terms': _terms(
            (
                'tube', 'tube plastique', 'tube aluminium', 'tube laminé',
                'tube cosmétique', 'tube pharmaceutique', 'tube mascara',
                'tube crème', 'tube pommade',
            ),
            (
                'أنبوب', 'أنابيب', 'أنبوب بلاستيكي', 'أنابيب بلاستيكية',
                'أنبوب ألمنيوم', 'أنابيب ألمنيوم', 'أنبوب متعدد الطبقات',
                'أنبوب مستحضرات التجميل', 'أنبوب دوائي', 'أنبوب كريم',
                'أنبوب مرهم', 'أنبوب ماسكارا', 'أنبوب معجون الأسنان',
            ),
            (
                'tube', 'plastic tube', 'aluminum tube', 'laminated tube',
                'cosmetic tube', 'pharmaceutical tube', 'mascara tube',
                'cream tube', 'ointment tube',
            ),
        ),
    },
    {
        'id': 'films',
        'kind': 'packaging',
        'terms': _terms(
            (
                'film', 'film étirable', 'film etirable', 'film rétractable',
                'film retractable', 'film à bulles', 'film alimentaire',
                'film aluminium', 'film barrière', 'film laminé', 'film métallisé',
            ),
            (
                'فيلم', 'أفلام', 'فيلم تغليف', 'فيلم مطاطي', 'فيلم قابل للتمدد',
                'فيلم قابل للانكماش', 'فيلم حراري', 'فيلم فقاعات', 'نايلون فقاعات',
                'فيلم غذائي', 'ورق ألمنيوم', 'فيلم حاجز', 'فيلم متعدد الطبقات',
                'فيلم معدني', 'لفافة تغليف',
            ),
            (
                'film', 'stretch film', 'shrink film', 'bubble wrap', 'food wrap',
                'aluminum foil', 'barrier film', 'laminated film', 'metallized film',
            ),
        ),
    },
    {
        'id': 'pharma_packaging',
        'kind': 'packaging',
        'terms': _terms(
            (
                'emballage pharmaceutique', 'emballage pharma', 'blister',
                'alu-alu', 'alu alu', 'flacon pharma', 'ampoule', 'vial',
                'étui pharmaceutique', 'boîte pharmaceutique', 'sachet pharma',
                'tube pharma', 'étiquette pharma',
            ),
            (
                'تغليف دوائي', 'تغليف صيدلاني', 'عبوة دوائية', 'عبوات دوائية',
                'شريط دوائي', 'بليستر', 'ألو ألو', 'قارورة دوائية', 'أمبولة',
                'فيال', 'علبة دواء', 'علب الأدوية', 'كيس دوائي', 'أنبوب دوائي',
                'ملصق دوائي',
            ),
            (
                'pharmaceutical packaging', 'pharma packaging', 'blister',
                'alu-alu', 'pharmaceutical bottle', 'ampoule', 'vial',
                'pharmaceutical carton', 'pharma pouch', 'pharma tube',
                'pharmaceutical label',
            ),
        ),
    },
    {
        'id': 'blister_pack',
        'kind': 'packaging',
        'terms': ('blister', 'alu-alu', 'alu alu', 'بليستر', 'ألو ألو', 'شريط دوائي'),
        'related': (),
    },
    {
        'id': 'pharma_carton',
        'kind': 'packaging',
        'terms': (
            'boîte pharmaceutique', 'étui pharmaceutique', 'pharmaceutical carton',
            'علبة دواء', 'علب الأدوية',
        ),
    },
    {
        'id': 'pharma_label',
        'kind': 'packaging',
        'terms': (
            'étiquette pharma', 'pharmaceutical label', 'ملصق دوائي', 'ملصقات دوائية',
        ),
    },
    {
        'id': 'dosage_tablet',
        'kind': 'dosage_form',
        'terms': ('comprimé', 'comprime', 'comprimés', 'tablet', 'tablets', 'قرص', 'أقراص', 'حبة', 'حبوب'),
        'related': PHARMA_PACKAGING_CONCEPTS,
    },
    {
        'id': 'dosage_capsule',
        'kind': 'dosage_form',
        'terms': ('gélule', 'gelule', 'gélules', 'capsule', 'capsules', 'كبسولة', 'كبسولات'),
        'related': PHARMA_PACKAGING_CONCEPTS,
    },
    {
        'id': 'dosage_syrup',
        'kind': 'dosage_form',
        'terms': ('sirop', 'syrup', 'solution buvable', 'شراب', 'محلول فموي'),
        'related': PHARMA_PACKAGING_CONCEPTS,
    },
    {
        'id': 'dosage_powder',
        'kind': 'dosage_form',
        'terms': ('poudre', 'powder', 'مسحوق', 'مساحيق'),
        'related': PHARMA_PACKAGING_CONCEPTS,
    },
    {
        'id': 'dosage_injectable',
        'kind': 'dosage_form',
        'terms': ('injectable', 'ampoule', 'حقن', 'أمبولة', 'vial', 'فيال'),
        'related': PHARMA_PACKAGING_CONCEPTS,
    },
    {
        'id': 'dosage_ointment',
        'kind': 'dosage_form',
        'terms': ('pommade', 'ointment', 'مرهم', 'crème', 'cream', 'كريم'),
        'related': PHARMA_PACKAGING_CONCEPTS,
    },
    {
        'id': 'restauration',
        'kind': 'packaging',
        'terms': _terms(
            (
                'emballage alimentaire', 'boîte alimentaire', 'boite alimentaire',
                'boîte pizza', 'boite pizza', 'boîte tacos', 'boîte burger',
                'barquette frites', 'gobelet carton', 'sachet café', 'emballage café',
                'doypack café',
            ),
            (
                'تغليف غذائي', 'عبوات غذائية', 'علبة طعام', 'علب الطعام',
                'علبة بيتزا', 'علب بيتزا', 'علبة تاكوس', 'علبة برغر', 'علبة شطيرة',
                'صحن بطاطا', 'علبة بطاطا مقلية', 'صينية غذائية', 'كوب كرتوني',
                'أكواب كرتونية', 'كيس قهوة', 'عبوة قهوة', 'تغليف القهوة',
                'كيس قهوة بصمام',
            ),
            (
                'food packaging', 'food box', 'pizza box', 'taco box', 'burger box',
                'fries tray', 'takeaway packaging', 'paper cup', 'coffee bag',
                'coffee packaging', 'coffee pouch',
            ),
        ),
    },
    {
        'id': 'ecommerce',
        'kind': 'packaging',
        'terms': _terms(
            (
                'emballage e-commerce', 'emballage ecommerce', 'boîte d expédition',
                'carton d expédition', 'pochette d expédition', 'enveloppe matelassée',
                'sac courrier', 'emballage retour', 'boîte colis',
            ),
            (
                'تغليف التجارة الإلكترونية', 'تغليف إلكتروني', 'صندوق شحن', 'علبة شحن',
                'كرتون شحن', 'ظرف شحن', 'ظرف مبطن', 'كيس شحن', 'كيس بريدي',
                'تغليف المرتجعات', 'علبة الطرود', 'صندوق الطرود',
            ),
            (
                'ecommerce packaging', 'shipping box', 'mailer box', 'mailing bag',
                'padded envelope', 'shipping pouch', 'return packaging', 'parcel box',
            ),
        ),
    },
    # Materials / legacy cross-language helpers
    {
        'id': 'plastic',
        'kind': 'material',
        'terms': ('plastic', 'plastique', 'plastiques', 'pet', 'hdpe', 'ldpe', 'pp', 'pe', 'بلاستيك', 'بلاستيكية', 'بلاستيكي'),
    },
    {
        'id': 'foam',
        'kind': 'material',
        'terms': ('foam', 'mousse', 'eps', 'epp', 'polystyrene'),
    },
    {
        'id': 'glass',
        'kind': 'material',
        'terms': ('glass', 'verre', 'زجاج', 'زجاجية', 'زجاجي'),
    },
    {
        'id': 'aluminum',
        'kind': 'material',
        'terms': ('aluminum', 'aluminium', 'alu', 'ألمنيوم'),
    },
    {
        'id': 'pharma_market',
        'kind': 'market',
        'terms': ('pharma', 'pharmaceutique', 'pharmacie', 'medicament', 'sante', 'دوائي', 'صيدلاني', 'صيدلانية'),
    },
    {
        'id': 'cosmetic_market',
        'kind': 'market',
        'terms': ('cosmetic', 'cosmetics', 'cosmetique', 'cosmetiques', 'beaute', 'beauty', 'تجميل', 'مستحضرات'),
    },
)


def _build_indexes() -> tuple[
    dict[str, str],
    dict[str, set[str]],
    dict[str, frozenset[str]],
]:
    token_to_concept: dict[str, str] = {}
    concept_terms: dict[str, set[str]] = {}
    concept_related: dict[str, frozenset[str]] = {}

    for group in CONCEPT_GROUPS:
        concept_id = group['id']
        folded_terms: set[str] = set()
        for raw in group['terms']:
            folded = fold_text(raw)
            if len(folded) < 2:
                continue

            def register(term: str) -> None:
                if len(term) < 2:
                    return
                folded_terms.add(term)
                token_to_concept.setdefault(term, concept_id)

            register(folded)
            for article in arabic_article_variants(folded):
                register(article)
            alt = arabic_ta_marbuta_variant(folded)
            if alt != folded:
                register(alt)
            for plural in french_plural_variants(folded):
                register(plural)
            latin = ARABIC_LATIN_TRANSLITERATIONS.get(raw.strip()) or ARABIC_LATIN_TRANSLITERATIONS.get(folded)
            if latin:
                register(fold_text(latin))
            for part in folded.split():
                register(part)
                for plural in french_plural_variants(part):
                    register(plural)

        concept_terms[concept_id] = folded_terms
        related = group.get('related')
        if related:
            concept_related[concept_id] = frozenset(related)

    return token_to_concept, concept_terms, concept_related


TOKEN_TO_CONCEPT, CONCEPT_TERMS, CONCEPT_RELATED = _build_indexes()


def concept_for_token(token: str) -> str | None:
    folded = fold_text(token)
    if folded in TOKEN_TO_CONCEPT:
        return TOKEN_TO_CONCEPT[folded]
    stripped = strip_arabic_definite_article(folded)
    return TOKEN_TO_CONCEPT.get(stripped)


def is_packaging_hint(token: str) -> bool:
    folded = fold_text(token)
    if folded in PACKAGING_HINT_TERMS:
        return True
    stripped = strip_arabic_definite_article(folded)
    return stripped in PACKAGING_HINT_TERMS


def query_has_packaging_hint(tokens: list[str]) -> bool:
    return any(is_packaging_hint(token) for token in tokens)


MAX_DB_VARIANTS = 18


def _cap_variants(variants: set[str], limit: int = MAX_DB_VARIANTS) -> set[str]:
    if len(variants) <= limit:
        return variants
    ordered = sorted(variants, key=lambda item: (-len(item), item))
    return set(ordered[:limit])


def _fuzzy_concept_terms(folded: str, concept_id: str, *, max_items: int = 4) -> set[str]:
    hits: set[str] = set()
    for term in CONCEPT_TERMS.get(concept_id, set()):
        if len(folded) >= 4 and len(term) >= 4 and levenshtein_distance(folded, term) <= 1:
            hits.add(term)
            if len(hits) >= max_items:
                break
    return hits


def db_search_variants(token: str, *, query_tokens: list[str] | None = None) -> set[str]:
    """Conservative variants for SQL icontains filters (avoids huge OR trees)."""
    folded = fold_text(token)
    if not folded:
        return set()

    variants: set[str] = {folded, token.strip()}
    variants.update(french_plural_variants(folded))
    variants.update(french_icontains_variants(folded))
    variants.update(arabic_article_variants(folded))
    variants.update(arabic_plural_variants(folded))
    variants.update(arabic_icontains_variants(token.strip()))
    if len(folded) >= 5 and folded.isascii():
        variants.add(folded[:5])
        variants.add(folded[:6])
    alt = arabic_ta_marbuta_variant(folded)
    if alt != folded:
        variants.add(alt)

    latin = ARABIC_LATIN_TRANSLITERATIONS.get(token.strip()) or ARABIC_LATIN_TRANSLITERATIONS.get(folded)
    if latin:
        variants.add(fold_text(latin))
        variants.add(latin)

    concept_id = concept_for_token(folded)
    context = query_tokens or []
    has_packaging_hint = query_has_packaging_hint(context)

    if concept_id:
        group = next((g for g in CONCEPT_GROUPS if g['id'] == concept_id), None)
        kind = group.get('kind') if group else None

        if kind == 'dosage_form':
            if has_packaging_hint:
                for related_id in CONCEPT_RELATED.get(concept_id, PHARMA_PACKAGING_CONCEPTS):
                    for term in sorted(CONCEPT_TERMS.get(related_id, set()), key=len, reverse=True)[:8]:
                        variants.add(term)
                        variants.update(arabic_icontains_variants(term))
            return _cap_variants({item for item in variants if len(item) >= 2})

        for term in sorted(CONCEPT_TERMS.get(concept_id, set()), key=len, reverse=True):
            variants.add(term)
            variants.update(arabic_icontains_variants(term))
            if len(variants) >= MAX_DB_VARIANTS:
                break
        variants.update(_fuzzy_concept_terms(folded, concept_id))
    elif len(folded) >= 4:
        for concept_id, terms in CONCEPT_TERMS.items():
            variants.update(_fuzzy_concept_terms(folded, concept_id, max_items=2))
            if len(variants) >= MAX_DB_VARIANTS:
                break

    return _cap_variants({item for item in variants if len(item) >= 2})


def expand_token_variants(token: str, *, query_tokens: list[str] | None = None) -> set[str]:
    """Expand a token across taxonomy synonyms and inflections."""
    folded = fold_text(token)
    if not folded:
        return set()

    variants: set[str] = {folded}
    variants.update(french_plural_variants(folded))
    variants.update(arabic_article_variants(folded))
    alt = arabic_ta_marbuta_variant(folded)
    if alt != folded:
        variants.add(alt)

    latin = ARABIC_LATIN_TRANSLITERATIONS.get(token.strip()) or ARABIC_LATIN_TRANSLITERATIONS.get(folded)
    if latin:
        variants.add(fold_text(latin))

    concept_id = concept_for_token(folded)
    context = query_tokens or []
    has_packaging_hint = query_has_packaging_hint(context)

    if concept_id:
        group = next((g for g in CONCEPT_GROUPS if g['id'] == concept_id), None)
        kind = group.get('kind') if group else None

        if kind != 'dosage_form' or has_packaging_hint:
            variants.update(CONCEPT_TERMS.get(concept_id, set()))

        if kind == 'dosage_form' and has_packaging_hint:
            related_ids = CONCEPT_RELATED.get(concept_id, PHARMA_PACKAGING_CONCEPTS)
            for related_id in related_ids:
                variants.update(CONCEPT_TERMS.get(related_id, set()))

    if len(folded) >= 3:
        variants.add(folded[:3])
    if len(folded) >= 4:
        variants.add(folded[:4])

    return {item for item in variants if len(item) >= 2}


def build_synonym_groups() -> tuple[tuple[str, ...], ...]:
    """Legacy tuple groups for fuzzy synonym matching."""
    groups: list[tuple[str, ...]] = []
    for concept_id, terms in CONCEPT_TERMS.items():
        if len(terms) >= 2:
            groups.append(tuple(sorted(terms)))
    return tuple(groups)


def dosage_form_concepts() -> frozenset[str]:
    return frozenset(
        group['id'] for group in CONCEPT_GROUPS if group.get('kind') == 'dosage_form'
    )
