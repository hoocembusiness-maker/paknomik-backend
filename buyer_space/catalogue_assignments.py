"""Multi-affectation produits ↔ kits, issue de catalogue_multi_affectation.xlsx.

Les produits restent des LibraryProduct et les kits des SuiteEnsemble.
packaging_kits porte le dictionnaire (statut, composants, remarques).
product_kit_assignments porte le rôle Principal ou Alternatif.
product_kit_audits porte l'onglet Audit pertinence.

L'onglet Nouveaux produits est déjà repris dans Affectations.
L'onglet Anomalies décrit le fichier source déjà corrigé : il n'est pas chargé.
"""

from __future__ import annotations

from pathlib import Path

from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils.text import slugify

from buyer_space.catalogue_models import SuiteEnsemble
from buyer_space.models import (
    LibraryProduct,
    LibrarySector,
    PackagingKit,
    ProductKitAssignment,
    ProductKitAudit,
)
from buyer_space.product_library import prompt_for

ROLE_BY_LABEL = {
    "principal": ProductKitAssignment.Role.PRINCIPAL,
    "alternatif": ProductKitAssignment.Role.ALTERNATIVE,
}


def assignments_for_product(product_code: str):
    """Kits principal et alternatifs d'un produit, le principal en premier."""

    return (
        ProductKitAssignment.objects.filter(product__code=product_code)
        .select_related("product", "product__sector", "kit", "kit__packaging_kit")
        .order_by(
            Case(
                When(role=ProductKitAssignment.Role.PRINCIPAL, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
            "kit__code",
        )
    )


def products_by_sector_and_kit_status(sector: str, kit_status: str):
    """Produits d'un secteur liés à au moins un kit du statut demandé."""

    return (
        LibraryProduct.objects.filter(
            Q(sector__code__iexact=sector) | Q(sector__label_fr__iexact=sector),
            kit_assignments__kit__packaging_kit__status_label__iexact=kit_status,
        )
        .distinct()
        .select_related("sector", "recommended_suite")
        .order_by("name")
    )


def assignment_audits(decision: str = ""):
    """Journal des décisions de pertinence, filtrable par le libellé de décision."""

    rows = ProductKitAudit.objects.select_related("product", "product__sector")
    if decision:
        rows = rows.filter(decision__iexact=decision)
    return rows.order_by("product__code")


def import_catalogue_assignments(path: Path) -> dict[str, int]:
    """Recharge produits, dictionnaire, affectations et audits depuis le classeur."""

    import openpyxl

    workbook = openpyxl.load_workbook(Path(path), data_only=True)
    report = {
        "sectors_created": 0,
        "products_created": 0,
        "products_updated": 0,
        "kits_created": 0,
        "sheets": 0,
        "assignments": 0,
        "principals": 0,
        "alternatives": 0,
        "audits": 0,
    }
    with transaction.atomic():
        sectors = {
            slugify(item.label_fr): item
            for item in LibrarySector.objects.all()
        }
        for item in LibrarySector.objects.all():
            sectors.setdefault(item.code, item)
        products = _import_products(
            workbook["Affectations"], sectors, report
        )
        kits = _import_kits(workbook["Dictionnaire des kits"], report)
        _import_assignments(workbook["Affectations"], products, kits, report)
        _import_audits(workbook["Audit pertinence"], products, report)
        _sync_principal_kits(products)
        report["kits_published"] = publish_dictionary_kits()
    return report


def _import_products(worksheet, sectors: dict, report: dict) -> dict[str, LibraryProduct]:
    headers = _header_map(worksheet)
    code_i = _col(headers, "Code produit")
    name_i = _col(headers, "Nom produit")
    sector_i = _col(headers, "Secteur")
    description_i = _col(headers, "Description")
    active_i = _col(headers, "Actif (oui/non)")
    products: dict[str, LibraryProduct] = {}
    seen: set[str] = set()
    for row_number, row in enumerate(worksheet.iter_rows(min_row=2, values_only=True), start=2):
        code = _text(_cell(row, code_i))
        if not code or code in seen:
            continue
        seen.add(code)
        sector_label = _text(_cell(row, sector_i))
        sector = _sector_for(sector_label, sectors, report)
        name = _text(_cell(row, name_i))[:200]
        description = _text(_cell(row, description_i))
        is_active = _text(_cell(row, active_i)).casefold() == "oui"
        product = LibraryProduct.objects.filter(code=code).first()
        if product is None:
            product = LibraryProduct.objects.create(
                code=code,
                sector=sector,
                name=name or code,
                description=description,
                is_active=is_active,
                illustrative=True,
                suggested_filename=f"prod-{code}.jpg",
                generation_prompt=prompt_for(description or name or code),
            )
            report["products_created"] += 1
        else:
            product.sector = sector
            product.name = name or product.name
            product.description = description
            product.is_active = is_active
            product.save(
                update_fields=["sector", "name", "description", "is_active", "updated_at"]
            )
            report["products_updated"] += 1
        products[code] = product
    return products


def _import_kits(worksheet, report: dict) -> dict[str, SuiteEnsemble]:
    headers = _header_map(worksheet)
    code_i = _col(headers, "Code kit")
    name_i = _col(headers, "Nom kit")
    status_i = _col(headers, "Statut kit")
    components_i = _col(headers, "Composants")
    count_i = _col(headers, "Nombre de composants")
    remarks_i = _col(headers, "Remarques")
    kits: dict[str, SuiteEnsemble] = {}
    codes: list[str] = []
    for row in worksheet.iter_rows(min_row=2, values_only=True):
        code = _text(_cell(row, code_i))
        if not code:
            continue
        codes.append(code)
        components = _text(_cell(row, components_i))
        ensemble = _ensure_kit(
            code,
            _text(_cell(row, name_i)),
            report,
        )
        PackagingKit.objects.update_or_create(
            ensemble=ensemble,
            defaults={
                "status_label": _text(_cell(row, status_i))[:40],
                "components_label": components,
                "component_count": _component_count(_cell(row, count_i), components),
                "remarks": _text(_cell(row, remarks_i)),
            },
        )
        report["sheets"] += 1
        kits[code] = ensemble
    PackagingKit.objects.exclude(ensemble__code__in=codes).delete()
    return kits


def _import_assignments(worksheet, products, kits, report: dict) -> None:
    headers = _header_map(worksheet)
    product_i = _col(headers, "Code produit")
    kit_i = _col(headers, "Code kit affecté")
    role_i = _col(headers, "Rôle de l'affectation")
    rows = []
    seen_pairs: set[tuple[str, str]] = set()
    principals: set[str] = set()
    for row_number, row in enumerate(worksheet.iter_rows(min_row=2, values_only=True), start=2):
        product_code = _text(_cell(row, product_i))
        kit_code = _text(_cell(row, kit_i))
        if not product_code and not kit_code:
            continue
        role_label = _text(_cell(row, role_i)).casefold()
        role = ROLE_BY_LABEL.get(role_label)
        if role is None:
            raise ValueError(f"Ligne {row_number} : rôle inconnu « {_text(_cell(row, role_i))} ».")
        product = products.get(product_code)
        kit = kits.get(kit_code)
        if product is None or kit is None:
            raise ValueError(
                f"Ligne {row_number} : produit {product_code} ou kit {kit_code} introuvable."
            )
        pair = (product_code, kit_code)
        if pair in seen_pairs:
            raise ValueError(f"Ligne {row_number} : doublon {product_code} / {kit_code}.")
        seen_pairs.add(pair)
        if role == ProductKitAssignment.Role.PRINCIPAL:
            if product_code in principals:
                raise ValueError(f"Ligne {row_number} : deux kits principaux pour {product_code}.")
            principals.add(product_code)
            report["principals"] += 1
        else:
            report["alternatives"] += 1
        rows.append(ProductKitAssignment(product=product, kit=kit, role=role))
    missing = sorted(set(products) - principals)
    if missing:
        raise ValueError("Produit sans kit principal : " + ", ".join(missing[:8]))
    ProductKitAssignment.objects.all().delete()
    ProductKitAssignment.objects.bulk_create(rows)
    report["assignments"] = len(rows)


def _import_audits(worksheet, products, report: dict) -> None:
    headers = _header_map(worksheet)
    code_i = _col(headers, "Code produit")
    original_i = _col(headers, "Kit principal d'origine")
    original_alts_i = _col(headers, "Kits alternatifs d'origine")
    decision_i = _col(headers, "Décision")
    retained_i = _col(headers, "Kit principal retenu")
    retained_alts_i = _col(headers, "Alternatifs retenus")
    justification_i = _col(headers, "Justification technique / industrielle / marketing")
    rows = []
    seen: set[str] = set()
    for row_number, row in enumerate(worksheet.iter_rows(min_row=2, values_only=True), start=2):
        code = _text(_cell(row, code_i))
        if not code:
            continue
        if code in seen:
            raise ValueError(f"Audit ligne {row_number} : produit {code} en double.")
        seen.add(code)
        product = products.get(code)
        if product is None:
            raise ValueError(f"Audit ligne {row_number} : produit {code} absent des affectations.")
        rows.append(
            ProductKitAudit(
                product=product,
                original_principal_code=_text(_cell(row, original_i))[:40],
                original_alternatives=_text(_cell(row, original_alts_i)),
                decision=_text(_cell(row, decision_i))[:40],
                retained_principal_code=_text(_cell(row, retained_i))[:40],
                retained_alternatives=_text(_cell(row, retained_alts_i)),
                justification=_text(_cell(row, justification_i)),
            )
        )
    ProductKitAudit.objects.all().delete()
    ProductKitAudit.objects.bulk_create(rows)
    report["audits"] = len(rows)


def _sync_principal_kits(products: dict[str, LibraryProduct]) -> None:
    principals = {
        item.product_id: item.kit_id
        for item in ProductKitAssignment.objects.filter(
            role=ProductKitAssignment.Role.PRINCIPAL,
            product__in=products.values(),
        )
    }
    for product in products.values():
        kit_id = principals.get(product.id)
        if product.recommended_suite_id != kit_id:
            product.recommended_suite_id = kit_id
            product.save(update_fields=["recommended_suite", "updated_at"])


def publish_dictionary_kits() -> int:
    """Rend visibles les kits du dictionnaire. Le catalogue public ignore les brouillons."""

    published = 0
    sheets = PackagingKit.objects.select_related("ensemble")
    for sheet in sheets:
        ensemble = sheet.ensemble
        fields = []
        if not (ensemble.description or "").strip():
            ensemble.description = (sheet.components_label or ensemble.name)[:2000]
            fields.append("description")
        if ensemble.editorial_status != SuiteEnsemble.Status.PUBLISHED:
            ensemble.editorial_status = SuiteEnsemble.Status.PUBLISHED
            fields.append("editorial_status")
        if not fields:
            continue
        fields.append("updated_at")
        ensemble.save(update_fields=fields)
        published += 1
    return published


def _ensure_kit(code: str, name: str, report: dict) -> SuiteEnsemble:
    ensemble = SuiteEnsemble.objects.filter(code=code).first()
    if ensemble is not None:
        return ensemble
    slug = slugify(code)[:80] or "kit"
    if SuiteEnsemble.objects.filter(slug=slug).exists():
        slug = slugify(f"{code}-kit")[:80]
    ensemble = SuiteEnsemble.objects.create(
        code=code[:40],
        name=(name or code)[:240],
        slug=slug,
        editorial_status=SuiteEnsemble.Status.PUBLISHED,
        internal_note="Créé depuis catalogue_multi_affectation.xlsx",
    )
    report["kits_created"] += 1
    return ensemble


def _sector_for(label: str, sectors: dict, report: dict) -> LibrarySector:
    key = slugify(label)[:80] or "secteur"
    sector = sectors.get(key)
    if sector is not None:
        return sector
    order = (
        LibrarySector.objects.order_by("-sort_order")
        .values_list("sort_order", flat=True)
        .first()
    )
    sector = LibrarySector.objects.create(
        code=key,
        label_fr=label[:120] or key,
        sort_order=(order or 0) + 1,
    )
    sectors[key] = sector
    report["sectors_created"] += 1
    return sector


def _header_map(worksheet) -> dict[str, int]:
    row = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True))
    return {_text(cell): index for index, cell in enumerate(row) if _text(cell)}


def _col(headers: dict[str, int], label: str) -> int:
    if label in headers:
        return headers[label]
    folded = label.casefold()
    for key, index in headers.items():
        if key.casefold().startswith(folded):
            return index
    raise KeyError(label)


def _cell(row, index):
    if index is None or index >= len(row):
        return None
    return row[index]


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _component_count(value, components: str) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    text = _text(value)
    if text.isdigit():
        return int(text)
    return len([part for part in components.split("|") if part.strip()])
