"""
Create a supplier account from a company name, email, and PDF listing.

    python manage.py onboard_supplier --name "Embalplus" --email "contact@embalplus.dz" --pdf listing.pdf
    python manage.py onboard_supplier --name "Embalplus" --email "contact@embalplus.dz" --products products.json
    python manage.py onboard_supplier --name "Embalplus" --email "contact@embalplus.dz" --pdf listing.pdf --dry-run
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from user.supplier_onboarding import OnboardError, onboard_supplier


class Command(BaseCommand):
    help = "Crée un compte fournisseur à partir du nom, de l'email et d'un PDF de listing."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Nom de l'entreprise")
        parser.add_argument("--email", required=True, help="Email de connexion du fournisseur")
        parser.add_argument("--pdf", help="Chemin du PDF de listing / catalogue")
        parser.add_argument(
            "--products",
            help="JSON optionnel [{name, description, category, subcategory, listingType}]",
        )
        parser.add_argument(
            "--ghost",
            action="store_true",
            help="Masque le fournisseur de la marketplace (compte admin uniquement)",
        )
        parser.add_argument("--dry-run", action="store_true", help="Parse sans écrire en base")

    def handle(self, *args, **options):
        pdf_path = Path(options["pdf"]) if options.get("pdf") else None
        products_path = Path(options["products"]) if options.get("products") else None
        if pdf_path is not None and not pdf_path.exists():
            raise CommandError(f"PDF introuvable: {pdf_path}")
        if products_path is not None and not products_path.exists():
            raise CommandError(f"Fichier JSON introuvable: {products_path}")
        if pdf_path is None and products_path is None:
            self.stdout.write(
                self.style.WARNING(
                    "Aucun listing fourni: le compte sera créé sans produits. "
                    "Passez --pdf ou --products."
                )
            )

        specs = None
        if products_path is not None:
            try:
                specs = json.loads(products_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise CommandError(f"JSON invalide: {exc}") from exc
            if not isinstance(specs, list):
                raise CommandError("Le fichier --products doit contenir une liste JSON.")

        if options["dry_run"]:
            from user.supplier_onboarding import (
                extract_pdf_text,
                listing_products_from_specs,
                parse_listing_products,
            )

            pdf_text = extract_pdf_text(pdf_path) if pdf_path is not None else ""
            listing = (
                listing_products_from_specs(specs)
                if specs is not None
                else parse_listing_products(pdf_text)
            )
            usable = [item for item in listing if item.is_usable]
            skipped = [item for item in listing if not item.is_usable]
            self.stdout.write(
                f"[dry-run] {options['name']!r} <{options['email']}> — "
                f"{len(usable)} produit(s) à créer, {len(skipped)} ignoré(s)"
            )
            for item in usable:
                family = item.category.name_fr or item.category.name_en
                kind = (
                    item.subcategory.name_fr or item.subcategory.name_en
                    if item.subcategory
                    else "—"
                )
                self.stdout.write(f"  + {item.name}  [{family} › {kind}]")
            for item in skipped:
                self.stdout.write(f"  - {item.name}  ({item.skip_reason})")
            return

        try:
            result = onboard_supplier(
                options["name"],
                options["email"],
                pdf_source=pdf_path,
                product_specs=specs,
                ghost=options["ghost"],
            )
        except OnboardError as exc:
            raise CommandError(str(exc)) from exc

        action = "créé" if result.created else "mis à jour"
        self.stdout.write(
            self.style.SUCCESS(
                f"Fournisseur {action}: {result.supplier.company_name} "
                f"(id {result.supplier.pk})"
            )
        )
        self.stdout.write(f"  Connexion: {result.user.email}  (identifiant « {result.user.username} »)")
        if result.password:
            self.stdout.write(
                self.style.WARNING(f"  Mot de passe: {result.password}  (affiché une seule fois)")
            )
        else:
            self.stdout.write("  Mot de passe inchangé.")
        if result.supplier.is_ghost:
            self.stdout.write("  Masqué de la marketplace (--ghost).")
        self.stdout.write(f"  Produits créés: {len(result.products_created)}")
        for product in result.products_created:
            self.stdout.write(f"    + {product.name}")
        if result.products_skipped:
            self.stdout.write(f"  Produits ignorés: {len(result.products_skipped)}")
            for item in result.products_skipped:
                self.stdout.write(f"    - {item.name} ({item.skip_reason})")
        if pdf_path is not None and not result.pdf_text:
            self.stdout.write(
                self.style.WARNING(
                    "  Le PDF n'a pas fourni de texte (scan image ?). "
                    "Passez un --products JSON, ou renvoyez un PDF avec du texte sélectionnable."
                )
            )
