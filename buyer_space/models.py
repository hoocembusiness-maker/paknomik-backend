from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

from buyer_space.constants import SERVICE_LABELS


class BuyerAccount(models.Model):
    """Dossier acheteur créé par défaut pour un utilisateur simple."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="buyer_account",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Dossier acheteur"
        verbose_name_plural = "Dossiers acheteur"

    def __str__(self):
        return f"Dossier #{self.pk} — user {self.user_id}"

    @property
    def balance(self) -> Decimal:
        total = self.entries.aggregate(total=Sum("amount"))["total"]
        return total if total is not None else Decimal("0")


class BuyerPragmadinarEntry(models.Model):
    """Journal en ajout seul. Le solde est la somme des écritures."""

    class EntryType(models.TextChoices):
        COMPANY_GRANT = "company_grant", "Enregistrement d'entreprise"
        SPEND = "spend", "Consommation"
        ADJUSTMENT = "adjustment", "Ajustement"

    account = models.ForeignKey(
        BuyerAccount, on_delete=models.PROTECT, related_name="entries"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    entry_type = models.CharField(max_length=32, choices=EntryType.choices)
    eligible_services = models.JSONField(
        default=list,
        blank=True,
        help_text="Vide = utilisable pour tout service. Sinon liste de service_type.",
    )
    consumed_entry = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="consumptions",
    )
    service_request = models.ForeignKey(
        "ServiceRequest",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pragmadinar_entries",
    )
    idempotency_key = models.CharField(max_length=160)
    reason = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Écriture Pragmadinar acheteur"
        verbose_name_plural = "Écritures Pragmadinar acheteur"
        constraints = [
            models.UniqueConstraint(
                fields=["account", "idempotency_key"],
                name="buyer_pd_entry_idempotency",
            )
        ]

    def __str__(self):
        return f"{self.amount} — {self.reason}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Le journal Pragmadinar acheteur est en ajout seul.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Une écriture Pragmadinar acheteur ne se supprime pas.")


class BuyerProduct(models.Model):
    """Produit de l'entreprise acheteur. Distinct d'une annonce fournisseur."""

    account = models.ForeignKey(
        BuyerAccount, on_delete=models.CASCADE, related_name="products"
    )
    name = models.CharField(max_length=200)
    category = models.ForeignKey(
        "user.Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="buyer_products",
    )
    category_label = models.CharField(max_length=200, blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Produit acheteur"
        verbose_name_plural = "Produits acheteur"

    def __str__(self):
        return self.name


class PackagingProject(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        ACTIVE = "active", "Actif"
        ARCHIVED = "archived", "Archivé"

    account = models.ForeignKey(
        BuyerAccount, on_delete=models.CASCADE, related_name="projects"
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    need = models.TextField(blank=True)
    constraints = models.TextField(blank=True)
    objective = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )
    buyer_product = models.ForeignKey(
        BuyerProduct,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    target_quantity = models.PositiveIntegerField(null=True, blank=True)
    due_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="packaging_projects_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Projet emballage"
        verbose_name_plural = "Projets emballage"

    def __str__(self):
        return self.name


class LibrarySector(models.Model):
    """Secteur d'activité de la bibliothèque produits (templates)."""

    code = models.SlugField(max_length=80, unique=True)
    label_fr = models.CharField(max_length=120)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "label_fr"]
        verbose_name = "Secteur bibliothèque produits"
        verbose_name_plural = "Secteurs bibliothèque produits"

    def __str__(self):
        return self.label_fr


class LibraryProduct(models.Model):
    """Produit générique illustratif — même logique visuelle que les types d'emballage."""

    sector = models.ForeignKey(
        LibrarySector, on_delete=models.CASCADE, related_name="products"
    )
    code = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    file = models.ImageField(upload_to="product_library/", blank=True)
    suggested_filename = models.CharField(max_length=255, blank=True, default="")
    generation_prompt = models.TextField(blank=True, default="")
    recommended_suite = models.ForeignKey(
        "buyer_space.SuiteEnsemble",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recommended_for_products",
        help_text="Kit principal, repris de l'affectation de rôle Principal.",
    )
    illustrative = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sector__sort_order", "sort_order", "name"]
        verbose_name = "Produit bibliothèque"
        verbose_name_plural = "Produits bibliothèque"

    def __str__(self):
        return self.name


