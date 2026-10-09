from django.test import SimpleTestCase

from user.product_pdf import description_to_html


class DescriptionToHtmlTests(SimpleTestCase):
    def test_markers_become_titles_and_lists(self):
        html = description_to_html(
            '# Boîte pizza\n## Points forts\n1. Carton kraft\n2. Impression 2 couleurs\n'
            '- Livrée à plat\n- Alimentaire\nTexte libre'
        )
        self.assertIn('<p class="desc-title">Boîte pizza</p>', html)
        self.assertIn('<p class="desc-subtitle">Points forts</p>', html)
        self.assertIn(
            '<ol class="desc-list"><li>Carton kraft</li><li>Impression 2 couleurs</li></ol>', html
        )
        self.assertIn('<ul class="desc-list"><li>Livrée à plat</li><li>Alimentaire</li></ul>', html)
        self.assertIn('<p class="block-text">Texte libre</p>', html)

    def test_plain_text_keeps_line_breaks_and_is_escaped(self):
        html = description_to_html('Ligne 1\nLigne <b>2</b>')
        self.assertEqual(html, '<p class="block-text">Ligne 1<br/>Ligne &lt;b&gt;2&lt;/b&gt;</p>')
