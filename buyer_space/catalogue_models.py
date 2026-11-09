"""Catalogue public PakSuites, distinct des options d'une demande de service."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class SuiteTaxon(models.Model):
    class Kind(models.TextChoices):
        FAMILY = "family", "Catégorie"
        TYPE = "type", "Type"
        SECTOR = "sector", "Secteur"
        FUNCTION = "function", "Fonction"
        MATERIAL = "material", "Matériau"
        ATTRIBUTE = "attribute", "Attribut technique"

    kind = models.CharField(max_length=16, choices=Kind.choices, db_index=True)
    code = models.CharField(max_length=80, unique=True)
    label_fr = models.CharField(max_length=200)
    label_en = models.CharField(max_length=200, blank=True)
    label_ar = models.CharField(max_length=200, blank=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    note = models.TextField(blank=True)
    origin = models.CharField(max_length=80, blank=True)
    marketplace_category = models.ForeignKey(
        "user.Category",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="paksuites_taxons",
        help_text="Sous-catégorie du marché, uniquement si un gestionnaire la relie.",
    )

    class Meta:
        ordering = ["kind", "label_fr"]
        verbose_name = "Taxon PakSuites"
        verbose_name_plural = "Taxons PakSuites"

    def __str__(self):
        return f"{self.get_kind_display()} — {self.label_fr}"

    def clean(self):
        if self.kind == self.Kind.TYPE and self.parent_id is None:
            raise ValidationError("Un type d'emballage doit être rattaché à une catégorie.")
        if self.kind == self.Kind.TYPE and self.parent and self.parent.kind != self.Kind.FAMILY:
            raise ValidationError("Le parent d'un type doit être une catégorie.")
        category = self.marketplace_category
        if category is not None and category.parent_id is None:
            raise ValidationError(
                "Le lien marché doit pointer vers une sous-catégorie, pas une catégorie."
            )


class SuiteSource(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=240)
    url = models.URLField(max_length=500, blank=True)
    info_type = models.CharField(max_length=240, blank=True)
    market = models.CharField(max_length=120, blank=True)
    is_public = models.BooleanField(default=True)
    consulted_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Source PakSuites"
        verbose_name_plural = "Sources PakSuites"

    def __str__(self):
        return f"{self.code} — {self.name}"


class SuiteEnsemble(models.Model):
    class Confidence(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmé"
        INFERRED = "inferred", "Déduit"
        HYPOTHESIS = "hypothesis", "Hypothèse"

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        REVIEW = "review", "En revue"
        PUBLISHED = "published", "Publié"
        ARCHIVED = "archived", "Archivé"

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=240)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=8, blank=True, db_index=True)
    target_product = models.CharField(max_length=300, blank=True)
    function_label = models.CharField(max_length=200, blank=True)
    function = models.ForeignKey(
        SuiteTaxon,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ensembles_by_function",
        limit_choices_to={"kind": SuiteTaxon.Kind.FUNCTION},
    )
    sectors = models.ManyToManyField(
        SuiteTaxon,
        blank=True,
        related_name="ensembles",
        limit_choices_to={"kind": SuiteTaxon.Kind.SECTOR},
    )
    sources = models.ManyToManyField(SuiteSource, blank=True, related_name="ensembles")
    confidence = models.CharField(
        max_length=16,
        choices=Confidence.choices,
        default=Confidence.INFERRED,
    )
    sourced_facts = models.TextField(blank=True)
    points_to_confirm = models.TextField(blank=True)
    algeria_relevance = models.TextField(blank=True)
    variant_note = models.TextField(blank=True)
    internal_note = models.TextField(blank=True)
    editorial_status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Ensemble PakSuites"
        verbose_name_plural = "Ensembles PakSuites"

    def __str__(self):
        return f"{self.code} — {self.name}"


class SuiteComponentDefinition(models.Model):
    code = models.SlugField(max_length=120, unique=True)
    label = models.CharField(max_length=240)
    visual_kind = models.SlugField(
        max_length=40,
        blank=True,
        help_text="Pièce d'illustration réutilisable (flacon, bouchon, étiquette…).",
    )
    taxon = models.ForeignKey(
        SuiteTaxon,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="definitions",
    )
    marketplace_type = models.ForeignKey(
        "user.ProductType",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="kit_component_definitions",
        help_text="Type du marché (flacon, bouchon, étiquette). Le kit reste une entité à part.",
    )

    class Meta:
        ordering = ["label"]
        verbose_name = "Définition de composant"
        verbose_name_plural = "Définitions de composants"

    def __str__(self):
        return self.label


class SuiteComponentLine(models.Model):
    class Level(models.TextChoices):
        PRIMARY = "primary", "Primaire"
        SECONDARY = "secondary", "Secondaire"
        TERTIARY = "tertiary", "Tertiaire"

    class Status(models.TextChoices):
        REQUIRED = "required", "Obligatoire"
        OPTIONAL = "optional", "Optionnel"
        DEPENDS = "depends", "Selon le produit"
        TO_CONFIRM = "to_confirm", "À confirmer"

    ensemble = models.ForeignKey(
        SuiteEnsemble, on_delete=models.CASCADE, related_name="lines"
    )
    definition = models.ForeignKey(
        SuiteComponentDefinition,
        on_delete=models.PROTECT,
        related_name="lines",
    )
    taxon = models.ForeignKey(
        SuiteTaxon,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lines",
    )
    role = models.CharField(max_length=120)
    level = models.CharField(max_length=16, choices=Level.choices, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TO_CONFIRM)
    material = models.CharField(max_length=240, blank=True)
    technical_notes = models.TextField(blank=True)
    compatibility_notes = models.TextField(blank=True)
    quantity_note = models.CharField(max_length=120, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Ligne de composant"
        verbose_name_plural = "Lignes de composants"

    def __str__(self):
        return f"{self.ensemble.code} — {self.definition.label}"


class SuiteAttribute(models.Model):
    code = models.SlugField(max_length=80, unique=True)
    label_fr = models.CharField(max_length=160)
    unit = models.CharField(max_length=40, blank=True)
    lines = models.ManyToManyField(SuiteComponentLine, blank=True, related_name="attributes")

    class Meta:
        ordering = ["label_fr"]
        verbose_name = "Attribut technique"
        verbose_name_plural = "Attributs techniques"

    def __str__(self):
        return self.label_fr


class SuiteReference(models.Model):
    """Référence fabricant. Ce n'est pas une offre disponible en Algérie."""

    definition = models.ForeignKey(
        SuiteComponentDefinition, on_delete=models.CASCADE, related_name="references"
    )
    manufacturer = models.CharField(max_length=200)
    reference_code = models.CharField(max_length=120, blank=True)
    sheet_url = models.URLField(max_length=500, blank=True)
    market = models.CharField(max_length=120, blank=True)

    class Meta:
        verbose_name = "Référence catalogue"
        verbose_name_plural = "Références catalogue"

    def __str__(self):
        return f"{self.manufacturer} {self.reference_code}".strip()


