from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import TestCase

from buyer_space.catalogue_assignments import (
    assignment_audits,
    assignments_for_product,
    import_catalogue_assignments,
    products_by_sector_and_kit_status,
)
from buyer_space.catalogue_models import SuiteEnsemble
from buyer_space.models import LibraryProduct, ProductKitAssignment


WORKBOOK = Path(settings.BASE_DIR) / "docs" / "catalogue_multi_affectation.xlsx"


class CatalogueAssignmentTests(TestCase):
    def test_workbook_loads_one_principal_and_keeps_the_audit(self):
        report = import_catalogue_assignments(WORKBOOK)
        self.assertEqual(report["assignments"], 432)
        self.assertEqual(report["principals"], 210)
        self.assertEqual(report["alternatives"], 222)
        self.assertEqual(report["audits"], 91)
        self.assertGreater(report["sheets"], 0)

        again = import_catalogue_assignments(WORKBOOK)
        self.assertEqual(again["assignments"], 432)
        self.assertEqual(ProductKitAssignment.objects.count(), 432)

        links = list(assignments_for_product("huile-alimentaire"))
        self.assertEqual([item.role for item in links], ["Principal", "Alternatif", "Alternatif"])
        self.assertEqual(
            [item.kit.code for item in links],
            ["PKS-BEV-004", "PKS-BEV-001", "PKS-IND-003"],
        )
        oil = LibraryProduct.objects.get(code="huile-alimentaire")
        self.assertEqual(oil.recommended_suite.code, "PKS-BEV-004")
        self.assertEqual(links[0].kit.packaging_kit.component_count, 3)

        published = products_by_sector_and_kit_status("Agroalimentaire", "Publié")
        self.assertTrue(published.filter(code="huile-alimentaire").exists())

        audit = assignment_audits("Corrigé").get(product__code="huile-alimentaire")
        self.assertEqual(audit.original_principal_code, "PKS-BEV-001")
        self.assertEqual(audit.retained_principal_code, "PKS-BEV-004")
        self.assertIn("verseur", audit.justification)

    def test_a_second_principal_or_duplicate_pair_is_rejected(self):
        import_catalogue_assignments(WORKBOOK)
        oil = LibraryProduct.objects.get(code="huile-alimentaire")
        other = SuiteEnsemble.objects.exclude(
            product_assignments__product=oil
        ).first()
        self.assertIsNotNone(other)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductKitAssignment.objects.create(
                    product=oil,
                    kit=other,
                    role=ProductKitAssignment.Role.PRINCIPAL,
                )
        current = oil.kit_assignments.filter(role=ProductKitAssignment.Role.PRINCIPAL).get()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductKitAssignment.objects.create(
                    product=oil,
                    kit=current.kit,
                    role=ProductKitAssignment.Role.ALTERNATIVE,
                )