class PackagingKit(models.Model):
    """Dictionnaire d'un kit : statut, composants et remarques du catalogue Excel.

    Le kit lui-même reste un SuiteEnsemble. Cette fiche porte les colonnes
    qui ne sont pas le graphe de composants.
    """

    ensemble = models.OneToOneField(
        "buyer_space.SuiteEnsemble",
        on_delete=models.CASCADE,
        related_name="packaging_kit",
    )
    status_label = models.CharField(max_length=40)
    components_label = models.TextField(blank=True)
    component_count = models.PositiveIntegerField(default=0)
    remarks = models.TextField(blank=True)

    class Meta:
        db_table = "packaging_kits"
        verbose_name = "Kit d'emballage"
        verbose_name_plural = "Kits d'emballage"
        indexes = [
            models.Index(fields=["status_label"], name="packaging_kit_status_idx"),
        ]

    def __str__(self):
        return self.ensemble.code

    def component_names(self) -> list[str]:
        return [part.strip() for part in self.components_label.split("|") if part.strip()]


class ProductKitAssignment(models.Model):
    """Affectation produit ↔ kit, avec un rôle principal ou alternatif."""

    class Role(models.TextChoices):
        PRINCIPAL = "Principal", "Principal"
        ALTERNATIVE = "Alternatif", "Alternatif"

    product = models.ForeignKey(
        LibraryProduct,
        on_delete=models.CASCADE,
        related_name="kit_assignments",
    )
    kit = models.ForeignKey(
        "buyer_space.SuiteEnsemble",
        on_delete=models.PROTECT,
        related_name="product_assignments",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_kit_assignments"
        verbose_name = "Affectation produit-kit"
        verbose_name_plural = "Affectations produit-kit"
        constraints = [
            models.UniqueConstraint(
                fields=["product", "kit"],
                name="uniq_product_kit_assignment",
            ),
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(role="Principal"),
                name="uniq_product_principal_kit",
            ),
            models.CheckConstraint(
                check=models.Q(role__in=["Principal", "Alternatif"]),
                name="kit_assignment_role_ok",
            ),
        ]
        indexes = [
            models.Index(fields=["role"], name="product_kit_role_idx"),
        ]
        ordering = ["product_id", "role", "id"]

    def __str__(self):
        return f"{self.product.code} · {self.kit.code} · {self.role}"


class ProductKitAudit(models.Model):
    """Décision de l'onglet Audit pertinence, une ligne par produit revu."""

    product = models.OneToOneField(
        LibraryProduct,
        on_delete=models.CASCADE,
        related_name="kit_audit",
    )
    original_principal_code = models.CharField(max_length=40, blank=True)
    original_alternatives = models.TextField(blank=True)
    decision = models.CharField(max_length=40)
    retained_principal_code = models.CharField(max_length=40, blank=True)
    retained_alternatives = models.TextField(blank=True)
    justification = models.TextField(blank=True)

    class Meta:
        db_table = "product_kit_audits"
        verbose_name = "Audit d'affectation"
        verbose_name_plural = "Audits d'affectation"
        indexes = [
            models.Index(fields=["decision"], name="product_kit_audit_decision"),
        ]

    def __str__(self):
        return f"{self.product.code} · {self.decision}"


class ProjectProductLink(models.Model):
    """Produit (bibliothèque ou acheteur) assemblé dans un projet."""

    project = models.ForeignKey(
        PackagingProject, on_delete=models.CASCADE, related_name="product_links"
    )
    library_product = models.ForeignKey(
        LibraryProduct,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="project_links",
    )
    buyer_product = models.ForeignKey(
        BuyerProduct,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="project_links",
    )
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Produit du projet"
        verbose_name_plural = "Produits du projet"

    def __str__(self):
        return self.name