class SuiteSupplierOffer(models.Model):
    line = models.ForeignKey(
        SuiteComponentLine, on_delete=models.CASCADE, related_name="offers"
    )
    supplier = models.ForeignKey(
        "user.Supplier", on_delete=models.CASCADE, related_name="paksuites_offers"
    )
    note = models.CharField(max_length=300, blank=True)
    confirmed_on = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["line", "supplier"], name="uniq_paksuites_offer_line_supplier"
            )
        ]
        verbose_name = "Offre fournisseur PakSuites"
        verbose_name_plural = "Offres fournisseurs PakSuites"

    def __str__(self):
        return f"{self.supplier} — {self.line}"


class SuiteCompatibility(models.Model):
    class Status(models.TextChoices):
        CONCEPTUAL = "conceptual", "Conceptuel"
        INFERRED = "inferred", "Déduit"
        CONFIRMED = "confirmed", "Confirmé"

    subject = models.ForeignKey(
        SuiteComponentLine, on_delete=models.CASCADE, related_name="compatibilities"
    )
    counterpart = models.ForeignKey(
        SuiteComponentLine,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="counterpart_compatibilities",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.INFERRED)
    justification = models.TextField(blank=True)
    verified_on = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "Compatibilité"
        verbose_name_plural = "Compatibilités"

    def clean(self):
        if self.status != self.Status.CONFIRMED:
            return
        if not (self.justification or "").strip():
            raise ValidationError(
                "Une compatibilité confirmée exige une preuve technique ou une confirmation fabricant."
            )
        if self.verified_on is None:
            raise ValidationError("Indiquez la date de vérification d'une compatibilité confirmée.")


