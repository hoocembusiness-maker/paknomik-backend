"""Create one existing private quote demand per PakSuites component."""

from __future__ import annotations

from django.db import transaction

from buyer_space.models import PaksuitesOption


class QuoteGenerationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _supplier_can_receive(supplier) -> bool:
    subcategory = supplier.interested_subcategories.select_related("parent").first()
    if subcategory is not None and subcategory.parent_id:
        return True
    category = supplier.interested_categories.first()
    if category is None:
        return False
    return category.children.exists()


@transaction.atomic
def generate_quotes_for_option(option: PaksuitesOption, buyer):
    from user.api.quote_demand_views import create_private_demand_from_rfq
    from user.models import QuoteDemand

    components = list(
        option.components.select_related("suggested_supplier").order_by("sort_order", "id")
    )
    if not components:
        raise QuoteGenerationError("Cette option n'a aucun composant.")

    missing = [
        component.packaging_type
        for component in components
        if component.suggested_supplier_id is None
    ]
    if missing:
        raise QuoteGenerationError(
            "Chaque composant doit avoir un fournisseur suggéré avant les devis : "
            + ", ".join(missing)
            + "."
        )
    unready = [
        component.suggested_supplier.company_name
        for component in components
        if not _supplier_can_receive(component.suggested_supplier)
    ]
    if unready:
        raise QuoteGenerationError(
            "Ces fournisseurs n'ont pas de catégorie pour recevoir un devis : "
            + ", ".join(unready)
            + "."
        )

    request = option.service_request
    created = []
    for component in components:
        existing = (
            QuoteDemand.objects.select_for_update()
            .filter(paksuites_component=component)
            .first()
        )
        if existing is not None:
            created.append(existing)
            continue
        description = "\n".join(
            part
            for part in (
                f"PakSuites — {option.title}",
                component.packaging_type,
                component.description.strip(),
                component.specification.strip(),
            )
            if part
        )
        quantity = 1
        if request.packaging_item_id and request.packaging_item.target_quantity:
            quantity = request.packaging_item.target_quantity
        demand = create_private_demand_from_rfq(
            buyer,
            component.suggested_supplier,
            None,
            description,
            quantity,
            origin="paksuites",
            origin_label=option.title,
        )
        if demand is None:
            raise QuoteGenerationError(
                f"Le devis du composant « {component.packaging_type} » n'a pas pu être créé."
            )
        demand.packaging_project = request.project
        demand.packaging_item = request.packaging_item
        demand.service_request = request
        demand.paksuites_component = component
        demand.save(
            update_fields=[
                "packaging_project",
                "packaging_item",
                "service_request",
                "paksuites_component",
                "updated_at",
            ]
        )
        created.append(demand)
    return created
