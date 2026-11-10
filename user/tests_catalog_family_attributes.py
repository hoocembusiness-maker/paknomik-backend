from django.test import TestCase

from user.catalog_family_attributes import (
    BOTTLE_L1,
    CAN_L1,
    CAN_SHAPES,
    COLORS,
    CONTAINER_SHAPES,
    GLASS_GRADES,
    INDUSTRIAL_L1,
    LABEL_L1,
    LABEL_SHAPES,
    apply_container_and_label_attributes,
    visible_links,
)
from user.models import Category, TechnicalAttribute


def _level1_names(category):
    return [
        link.attribute.name_fr
        for link in visible_links(category)
        if link.attribute.attribute_level == 1
    ]


def _options(category, name):
    for link in visible_links(category):
        if (link.attribute.name_fr or "").casefold() == name.casefold():
            return [option.value_fr or option.value_en for option in link.options.all()]
    return None


class ContainerAndLabelAttributeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        apply_container_and_label_attributes()

    def test_each_type_stays_within_five_level1_characteristics(self):
        bottles = Category.objects.get(slug="bouteilles-et-flacons")
        for category in bottles.children.all():
            names = _level1_names(category)
            self.assertCountEqual(names, BOTTLE_L1)
            self.assertLessEqual(len(names), 5)

        cans = Category.objects.get(slug="canettes-contenants-metalliques")
        for category in cans.children.all():
            self.assertCountEqual(_level1_names(category), CAN_L1)

        industrial = Category.objects.get(slug="emballage-industriel-protection")
        for slug in ("bidon-plastique", "jerricane", "fut-industriel", "seau-industriel"):
            category = industrial.children.get(slug=slug)
            self.assertCountEqual(_level1_names(category), INDUSTRIAL_L1)

        labels = Category.objects.get(slug="etiquettes")
        for category in labels.children.all():
            self.assertCountEqual(_level1_names(category), LABEL_L1)

    def test_shared_shape_and_colour_options_follow_the_spec(self):
        bottle = Category.objects.get(slug="bouteille-verre")
        can = Category.objects.get(slug="canette-aluminium")
        label = Category.objects.get(slug="etiquette-vierge")
        self.assertEqual(_options(bottle, "Forme"), [item[1] for item in CONTAINER_SHAPES])
        self.assertEqual(_options(can, "Forme"), [item[1] for item in CAN_SHAPES])
        self.assertEqual(_options(label, "Forme"), [item[1] for item in LABEL_SHAPES])
        self.assertEqual(_options(bottle, "Couleur"), [item[1] for item in COLORS])
        self.assertEqual(
            {item for item in _options(bottle, "Forme") if item != "Conique"},
            set(_options(can, "Forme")),
        )

    def test_capacity_units_are_not_mixed_inside_a_family(self):
        bottle = Category.objects.get(slug="bouteille-pet")
        can = Category.objects.get(slug="boite-metallique")
        drum = Category.objects.get(slug="fut-industriel")
        self.assertEqual(_unit(bottle, "Contenance"), "ml")
        self.assertEqual(_unit(can, "Contenance"), "ml")
        self.assertEqual(_unit(drum, "Contenance"), "L")

    def test_glass_grade_is_only_on_glass_types(self):
        glass = Category.objects.get(slug="ampoule-emballage")
        plastic = Category.objects.get(slug="bouteille-plastique")
        self.assertEqual(_options(glass, "Type de verre"), [item[1] for item in GLASS_GRADES])
        self.assertIsNone(_options(plastic, "Type de verre"))
        pharma = Category.objects.get(slug="flacon-pharmaceutique")
        self.assertEqual(_options(pharma, "Type de verre"), [item[1] for item in GLASS_GRADES])

    def test_dimensions_stay_unchanged_and_mousse_is_left_alone(self):
        dimensions = TechnicalAttribute.objects.get(external_id="core-packaging-dimensions")
        self.assertEqual(dimensions.name_fr, "Dimensions")
        self.assertEqual(dimensions.attribute_type, "text")
        self.assertEqual(dimensions.attribute_level, 1)

        mousse = Category.objects.get(slug="mousse-protection")
        names = {link.attribute.name_fr for link in visible_links(mousse)}
        self.assertNotIn("Homologation UN (matières dangereuses)", names)
        self.assertNotIn("Type d'ouverture", names)
        self.assertNotIn("Empilable", names)
        self.assertIn("Dimensions", _level1_names(mousse))

    def test_apply_is_idempotent(self):
        before = {
            category.slug: _level1_names(category)
            for category in Category.objects.filter(
                slug__in=("bouteille-verre", "canette-aluminium", "bidon-plastique", "etiquette-vierge")
            )
        }
        apply_container_and_label_attributes()
        after = {
            category.slug: _level1_names(category)
            for category in Category.objects.filter(slug__in=before)
        }
        self.assertEqual(before, after)


def _unit(category, name):
    for link in visible_links(category):
        if (link.attribute.name_fr or "").casefold() == name.casefold() and link.attribute.unit_id:
            return link.attribute.unit.symbol
    return None
