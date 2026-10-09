"""
Rich text (product description, supplier presentation).
Run: python manage.py test user.tests_rich_text
"""

from django.test import TestCase
from rest_framework.test import APIClient

from user.models import Category, Product, Supplier, User
from user.rich_text import rich_html_to_text, sanitize_rich_html


class SanitizerTests(TestCase):
    def test_keeps_basic_formatting(self):
        html = (
            '<h3 style="text-align: center">Titre</h3>'
            '<p><strong>Gras</strong> <em>italique</em> <u>souligné</u> <s>barré</s></p>'
            '<p><span style="color: #6c3b9e; background-color: rgb(255, 245, 157); font-size: x-large">couleur</span></p>'
            '<ul><li>un</li><li>deux</li></ul><ol><li>premier</li></ol>'
        )
        cleaned = sanitize_rich_html(html)
        self.assertIn('<h3 style="text-align: center">Titre</h3>', cleaned)
        self.assertIn("<strong>Gras</strong>", cleaned)
        self.assertIn("<u>souligné</u>", cleaned)
        self.assertIn("color: #6c3b9e", cleaned)
        self.assertIn("background-color: rgb(255, 245, 157)", cleaned)
        self.assertIn("font-size: x-large", cleaned)
        self.assertIn("<ul><li>un</li><li>deux</li></ul>", cleaned)

    def test_strips_scripts_handlers_links_and_images(self):
        html = (
            '<p onclick="alert(1)">ok</p>'
            "<script>alert('x')</script>"
            '<a href="javascript:alert(1)">lien</a>'
            '<img src="x" onerror="alert(1)">'
            '<iframe src="https://evil.example"></iframe>'
            '<style>body{display:none}</style>'
        )
        cleaned = sanitize_rich_html(html)
        self.assertEqual(cleaned, "<p>ok</p>lien")
        for forbidden in ("script", "onclick", "onerror", "javascript", "<a", "<img", "iframe", "style>"):
            self.assertNotIn(forbidden, cleaned)

    def test_drops_dangerous_css(self):
        cleaned = sanitize_rich_html(
            '<p style="position: fixed; background-image: url(https://x); color: expression(alert(1)); '
            'font-size: 400px; text-align: right">x</p>'
        )
        self.assertEqual(cleaned, '<p style="text-align: right">x</p>')

    def test_normalizes_legacy_tags(self):
        cleaned = sanitize_rich_html('<b>a</b><i>b</i><font size="5" color="#ff0000">c</font><h1>d</h1>')
        self.assertEqual(
            cleaned,
            '<strong>a</strong><em>b</em>'
            '<span style="font-size: x-large; color: #ff0000">c</span><h3>d</h3>',
        )

    def test_font_family_is_limited_to_the_offered_fonts(self):
        cleaned = sanitize_rich_html(
            '<p><span style="font-family: &quot;Courier New&quot;, monospace">a</span>'
            '<font face="Comic Sans MS">b</font></p>'
        )
        self.assertEqual(cleaned, '<p><span style="font-family: courier new">a</span><span>b</span></p>')

    def test_word_paste_keeps_text_and_allowed_styles_only(self):
        cleaned = sanitize_rich_html(
            '<p class="MsoNormal" style="mso-line-height-alt: 1pt; color: red"><o:p>Word</o:p></p>'
        )
        self.assertEqual(cleaned, '<p style="color: red">Word</p>')

    def test_escapes_text_and_closes_open_tags(self):
        cleaned = sanitize_rich_html("<p><strong>1 < 2 & 3")
        self.assertEqual(cleaned, "<p><strong>1 &lt; 2 &amp; 3</strong></p>")

    def test_formatting_without_text_is_empty(self):
        self.assertEqual(sanitize_rich_html("<p><br></p><ul><li></li></ul>"), "")
        self.assertEqual(sanitize_rich_html("<p>100</p>"), "<p>100</p>")

    def test_plain_text_keeps_one_line_per_block_and_list_markers(self):
        text = rich_html_to_text(
            "<h3>Titre</h3><p>Ligne <strong>un</strong><br>ligne deux</p>"
            "<ul><li>a</li><li>b</li></ul><ol><li>x</li><li>y</li></ol>"
        )
        self.assertEqual(text, "Titre\nLigne un\nligne deux\n- a\n- b\n1. x\n2. y")


class RichTextApiTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_en="Boxes", name_fr="Boîtes", slug="rich-boxes")
        self.supplier = Supplier.objects.create(company_name="Rich SA", email="rich@example.com")
        self.user = User.objects.create_user(
            username="rich_supplier", email="rich_supplier@example.com", password="TestPass123!"
        )
        self.user.supplier = self.supplier
        self.user.save(update_fields=["supplier"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_product_create_stores_clean_html_and_derives_plain_text(self):
        response = self.client.post(
            "/api/supplier/products/",
            {
                "name": "Boîte kraft",
                "categoryId": self.category.id,
                "descriptionHtml": '<p><strong>Solide</strong> et <span style="color: #6c3b9e">recyclable</span></p>'
                "<script>alert(1)</script><ul><li>Format A4</li></ul>",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(pk=response.data["id"])
        self.assertNotIn("script", product.description_html)
        self.assertIn("<strong>Solide</strong>", product.description_html)
        self.assertEqual(product.description, "Solide et recyclable\n- Format A4")
        self.assertEqual(response.data["descriptionHtml"], product.description_html)

        public = self.client.get(f"/api/products/{product.id}/")
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.data["descriptionHtml"], product.description_html)

    def test_plain_text_edit_replaces_formatting_but_unchanged_text_keeps_it(self):
        product = Product.objects.create(
            supplier=self.supplier,
            category=self.category,
            name="Sac",
            description="",
            description_html="<p><em>Sac</em> papier</p>",
        )
        self.assertEqual(product.description, "Sac papier")

        self.client.patch(
            f"/api/supplier/products/{product.id}/",
            {"description": "Sac papier"},
            format="multipart",
        )
        product.refresh_from_db()
        self.assertEqual(product.description_html, "<p><em>Sac</em> papier</p>")

        self.client.patch(
            f"/api/supplier/products/{product.id}/",
            {"description": "Nouveau texte brut"},
            format="multipart",
        )
        product.refresh_from_db()
        self.assertEqual(product.description_html, "")
        self.assertEqual(product.description, "Nouveau texte brut")

    def test_supplier_presentation_round_trip(self):
        response = self.client.patch(
            "/api/supplier/profile/",
            {
                "presentationHtml": '<h3>Qui sommes-nous</h3><p style="text-align: justify" onmouseover="x()">'
                "Fabricant <u>depuis 1998</u></p>",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.supplier.refresh_from_db()
        self.assertEqual(
            self.supplier.presentation_html,
            '<h3>Qui sommes-nous</h3><p style="text-align: justify">Fabricant <u>depuis 1998</u></p>',
        )
        self.assertEqual(self.supplier.presentation, "Qui sommes-nous\nFabricant depuis 1998")

        public = APIClient().get(f"/api/suppliers/{self.supplier.id}/")
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.data["aboutHtml"], self.supplier.presentation_html)
        self.assertEqual(public.data["about"], self.supplier.presentation)

    def test_oversized_html_is_rejected(self):
        response = self.client.patch(
            "/api/supplier/profile/",
            {"presentationHtml": "<p>" + "a" * 31000 + "</p>"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
