import re

from django.test import SimpleTestCase, TestCase
from PIL import Image

from user.models import Category, Product, Supplier
from user.product_pdf import (
    build_product_datasheet_context,
    description_to_html,
    product_datasheet_pdf_bytes,
    render_product_datasheet_html,
)
from user.proposal_pdf import (
    PAGE_BG,
    PAKNOMIK_DARK,
    _contrast_ratio,
    _fitted_image,
    brand_ink_color,
    build_proposal_pdf_context,
    mark_last_list_items,
    readable_text_color,
    render_proposal_pdf_html,
)


def _page_count(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf))


class ListKeepTogetherTests(SimpleTestCase):
    def test_marks_only_the_last_item_of_each_list(self):
        markup = mark_last_list_items("<ul><li>A</li><li>B</li></ul><ol><li>C</li><li>D</li></ol>")
        self.assertEqual(
            markup,
            '<ul><li>A</li><li class="pdf-li-last">B</li></ul>'
            '<ol><li>C</li><li class="pdf-li-last">D</li></ol>',
        )

    def test_nested_lists_and_attributes(self):
        markup = mark_last_list_items(
            '<ul><li style="text-align: center">A<ul><li>A1</li><li>A2</li></ul></li><li>B</li></ul>'
        )
        self.assertEqual(
            markup,
            '<ul><li style="text-align: center">A<ul><li>A1</li><li class="pdf-li-last">A2</li></ul>'
            '</li><li class="pdf-li-last">B</li></ul>',
        )

    def test_text_without_lists_is_untouched(self):
        self.assertEqual(mark_last_list_items("<p>Rien</p>"), "<p>Rien</p>")

    def test_blank_lines_between_items_keep_a_single_list(self):
        html = description_to_html("- Un\n\n- Deux\n\n- Trois\n\nFin")
        self.assertIn('<ul class="desc-list"><li>Un</li><li>Deux</li><li>Trois</li></ul>', html)
        self.assertEqual(html.count("<ul"), 1)


class ColorTests(SimpleTestCase):
    def test_banner_text_switches_to_dark_on_light_brand_colors(self):
        self.assertEqual(readable_text_color("#6c3b9e"), "#ffffff")
        self.assertEqual(readable_text_color("#1a003a"), "#ffffff")
        self.assertEqual(readable_text_color("#f5d000"), PAKNOMIK_DARK)
        self.assertEqual(readable_text_color("#e3d9f7"), PAKNOMIK_DARK)

    def test_brand_ink_keeps_legible_colors_and_darkens_light_ones(self):
        self.assertEqual(brand_ink_color("#6c3b9e"), "#6c3b9e")
        self.assertEqual(brand_ink_color("#1b75bb"), "#1b75bb")
        darkened = brand_ink_color("#f5d000")
        self.assertNotEqual(darkened, "#f5d000")
        self.assertGreaterEqual(_contrast_ratio(darkened, PAGE_BG), 3.0)


class FittedImageTests(SimpleTestCase):
    def test_large_image_is_scaled_into_the_box_keeping_its_ratio(self):
        fitted = _fitted_image(Image.new("RGB", (2000, 1000), "white"), 250, 230)
        self.assertEqual((fitted["width"], fitted["height"]), (250, 125))
        self.assertTrue(fitted["uri"].startswith("data:image/jpeg;base64,"))

    def test_small_image_is_never_upscaled(self):
        fitted = _fitted_image(Image.new("RGBA", (80, 40)), 250, 230)
        self.assertEqual((fitted["width"], fitted["height"]), (80, 40))
        self.assertTrue(fitted["uri"].startswith("data:image/png;base64,"))


class DatasheetLayoutTests(TestCase):
    def setUp(self):
        category = Category.objects.create(name_en="Bottles", name_fr="Bouteilles", slug="pdf-bottles")
        self.supplier = Supplier.objects.create(
            company_name="Embalium Test", email="pdf@example.com", brand_color="#f5d000"
        )
        self.product = Product.objects.create(
            supplier=self.supplier,
            category=category,
            name="Bouteille PET transparente",
            description="",
            description_html=(
                "<h4>Pourquoi choisir nos bouteilles ?</h4>"
                "<ul><li>Transparence</li><li>Légèreté</li><li>Recyclable</li></ul>"
            ),
        )

    def test_datasheet_has_paknomik_chrome_and_no_orphan_contact_page(self):
        context = build_product_datasheet_context(self.product)
        html = render_product_datasheet_html(**context)
        self.assertIn('id="paknomik-band"', html)
        self.assertIn('id="paknomik-footer"', html)
        self.assertIn("Document généré via Paknomik", html)
        self.assertIn("<pdf:pagenumber/>", html)
        self.assertNotIn("doc-footer", html)
        self.assertNotIn("background=", html)
        self.assertIn('<li class="pdf-li-last">Recyclable</li>', html)
        self.assertEqual(context["brand_text_color"], PAKNOMIK_DARK)

        pdf, filename = product_datasheet_pdf_bytes(self.product)
        self.assertEqual(filename, "fiche-technique-bouteille-pet-transparente.pdf")
        self.assertEqual(_page_count(pdf), 1)

    def test_proposal_uses_the_same_chrome_and_readable_hero(self):
        context = build_proposal_pdf_context(
            supplier=self.supplier,
            document_title="Proposition",
            subtitle="",
            blocks=[{"type": "process", "enabled": True, "title": "Process", "steps": ["A", "B"]}],
            message="",
            proposed_price="10",
            lead_time_days=7,
            client_name="Client",
        )
        html = render_proposal_pdf_html(**context)
        self.assertIn('id="paknomik-footer"', html)
        self.assertIn('class="pak-hero-mid">Proposition commerciale<', html)
        self.assertIn('<li class="pdf-li-last">B</li>', html)
        self.assertNotIn("doc-footer", html)
        self.assertNotIn("background=", html)
