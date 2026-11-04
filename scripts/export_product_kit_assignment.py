"""Export the current library product → PakSuit kit assignment."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "paknomik.settings")
django.setup()

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from buyer_space.models import LibraryProduct
from buyer_space.product_library import RECOMMENDED_SUITE_BY_CODE

OUT = ROOT / "docs" / "affectation_produits_kits.xlsx"


def main():
    products = (
        LibraryProduct.objects.select_related("sector", "recommended_suite")
        .prefetch_related("recommended_suite__lines__definition")
        .order_by("sector__sort_order", "sort_order", "name")
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Affectation"
    headers = [
        "Code produit",
        "Nom produit",
        "Secteur",
        "Description",
        "Actif",
        "Code kit",
        "Nom kit",
        "Statut kit",
        "Composants du kit",
        "Code kit prévu (seed)",
        "Écart seed / base",
    ]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for product in products:
        suite = product.recommended_suite
        components = ""
        status = ""
        suite_code = ""
        suite_name = ""
        if suite is not None:
            suite_code = suite.code
            suite_name = suite.name
            status = suite.get_editorial_status_display()
            components = " | ".join(
                line.definition.label
                for line in suite.lines.all()
                if line.definition_id
            )
        seed_code = RECOMMENDED_SUITE_BY_CODE.get(product.code, "")
        sheet.append([
            product.code,
            product.name,
            product.sector.label_fr,
            product.description,
            "oui" if product.is_active else "non",
            suite_code,
            suite_name,
            status,
            components,
            seed_code,
            "oui" if seed_code != suite_code else "non",
        ])

    for index, header in enumerate(headers, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = min(max(len(header) + 4, 18), 60)
    sheet.auto_filter.ref = sheet.dimensions
    sheet.freeze_panes = "A2"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(OUT)
    print(f"{products.count()} produits -> {OUT}")


if __name__ == "__main__":
    main()
