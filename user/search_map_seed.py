"""
Idempotent seed for hierarchical SearchTerm / SearchTermMapping (Bobines + ambigus).
"""

from __future__ import annotations

from django.db import transaction

from user.models import Category, SearchTerm, SearchTermMapping
from user.search_term_normalize import normalize_search_term

AMBIGUOUS_NORMALIZED = {
    normalize_search_term(w)
    for w in (
        "film",
        "rouleau",
        "bobine",
        "plastique",
        "emballage",
        "papier",
        "alu",
        "aluminium",
        "transparent",
        "alimentaire",
    )
}


def _get_bobines_tree():
    parent = Category.objects.filter(slug="bobines", parent__isnull=True).first()
    if parent is None:
        return None, {}
    subs = {
        s.slug: s
        for s in Category.objects.filter(parent=parent).only(
            "id", "slug", "name_en", "name_fr", "parent_id"
        )
    }
    return parent, subs


def _upsert_term(
    term: str,
    *,
    language: str,
    term_type: str,
    is_ambiguous: bool,
    priority: int = 60,
    weight: int = 60,
) -> SearchTerm:
    normalized = normalize_search_term(term)
    obj, _ = SearchTerm.objects.update_or_create(
        normalized_term=normalized,
        language=language,
        term_type=term_type,
        defaults={
            "term": term.strip(),
            "is_active": True,
            "is_ambiguous": is_ambiguous or normalized in AMBIGUOUS_NORMALIZED,
            "priority": priority,
            "weight": weight,
        },
    )
    return obj


def _map(
    term_obj: SearchTerm,
    *,
    parent,
    sub,
    confidence: int = 85,
    priority: int = 60,
    context: str = "",
    attribute_value: str = "",
):
    SearchTermMapping.objects.update_or_create(
        search_term=term_obj,
        category=parent,
        subcategory=sub,
        attribute=None,
        attribute_value=attribute_value or "",
        defaults={
            "confidence": confidence,
            "priority": priority,
            "context": context,
            "is_active": True,
        },
    )


@transaction.atomic
def seed_bobines_search_map() -> dict:
    parent, subs = _get_bobines_tree()
    if parent is None or not subs:
        return {"skipped": True, "reason": "bobines catalog missing"}

    stats = {"terms": 0, "mappings": 0}

    def add(term, lang, ttype, sub_slug, *, conf=88, pri=65, ctx="", ambiguous=False):
        sub = subs.get(sub_slug)
        if sub is None:
            return
        stats["terms"] += 1
        obj = _upsert_term(
            term,
            language=lang,
            term_type=ttype,
            is_ambiguous=ambiguous,
            priority=pri,
            weight=pri,
        )
        _map(obj, parent=parent, sub=sub, confidence=conf, priority=pri, context=ctx)
        stats["mappings"] += 1

    # Bobine de papier
    for t, lang, tt in (
        ("bobine papier", "fr", SearchTerm.TermType.CANONICAL),
        ("bobine de papier", "fr", SearchTerm.TermType.SYNONYM),
        ("rouleau papier", "fr", SearchTerm.TermType.SYNONYM),
        ("papier kraft en bobine", "fr", SearchTerm.TermType.TECHNICAL),
        ("kraftliner", "en", SearchTerm.TermType.TECHNICAL),
        ("paper roll", "en", SearchTerm.TermType.TRANSLATION),
        ("kraft paper roll", "en", SearchTerm.TermType.TECHNICAL),
        ("رولو ورق", "ar", SearchTerm.TermType.TRANSLATION),
        ("ورق كرافت", "ar", SearchTerm.TermType.TRANSLATION),
    ):
        add(t, lang, tt, "bobine_papier", ctx="bobines papier")

    add("papier kraft", "fr", SearchTerm.TermType.TECHNICAL, "bobine_papier", ctx="kraft")

    # Aluminium
    for t, lang in (
        ("bobine aluminium", "fr"),
        ("rouleau aluminium", "fr"),
        ("feuille aluminium en bobine", "fr"),
        ("aluminium foil roll", "en"),
        ("رولو ألمنيوم", "ar"),
    ):
        add(t, lang, SearchTerm.TermType.SYNONYM, "bobine_aluminium", ctx="bobines alu")

    # PVC
    for t, lang in (
        ("bobine pvc", "fr"),
        ("film pvc", "fr"),
        ("pvc thermoretractable", "fr"),
        ("pvc roll", "en"),
        ("فيلم pvc", "ar"),
    ):
        add(t, lang, SearchTerm.TermType.SYNONYM, "bobine_pvc", ctx="pvc")

    add(
        "film pvc thermoretractable",
        "fr",
        SearchTerm.TermType.TECHNICAL,
        "bobine_pvc",
        conf=92,
        pri=75,
    )

    # Film plastique
    for t, lang in (
        ("bobine film plastique", "fr"),
        ("film pe", "fr"),
        ("film pet", "fr"),
        ("film bopp", "en"),
        ("plastic film roll", "en"),
        ("stretch film", "en"),
        ("فيلم بلاستيكي", "ar"),
    ):
        add(t, lang, SearchTerm.TermType.SYNONYM, "bobine_film_plastique", ctx="film plastique")

    add(
        "film alimentaire",
        "fr",
        SearchTerm.TermType.COMMERCIAL,
        "bobine_film_plastique",
        conf=75,
        pri=55,
        ctx="alimentaire",
    )

    # Laminé / composite
    for t, lang in (
        ("bobine laminee", "fr"),
        ("film lamine", "fr"),
        ("pet pe", "en"),
        ("pet aluminium pe", "en"),
        ("multilayer film", "en"),
        ("فيلم مركب", "ar"),
    ):
        add(
            t,
            lang,
            SearchTerm.TermType.TECHNICAL,
            "bobine_laminee_composite",
            ctx="composite",
        )

    # Catégorie parent seule (termes ambigus)
    for word in ("bobine", "bobines", "rouleau", "rouleaux", "roll", "rolls", "بوبينة", "لفافة"):
        stats["terms"] += 1
        obj = _upsert_term(
            word,
            language="fr" if word.isascii() is False or "bobine" in word else "en",
            term_type=SearchTerm.TermType.CANONICAL,
            is_ambiguous=True,
            priority=40,
            weight=40,
        )
        SearchTermMapping.objects.update_or_create(
            search_term=obj,
            category=parent,
            subcategory=None,
            attribute=None,
            attribute_value="",
            defaults={
                "confidence": 55,
                "priority": 40,
                "context": "bobines generique",
                "is_active": True,
            },
        )
        stats["mappings"] += 1

    # Film seul → catégorie bobines + plusieurs sous-catégories possibles
    film = _upsert_term(
        "film",
        language="fr",
        term_type=SearchTerm.TermType.CANONICAL,
        is_ambiguous=True,
        priority=35,
        weight=35,
    )
    stats["terms"] += 1
    for slug, conf in (
        ("bobine_film_plastique", 60),
        ("bobine_pvc", 55),
        ("bobine_laminee_composite", 50),
    ):
        sub = subs.get(slug)
        if sub:
            _map(film, parent=parent, sub=sub, confidence=conf, priority=40, context="film ambigu")
            stats["mappings"] += 1

    return stats
