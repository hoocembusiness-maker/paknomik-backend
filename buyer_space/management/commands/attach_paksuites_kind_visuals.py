from pathlib import Path

from django.core.management.base import BaseCommand

from buyer_space.catalogue_models import SuiteComponentDefinition, SuiteImage, SuiteTaxon
from buyer_space.suite_visuals import infer_visual_kind, prompt_for_kind
from django.conf import settings
from django.core.files import File
import shutil


KIND_SOURCES = {
    "ampoule": "pks-kind-ampoule.jpg",
    "barquette": "pks-kind-barquette.jpg",
    "boite-pliante": "pks-kind-boite-pliante.jpg",
    "bouchon": "pks-kind-bouchon.jpg",
    "caisse": "pks-kind-caisse.jpg",
    "compte-gouttes": "pks-kind-compte-gouttes.jpg",
    "etiquette": "pks-kind-etiquette.jpg",
    "film": "pks-kind-film.jpg",
    "flacon": "pks-kind-flacon.jpg",
    "generique": "pks-kind-generique.jpg",
    "notice": "pks-kind-notice.jpg",
    "opercule": "pks-kind-opercule.jpg",
    "pompe": "pks-kind-pompe.jpg",
    "pot": "pks-kind-pot.jpg",
    "sachet": "pks-kind-sachet.jpg",
    "seau": "pks-kind-seau.jpg",
    "tube": "pks-kind-tube.jpg",
    "actionneur": "pks-comp-actionneur.jpg",
    "bille": "pks-comp-bille-roll-on.jpg",
    "stick": "pks-comp-stick-twist-up.jpg",
    "canette": "pks-comp-canette-alu.jpg",
    "zip": "pks-comp-zip.jpg",
    "joint": "pks-comp-joint.jpg",
    "pince": "pks-comp-pince.jpg",
    "papier-gras": "pks-comp-papier-gras.jpg",
    "mousse": "pks-comp-mousse.jpg",
    "palette": "pks-comp-palette.jpg",
    "carte": "pks-comp-carte-de-visite.jpg",
    "flyer": "pks-comp-flyer.jpg",
    "ruban": "pks-comp-ruban-adhesif.jpg",
}


class Command(BaseCommand):
    help = (
        "Attache les visuels par kind aux définitions de composants "
        "et aux types d'emballage (SuiteTaxon)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--assets",
            default=str(
                Path(
                    r"C:\Users\Hocem\.cursor\projects"
                    r"\c-Users-Hocem-pragmatik-paknomik-paknomik-backend\assets"
                )
            ),
            help="Dossier contenant les JPG générés (pks-kind-*.jpg).",
        )

    def handle(self, *args, **options):
        assets = Path(options["assets"])
        media_kind = Path(settings.MEDIA_ROOT) / "paksuites" / "kinds"
        media_kind.mkdir(parents=True, exist_ok=True)

        for definition in SuiteComponentDefinition.objects.all():
            kind = infer_visual_kind(definition.code, definition.label)
            if definition.visual_kind != kind:
                definition.visual_kind = kind
                definition.save(update_fields=["visual_kind"])

        masters: dict[str, Path] = {}
        for kind, name in KIND_SOURCES.items():
            src = assets / name
            if not src.exists():
                src = Path(settings.MEDIA_ROOT) / "paksuites" / name
            if not src.exists():
                # already attached master
                existing = media_kind / f"{kind}.jpg"
                if existing.exists():
                    masters[kind] = existing
                continue
            dest = media_kind / f"{kind}.jpg"
            shutil.copy2(src, dest)
            masters[kind] = dest

        def_ok = type_ok = 0
        for definition in SuiteComponentDefinition.objects.all():
            kind = definition.visual_kind or "generique"
            master = masters.get(kind) or masters.get("generique")
            if not master:
                continue
            image = SuiteImage.objects.filter(definition=definition).first()
            if not image:
                image = SuiteImage(definition=definition, view_type="composant")
            self._apply(image, kind if kind in masters else "generique", master)
            def_ok += 1

        for taxon in SuiteTaxon.objects.filter(kind=SuiteTaxon.Kind.TYPE):
            kind = infer_visual_kind(taxon.code, taxon.label_fr)
            master = masters.get(kind) or masters.get("generique")
            if not master:
                continue
            image = SuiteImage.objects.filter(taxon=taxon).first()
            if not image:
                image = SuiteImage(taxon=taxon, view_type="type")
            self._apply(image, kind if kind in masters else "generique", master)
            type_ok += 1

        self.stdout.write(f"Définitions: {def_ok} · Types: {type_ok} · Masters: {len(masters)}")

    def _apply(self, image: SuiteImage, kind: str, master: Path) -> None:
        with master.open("rb") as fh:
            image.file.save(f"kind-{kind}.jpg", File(fh), save=False)
        image.view_type = image.view_type or ("type" if image.taxon_id else "composant")
        image.suggested_filename = f"pks-kind-{kind}.jpg"
        image.generation_prompt = prompt_for_kind(kind)
        image.production_status = SuiteImage.ProductionStatus.PRODUCED_INTERNAL
        image.rights = SuiteImage.Rights.ALLOWED
        image.illustrative = True
        image.license = SuiteImage.License.PAKNOMIK
        image.save()
