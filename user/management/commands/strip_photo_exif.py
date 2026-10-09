"""Remove EXIF metadata (GPS, device…) from buyer photos already stored.

Usage: python manage.py strip_photo_exif [--dry-run]
"""

from django.core.management.base import BaseCommand

from user.models import QuoteDemand, QuoteDemandPhoto, RFQRequestPhoto, SampleRequestPhoto
from user.photo_uploads import has_metadata, strip_metadata


class Command(BaseCommand):
    help = "Supprime les métadonnées EXIF des photos jointes aux demandes déjà stockées."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compte les fichiers concernés sans les modifier.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        targets = [
            (QuoteDemandPhoto.objects.exclude(image=""), "image"),
            (RFQRequestPhoto.objects.exclude(image=""), "image"),
            (SampleRequestPhoto.objects.exclude(image=""), "image"),
            (QuoteDemand.objects.exclude(attachment="").exclude(attachment__isnull=True), "attachment"),
        ]
        cleaned = skipped = missing = 0
        for queryset, field_name in targets:
            for row in queryset.iterator():
                field = getattr(row, field_name)
                result = self._clean(field, dry_run)
                if result == "cleaned":
                    cleaned += 1
                elif result == "missing":
                    missing += 1
                else:
                    skipped += 1
        verb = "à nettoyer" if dry_run else "nettoyées"
        self.stdout.write(
            self.style.SUCCESS(
                f"{cleaned} photo(s) {verb}, {skipped} déjà propre(s) ou non-image, "
                f"{missing} fichier(s) introuvable(s)."
            )
        )

    def _clean(self, field, dry_run):
        storage = field.storage
        name = field.name
        if not storage.exists(name):
            return "missing"
        with storage.open(name, "rb") as handle:
            if not has_metadata(handle):
                return "skipped"
            if dry_run:
                return "cleaned"
            content = strip_metadata(handle, name=name)
        if content is None:
            return "skipped"
        storage.delete(name)
        saved_name = storage.save(name, content)
        if saved_name != name:
            field.name = saved_name
            field.instance.save(update_fields=[field.field.name])
        return "cleaned"
