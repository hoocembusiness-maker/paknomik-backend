"""Move SuiteTaxon types stuck in « Famille à classer » to the right category."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from buyer_space.catalogue_import import _infer_family_letter
from buyer_space.catalogue_models import SuiteComponentDefinition, SuiteTaxon


# Explicit overrides for known orphans (label substring → family letter).
ORPHAN_TARGETS: dict[str, tuple[str, str]] = {
    "joint-type-a-creer-(à créer)": ("H", "Joint (type à créer)"),
    "pince-de-fermeture-(à créer)": ("H", "Pince de fermeture (type à créer)"),
    "ruban-adhesif-type-a-creer-(à créer)": ("K", "Ruban adhésif (type à créer)"),
}


class Command(BaseCommand):
    help = "Classe les types PakSuites orphelins (F-X) dans les catégories A–L."

    @transaction.atomic
    def handle(self, *args, **options):
        families = {
            letter: SuiteTaxon.objects.filter(
                kind=SuiteTaxon.Kind.FAMILY, code=f"F-{letter}"
            ).first()
            for letter in "ABCDEFGHIJKL"
        }
        moved = 0
        orphans = SuiteTaxon.objects.filter(
            kind=SuiteTaxon.Kind.TYPE, parent__code="F-X"
        )
        for taxon in orphans:
            target = ORPHAN_TARGETS.get(taxon.code)
            if target:
                letter, new_label = target
            else:
                letter = _infer_family_letter(taxon.label_fr, "")
                new_label = taxon.label_fr
                if "type à créer" not in new_label.casefold() and letter:
                    # keep label; only reparent
                    pass
            family = families.get(letter)
            if not family:
                self.stdout.write(self.style.WARNING(f"Pas de famille pour {taxon.code}"))
                continue

            base = slugify(new_label.replace("(type à créer)", "").strip())[:60] or slugify(
                taxon.label_fr
            )[:60]
            new_code = f"{base}-type-a-creer-{letter.lower()}"[:80]
            # Avoid colliding with an existing sibling
            clash = (
                SuiteTaxon.objects.filter(code=new_code).exclude(pk=taxon.pk).exists()
            )
            if clash:
                new_code = f"{taxon.code}-{letter.lower()}"[:80]

            old_code = taxon.code
            taxon.parent = family
            taxon.label_fr = new_label[:200]
            taxon.code = new_code
            taxon.origin = taxon.origin or "Composant"
            taxon.save(update_fields=["parent", "label_fr", "code", "origin"])

            for definition in SuiteComponentDefinition.objects.filter(taxon=taxon):
                if definition.code == old_code or definition.code.startswith(old_code[:40]):
                    definition.code = new_code[:120]
                    definition.label = new_label[:240]
                    definition.save(update_fields=["code", "label"])

            self.stdout.write(f"{old_code} → {family.label_fr} ({new_code})")
            moved += 1

        # Also reparent any remaining F-X orphans via hints
        remaining = SuiteTaxon.objects.filter(
            kind=SuiteTaxon.Kind.TYPE, parent__code="F-X"
        )
        for taxon in remaining:
            letter = _infer_family_letter(taxon.label_fr, "")
            family = families.get(letter)
            if not family:
                continue
            taxon.parent = family
            taxon.save(update_fields=["parent"])
            self.stdout.write(f"{taxon.code} → {family.label_fr} (hint)")
            moved += 1

        left = SuiteTaxon.objects.filter(
            kind=SuiteTaxon.Kind.TYPE, parent__code="F-X"
        ).count()
        self.stdout.write(self.style.SUCCESS(f"Déplacés: {moved} · Restants F-X: {left}"))
