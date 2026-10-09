from types import SimpleNamespace

from django.test import SimpleTestCase

from user.api.serializers import SupplierSettingsSerializer, _product_secondary_image_url


def _fake_product(urls):
    images = [SimpleNamespace(image=SimpleNamespace(url=url)) for url in urls]
    return SimpleNamespace(images=SimpleNamespace(all=lambda: images))


class HorizontalImagesSettingTests(SimpleTestCase):
    def validate(self, value):
        return SupplierSettingsSerializer().validate_productCardStyle(value)

    def test_two_photos_is_kept(self):
        style = self.validate({'layout': 'horizontal', 'horizontalImages': '2'})
        self.assertEqual(style['horizontalImages'], '2')

    def test_missing_or_invalid_value_defaults_to_one(self):
        self.assertEqual(self.validate({'layout': 'horizontal'})['horizontalImages'], '1')
        self.assertEqual(self.validate({'horizontalImages': '5'})['horizontalImages'], '1')


class SecondaryImageUrlTests(SimpleTestCase):
    def test_returns_second_photo(self):
        product = _fake_product(['/media/products/a.jpg', '/media/products/b.jpg'])
        self.assertEqual(_product_secondary_image_url(product, None), '/media/products/b.jpg')

    def test_single_photo_has_no_secondary(self):
        product = _fake_product(['/media/products/a.jpg'])
        self.assertIsNone(_product_secondary_image_url(product, None))
