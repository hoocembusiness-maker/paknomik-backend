import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from user.hosting_catalog import export_hosting_catalog, import_hosting_catalog
from user.models import Category, Order, PragmadinarLedger, Product, ProductImage, Supplier
from user.pragmadinar import credit_wallet

User = get_user_model()


class HostingCatalogTests(TestCase):
    def test_export_keeps_suppliers_and_products_without_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "media"
            dest = Path(tmp) / "bundle"
            media.mkdir()
            with override_settings(MEDIA_ROOT=str(media)):
                category = Category.objects.create(name_en="Boxes", slug="boxes", level=1)
                supplier = Supplier.objects.create(
                    company_name="ARTPAP",
                    email="artpap@example.com",
                    presentation="Carton",
                )
                user = User.objects.create_user(
                    username="artpap",
                    email="artpap@example.com",
                    password="secret-pass",
                )
                user.supplier = supplier
                user.save(update_fields=["supplier"])
                product = Product.objects.create(
                    supplier=supplier,
                    category=category,
                    name="Caisse",
                    description="Caisse américaine",
                    price="120.00",
                )
                image = ProductImage(product=product, is_primary=True)
                image.image.save("products/caisse.png", ContentFile(b"image-bytes"), save=True)
                Order.objects.create(
                    supplier=supplier,
                    product=product,
                    quantity=2,
                    unit_price="120.00",
                )
                credit_wallet(supplier, 50, reason="Achat historique", entry_type=PragmadinarLedger.EntryType.PURCHASE)

                report = export_hosting_catalog(dest)
                self.assertEqual(report["suppliers"], 1)
                self.assertEqual(report["products"], 1)
                self.assertGreaterEqual(report["media_files"], 1)

                catalog_path = dest / "catalog.json"
                payload = json.loads(catalog_path.read_text(encoding="utf-8"))
                self.assertNotIn("users", payload["suppliers"][0])

                # Old bundles may still contain user credentials; the importer
                # must ignore them rather than recreate privileged accounts.
                payload["suppliers"][0]["users"] = [
                    {
                        "username": "legacy-admin",
                        "email": "legacy-admin@example.com",
                        "password": "legacy-test-hash",
                        "is_staff": True,
                        "is_superuser": True,
                    }
                ]
                catalog_path.write_text(json.dumps(payload), encoding="utf-8")

                Order.objects.all().delete()
                user.delete()
                supplier.delete()

                imported = import_hosting_catalog(dest)
                again = import_hosting_catalog(dest)

        self.assertTrue(imported["loaded"])
        self.assertEqual(imported["suppliers"], 1)
        self.assertEqual(imported["products"], 1)
        self.assertEqual(Order.objects.count(), 0)
        restored = Supplier.objects.get(email="artpap@example.com")
        self.assertEqual(restored.company_name, "ARTPAP")
        self.assertEqual(restored.products.get().name, "Caisse")
        self.assertTrue(restored.products.get().images.get().image.name)
        self.assertFalse(User.objects.filter(username="artpap").exists())
        self.assertFalse(User.objects.filter(username="legacy-admin").exists())
        self.assertEqual(PragmadinarLedger.objects.count(), 1)
        self.assertEqual(PragmadinarLedger.objects.get().entry_type, PragmadinarLedger.EntryType.GRANT)
        self.assertFalse(again["loaded"])
        self.assertEqual(again["reason"], "suppliers_already_present")
        self.assertEqual(Supplier.objects.count(), 1)
