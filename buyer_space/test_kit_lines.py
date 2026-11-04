from django.test import TestCase

from buyer_space.catalogue_models import SuiteEnsemble, SuiteTaxon
from buyer_space.kit_line_rebuild import rebuild_empty_dictionary_kits
from buyer_space.models import PackagingKit


class DictionaryKitLineTests(TestCase):
    def test_empty_dictionary_kit_reuses_a_type_and_adds_a_missing_one(self):
        family = SuiteTaxon.objects.create(
            kind=SuiteTaxon.Kind.FAMILY,
            code="F-E",
            label_fr="E. Canettes et contenants métalliques",
        )
        can = SuiteTaxon.objects.create(
            kind=SuiteTaxon.Kind.TYPE,
            code="canette-en-aluminium",
            label_fr="Canette en aluminium",
            parent=family,
        )
        ensemble = SuiteEnsemble.objects.create(
            code="PKS-BEV-005",
            name="Canette",
            slug="pks-bev-005",
            editorial_status=SuiteEnsemble.Status.PUBLISHED,
        )
        PackagingKit.objects.create(
            ensemble=ensemble,
            status_label="Publié",
            components_label="Canette en aluminium | Revêtement intérieur protecteur",
            component_count=2,
        )
        kept = SuiteEnsemble.objects.create(
            code="PKS-BAK-001",
            name="Boîte",
            slug="pks-bak-001",
            editorial_status=SuiteEnsemble.Status.PUBLISHED,
        )
        PackagingKit.objects.create(
            ensemble=kept,
            status_label="Publié",
            components_label="Boîte pliante | Étiquette",
            component_count=2,
        )
        kept.lines.create(
            definition_id=self._definition(can).id,
            taxon=can,
            role="Contenant",
            status="required",
        )

        report = rebuild_empty_dictionary_kits()
        self.assertEqual(report["kits"], 1)
        self.assertEqual(report["lines"], 2)
        self.assertEqual(kept.lines.count(), 1)

        lines = list(ensemble.lines.select_related("taxon", "taxon__parent"))
        self.assertEqual(lines[0].taxon_id, can.id)
        self.assertEqual(lines[0].role, "Contenant")
        created = lines[1].taxon
        self.assertEqual(created.label_fr, "Revêtement intérieur protecteur")
        self.assertEqual(created.parent.code, "F-X")
        self.assertTrue(created.origin.startswith("catalogue_multi_affectation"))

        again = rebuild_empty_dictionary_kits()
        self.assertEqual(again["kits"], 0)
        self.assertEqual(ensemble.lines.count(), 2)

    def _definition(self, taxon):
        from buyer_space.catalogue_models import SuiteComponentDefinition

        return SuiteComponentDefinition.objects.create(
            code=taxon.code,
            label=taxon.label_fr,
            taxon=taxon,
        )
