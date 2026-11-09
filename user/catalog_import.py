"""Excel template and variant import for one product type."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.db import transaction
from openpyxl import Workbook, load_workbook
from rest_framework.exceptions import ValidationError

from user.catalog_products import save_gamme
from user.catalog_taxonomy import OFFER_STOCK, OFFER_SUR_MESURE
from user.entitlements import QuotaExceeded, assert_can_add_product
from user.models import ProductType


def template_bytes(product_type: ProductType) -> bytes:
    book = Workbook()
    gammes = book.active
    gammes.title = "Gammes"
    headers = ["nom", "description", "mode", "mixte", "phare"]
    headers.extend(item["key"] for item in product_type.attribute_schema or [])
    gammes.append(headers)
    variants = book.create_sheet("Variantes")
    variants.append(
        [
            "nom_gamme",
            "sku",
            "axe_1",
            "valeur_1",
            "axe_2",
            "valeur_2",
            "axe_3",
            "valeur_3",
            "prix_depart",
            "moq",
            "disponible",
        ]
    )
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _yes(value) -> bool:
    return str(value or "").strip().lower() in {"1", "oui", "yes", "true", "vrai"}


def _cell(row, index):
    if index >= len(row):
        return None
    return row[index]


def import_workbook(*, supplier, product_type: ProductType, upload, actor) -> dict:
    try:
        book = load_workbook(upload, data_only=True)
    except Exception as exc:
        raise ValidationError({"file": "Fichier Excel illisible."}) from exc
    if "Gammes" not in book.sheetnames or "Variantes" not in book.sheetnames:
        raise ValidationError(
            {"file": "Le classeur doit contenir les feuilles Gammes et Variantes."}
        )

    gamme_sheet = book["Gammes"]
    header = [str(cell.value or "").strip() for cell in next(gamme_sheet.iter_rows(max_row=1))]
    index = {name: position for position, name in enumerate(header)}
    for required in ("nom", "description", "mode"):
        if required not in index:
            raise ValidationError({"file": f"Colonne manquante : {required}."})

    grouped: dict[str, list] = {}
    variant_sheet = book["Variantes"]
    for row in variant_sheet.iter_rows(min_row=2, values_only=True):
        name = str(_cell(row, 0) or "").strip()
        if not name:
            continue
        axes = {}
        for key_at, value_at in ((2, 3), (4, 5), (6, 7)):
            axis = str(_cell(row, key_at) or "").strip()
            value = _cell(row, value_at)
            if axis and value not in (None, ""):
                axes[axis] = value if not _is_int_like(value) else int(value)
        price = _cell(row, 8)
        moq = _cell(row, 9)
        grouped.setdefault(name, []).append(
            {
                "sku": str(_cell(row, 1) or ""),
                "axes": axes,
                "priceFrom": _decimal(price),
                "moq": int(moq) if str(moq or "").strip() else None,
                "available": _yes(_cell(row, 10) if _cell(row, 10) is not None else "oui"),
            }
        )

    schema_keys = [item["key"] for item in product_type.attribute_schema or []]
    created = 0
    created_ids = []
    errors = []
    with transaction.atomic():
        for offset, row in enumerate(gamme_sheet.iter_rows(min_row=2, values_only=True), start=2):
            name = str(_cell(row, index["nom"]) or "").strip()
            if not name:
                continue
            mode = str(_cell(row, index["mode"]) or OFFER_STOCK).strip().upper()
            if mode not in {OFFER_STOCK, OFFER_SUR_MESURE}:
                errors.append({"row": offset, "message": "Mode inconnu."})
                continue
            attributes = {}
            for key in schema_keys:
                if key not in index:
                    continue
                value = _cell(row, index[key])
                if value not in (None, ""):
                    attributes[key] = _coerce(value)
            payload = {
                "productTypeId": product_type.pk,
                "name": name,
                "description": str(_cell(row, index["description"]) or ""),
                "offerMode": mode,
                "mixed": _yes(_cell(row, index["mixte"])) if "mixte" in index else False,
                "flagship": _yes(_cell(row, index["phare"])) if "phare" in index else False,
                "attributes": attributes,
                "variants": grouped.get(name, []),
                "publish": True,
            }
            try:
                assert_can_add_product(supplier)
                product = save_gamme(
                    supplier=supplier,
                    payload=payload,
                    files={},
                    actor=actor,
                )
            except ValidationError as exc:
                errors.append({"row": offset, "message": exc.detail})
                continue
            except QuotaExceeded as exc:
                errors.append({"row": offset, "message": exc.detail})
                continue
            created += 1
            created_ids.append(product.pk)
        if errors:
            transaction.set_rollback(True)
            created = 0
            created_ids = []
    return {"created": created, "errors": errors, "ids": created_ids}


def _is_int_like(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    text = str(value).strip()
    return text.isdigit()


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return str(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return value


def _coerce(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.lower() in {"oui", "true", "vrai"}:
        return True
    if text.lower() in {"non", "false", "faux"}:
        return False
    if "," in text and not any(char.isalpha() for char in text):
        return [part.strip() for part in text.split(",") if part.strip()]
    return text
