from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from buyer_space.catalogue_models import SuiteEnsemble, SuiteImage
from buyer_space.models import LibraryProduct


class Command(BaseCommand):
    help = (
        "Rattache des visuels déjà générés. "
        "prod-<code>.png|jpg pour un produit, kit-<code>.png|jpg pour un kit."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dir", required=True, help="Dossier des fichiers générés.")

    def handle(self, *args, **options):
        folder = Path(options["dir"])
        if not folder.is_dir():
            raise CommandError(f"Dossier introuvable : {folder}")
        products = kits = skipped = 0
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            stem = path.stem
            if stem.startswith("prod-"):
                if _attach_product(stem[5:], path):
                    products += 1
                else:
                    skipped += 1
            elif stem.startswith("kit-"):
                if _attach_kit(stem[4:], path):
                    kits += 1
                else:
                    skipped += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Produits: {products} · Kits: {kits} · Ignorés: {skipped}"
            )
        )


def _attach_product(code: str, path: Path) -> bool:
    product = LibraryProduct.objects.filter(code=code).first()
    if product is None:
        return False
    with path.open("rb") as handle:
        product.file.save(path.name, File(handle), save=True)
    return True


def _attach_kit(code: str, path: Path) -> bool:
    ensemble = SuiteEnsemble.objects.filter(code=code).first()
    if ensemble is None:
        return False
    image = SuiteImage.objects.filter(ensemble=ensemble, view_type="overview").first()
    if image is None:
        image = SuiteImage(
            ensemble=ensemble,
            view_type="overview",
            illustrative=True,
            license=SuiteImage.License.PAKNOMIK,
        )
    image.rights = SuiteImage.Rights.ALLOWED
    image.production_status = SuiteImage.ProductionStatus.PRODUCED_INTERNAL
    image.suggested_filename = path.name
    image.save()
    with path.open("rb") as handle:
        image.file.save(path.name, File(handle), save=True)
    return True
