from django.core.management.base import BaseCommand

from buyer_space.kit_line_rebuild import rebuild_empty_dictionary_kits


class Command(BaseCommand):
    help = (
        "Reconstruit les composants des kits du dictionnaire qui n'ont aucune ligne. "
        "Ajoute les types absents de la taxonomie."
    )

    def handle(self, *args, **options):
        report = rebuild_empty_dictionary_kits()
        self.stdout.write(
            "Kits reconstruits: {kits} · Lignes: {lines} · "
            "Types créés: {types_created} · Types repris: {types_reused} · "
            "Ignorés: {skipped}".format(**report)
        )
        self.stdout.write(self.style.SUCCESS("Kits du dictionnaire reconstruits."))
