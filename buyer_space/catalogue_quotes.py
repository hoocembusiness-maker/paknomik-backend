"""Devis rapide PakSuites : une demande par composant, sans formulaire."""

from __future__ import annotations

import re
import unicodedata

from django.db import transaction
from django.db.models import Q

from buyer_space.catalogue_models import SuiteComponentLine
from user.models import Category, QuoteDemand, QuoteDemandSelection, Supplier
from user.quote_market import initial_market_fields


class QuickQuoteError(Exception):
    def __init__(self, message: str, code: str = "quick_quote_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(stripped.casefold().split())


def _family_label(label: str) -> str:
    """Retire le préfixe « C. » / « F-A » des libellés de famille PakSuites."""
    cleaned = re.sub(r"^[A-Z]-?[A-Z]?\.\s*", "", (label or "").strip())
    cleaned = re.sub(r"^[A-Z]\.\s*", "", cleaned)
    return cleaned.strip() or (label or "").strip()


def resolve_marketplace_category(line: SuiteComponentLine) -> Category | None:
    """Sous-catégorie marché liée au composant, ou rapprochement par libellé."""
    taxon = line.taxon
    if taxon is not None and taxon.marketplace_category_id:
        category = taxon.marketplace_category
        if category is not None and category.parent_id:
            return category

    labels: list[str] = []
    if taxon is not None and taxon.label_fr:
        labels.append(taxon.label_fr)
    if line.definition_id and line.definition.label:
        labels.append(line.definition.label)
    for label in labels:
        folded = _fold(label)
        for candidate in Category.objects.filter(parent__isnull=False).select_related("parent"):
            if _fold(candidate.name_fr) == folded or _fold(candidate.name_en) == folded:
                if taxon is not None and taxon.marketplace_category_id != candidate.id:
                    taxon.marketplace_category = candidate
                    taxon.save(update_fields=["marketplace_category"])
                return candidate

    family_label = ""
    if taxon is not None and taxon.parent_id:
        family_label = _family_label(taxon.parent.label_fr)
    if family_label:
        parent = (
            Category.objects.filter(parent__isnull=True)
            .filter(Q(name_fr__iexact=family_label) | Q(name_en__iexact=family_label))
            .first()
        )
        if parent is None:
            for candidate in Category.objects.filter(parent__isnull=True):
                if _fold(candidate.name_fr) == _fold(family_label):
                    parent = candidate
                    break
        if parent is not None:
            child = parent.children.order_by("id").first()
            if child is not None:
                return child
    return None


def _suppliers_for_line(line: SuiteComponentLine, subcategory: Category) -> list[Supplier]:
    offered = [
        offer.supplier
        for offer in line.offers.select_related("supplier").all()
        if offer.supplier.is_active and not offer.supplier.is_ghost
    ]
    if offered:
        return list({supplier.id: supplier for supplier in offered}.values())

    filters = Q(is_active=True, is_ghost=False) & (
        Q(interested_subcategories=subcategory)
        | Q(products__subcategory=subcategory, products__is_active=True)
        | Q(products__category=subcategory, products__is_active=True)
        | Q(interested_categories=subcategory.parent_id)
        | Q(interested_subcategories__parent_id=subcategory.parent_id)
    )
    return list(
        Supplier.objects.filter(filters)
        .distinct()
        .order_by("company_name")[:8]
    )


def _description(line: SuiteComponentLine) -> str:
    parts = [
        f"PakSuites {line.ensemble.code} — {line.ensemble.name}",
        f"Composant : {line.definition.label} ({line.role})",
    ]
    if line.material:
        parts.append(f"Matériau : {line.material}")
    if line.technical_notes:
        parts.append(line.technical_notes)
    return "\n".join(parts)


@transaction.atomic
def quick_quotes_for_lines(
    buyer,
    lines: list[SuiteComponentLine],
    *,
    quantity: int = 1,
) -> dict:
    """Crée une demande de devis par ligne sélectionnée, sans saisie acheteur."""
    from user.api.quote_demand_views import (
        _log_quote_created,
        _notify_selected_suppliers,
        _public_cooldown_payload,
    )

    if not lines:
        raise QuickQuoteError("Sélectionnez au moins un composant.", "no_lines")
    if quantity < 1:
        raise QuickQuoteError("La quantité doit être au moins 1.", "invalid_quantity")

    created_ids: list[int] = []
    reused_ids: list[int] = []
    skipped: list[dict] = []
    public_blocked = None

    for line in lines:
        existing = (
            QuoteDemand.objects.select_for_update()
            .filter(buyer=buyer, catalogue_line=line, status=QuoteDemand.Status.OPEN)
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            reused_ids.append(existing.id)
            continue

        subcategory = resolve_marketplace_category(line)
        if subcategory is None or subcategory.parent_id is None:
            skipped.append(
                {
                    "lineId": line.id,
                    "label": line.definition.label,
                    "code": "category_unmapped",
                    "error": "Composant non relié au marché.",
                }
            )
            continue

        parent = subcategory.parent
        suppliers = _suppliers_for_line(line, subcategory)
        if suppliers:
            demand = QuoteDemand.objects.create(
                buyer=buyer,
                kind=QuoteDemand.Kind.PRIVATE,
                category=parent,
                subcategory=subcategory,
                quantity=quantity,
                description=_description(line),
                wilaya=(buyer.wilaya or "").strip(),
                catalogue_line=line,
                **initial_market_fields(kind=QuoteDemand.Kind.PRIVATE, has_selections=True),
            )
            for supplier in suppliers:
                QuoteDemandSelection.objects.create(demand=demand, supplier=supplier)
            _notify_selected_suppliers(
                demand,
                suppliers,
                origin="paksuites",
                origin_label=line.ensemble.name,
            )
            _log_quote_created(demand, [supplier.id for supplier in suppliers])
            created_ids.append(demand.id)
            continue

        if public_blocked is not None:
            skipped.append(
                {
                    "lineId": line.id,
                    "label": line.definition.label,
                    "code": "public_cooldown",
                    "error": public_blocked["error"],
                }
            )
            continue

        cooldown = _public_cooldown_payload(buyer)
        if cooldown is not None:
            public_blocked = cooldown
            skipped.append(
                {
                    "lineId": line.id,
                    "label": line.definition.label,
                    "code": "public_cooldown",
                    "error": cooldown["error"],
                }
            )
            continue

        demand = QuoteDemand.objects.create(
            buyer=buyer,
            kind=QuoteDemand.Kind.PUBLIC,
            category=parent,
            subcategory=subcategory,
            quantity=quantity,
            description=_description(line),
            wilaya=(buyer.wilaya or "").strip(),
            catalogue_line=line,
            **initial_market_fields(kind=QuoteDemand.Kind.PUBLIC, has_selections=False),
        )
        _log_quote_created(demand, [])
        created_ids.append(demand.id)
        # Une seule publique par clic (cooldown) : les suivantes sans fournisseur sont reportées.
        public_blocked = {
            "error": "Une demande publique a déjà été créée pour ce clic. "
            "Les autres composants sans fournisseur pourront être publiés plus tard.",
            "code": "public_limit_per_click",
        }

    if not created_ids and not reused_ids:
        if public_blocked and public_blocked.get("code") == "public_cooldown":
            raise QuickQuoteError(public_blocked["error"], "public_cooldown")
        if skipped and all(item["code"] == "category_unmapped" for item in skipped):
            raise QuickQuoteError(
                "Aucun composant n'est encore relié au marché pour un devis.",
                "category_unmapped",
            )
        raise QuickQuoteError(
            "Aucune demande de devis n'a pu être créée.",
            "none_created",
        )

    return {
        "quoteDemandIds": created_ids + reused_ids,
        "createdCount": len(created_ids),
        "reusedCount": len(reused_ids),
        "skipped": skipped,
    }