class ProjectProductVariety(models.Model):
    """Variété d'un même type de produit. Le kit reste commun ; l'étiquette et le bouchon peuvent changer."""

    link = models.ForeignKey(
        ProjectProductLink, on_delete=models.CASCADE, related_name="varieties"
    )
    name = models.CharField(max_length=120)
    label_text = models.CharField(max_length=200, blank=True, default="")
    cap_color = models.CharField(max_length=80, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Variété de produit"
        verbose_name_plural = "Variétés de produit"

    def __str__(self):
        return self.name


class ProjectSuiteKit(models.Model):
    """Kit PakSuits rattaché à un projet."""

    project = models.ForeignKey(
        PackagingProject, on_delete=models.CASCADE, related_name="suite_kits"
    )
    ensemble = models.ForeignKey(
        "buyer_space.SuiteEnsemble",
        on_delete=models.PROTECT,
        related_name="project_kits",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["project", "ensemble"]]
        ordering = ["id"]
        verbose_name = "Kit PakSuits du projet"
        verbose_name_plural = "Kits PakSuits du projet"

    def __str__(self):
        return f"{self.project_id} · {self.ensemble_id}"


SPEC_FIELDS = (
    "packaging_type",
    "material",
    "length",
    "width",
    "height",
    "dimension_unit",
    "capacity",
    "capacity_unit",
    "food_contact",
    "printing",
    "target_quantity",
)


class PackagingItem(models.Model):
    class SourceKind(models.TextChoices):
        CUSTOM = "custom", "Personnalisé"
        ARTICLE = "article", "Article d'emballage"
        SUITE_KIT = "suite_kit", "Kit PakSuits"

    project = models.ForeignKey(
        PackagingProject, on_delete=models.CASCADE, related_name="items"
    )
    name = models.CharField(max_length=200)
    packaging_type = models.CharField(max_length=120, blank=True)
    source_kind = models.CharField(
        max_length=16, choices=SourceKind.choices, default=SourceKind.CUSTOM
    )
    packaging_taxon = models.ForeignKey(
        "buyer_space.SuiteTaxon",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="packaging_items",
    )
    suite_kit = models.ForeignKey(
        "buyer_space.ProjectSuiteKit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="items",
    )
    material = models.CharField(max_length=120, blank=True)
    length = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    width = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    height = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    dimension_unit = models.CharField(max_length=8, default="mm")
    capacity = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    capacity_unit = models.CharField(max_length=8, blank=True)
    food_contact = models.BooleanField(default=False)
    printing = models.CharField(max_length=200, blank=True)
    target_quantity = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    spec_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Emballage"
        verbose_name_plural = "Emballages"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.pk:
            previous = (
                PackagingItem.objects.filter(pk=self.pk).values(*SPEC_FIELDS).first()
            )
            if previous and _spec_changed(previous, self):
                self.spec_version = (self.spec_version or 1) + 1
                update_fields = kwargs.get("update_fields")
                if update_fields is not None:
                    kwargs["update_fields"] = set(update_fields) | {"spec_version"}
        super().save(*args, **kwargs)


def _spec_changed(previous, item) -> bool:
    for field in SPEC_FIELDS:
        old = previous[field]
        new = getattr(item, field)
        if old is None and new is None:
            continue
        if isinstance(old, Decimal) or isinstance(new, Decimal):
            if old is None or new is None or Decimal(old) != Decimal(new):
                return True
            continue
        if old != new:
            return True
    return False


class ProjectCatalogProduct(models.Model):
    """Produit catalogue rattaché à un projet. Les favoris restent dans user."""

    project = models.ForeignKey(
        PackagingProject, on_delete=models.CASCADE, related_name="catalog_products"
    )
    product = models.ForeignKey(
        "user.Product", on_delete=models.CASCADE, related_name="buyer_project_links"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["project", "product"]]
        verbose_name = "Produit catalogue du projet"
        verbose_name_plural = "Produits catalogue du projet"


class ProjectSupplier(models.Model):
    project = models.ForeignKey(
        PackagingProject, on_delete=models.CASCADE, related_name="suppliers"
    )
    supplier = models.ForeignKey(
        "user.Supplier", on_delete=models.CASCADE, related_name="buyer_project_links"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["project", "supplier"]]
        verbose_name = "Fournisseur du projet"
        verbose_name_plural = "Fournisseurs du projet"


class ServiceAvailability(models.Model):
    class State(models.TextChoices):
        AVAILABLE_ON_REQUEST = "available_on_request", "Sur demande"
        COMING_SOON = "coming_soon", "Bientôt"
        UNAVAILABLE = "unavailable", "Indisponible"

    service_type = models.CharField(max_length=16, unique=True)
    label = models.CharField(max_length=80)
    state = models.CharField(
        max_length=32,
        choices=State.choices,
        default=State.AVAILABLE_ON_REQUEST,
    )
    message = models.CharField(max_length=300, blank=True)
    free_quota = models.PositiveIntegerField(null=True, blank=True)
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Barème en Pragmadinars. Vide = aucun débit à l'envoi.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Disponibilité de service"
        verbose_name_plural = "Disponibilités de service"

    def __str__(self):
        return SERVICE_LABELS.get(self.service_type, self.label)


class ServiceRequest(models.Model):
    class ServiceType(models.TextChoices):
        DESIGN = "DESIGN", "Paknoma Design"
        ANALYTICS = "ANALYTICS", "Paklytik"
        PAKSUITES = "PAKSUITES", "PakSuits"
        PAKMATH = "PAKMATH", "Pakmath"

    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Envoyée"
        NEEDS_INFO = "NEEDS_INFO", "Information demandée"
        IN_PROGRESS = "IN_PROGRESS", "En cours"
        RESULT_READY = "RESULT_READY", "Résultat disponible"
        CLOSED = "CLOSED", "Clôturée"

    reference = models.CharField(max_length=16, unique=True, null=True, blank=True)
    account = models.ForeignKey(
        BuyerAccount, on_delete=models.CASCADE, related_name="service_requests"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="service_requests",
    )
    service_type = models.CharField(max_length=16, choices=ServiceType.choices)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SUBMITTED
    )
    project = models.ForeignKey(
        PackagingProject,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="service_requests",
    )
    packaging_item = models.ForeignKey(
        PackagingItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="service_requests",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    payload_schema_version = models.PositiveIntegerField(default=1)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_service_requests",
    )
    desired_on = models.DateField(null=True, blank=True)
    quota_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Demande de service"
        verbose_name_plural = "Demandes de service"
        indexes = [
            models.Index(fields=["account", "status"]),
            models.Index(fields=["service_type", "status"]),
        ]

    def __str__(self):
        return self.reference or f"Demande #{self.pk}"