class SuiteImage(models.Model):
    class Rights(models.TextChoices):
        TO_VERIFY = "to_verify", "À vérifier"
        ALLOWED = "allowed", "Autorisée"
        ASK = "ask", "Autorisation à demander"
        FORBIDDEN = "forbidden", "Utilisation interdite"

    class License(models.TextChoices):
        PAKNOMIK = "paknomik", "Propriété Paknomik"

    class ProductionStatus(models.TextChoices):
        TO_PRODUCE = "to_produce", "À produire"
        PRODUCED_INTERNAL = "produced_internal", "Produit en interne"

    ensemble = models.ForeignKey(
        SuiteEnsemble,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="images",
    )
    line = models.ForeignKey(
        SuiteComponentLine,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="images",
    )
    source = models.ForeignKey(
        SuiteSource,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="images",
    )
    definition = models.ForeignKey(
        "SuiteComponentDefinition",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="images",
    )
    taxon = models.ForeignKey(
        SuiteTaxon,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="images",
    )
    file = models.ImageField(upload_to="paksuites/", blank=True)
    remote_url = models.URLField(max_length=500, blank=True)
    view_type = models.CharField(max_length=80, blank=True)
    rights = models.CharField(max_length=16, choices=Rights.choices, default=Rights.TO_VERIFY)
    license = models.CharField(max_length=24, choices=License.choices, default=License.PAKNOMIK)
    production_status = models.CharField(
        max_length=24,
        choices=ProductionStatus.choices,
        default=ProductionStatus.TO_PRODUCE,
    )
    illustrative = models.BooleanField(default=True)
    suggested_filename = models.CharField(max_length=255, blank=True, default="")
    generation_prompt = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Image PakSuites"
        verbose_name_plural = "Images PakSuites"

    def __str__(self) -> str:
        return f"{self.ensemble_id or '—'} · {self.view_type or 'image'}"

    def clean(self):
        if (
            not self.ensemble_id
            and not self.line_id
            and not self.definition_id
            and not self.taxon_id
        ):
            raise ValidationError(
                "Associez l'image à un ensemble, un composant, une définition ou un type."
            )
        if not self.rights:
            raise ValidationError("Le statut des droits est requis.")



class SuiteVariant(models.Model):
    class Tier(models.TextChoices):
        ECONOMY = "economy", "Économique"
        STANDARD = "standard", "Standard"
        PREMIUM = "premium", "Premium"

    ensemble = models.ForeignKey(
        SuiteEnsemble, on_delete=models.CASCADE, related_name="variants"
    )
    tier = models.CharField(max_length=16, choices=Tier.choices)
    summary = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ensemble", "tier"], name="uniq_paksuites_variant_tier"
            )
        ]
        verbose_name = "Variante"
        verbose_name_plural = "Variantes"

    def __str__(self):
        return f"{self.ensemble.code} — {self.get_tier_display()}"


class SuiteFavorite(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="paksuites_favorites"
    )
    ensemble = models.ForeignKey(
        SuiteEnsemble, on_delete=models.CASCADE, related_name="favorites"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "ensemble"], name="uniq_paksuites_favorite"
            )
        ]
        ordering = ["-created_at"]


def publish_blockers(ensemble: SuiteEnsemble) -> list[str]:
    errors = []
    if not (ensemble.name or "").strip() or not (ensemble.description or "").strip():
        errors.append("Le nom et la description sont requis.")
    lines = list(ensemble.lines.select_related("definition"))
    if not lines:
        errors.append("Ajoutez au moins un composant.")
    elif any(not line.role or not line.definition_id for line in lines):
        errors.append("Chaque composant doit avoir un type et un rôle.")
    if not ensemble.sources.exists() and not (ensemble.sourced_facts or "").strip():
        errors.append("Une source ou une justification est requise.")
    if ensemble.images.filter(rights="").exists():
        errors.append("Le statut des droits de chaque image doit être renseigné.")
    if ensemble.confidence == SuiteEnsemble.Confidence.HYPOTHESIS and not (
        ensemble.points_to_confirm or ""
    ).strip():
        errors.append("Une hypothèse doit indiquer explicitement ses limites.")
    unresolved = [
        line
        for line in lines
        if line.status == SuiteComponentLine.Status.TO_CONFIRM
        or (line.compatibility_notes or "").strip()
    ]
    if unresolved and not (ensemble.points_to_confirm or "").strip():
        errors.append("Signalez les points techniques qui restent à confirmer.")
    return errors
