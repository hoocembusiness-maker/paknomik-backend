"""Assemble products + packaging articles + PakSuits kits into a project."""

from __future__ import annotations

from django.db import transaction

from buyer_space.catalogue_models import SuiteEnsemble, SuiteTaxon
from buyer_space.models import (
    BuyerProduct,
    LibraryProduct,
    PackagingItem,
    PackagingProject,
    ProjectProductLink,
    ProjectProductVariety,
    ProjectSuiteKit,
)


@transaction.atomic
def assemble_project(
    *,
    project: PackagingProject,
    account,
    library_product_ids: list[int] | None = None,
    packaging_type_codes: list[str] | None = None,
    suite_codes: list[str] | None = None,
    varieties: list[dict] | None = None,
    target_quantity: int | None = None,
) -> PackagingProject:
    if target_quantity is not None:
        project.target_quantity = target_quantity
        project.save(update_fields=["target_quantity", "updated_at"])

    # Un projet ne porte qu’un produit de bibliothèque et qu’un kit.
    incoming_products = [] if ProjectProductLink.objects.filter(project=project).exists() else list(library_product_ids or [])[:1]
    for lib_id in incoming_products:
        library = LibraryProduct.objects.filter(pk=lib_id, is_active=True).first()
        if library is None:
            continue
        if ProjectProductLink.objects.filter(
            project=project, library_product=library
        ).exists():
            continue
        buyer_product = BuyerProduct.objects.create(
            account=account,
            name=library.name,
            category_label=library.sector.label_fr,
            attributes={
                "libraryProductId": library.id,
                "libraryCode": library.code,
            },
        )
        link = ProjectProductLink.objects.create(
            project=project,
            library_product=library,
            buyer_product=buyer_product,
            name=library.name,
        )
        _replace_varieties(link, varieties or [])
        if project.buyer_product_id is None:
            project.buyer_product = buyer_product
            project.save(update_fields=["buyer_product", "updated_at"])

    for type_code in packaging_type_codes or []:
        taxon = SuiteTaxon.objects.filter(
            kind=SuiteTaxon.Kind.TYPE, code=type_code
        ).first()
        if taxon is None:
            continue
        if PackagingItem.objects.filter(
            project=project,
            packaging_taxon=taxon,
            source_kind=PackagingItem.SourceKind.ARTICLE,
        ).exists():
            continue
        PackagingItem.objects.create(
            project=project,
            name=taxon.label_fr,
            packaging_type=taxon.label_fr,
            source_kind=PackagingItem.SourceKind.ARTICLE,
            packaging_taxon=taxon,
            target_quantity=project.target_quantity,
        )

    incoming_suites = [] if ProjectSuiteKit.objects.filter(project=project).exists() else list(suite_codes or [])[:1]
    for suite_code in incoming_suites:
        ensemble = SuiteEnsemble.objects.filter(
            code=suite_code, editorial_status=SuiteEnsemble.Status.PUBLISHED
        ).first()
        if ensemble is None:
            continue
        kit, created = ProjectSuiteKit.objects.get_or_create(
            project=project, ensemble=ensemble
        )
        if not created:
            continue
        for line in ensemble.lines.select_related("definition", "taxon").all():
            label = line.definition.label if line.definition_id else line.role
            PackagingItem.objects.create(
                project=project,
                name=label,
                packaging_type=line.taxon.label_fr if line.taxon_id else label,
                source_kind=PackagingItem.SourceKind.SUITE_KIT,
                packaging_taxon=line.taxon,
                suite_kit=kit,
                material=line.material,
                notes=f"Kit {ensemble.code}",
                target_quantity=project.target_quantity,
            )

    if varieties is not None and not incoming_products:
        existing = ProjectProductLink.objects.filter(project=project).order_by("id").first()
        if existing is not None:
            _replace_varieties(existing, varieties)

    return project


def _replace_varieties(link: ProjectProductLink, varieties: list[dict]) -> None:
    link.varieties.all().delete()
    ProjectProductVariety.objects.bulk_create([
        ProjectProductVariety(
            link=link,
            name=item["name"],
            label_text=item.get("label_text") or "",
            cap_color=item.get("cap_color") or "",
            sort_order=index,
        )
        for index, item in enumerate(varieties)
        if str(item.get("name") or "").strip()
    ])