class ServiceRequestFile(models.Model):
    request = models.ForeignKey(
        ServiceRequest, on_delete=models.CASCADE, related_name="files"
    )
    file = models.FileField(upload_to="buyer_space/requests/")
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="service_request_files",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class ServiceRequestMessage(models.Model):
    class Role(models.TextChoices):
        BUYER = "buyer", "Acheteur"
        TEAM = "team", "Équipe"

    request = models.ForeignKey(
        ServiceRequest, on_delete=models.CASCADE, related_name="messages"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="service_request_messages",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class ServiceRequestMessageFile(models.Model):
    message = models.ForeignKey(
        ServiceRequestMessage, on_delete=models.CASCADE, related_name="files"
    )
    file = models.FileField(upload_to="buyer_space/messages/")
    original_name = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]


class ServiceRequestDeliverable(models.Model):
    request = models.ForeignKey(
        ServiceRequest, on_delete=models.CASCADE, related_name="deliverables"
    )
    label = models.CharField(max_length=200)
    file = models.FileField(upload_to="buyer_space/deliverables/", blank=True)
    result = models.JSONField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    is_indicative = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="service_deliverables_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["version", "id"]
        verbose_name = "Livrable"
        verbose_name_plural = "Livrables"


class ServiceRequestEvent(models.Model):
    request = models.ForeignKey(
        ServiceRequest, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="service_request_events",
    )
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Événement de demande"
        verbose_name_plural = "Événements de demande"


class PaksuitesOption(models.Model):
    service_request = models.ForeignKey(
        ServiceRequest, on_delete=models.CASCADE, related_name="paksuites_options"
    )
    title = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Option PakSuits"
        verbose_name_plural = "Options PakSuits"

    def __str__(self):
        return self.title


class PaksuitesComponent(models.Model):
    option = models.ForeignKey(
        PaksuitesOption, on_delete=models.CASCADE, related_name="components"
    )
    packaging_type = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    specification = models.TextField(blank=True)
    suggested_supplier = models.ForeignKey(
        "user.Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paksuites_components",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Composant PakSuits"
        verbose_name_plural = "Composants PakSuits"

    def __str__(self):
        return self.packaging_type


from buyer_space.catalogue_models import (  # noqa: E402,F401
    SuiteAttribute,
    SuiteCompatibility,
    SuiteComponentDefinition,
    SuiteComponentLine,
    SuiteEnsemble,
    SuiteFavorite,
    SuiteImage,
    SuiteReference,
    SuiteSource,
    SuiteSupplierOffer,
    SuiteTaxon,
    SuiteVariant,
)
