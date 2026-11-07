"""Onboard a supplier from name, email, and a PDF / JSON listing."""

import io
import json
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from user.entitlements import seed_entitlements
from user.models import Category, Product, Supplier, User
from user.pragmadinar import seed_pragmadinar_catalog
from user.supplier_onboarding import (
    OnboardError,
    extract_pdf_text,
    match_category,
    onboard_supplier,
    parse_listing_products,
)
from user.tests_crm_pragmadinar import make_buyer


def _simple_pdf(lines):
    """One-page PDF whose text pypdf can extract."""
    from xhtml2pdf import pisa

    html = "<html><body>" + "".join(f"<p>{line}</p>" for line in lines) + "</body></html>"
    result = io.BytesIO()
    pdf = pisa.CreatePDF(html, dest=result, encoding="utf-8")
    if pdf.err:
        raise RuntimeError("Could not build test PDF")
    return result.getvalue()


class SupplierOnboardingTests(TestCase):
    def setUp(self):
        seed_pragmadinar_catalog()
        seed_entitlements()
        self.family = Category.objects.create(
            name_en="Onboard family",
            name_fr="Famille onboardium",
            slug="onboard-famille-test",
            level=1,
        )
        self.bottle = Category.objects.create(
            parent=self.family,
            name_en="Onboard bottle",
            name_fr="Flacon onboardium",
            slug="onboard-flacon-test",
            level=2,
        )
        self.film = Category.objects.create(
            parent=self.family,
            name_en="Onboard film",
            name_fr="Film onboardium",
            slug="onboard-film-test",
            level=2,
        )

    def test_creates_account_and_products_from_specs(self):
        result = onboard_supplier(
            "Embalplus",
            "contact@embalplus.dz",
            product_specs=[
                {
                    "name": "Flacon onboardium 500 ml",
                    "description": "Flacon transparent",
                    "subcategory": "Flacon onboardium",
                },
                {"name": "Film onboardium 23 microns"},
            ],
        )
        self.assertTrue(result.created)
        self.assertEqual(result.supplier.email, "contact@embalplus.dz")
        self.assertEqual(result.user.supplier_id, result.supplier.pk)
        self.assertIsNotNone(result.password)
        names = {product.name for product in result.products_created}
        self.assertEqual(names, {"Flacon onboardium 500 ml", "Film onboardium 23 microns"})
        bottle = Product.objects.get(name="Flacon onboardium 500 ml")
        self.assertEqual(bottle.category_id, self.family.pk)
        self.assertEqual(bottle.subcategory_id, self.bottle.pk)
        self.assertIn(self.family, result.supplier.interested_categories.all())

    def test_skips_unmatched_and_duplicate_products(self):
        onboard_supplier(
            "Embalplus",
            "contact@embalplus.dz",
            product_specs=[{"name": "Flacon onboardium 1L"}],
        )
        result = onboard_supplier(
            "Embalplus",
            "contact@embalplus.dz",
            product_specs=[
                {"name": "Flacon onboardium 1L"},
                {"name": "Logiciel CRM interne"},
            ],
        )
        self.assertFalse(result.created)
        self.assertEqual(result.products_created, [])
        reasons = {item.skip_reason for item in result.products_skipped}
        self.assertTrue(any("déjà présent" in reason for reason in reasons))
        self.assertTrue(any("catégorie" in reason for reason in reasons))
        self.assertEqual(Product.objects.filter(supplier=result.supplier).count(), 1)

    def test_buyer_email_is_rejected(self):
        make_buyer("clash")
        with self.assertRaises(OnboardError):
            onboard_supplier("Embalplus", "buyerclash@example.com")

    def test_pdf_text_is_parsed_into_taxonomy_products(self):
        pdf = _simple_pdf(
            [
                "Catalogue Embalplus 2026",
                "Flacon onboardium 500ml",
                "Flacon transparent pour jus et eau minerale",
                "Film onboardium alimentaire",
                "Page 1",
            ]
        )
        text = extract_pdf_text(pdf)
        self.assertIn("Flacon onboardium 500ml", text)
        listing = parse_listing_products(text)
        usable = [item for item in listing if item.is_usable]
        names = {item.name for item in usable}
        self.assertTrue(any("Flacon onboardium" in name for name in names))
        family, product_type = match_category("Flacon onboardium 500ml")
        self.assertEqual(family, self.family)
        self.assertEqual(product_type, self.bottle)

        result = onboard_supplier(
            "Embalplus",
            "pdf@embalplus.dz",
            pdf_source=io.BytesIO(pdf),
        )
        self.assertGreaterEqual(len(result.products_created), 1)
        self.assertTrue(
            Product.objects.filter(
                supplier=result.supplier, subcategory=self.bottle
            ).exists()
        )

    def test_management_command_dry_run_does_not_write(self):
        pdf_path = self._write_pdf()
        call_command(
            "onboard_supplier",
            name="Embalplus",
            email="cli@embalplus.dz",
            pdf=str(pdf_path),
            dry_run=True,
        )
        self.assertFalse(Supplier.objects.filter(email="cli@embalplus.dz").exists())

    def test_management_command_creates_the_account(self):
        products_path = self._write_products()
        call_command(
            "onboard_supplier",
            name="Embalplus",
            email="cli@embalplus.dz",
            products=str(products_path),
        )
        supplier = Supplier.objects.get(email="cli@embalplus.dz")
        self.assertEqual(supplier.company_name, "Embalplus")
        self.assertEqual(supplier.products.count(), 1)

    def test_admin_api_onboards_from_pdf(self):
        ultra = User.objects.create_user(
            username="hoocem",
            email="hoocem@example.com",
            password="Testpass!2345",
            is_staff=True,
            is_superuser=True,
        )
        client = APIClient()
        client.force_authenticate(ultra)
        pdf = SimpleUploadedFile(
            "listing.pdf",
            _simple_pdf(["Flacon onboardium 330ml"]),
            content_type="application/pdf",
        )
        response = client.post(
            reverse("api_admin_supplier_onboard"),
            {
                "companyName": "Embalplus",
                "email": "api@embalplus.dz",
                "listing": pdf,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["email"], "api@embalplus.dz")
        self.assertIn("password", response.data)
        self.assertGreaterEqual(len(response.data["productsCreated"]), 1)

        outsider = APIClient()
        outsider.force_authenticate(make_buyer("nope"))
        denied = outsider.post(
            reverse("api_admin_supplier_onboard"),
            {"companyName": "X", "email": "x@example.com"},
            format="multipart",
        )
        self.assertEqual(denied.status_code, 403)

    def _write_pdf(self):
        path = self._temp("listing.pdf")
        path.write_bytes(_simple_pdf(["Flacon onboardium 500ml"]))
        return path

    def _write_products(self):
        path = self._temp("products.json")
        path.write_text(
            json.dumps(
                [{"name": "Flacon onboardium 5L", "subcategory": "Flacon onboardium"}]
            ),
            encoding="utf-8",
        )
        return path

    def _temp(self, name):
        if not hasattr(self, "_tmpdir"):
            tmp = tempfile.TemporaryDirectory(prefix="onboard-")
            self.addCleanup(tmp.cleanup)
            self._tmpdir = Path(tmp.name)
        return self._tmpdir / name
