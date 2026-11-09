"""
Database models for the Paknomik app (Django ORM).

Each `class` in this file describes one database table. Examples:
users and login (`User`), supplier companies (`Supplier`), catalog
products (`Product`), orders, quote requests, messages, and categories.

Reference tables (categories, materials, search tags, technical attributes)
come mostly from the Excel knowledge base. Marketplace tables are created
when real suppliers and buyers use the site.

If you are new to Django: changing a model requires
`python manage.py makemigrations` then `migrate` to update the database.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models


# ========================================================================
# ACCOUNTS
# ========================================================================


class User(AbstractUser):
    """Paknomik user — a Simple Client by default, upgradeable to Verified Client."""

    email = models.EmailField(unique=True)
    is_verified_client = models.BooleanField(default=False)
    
    company_name = models.CharField(max_length=255, blank=True, null=True)
    nature_of_activity = models.CharField(max_length=100, blank=True, null=True)
    annual_purchase_volume = models.CharField(max_length=100, blank=True, null=True)
    wilaya = models.CharField(max_length=100, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    profile_picture = models.ImageField(
        upload_to="buyers/profile/",
        blank=True,
        null=True,
    )
    favorites_notifications_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the supplier last opened the favorites activity page.",
    )
    orders_notifications_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the user last opened the orders page (navbar badge).",
    )
    interested_subcategories = models.ManyToManyField(
        "Category",
        blank=True,
        related_name="buyers_interested",
        help_text="Sous-catégies d'intérêt déclarées par l'acheteur (signaux CRM).",
    )

    supplier = models.ForeignKey(
        "Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        status = "Verified Client" if self.is_verified_client else "Simple Client"
        return f"{self.email} ({status})"


# ========================================================================
# KNOWLEDGE BASE — CORE (taxonomy + attributes = the MVP's backbone)
# ========================================================================


class Category(models.Model):
    """Product taxonomy tree: Famille (root) → Type de produit (child).

    Level 1 rows are families (e.g. Contenants rigides). Level 2 rows are
    product types linked via ``parent`` (e.g. Bouteille PET). Products should
    reference a type when possible. Commercial sectors stay on
    ``Product.target_markets``, not as taxonomy families.
    """

    external_id = models.CharField(max_length=64, unique=True, blank=True, null=True)
    slug = models.SlugField(max_length=80, unique=True, null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )
    level = models.PositiveSmallIntegerField(default=1)
    image = models.ImageField(upload_to="categories/", blank=True, null=True)
    name_en = models.CharField(max_length=200)
    name_fr = models.CharField(max_length=200, blank=True)
    name_ar = models.CharField(max_length=200, blank=True)
    plain_description = models.TextField(blank=True)
    is_new_in_v2 = models.BooleanField(default=False)

    scientific_description = models.TextField(blank=True)
    typical_industries = models.TextField(blank=True)
    typical_applications = models.TextField(blank=True)
    examples = models.TextField(blank=True)

    class Meta:
        ordering = ["sort_order", "name_en"]
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name_en

    @property
    def is_family(self) -> bool:
        return self.parent_id is None

    @property
    def is_service_node(self) -> bool:
        from user.taxonomy_v3 import is_service_category

        return is_service_category(self)

    @property
    def total_products(self):
        return Product.objects.filter(category=self).count()
        


class TechnicalAttribute(models.Model):
    """Reusable technical attribute definition (e.g. Height, Material).

    v2 catalog uses two levels per category:
      - Level 1: search-filter dropdowns (attribute_type=choice, required to publish)
      - Level 2: product specs (number, text, or boolean — optional, improves score)

    Not bound directly to a Category — see CategoryAttribute.
    """

    ATTRIBUTE_TYPES = (
        ("choice", "Choice"),
        ("number", "Number"),
        ("text", "Text"),
        ("boolean", "Boolean"),
    )
    ATTRIBUTE_LEVELS = (
        (1, "Level 1 — Search filter"),
        (2, "Level 2 — Product spec"),
    )
    # A product may list several values for these types (e.g. 20, 30 and 50 µm);
    # boolean and text attributes keep a single value.
    MULTI_VALUE_TYPES = ("number", "choice")

    external_id = models.CharField(max_length=64, unique=True, blank=True, null=True)
    name_en = models.CharField(max_length=150)
    name_fr = models.CharField(max_length=150, blank=True)
    name_ar = models.CharField(max_length=150, blank=True)
    attribute_level = models.PositiveSmallIntegerField(
        choices=ATTRIBUTE_LEVELS, default=2
    )
    attribute_type = models.CharField(
        max_length=20, choices=ATTRIBUTE_TYPES, default="text"
    )
    unit = models.ForeignKey(
        "Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attributes",
    )
    is_required = models.BooleanField(default=False)
    is_searchable = models.BooleanField(default=False)
    is_comparable = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    examples = models.TextField(blank=True)

    categories = models.ManyToManyField(
        Category, through="CategoryAttribute", related_name="attributes"
    )

    class Meta:
        ordering = ["attribute_level", "name_en"]

    def __str__(self):
        return self.name_en

    @property
    def allows_multiple_values(self) -> bool:
        return self.attribute_type in self.MULTI_VALUE_TYPES


class CategoryAttribute(models.Model):
    """Bridge table: which technical attributes apply to which category.

    Level 1 links are required for publishing; Level 2 links are optional
    but improve listing quality. Dropdown options live in AttributeOption.
    """

    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="category_attributes"
    )
    attribute = models.ForeignKey(
        TechnicalAttribute, on_delete=models.CASCADE, related_name="category_attributes"
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_required_for_publish = models.BooleanField(default=False)

    class Meta:
        unique_together = [["category", "attribute"]]
        ordering = ["sort_order", "attribute__name_en"]

    def __str__(self):
        return f"{self.category.name_en} — {self.attribute.name_en}"


class AttributeOption(models.Model):
    """Predefined choice for a Level 1 (dropdown) category attribute."""

    category_attribute = models.ForeignKey(
        CategoryAttribute, on_delete=models.CASCADE, related_name="options"
    )
    value_en = models.CharField(max_length=150)
    value_fr = models.CharField(max_length=150, blank=True)
    value_ar = models.CharField(max_length=150, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "value_en"]
        unique_together = [["category_attribute", "value_en"]]

    def __str__(self):
        return self.value_en


# ========================================================================
# KNOWLEDGE BASE — FLAT LOOKUPS (kept simple on purpose)
# ========================================================================


class Unit(models.Model):
    """Measurement unit — flat lookup, imported from 'Units Dictionary'."""

    name = models.CharField(max_length=100, unique=True)
    symbol = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.symbol or self.name


class Industry(models.Model):
    """Reference industry — flat lookup, imported from 'Packaging Industries'."""

    name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Industries"

    def __str__(self):
        return self.name


class Material(models.Model):
    """Packaging material — flat lookup, imported from 'Packaging Materials'.

    Trimmed to the fields needed for filtering/labelling a product listing;
    the richer descriptive columns (advantages, limitations, recyclability,
    etc.) live in the source spreadsheet but aren't modelled yet.
    """

    external_id = models.CharField(max_length=20, unique=True, blank=True, null=True)
    name_en = models.CharField(max_length=150)
    name_fr = models.CharField(max_length=150, blank=True)
    name_ar = models.CharField(max_length=150, blank=True)
    family = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["name_en"]

    def __str__(self):
        return self.name_en


# ========================================================================
# KNOWLEDGE BASE — SEARCH VOCABULARY
# ========================================================================


class Lexicon(models.Model):
    """Raw, full-fidelity import of the 'Packaging Lexicon' sheet."""

    keyword = models.CharField(max_length=200)
    french = models.CharField(max_length=200, blank=True)
    arabic = models.CharField(max_length=200, blank=True)
    category_name = models.CharField(max_length=200, blank=True)
    industry_name = models.CharField(max_length=200, blank=True)
    related_product = models.CharField(max_length=255, blank=True)
    synonyms = models.TextField(blank=True)
    alternative_names = models.TextField(blank=True)
    search_weight = models.PositiveSmallIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-search_weight", "keyword"]
        verbose_name_plural = "Lexicon entries"

    def __str__(self):
        return self.keyword


class SearchTerm(models.Model):
    """Controlled search keyword linked to taxonomy (category / subcategory / attribute)."""

    class Language(models.TextChoices):
        FR = "fr", "Français"
        AR = "ar", "Arabe"
        EN = "en", "Anglais"
        DZ = "dz", "Local / dialecte"

    class TermType(models.TextChoices):
        CANONICAL = "canonical", "Canonique"
        SYNONYM = "synonym", "Synonyme"
        TECHNICAL = "technical", "Technique"
        COMMERCIAL = "commercial", "Commercial"
        ABBREVIATION = "abbreviation", "Abréviation"
        TRANSLATION = "translation", "Traduction"
        MISSPELLING = "common_misspelling", "Faute courante"
        LOCAL = "local_term", "Terme local"

    term = models.CharField(max_length=200)
    normalized_term = models.CharField(max_length=200, db_index=True)
    language = models.CharField(
        max_length=8, choices=Language.choices, default=Language.FR
    )
    term_type = models.CharField(
        max_length=32, choices=TermType.choices, default=TermType.CANONICAL
    )
    is_active = models.BooleanField(default=True)
    is_ambiguous = models.BooleanField(
        default=False,
        help_text="Terme générique (ex. film, rouleau) — ne force pas une seule sous-catégorie.",
    )
    priority = models.PositiveSmallIntegerField(default=50)
    weight = models.PositiveSmallIntegerField(default=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "term"]
        indexes = [
            models.Index(fields=["normalized_term", "is_active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["normalized_term", "language", "term_type"],
                name="uniq_search_term_norm_lang_type",
            )
        ]

    def __str__(self):
        return self.term


class SearchTermMapping(models.Model):
    """Hierarchical link: keyword → category → subcategory → optional attribute value."""

    search_term = models.ForeignKey(
        SearchTerm, on_delete=models.CASCADE, related_name="mappings"
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="search_term_mappings",
    )
    subcategory = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="search_term_mappings_as_sub",
    )
    attribute = models.ForeignKey(
        TechnicalAttribute,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="search_term_mappings",
    )
    attribute_value = models.CharField(max_length=200, blank=True)
    confidence = models.PositiveSmallIntegerField(default=80)
    priority = models.PositiveSmallIntegerField(default=50)
    context = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-priority", "-confidence"]
        indexes = [
            models.Index(fields=["is_active", "subcategory"]),
        ]

    def __str__(self):
        target = self.subcategory or self.category
        return f"{self.search_term.term} → {target}"


class SearchQueryMiss(models.Model):
    """Logged searches that returned no product results."""

    original_query = models.CharField(max_length=255)
    normalized_query = models.CharField(max_length=255, db_index=True)
    language_hint = models.CharField(max_length=8, blank=True)
    results_count = models.PositiveIntegerField(default=0)
    suggested_subcategory_id = models.IntegerField(null=True, blank=True)
    buyer_id = models.IntegerField(null=True, blank=True)
    source = models.CharField(max_length=32, default="products")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Recherche sans résultat"
        verbose_name_plural = "Recherches sans résultat"


class SearchTag(models.Model):
    """Product search keyword — imported from paknomik_mots_cles_produits_exhaustif."""

    tag = models.CharField(max_length=150, unique=True)
    tag_fr = models.CharField(max_length=150, blank=True)
    tag_ar = models.CharField(max_length=150, blank=True)
    tag_group = models.CharField(max_length=100, blank=True)
    industry_name = models.CharField(max_length=150, blank=True)
    sub_category = models.CharField(max_length=150, blank=True)
    search_weight = models.PositiveSmallIntegerField(default=0)
    synonyms = models.TextField(blank=True)

    class Meta:
        ordering = ["-search_weight", "tag"]

    def __str__(self):
        return self.tag_fr or self.tag


class ProductPackagingMapping(models.Model):
    """Final product -> full packaging chain, from 'Product Packaging Mapping'.

    Reference/advisor data: not yet wired into the search endpoint.
    """

    final_product = models.CharField(max_length=200)
    industry = models.ForeignKey(
        Industry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="packaging_mappings",
    )
    industry_name_raw = models.CharField(max_length=200, blank=True)

    primary_packaging = models.TextField(blank=True)
    secondary_packaging = models.TextField(blank=True)
    tertiary_packaging = models.TextField(blank=True)
    transport_packaging = models.TextField(blank=True)
    protective_packaging = models.TextField(blank=True)

    possible_materials = models.TextField(blank=True)
    typical_labels = models.TextField(blank=True)
    typical_closures = models.TextField(blank=True)
    examples = models.TextField(blank=True)

    class Meta:
        ordering = ["final_product"]
        indexes = [models.Index(fields=["final_product"])]

    def __str__(self):
        return self.final_product


class PackagingComponent(models.Model):
    """Component breakdown per product category, from 'Packaging Components'.

    Reference/advisor data: not yet wired into the search endpoint.
    """

    product_category = models.CharField(max_length=200)
    component_level = models.CharField(max_length=100, blank=True)
    component_name = models.CharField(max_length=200)
    material = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    role = models.TextField(blank=True)
    typical_spec = models.CharField(max_length=255, blank=True)
    examples = models.TextField(blank=True)

    class Meta:
        ordering = ["product_category", "component_level"]

    def __str__(self):
        return f"{self.product_category} — {self.component_name}"


# ========================================================================
# MARKETPLACE — SUPPLIERS & LISTINGS (empty until suppliers onboard)
# ========================================================================


class Supplier(models.Model):
    """Supplier/company profile."""
    # fr fr
    WILAYA_CHOICES = (
        ('adrar', 'Adrar'),
        ('algiers', 'Alger'),
        ('annaba', 'Annaba'),
        ('constantine', 'Constantine'),
        ('oran', 'Oran'),
        ('setif', 'Setif'),
        ('tizi_ouzou', 'Tizi Ouzou'),
        ('blida', 'Blida'),
        ('bejaia', 'Bejaia'),
        ('batna', 'Batna'),
        ('biskra', 'Biskra'),
        ('tebessa', 'Tebessa'),
        ('tipaza', 'Tipaza'),
        ('tlemcen', 'Tlemcen'),
        ('djelfa', 'Djelfa'),
        ('souk_ahras', 'Souk Ahras'),
        ('ghardaia', 'Ghardaia'),
        ('mascara', 'Mascara'),
        ('medea', 'Medea'),
        ('mila', 'Mila'),
        ('mostaganem', 'Mostaganem'),
        ('msila', 'Msila'),
        ('naama', 'Naama'),
        ('ouargla', 'Ouargla'),
        ('el_bayadh', 'El Bayadh'),
        ('el_oued', 'El Oued'),
        ('el_tarf', 'El Tarf'),
        ('bordj_bou_arreridj', 'Bordj Bou Arreridj'),
        ('bouira', 'Bouira'),
        ('bumerdes', 'Bumerdes'),
        ('chlef', 'Chlef'),
        ('relizane', 'Relizane'),
        ('saida', 'Saida'),
        ('sidi_bel_abbes', 'Sidi Bel Abbes'),
        ('skikda', 'Skikda'),
        ('ain_defda', 'Ain Defda'),
        ('ain_temouchent', 'Ain Temouchent'),
        ('bechar', 'Bechar'),
        ('beni_abbes', 'Beni Abbes'),
        ('djanet', 'Djanet'),
        ('el_meghaier', 'El Meghaier'),
        ('el_meniaa', 'El Meniaa'),
        ('illizi', 'Illizi'),
        ('in_guezzam', 'In Guezzam'),
        ('in_salah', 'In Salah'),
        ('laghouat', 'Laghouat'),
        ('tamanrasset', 'Tamanrasset'),
        ('timimoun', 'Timimoun'),
        ('tindouf', 'Tindouf'),
        ('touggourt', 'Touggourt'),
    )
    
    company_name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    address = models.TextField(blank=True, null=True)
    wilaya = models.CharField(
        max_length=100, choices=WILAYA_CHOICES, blank=True, null=True
    )
    TYPE_CHOICES = (
        ("manufacturer", "Manufacturer"),
        ("distributor", "Distributor"),
    )
    SUBSCRIPTION_TYPE_CHOICES = (
        ("Classic", "Classic"),
        ("Extra", "Extra"),
        ("Gold", "Gold"),
    )
    subscription_type = models.CharField(
        max_length=100,
        choices=SUBSCRIPTION_TYPE_CHOICES,
        blank=True,
        null=True,
        default="Classic",
    )

    subscription_start_date = models.DateTimeField(blank=True, null=True)
    subscription_end_date = models.DateTimeField(blank=True, null=True)
    PAKNOMIK_PLAN_CHOICES = (
        ("genereux", "Paknomik Généreux"),
        ("business", "Paknomik Business"),
    )
    paknomik_plan = models.CharField(
        max_length=20,
        choices=PAKNOMIK_PLAN_CHOICES,
        default="genereux",
        db_index=True,
    )
    type = models.CharField(max_length=100, choices=TYPE_CHOICES, blank=True, null=True)
    KIND_CHOICES = (
        ("DISTRIBUTOR", "Distributeur"),
        ("MANUFACTURER", "Fabricant"),
        ("BOTH", "Distributeur et fabricant"),
    )
    kind = models.CharField(
        max_length=16,
        choices=KIND_CHOICES,
        blank=True,
        default="",
        help_text=(
            "Mode par défaut à la création d'une gamme, et badge. "
            "Ne crée pas un catalogue séparé."
        ),
    )
    website = models.URLField(blank=True, null=True)
    instagram_url = models.URLField(blank=True, null=True)
    facebook_url = models.URLField(blank=True, null=True)
    linkedin_url = models.URLField(blank=True, null=True)
    whatsapp_number = models.CharField(max_length=20, blank=True, null=True)
    presentation = models.TextField(blank=True, null=True)
    presentation_html = models.TextField(
        blank=True,
        default="",
        help_text="Présentation mise en forme (HTML filtré). Le texte brut est dérivé dans « presentation ».",
    )
    brand_color = models.CharField(max_length=20, blank=True, null=True)
    brand_button_color = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="Couleur d'arrière-plan des boutons (devis, commande, contact).",
    )
    brand_neutral_color = models.CharField(
        max_length=7,
        choices=(
            ("#ffffff", "Blanc"),
            ("#000000", "Noir"),
        ),
        default="#ffffff",
        help_text="Couleur du texte sur les cartes produit (blanc ou noir uniquement).",
    )
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    is_ghost = models.BooleanField(
        default=False,
        help_text="Masque le fournisseur et ses produits sur le site public. Le compte reste dans l'administration.",
    )
    interested_categories = models.ManyToManyField(
        Category,
        blank=True,
        related_name='suppliers_by_category',
        limit_choices_to={'parent__isnull': True},
    )
    interested_subcategories = models.ManyToManyField(
        Category,
        blank=True,
        related_name='suppliers_by_subcategory',
        limit_choices_to={'parent__isnull': False},
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # supplier logo
    logo = models.ImageField(upload_to="suppliers/", blank=True, null=True)
    strategy_tool_visits = models.PositiveSmallIntegerField(default=0)
    strategy_tool_last_visit_token = models.CharField(max_length=64, blank=True, default="")
    strategy_reminder_shown_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ["company_name"]

    def __str__(self):
        return self.company_name

    def save(self, *args, **kwargs):
        from user.rich_text import sync_rich_field

        update_fields = kwargs.get("update_fields")
        sync_rich_field(self, "presentation_html", "presentation", update_fields)
        if update_fields is not None and "presentation_html" in update_fields:
            kwargs["update_fields"] = {*update_fields, "presentation"}
        super().save(*args, **kwargs)


class SupplierScore(models.Model):
    """Paknomik score for a supplier — total plus category sub-scores."""

    supplier = models.OneToOneField(
        Supplier,
        on_delete=models.CASCADE,
        related_name="score_record",
    )
    score_total = models.BigIntegerField(default=50)
    score_presence = models.BigIntegerField(default=0)
    score_responsivity = models.BigIntegerField(default=0)
    score_catalog = models.BigIntegerField(default=0)
    score_profile = models.BigIntegerField(default=0)
    score_commercial = models.BigIntegerField(default=0)
    last_login_date = models.DateField(null=True, blank=True)
    presence_streak = models.PositiveIntegerField(default=0)
    profile_bonuses_awarded = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Supplier score"
        verbose_name_plural = "Supplier scores"
        ordering = ["-score_total", "supplier_id"]

    def __str__(self):
        return f"{self.supplier.company_name} — {self.score_total}"


class SupplierSettings(models.Model):
    """Per-supplier configuration: FAQ, card design, listing fields, proposal template."""

    supplier = models.OneToOneField(
        Supplier,
        on_delete=models.CASCADE,
        related_name="settings",
    )
    product_card_style = models.JSONField(default=dict, blank=True)
    faq_items = models.JSONField(default=list, blank=True)
    listing_form_fields = models.JSONField(default=list, blank=True)
    listing_card_fields = models.JSONField(default=list, blank=True)
    listing_rules = models.JSONField(default=list, blank=True)
    proposal_template = models.TextField(blank=True, default="")
    proposal_blocks = models.JSONField(default=list, blank=True)
    proposal_templates = models.JSONField(default=list, blank=True)
    page_design = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Supplier settings"
        verbose_name_plural = "Supplier settings"

    def __str__(self):
        return f"Settings — {self.supplier.company_name}"


class Service(models.Model):
    """A supplier's service."""

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="services"
    )
    name = models.CharField(max_length=200)
    description = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

class Product(models.Model):
    """A supplier's sellable packaging item (marketplace listing)."""

    class ListingType(models.TextChoices):
        SELL = "sell", "Produit à vendre"
        MAKE = "make", "Produit à fabriquer"

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="products"
    )
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products"
    )
    subcategory = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products_subcategory"
    , null=True, blank=True)
    material = models.ForeignKey(
        Material,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )
    search_tags = models.ManyToManyField(
        SearchTag,
        blank=True,
        related_name="products",
    )
    custom_keywords = models.JSONField(
        default=list,
        blank=True,
        help_text="Supplier-defined search keywords outside the Paknomik catalog.",
    )
    target_markets = models.JSONField(
        default=list,
        blank=True,
        help_text="Commercial segments this product targets (restaurant, cafe, pharma, etc.).",
    )

    name = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True, null=True)
    description = models.TextField()
    description_html = models.TextField(
        blank=True,
        default="",
        help_text="Description mise en forme (HTML filtré). Le texte brut est dérivé dans « description ».",
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Prix fixe, ou borne basse quand « price_max » est renseigné.",
    )
    price_max = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Borne haute d'un intervalle de prix (vide = prix fixe).",
    )
    min_order_quantity = models.PositiveIntegerField(default=1)
    lead_time_days = models.PositiveIntegerField(default=14)
    listing_type = models.CharField(
        max_length=8,
        choices=ListingType.choices,
        default=ListingType.SELL,
        help_text="Vendu en l'état (commande directe) ou fabriqué sur demande (demande de devis).",
    )
    class OfferMode(models.TextChoices):
        STOCK = "STOCK", "À vendre"
        SUR_MESURE = "SUR_MESURE", "Sur mesure"

    product_type = models.ForeignKey(
        "ProductType",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="products",
        help_text="Type de la nouvelle taxonomie. Vide = fiche encore classée seulement par catégorie.",
    )
    offer_mode = models.CharField(
        max_length=16,
        choices=OfferMode.choices,
        blank=True,
        default="",
        db_index=True,
        help_text="STOCK = à vendre, SUR_MESURE = à concevoir. Vide = comportement historique.",
    )
    attributes = models.JSONField(
        default=dict,
        blank=True,
        help_text="Valeurs de la gamme, validées par le gabarit du type.",
    )
    technical_sheet = models.FileField(
        upload_to="products/sheets/",
        blank=True,
        null=True,
        help_text="Fiche technique PDF.",
    )
    is_flagship = models.BooleanField(
        default=False,
        help_text="Modèle phare du fournisseur pour ce type. Le classement le préfère.",
    )
    is_mixed = models.BooleanField(
        default=False,
        help_text="Standard et personnalisable : action principale selon le mode, lien vers l'autre.",
    )

    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} — {self.supplier.company_name}"

    @property
    def has_price_range(self) -> bool:
        return self.price is not None and self.price_max is not None

    @property
    def fixed_unit_price(self):
        """Unit price usable on an order: None when the price is a range or unknown."""
        return None if self.has_price_range else self.price

    def save(self, *args, **kwargs):
        from user.rich_text import sync_rich_field

        update_fields = kwargs.get("update_fields")
        sync_rich_field(self, "description_html", "description", update_fields)
        if update_fields is not None and "description_html" in update_fields:
            kwargs["update_fields"] = {*update_fields, "description"}
        super().save(*args, **kwargs)

    def get_display_image(self):
        """Product image, or subcategory/category image as fallback."""
        product_image = (
            self.images.filter(is_primary=True).first() or self.images.first()
        )
        if product_image:
            return product_image.image
        if self.subcategory_id and self.subcategory.image:
            return self.subcategory.image
        if self.category_id and self.category.image:
            return self.category.image
        return None


class ProductAttributeValue(models.Model):
    """Value of a technical attribute for a specific product listing.

    One row per value: number and choice attributes may have several rows for
    the same product (see TechnicalAttribute.allows_multiple_values).
    """

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="attribute_values"
    )
    attribute = models.ForeignKey(
        TechnicalAttribute, on_delete=models.CASCADE, related_name="product_values"
    )
    num_value = models.FloatField(blank=True, null=True)
    txt_value = models.TextField(blank=True, null=True)
    bool_value = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["product", "attribute"])]

    def __str__(self):
        if self.num_value is not None:
            return f"{self.attribute.name_en}: {self.num_value} {self.attribute.unit or ''}"
        if self.txt_value:
            return f"{self.attribute.name_en}: {self.txt_value}"
        return f"{self.attribute.name_en}: {'yes' if self.bool_value else 'no'}"


class ProductImage(models.Model):
    """Additional images for a product listing."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to="products/")
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_primary", "id"]

    def __str__(self):
        return f"Image for {self.product.name}"


class ProductFamily(models.Model):
    """Niveau 1 de la taxonomie (« Catégorie » des formulaires)."""

    slug = models.SlugField(max_length=80, unique=True)
    name_fr = models.CharField(max_length=200)
    name_en = models.CharField(max_length=200, blank=True)
    name_ar = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    legacy_category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="catalog_families",
        help_text="Catégorie historique conservée. Rien n'est supprimé de l'ancien arbre.",
    )

    class Meta:
        ordering = ["sort_order", "name_fr"]
        verbose_name = "Famille de produits"
        verbose_name_plural = "Familles de produits"

    def __str__(self):
        return self.name_fr


class ProductType(models.Model):
    """Niveau 2 (« Sous-catégorie »). Tout le reste est un attribut du gabarit."""

    family = models.ForeignKey(
        ProductFamily, on_delete=models.PROTECT, related_name="types"
    )
    slug = models.SlugField(max_length=80, unique=True)
    name_fr = models.CharField(max_length=200)
    name_en = models.CharField(max_length=200, blank=True)
    name_ar = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    attribute_schema = models.JSONField(default=list, blank=True)
    legacy_category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="catalog_types",
        help_text="Sous-catégorie historique. Conservée pour l'ancien catalogue.",
    )

    class Meta:
        ordering = ["family__sort_order", "sort_order", "name_fr"]
        verbose_name = "Type de produit"
        verbose_name_plural = "Types de produit"

    def __str__(self):
        return self.name_fr

    def clean(self):
        from django.core.exceptions import ValidationError

        from user.catalog_taxonomy import SchemaError, validate_attribute_schema

        try:
            self.attribute_schema = validate_attribute_schema(self.attribute_schema)
        except SchemaError as exc:
            raise ValidationError({"attribute_schema": exc.messages}) from exc


class TypeAlternative(models.Model):
    """Alternative commerciale entre deux types. Saisie manuelle, sans apprentissage."""

    class Level(models.TextChoices):
        DIRECT = "DIRECT", "Direct"
        PROCHE = "PROCHE", "Proche"

    from_type = models.ForeignKey(
        ProductType, on_delete=models.CASCADE, related_name="alternatives_out"
    )
    to_type = models.ForeignKey(
        ProductType, on_delete=models.CASCADE, related_name="alternatives_in"
    )
    level = models.CharField(max_length=8, choices=Level.choices)
    bidirectional = models.BooleanField(default=True)
    attribute_map = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=200)

    class Meta:
        ordering = ["from_type_id", "to_type_id"]
        verbose_name = "Alternative de type"
        verbose_name_plural = "Alternatives de type"
        constraints = [
            models.UniqueConstraint(
                fields=["from_type", "to_type"],
                name="uniq_type_alternative_pair",
            ),
            models.CheckConstraint(
                check=~models.Q(from_type=models.F("to_type")),
                name="type_alternative_distinct_types",
            ),
        ]

    def __str__(self):
        return f"{self.from_type} → {self.to_type} ({self.level})"


class ProductVariant(models.Model):
    """SKU d'une gamme. Trois axes au plus (capacité, couleur, …)."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variants"
    )
    sku = models.CharField(max_length=64, blank=True)
    axes = models.JSONField(default=dict, blank=True)
    price_from = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    moq = models.PositiveIntegerField(null=True, blank=True)
    is_available = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Variante"
        verbose_name_plural = "Variantes"

    def __str__(self):
        return self.sku or f"Variante #{self.pk} — {self.product_id}"


class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.email

class TradeScope(models.TextChoices):
    LOCAL = "local", "Locale (Algérie)"
    EXPORT = "export", "Export"


class Order(models.Model):
    """Workflow 1 — Commande directe (produit catalogue → 1 fournisseur).

    A follow-up record between buyer and supplier: Paknomik never collects the
    payment, guarantees the transaction or organises the transport.
    """

    class Origin(models.TextChoices):
        PRODUCT_PAGE = "paknomik_product", "Fiche produit Paknomik"
        QUOTE_DEMAND = "paknomik_quote", "Demande de devis Paknomik"
        RFQ = "paknomik_rfq", "Devis boutique Paknomik"
        EXTERNAL = "external", "Vente externe (hors Paknomik)"

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        CONFIRMED = "confirmed", "Confirmée"
        IN_NEGOTIATION = "in_negotiation", "En négociation"
        WON = "won", "Gagnée"
        LOST = "lost", "Perdue"
        IN_PRODUCTION = "in_production", "En production"
        SHIPPED = "shipped", "Expédiée"
        DELIVERED = "delivered", "Livrée"
        COMPLETED = "completed", "Terminée"
        CANCELLED = "cancelled", "Annulée"
        EXPIRED = "expired", "Expirée"

    buyer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    guest_name = models.CharField(max_length=255, blank=True)
    guest_phone_country_code = models.CharField(max_length=8, blank=True, default="+213")
    guest_phone = models.CharField(max_length=32, blank=True)
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="orders"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="orders"
    )
    variant = models.ForeignKey(
        "ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        help_text="SKU commandé. Vide = commande historique sur la fiche seule.",
    )
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    specifications = models.TextField(
        blank=True,
        help_text="Spécifications complémentaires saisies à la commande.",
    )
    commercial_snapshot = models.JSONField(
        null=True,
        blank=True,
        help_text="Instantané des lignes et conditions acceptées (proposition / devis).",
    )
    buyer_po_reference = models.CharField(max_length=120, blank=True)
    billing_address = models.TextField(blank=True)
    delivery_address = models.TextField(blank=True)
    delivery_instructions = models.TextField(blank=True)
    receiver_contact = models.CharField(max_length=255, blank=True)
    expected_delivery_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    quote_proposal = models.OneToOneField(
        "QuoteProposal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="converted_order",
        help_text="Proposition de devis à l'origine de cette commande, le cas échéant.",
    )
    quote_response = models.OneToOneField(
        "QuoteResponse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="converted_order",
        help_text="Réponse devis boutique (RFQ) à l'origine de cette commande.",
    )
    execution_notes = models.TextField(blank=True)
    origin = models.CharField(
        max_length=24,
        choices=Origin.choices,
        default=Origin.EXTERNAL,
        db_index=True,
        help_text="Canal d'origine. Seules les commandes nées sur Paknomik sont commissionnables.",
    )
    trade_scope = models.CharField(
        max_length=8, choices=TradeScope.choices, default=TradeScope.LOCAL
    )
    delivery_wilaya = models.CharField(max_length=100, blank=True)
    destination_country = models.CharField(
        max_length=2, blank=True, help_text="Code pays ISO 3166-1 (commandes export)."
    )
    destination_city = models.CharField(
        max_length=160, blank=True, help_text="Ville ou port de destination."
    )
    incoterm = models.CharField(
        max_length=8, blank=True, help_text="Vide = à définir avec le fournisseur."
    )
    buyer_note = models.TextField(blank=True)
    supplier_archived_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Archivée par le fournisseur : masquée de sa liste « Toutes ».",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    PAKNOMIK_ORIGINS = frozenset(
        {Origin.PRODUCT_PAGE, Origin.QUOTE_DEMAND, Origin.RFQ}
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Order"
        verbose_name_plural = "Orders"

    def __str__(self):
        return f"Order #{self.pk} — {self.product.name} × {self.quantity}"

    @property
    def is_paknomik_origin(self) -> bool:
        return self.origin in self.PAKNOMIK_ORIGINS


class OrderStatusEvent(models.Model):
    """Append-only status history of an order (who, when, from → to)."""

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="status_events"
    )
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_status_events",
    )
    actor_role = models.CharField(max_length=16, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Order status event"
        verbose_name_plural = "Order status events"

    def __str__(self):
        return f"Order #{self.order_id}: {self.from_status or '∅'} → {self.to_status}"


class OrderSupplierReview(models.Model):
    """Buyer rating of the supplier, once the order has been marked delivered."""

    order = models.OneToOneField(
        Order, on_delete=models.CASCADE, related_name="supplier_review"
    )
    buyer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="supplier_delivery_reviews"
    )
    supplier = models.ForeignKey(
        "Supplier", on_delete=models.CASCADE, related_name="delivery_reviews"
    )
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Order supplier review"
        verbose_name_plural = "Order supplier reviews"
        constraints = [
            models.CheckConstraint(
                check=models.Q(rating__gte=1, rating__lte=5),
                name="order_supplier_review_rating_range",
            ),
        ]

    def __str__(self):
        return f"Note {self.rating}/5 — commande #{self.order_id}"


class SupplierMandate(models.Model):
    """Commission mandate signed by a supplier for orders born on Paknomik.

    The rate is the one written in the mandate; it is never hard-coded.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        ACTIVE = "active", "Actif"
        SUSPENDED = "suspended", "Suspendu"
        ENDED = "ended", "Terminé"

    class Scope(models.TextChoices):
        ALL = "all", "Toutes les commandes Paknomik"
        LOCAL = "local", "Commandes locales"
        EXPORT = "export", "Commandes export"

    class CalculationBase(models.TextChoices):
        ORDER_TOTAL = "order_total", "Montant total convenu de la commande"
        CUSTOM = "custom", "Autre base (voir les termes du mandat)"

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="mandates"
    )
    reference = models.CharField(max_length=64, blank=True)
    scope = models.CharField(max_length=8, choices=Scope.choices, default=Scope.ALL)
    commission_rate = models.DecimalField(
        max_digits=5, decimal_places=2, help_text="Taux convenu dans le mandat (%)."
    )
    calculation_base = models.CharField(
        max_length=16, choices=CalculationBase.choices, default=CalculationBase.ORDER_TOTAL
    )
    calculation_terms = models.TextField(
        blank=True, help_text="Base de calcul telle que rédigée dans le mandat."
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_mandates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-starts_on", "-created_at"]
        verbose_name = "Supplier mandate"
        verbose_name_plural = "Supplier mandates"

    def __str__(self):
        label = self.reference or f"#{self.pk}"
        return f"Mandat {label} — {self.supplier} ({self.commission_rate} %)"


class OrderCommission(models.Model):
    """Commission owed by the supplier on an order born on Paknomik.

    Tracked for suppliers and administrators only: never billed to, nor shown to,
    the buyer. The rate is frozen when the order is recorded.
    """

    class Status(models.TextChoices):
        TO_VALIDATE = "to_validate", "À valider"
        CALCULATED = "calculated", "Calculée — en attente de validation"
        VALIDATED = "validated", "Validée"
        NOT_DUE = "not_due", "Non due"

    class Settlement(models.TextChoices):
        PENDING = "pending", "Non réglée"
        INVOICED = "invoiced", "Facturée au fournisseur"
        SETTLED = "settled", "Réglée"
        WAIVED = "waived", "Abandonnée"

    order = models.OneToOneField(
        Order, on_delete=models.CASCADE, related_name="commission"
    )
    mandate = models.ForeignKey(
        SupplierMandate,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="commissions",
    )
    trade_scope = models.CharField(
        max_length=8, choices=TradeScope.choices, default=TradeScope.LOCAL
    )
    rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    calculation_base = models.CharField(max_length=16, blank=True)
    reference_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=8, default="DZD")
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Vide = à calculer.",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.TO_VALIDATE, db_index=True
    )
    status_reason = models.CharField(max_length=255, blank=True)
    settlement_status = models.CharField(
        max_length=12, choices=Settlement.choices, default=Settlement.PENDING
    )
    validated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="validated_commissions",
    )
    validated_at = models.DateTimeField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    admin_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Order commission"
        verbose_name_plural = "Order commissions"

    def __str__(self):
        return f"Commission commande #{self.order_id} — {self.get_status_display()}"


class RFQRequest(models.Model):
    """Workflow 2 — Demande de devis / projet de conception (1 acheteur → 1 fournisseur)."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Envoyée"
        IN_REVIEW = "in_review", "En étude"
        QUOTED = "quoted", "Devis répondu"
        ACCEPTED = "accepted", "Acceptée"
        REJECTED = "rejected", "Refusée"
        CANCELLED = "cancelled", "Annulée"

    buyer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_requests",
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="quote_requests"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_requests",
        help_text="Produit de référence optionnel.",
    )
    title = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=255)
    company = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    quantity_estimate = models.PositiveIntegerField(null=True, blank=True)
    quantity_unit = models.CharField(max_length=40, blank=True, default="")
    destination = models.CharField(max_length=255, blank=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="typed_quote_requests",
        help_text="Type d'emballage demandé. Les specs sont figées dans specs_snapshot.",
    )
    specs_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Copie des spécifications au moment de l'envoi.",
    )
    message = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SUBMITTED
    )
    responded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Première réaction du fournisseur (prise en charge ou devis).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Quote request"
        verbose_name_plural = "Quote requests"

    def __str__(self):
        return f"Quote #{self.pk} — {self.email} → {self.supplier.company_name}"


class RFQRequestPhoto(models.Model):
    """Photo jointe par l'acheteur à une demande de devis boutique (EXIF supprimées)."""

    rfq = models.ForeignKey(RFQRequest, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="quote_requests/photos/")
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"Photo #{self.pk} — demande boutique #{self.rfq_id}"


class RFQRequestFile(models.Model):
    """Document technique joint à une demande de devis (PDF)."""

    rfq = models.ForeignKey(RFQRequest, on_delete=models.CASCADE, related_name="files")
    file = models.FileField(upload_to="quote_requests/files/")
    original_name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"Fichier #{self.pk} — demande boutique #{self.rfq_id}"


class RFQRequestEvent(models.Model):
    """Historique d'une demande de devis boutique."""

    class Action(models.TextChoices):
        SUBMITTED = "submitted", "Envoyée"
        STATUS_CHANGED = "status_changed", "Statut modifié"
        QUOTED = "quoted", "Devis répondu"
        NOTE = "note", "Note"

    rfq = models.ForeignKey(RFQRequest, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=24, choices=Action.choices)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rfq_events",
    )
    comment = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Quote request event"
        verbose_name_plural = "Quote request events"

    def __str__(self):
        return f"Demande #{self.rfq_id} — {self.action}"


class SampleRequest(models.Model):
    """Demande d'échantillon envoyée depuis la boutique (1 acheteur → 1 fournisseur).

    Gratuite. Réutilise les statuts des demandes boutique (sans « Devis répondu »).
    """

    STATUS_CHOICES = [
        (value, label)
        for value, label in RFQRequest.Status.choices
        if value != RFQRequest.Status.QUOTED
    ]

    buyer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sample_requests",
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="sample_requests"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sample_requests",
    )
    name = models.CharField(max_length=255)
    company = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    delivery_address = models.CharField(max_length=500)
    message = models.TextField(blank=True)
    quantity = models.PositiveIntegerField(
        help_text="Quantité fixée par le fournisseur au moment de l'envoi (figée)."
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=RFQRequest.Status.SUBMITTED
    )
    responded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Première réaction du fournisseur (changement de statut).",
    )
    packaging_project = models.ForeignKey(
        "buyer_space.PackagingProject",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sample_requests",
    )
    packaging_item = models.ForeignKey(
        "buyer_space.PackagingItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sample_requests",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Sample request"
        verbose_name_plural = "Sample requests"

    def __str__(self):
        return f"Échantillon #{self.pk} — {self.name} → {self.supplier.company_name}"


class SampleRequestPhoto(models.Model):
    """Photo de référence jointe à une demande d'échantillon (EXIF supprimées)."""

    sample_request = models.ForeignKey(
        SampleRequest, on_delete=models.CASCADE, related_name="photos"
    )
    image = models.ImageField(upload_to="sample_requests/photos/")
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"Photo #{self.pk} — échantillon #{self.sample_request_id}"


class QuoteResponse(models.Model):
    """Réponse fournisseur à une demande de devis."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        SUBMITTED = "submitted", "Envoyée"
        ACCEPTED = "accepted", "Acceptée"
        REJECTED = "rejected", "Refusée"

    quote_request = models.ForeignKey(
        RFQRequest, on_delete=models.CASCADE, related_name="responses"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="quote_responses"
    )
    message = models.TextField()
    proposal_template_id = models.CharField(max_length=64, blank=True, default="")
    proposal_template_name = models.CharField(max_length=200, blank=True, default="")
    proposal_blocks = models.JSONField(default=list, blank=True)
    proposed_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=8, default="DZD")
    lead_time_days = models.PositiveIntegerField(null=True, blank=True)
    moq = models.PositiveIntegerField(null=True, blank=True)
    customization_fee = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    commercial_terms = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SUBMITTED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Quote response #{self.pk} for request #{self.quote_request_id}"


class QuoteFormTemplate(models.Model):
    """Platform quote form bound to a packaging family or type."""

    category = models.OneToOneField(
        Category, on_delete=models.CASCADE, related_name="quote_form_template"
    )
    title_fr = models.CharField(max_length=200)
    title_en = models.CharField(max_length=200, blank=True)
    buyer_instructions = models.TextField(blank=True, default="")
    quantity_unit = models.CharField(max_length=40, default="pièces")
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Quote form template"
        verbose_name_plural = "Quote form templates"

    def __str__(self):
        return f"{self.title_fr} — {self.category}"


class QuoteFormField(models.Model):
    """One technical question on a platform quote form."""

    class FieldType(models.TextChoices):
        TEXT = "text", "Texte"
        TEXTAREA = "textarea", "Commentaire"
        NUMBER = "number", "Nombre"
        CHOICE = "choice", "Liste"
        BOOLEAN = "boolean", "Oui / non"

    template = models.ForeignKey(
        QuoteFormTemplate, on_delete=models.CASCADE, related_name="fields"
    )
    key = models.SlugField(max_length=64)
    label_fr = models.CharField(max_length=200)
    label_en = models.CharField(max_length=200, blank=True)
    field_type = models.CharField(max_length=20, choices=FieldType.choices)
    unit_label = models.CharField(max_length=40, blank=True, default="")
    required = models.BooleanField(default=False)
    locked = models.BooleanField(
        default=False,
        help_text="Le fournisseur ne peut pas masquer ce champ.",
    )
    positive = models.BooleanField(default=False)
    options = models.JSONField(default=list, blank=True)
    condition = models.JSONField(null=True, blank=True)
    group = models.CharField(max_length=40, blank=True, default="")
    help_fr = models.CharField(max_length=300, blank=True, default="")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = [["template", "key"]]
        verbose_name = "Quote form field"
        verbose_name_plural = "Quote form fields"

    def __str__(self):
        return f"{self.template_id} — {self.label_fr}"


class SupplierQuoteForm(models.Model):
    """Free digital customization of a quote form by one supplier."""

    class PhysicalCustomization(models.TextChoices):
        INCLUDED = "included", "Incluse"
        PAID = "paid", "Payante"
        NEGOTIATE = "negotiate", "À négocier sur devis"

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="quote_forms"
    )
    template = models.ForeignKey(
        QuoteFormTemplate, on_delete=models.CASCADE, related_name="supplier_forms"
    )
    buyer_instructions = models.TextField(blank=True, default="")
    physical_customization = models.CharField(
        max_length=16,
        choices=PhysicalCustomization.choices,
        default=PhysicalCustomization.NEGOTIATE,
    )
    extra_questions = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["supplier", "template"]]
        verbose_name = "Supplier quote form"
        verbose_name_plural = "Supplier quote forms"

    def __str__(self):
        return f"{self.supplier} — {self.template}"


class SupplierQuoteFormField(models.Model):
    """Per-field override: visibility, required flag, and allowed options."""

    form = models.ForeignKey(
        SupplierQuoteForm, on_delete=models.CASCADE, related_name="field_settings"
    )
    field = models.ForeignKey(
        QuoteFormField, on_delete=models.CASCADE, related_name="supplier_settings"
    )
    enabled = models.BooleanField(default=True)
    required = models.BooleanField(null=True, blank=True)
    options = models.JSONField(null=True, blank=True)

    class Meta:
        unique_together = [["form", "field"]]

    def __str__(self):
        return f"{self.form_id} — {self.field.key}"


class OrderResponse(models.Model):
    """Réponse fournisseur à une commande catalogue."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        SUBMITTED = "submitted", "Envoyée"
        ACCEPTED = "accepted", "Acceptée"
        REJECTED = "rejected", "Refusée"

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="responses"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="order_responses"
    )
    message = models.TextField()
    proposal_template_id = models.CharField(max_length=64, blank=True, default="")
    proposal_template_name = models.CharField(max_length=200, blank=True, default="")
    proposal_blocks = models.JSONField(default=list, blank=True)
    proposed_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=8, default="DZD")
    lead_time_days = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SUBMITTED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order response #{self.pk} for order #{self.order_id}"


class Message(models.Model):
    """Direct message between a buyer and a supplier."""

    class MessageType(models.TextChoices):
        TEXT = "text", "Text"
        PROPOSAL = "proposal", "Proposal notice"

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="messages"
    )
    buyer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="supplier_conversations"
    )
    sender = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="sent_messages"
    )
    content = models.TextField()
    message_type = models.CharField(
        max_length=20,
        choices=MessageType.choices,
        default=MessageType.TEXT,
    )
    metadata = models.JSONField(default=dict, blank=True)
    read_by_supplier = models.BooleanField(default=False)
    read_by_buyer = models.BooleanField(default=False)
    read_by_supplier_at = models.DateTimeField(null=True, blank=True)
    read_by_buyer_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Message"
        verbose_name_plural = "Messages"
        indexes = [
            models.Index(fields=["supplier", "buyer", "-created_at"]),
        ]

    def __str__(self):
        return f"Message #{self.pk} — {self.buyer_id} ↔ {self.supplier_id}"


class SupportConversation(models.Model):
    """Tech support thread between a platform user and Paknomik staff."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="support_conversations"
    )
    subject = models.CharField(max_length=255, default="Assistance technique")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Support conversation"
        verbose_name_plural = "Support conversations"

    def __str__(self):
        return f"Support #{self.pk} — {self.user_id} ({self.status})"


class SupportMessage(models.Model):
    """Message inside a tech support conversation."""

    conversation = models.ForeignKey(
        SupportConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="support_messages_sent",
    )
    content = models.TextField()
    is_staff_reply = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Support message"
        verbose_name_plural = "Support messages"

    def __str__(self):
        return f"Support message #{self.pk} in conversation #{self.conversation_id}"


class BuyerSearchHistory(models.Model):
    """Saved buyer search queries shown in the buyer space."""

    buyer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="search_history"
    )
    query = models.CharField(max_length=255)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Buyer search history"
        verbose_name_plural = "Buyer search history"

    def __str__(self):
        return f"{self.buyer_id}: {self.query}"


class BuyerFavoriteSupplier(models.Model):
    buyer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="favorite_suppliers"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="favorited_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("buyer", "supplier")]
        verbose_name = "Buyer favorite supplier"
        verbose_name_plural = "Buyer favorite suppliers"

    def __str__(self):
        return f"{self.buyer_id} ♥ {self.supplier.company_name}"


class BuyerFavoriteProduct(models.Model):
    buyer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="favorite_products"
    )
    product = models.ForeignKey(
        "Product", on_delete=models.CASCADE, related_name="favorited_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("buyer", "product")]
        verbose_name = "Buyer favorite product"
        verbose_name_plural = "Buyer favorite products"

    def __str__(self):
        return f"{self.buyer_id} ♥ product #{self.product_id}"


class TenderCall(models.Model):
    """Workflow 3 — Consultation fournisseurs (1 acheteur → N fournisseurs)."""

    class TenderType(models.TextChoices):
        SIMPLE = "simple", "Simple"
        DETAILED = "detailed", "Détaillée"

    class Status(models.TextChoices):
        OPEN = "open", "Ouverte"
        CLOSED = "closed", "Clôturée"
        AWARDED = "awarded", "Attribuée"
        CANCELLED = "cancelled", "Annulée"

    buyer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="sourcing_requests"
    )
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="sourcing_requests"
    )
    subcategory = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="sourcing_requests_as_subcategory",
    )
    quantity = models.PositiveIntegerField()
    description = models.TextField(blank=True)
    tender_type = models.CharField(
        max_length=20, choices=TenderType.choices, default=TenderType.SIMPLE
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Sourcing request"
        verbose_name_plural = "Sourcing requests"

    def __str__(self):
        return (
            f"Consultation #{self.pk} — "
            f"{self.subcategory.name_en} ({self.quantity})"
        )


class TenderCallAttributeValue(models.Model):
    tender_call = models.ForeignKey(
        TenderCall, on_delete=models.CASCADE, related_name="attribute_values"
    )
    attribute = models.ForeignKey(
        TechnicalAttribute, on_delete=models.CASCADE, related_name="tender_values"
    )
    value = models.TextField()

    class Meta:
        unique_together = [["tender_call", "attribute"]]

    def __str__(self):
        return f"{self.attribute.name_en}: {self.value}"


class TenderCallNotification(models.Model):
    """Invitation d'un fournisseur à répondre à une consultation."""

    class Status(models.TextChoices):
        INVITED = "invited", "Invité"
        VIEWED = "viewed", "Consultée"
        RESPONDED = "responded", "Répondu"
        DECLINED = "declined", "Déclinée"

    tender_call = models.ForeignKey(
        TenderCall, on_delete=models.CASCADE, related_name="invitations"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="sourcing_invitations"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.INVITED
    )
    notified_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        unique_together = [["tender_call", "supplier"]]
        ordering = ["-notified_at"]
        verbose_name = "Sourcing invitation"
        verbose_name_plural = "Sourcing invitations"

    def __str__(self):
        return (
            f"Invitation to {self.supplier.company_name} "
            f"for consultation #{self.tender_call_id}"
        )


class SourcingResponse(models.Model):
    """Offre d'un fournisseur dans une consultation multi-fournisseurs."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        SUBMITTED = "submitted", "Envoyée"
        SHORTLISTED = "shortlisted", "Présélectionnée"
        ACCEPTED = "accepted", "Retenue"
        REJECTED = "rejected", "Refusée"

    sourcing_request = models.ForeignKey(
        TenderCall, on_delete=models.CASCADE, related_name="responses"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="sourcing_responses"
    )
    invitation = models.ForeignKey(
        TenderCallNotification,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responses",
    )
    message = models.TextField()
    proposed_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=8, default="DZD")
    moq = models.PositiveIntegerField(null=True, blank=True)
    lead_time_days = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SUBMITTED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [["sourcing_request", "supplier"]]

    def __str__(self):
        return (
            f"Sourcing response #{self.pk} "
            f"for consultation #{self.sourcing_request_id}"
        )


# Aliases conceptuels (tables Django inchangées pour TenderCall / RFQRequest).
SourcingRequest = TenderCall
QuoteRequest = RFQRequest
SourcingInvitation = TenderCallNotification


# ========================================================================
# RBAC — ROLES, PERMISSIONS, AUDIT
# ========================================================================


class PlatformRole(models.Model):
    """Named admin role holding a list of atomic permission codes."""

    code = models.CharField(max_length=40, unique=True)
    label = models.CharField(max_length=120)
    permissions = models.JSONField(default=list, blank=True)
    is_system = models.BooleanField(
        default=True,
        help_text="Rôle livré par la plateforme : non supprimable depuis l'interface.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Platform role"
        verbose_name_plural = "Platform roles"

    def __str__(self):
        return self.label


class RoleGrant(models.Model):
    """Assignment of a role to a user. Revoking keeps the historical row."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="role_grants"
    )
    role = models.ForeignKey(
        PlatformRole, on_delete=models.PROTECT, related_name="grants"
    )
    granted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="roles_granted",
    )
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="roles_revoked",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Role grant"
        verbose_name_plural = "Role grants"
        indexes = [models.Index(fields=["user", "revoked_at"])]

    def __str__(self):
        state = "révoqué" if self.revoked_at else "actif"
        return f"{self.user_id} → {self.role.code} ({state})"


class AuditLog(models.Model):
    """Append-only trail for sensitive reads and writes."""

    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=80, db_index=True)
    entity_type = models.CharField(max_length=80, blank=True)
    entity_id = models.PositiveIntegerField(null=True, blank=True)
    old_value = models.JSONField(default=dict, blank=True)
    new_value = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    ip_address = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Audit log"
        verbose_name_plural = "Audit log"

    def __str__(self):
        return f"{self.action} — {self.entity_type}#{self.entity_id}"


class PlatformSetting(models.Model):
    """Admin-editable scalar configuration (no frontend deploy needed)."""

    key = models.CharField(max_length=80, unique=True)
    value = models.CharField(max_length=255, blank=True)
    label = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]
        verbose_name = "Platform setting"
        verbose_name_plural = "Platform settings"

    def __str__(self):
        return f"{self.key} = {self.value}"


# ========================================================================
# ENTITLEMENTS — PLAN LIMITS AND MONTHLY QUOTAS
# ========================================================================


class PlanEntitlement(models.Model):
    """One limit or feature flag for a plan. limit_value null means unlimited."""

    plan_code = models.CharField(max_length=20, db_index=True)
    key = models.CharField(max_length=80, db_index=True)
    label = models.CharField(max_length=200, blank=True)
    limit_value = models.IntegerField(
        null=True,
        blank=True,
        help_text="Null = illimité. 0 = interdit.",
    )
    is_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan_code", "key"]
        unique_together = [["plan_code", "key"]]
        verbose_name = "Plan entitlement"
        verbose_name_plural = "Plan entitlements"

    def __str__(self):
        cap = "illimité" if self.limit_value is None else self.limit_value
        return f"{self.plan_code}.{self.key} = {cap}"


class UsageCounter(models.Model):
    """Monthly consumption per supplier and metered key."""

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="usage_counters"
    )
    key = models.CharField(max_length=80, db_index=True)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    used = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "-month", "key"]
        unique_together = [["supplier", "key", "year", "month"]]
        verbose_name = "Usage counter"
        verbose_name_plural = "Usage counters"

    def __str__(self):
        return f"{self.supplier_id}/{self.key} {self.month}-{self.year}: {self.used}"


# ========================================================================
# CRM — COMPANIES, CONTACTS, OPPORTUNITIES, ACTIVITIES
# ========================================================================


class CrmCompany(models.Model):
    """A company tracked by one supplier. Always scoped to its owner."""

    class Status(models.TextChoices):
        PROSPECT = "prospect", "Prospect"
        CLIENT = "client", "Client"
        PARTNER = "partner", "Partenaire"
        SUPPLIER = "supplier", "Fournisseur"
        BUYER = "buyer", "Acheteur"

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="crm_companies"
    )
    buyer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="crm_company_records",
        help_text="Compte acheteur Paknomik lié, si connu.",
    )
    name = models.CharField(max_length=255)
    sector = models.CharField(max_length=120, blank=True)
    wilaya = models.CharField(max_length=100, blank=True)
    size = models.CharField(max_length=60, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PROSPECT, db_index=True
    )
    website = models.URLField(blank=True, null=True)
    notes = models.TextField(blank=True)
    owner_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="crm_companies_owned",
    )
    is_archived = models.BooleanField(default=False)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "CRM company"
        verbose_name_plural = "CRM companies"
        indexes = [models.Index(fields=["supplier", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "buyer"],
                condition=models.Q(buyer__isnull=False),
                name="crm_company_unique_supplier_buyer",
            )
        ]

    def __str__(self):
        return self.name


class CrmContact(models.Model):
    class Channel(models.TextChoices):
        EMAIL = "email", "E-mail"
        PHONE = "phone", "Téléphone"
        WHATSAPP = "whatsapp", "WhatsApp"
        MESSAGING = "messaging", "Messagerie Paknomik"

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="crm_contacts"
    )
    company = models.ForeignKey(
        CrmCompany, on_delete=models.CASCADE, related_name="contacts"
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="crm_contact_records",
    )
    first_name = models.CharField(max_length=120, blank=True)
    last_name = models.CharField(max_length=120, blank=True)
    job_title = models.CharField(max_length=150, blank=True)
    decision_role = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    whatsapp = models.CharField(max_length=40, blank=True)
    preferred_channel = models.CharField(
        max_length=20, choices=Channel.choices, default=Channel.MESSAGING
    )
    contact_consent = models.BooleanField(
        default=False,
        help_text="L'acheteur a autorisé le contact direct hors messagerie Paknomik.",
    )
    consent_updated_at = models.DateTimeField(null=True, blank=True)
    last_interaction_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "CRM contact"
        verbose_name_plural = "CRM contacts"
        indexes = [models.Index(fields=["supplier", "company"])]

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email


class Opportunity(models.Model):
    class Source(models.TextChoices):
        ORGANIC_SEARCH = "organic_search", "Recherche organique"
        CATEGORY_INTENT = "category_intent", "Intérêt catégorie"
        STORE_VISIT = "store_visit", "Visite boutique"
        PRODUCT_VIEW = "product_view", "Consultation de produit"
        FAVORITE = "favorite", "Favori"
        QUOTE_REQUEST = "quote_request", "Demande de devis"
        SAMPLE_REQUEST = "sample_request", "Demande d'échantillon"
        ORDER = "order", "Commande"
        DIRECT_CONTACT = "direct_contact", "Contact direct"
        PAKMEDIA = "pakmedia", "Pakmedia"
        PAK_DATING = "pak_dating", "Pak-dating accepté"
        REFERRAL = "referral", "Recommandation"

    class Stage(models.TextChoices):
        PROSPECT = "prospect", "Prospect"
        REQUEST_RECEIVED = "request_received", "Demande reçue"
        QUOTE_SENT = "quote_sent", "Devis envoyé"
        NEGOTIATION = "negotiation", "Négociation"
        WON = "won", "Gagné"
        LOST = "lost", "Perdu"

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="opportunities"
    )
    company = models.ForeignKey(
        CrmCompany,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunities",
    )
    contact = models.ForeignKey(
        CrmContact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunities",
    )
    demand = models.ForeignKey(
        "QuoteDemand",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunities",
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunities",
    )
    title = models.CharField(max_length=255, blank=True)
    source = models.CharField(
        max_length=32, choices=Source.choices, default=Source.DIRECT_CONTACT
    )
    stage = models.CharField(
        max_length=24, choices=Stage.choices, default=Stage.PROSPECT, db_index=True
    )
    products = models.ManyToManyField(
        Product, blank=True, related_name="opportunities"
    )
    estimated_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    probability = models.PositiveSmallIntegerField(default=20)
    next_action = models.CharField(max_length=255, blank=True)
    next_action_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.CharField(max_length=255, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Opportunity"
        verbose_name_plural = "Opportunities"
        indexes = [models.Index(fields=["supplier", "stage"])]

    def __str__(self):
        return self.title or f"Opportunité #{self.pk}"


class CrmActivity(models.Model):
    class Kind(models.TextChoices):
        NOTE = "note", "Note"
        TASK = "task", "Tâche"
        CALL = "call", "Appel"
        MESSAGE = "message", "Message"
        MEETING = "meeting", "Rendez-vous"
        REMINDER = "reminder", "Relance"
        STAGE_CHANGE = "stage_change", "Changement d'étape"
        SYSTEM = "system", "Événement système"

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="crm_activities"
    )
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="activities",
    )
    company = models.ForeignKey(
        CrmCompany,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="activities",
    )
    contact = models.ForeignKey(
        CrmContact,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="activities",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.NOTE)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    done_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="crm_activities_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "CRM activity"
        verbose_name_plural = "CRM activities"
        indexes = [models.Index(fields=["supplier", "due_at", "done_at"])]

    def __str__(self):
        return f"{self.get_kind_display()} — {self.title}"


class MarketReport(models.Model):
    """A report a supplier paid for. Stored so it is never billed twice."""

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="market_reports"
    )
    service = models.ForeignKey(
        "PragmadinarService",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="market_reports",
    )
    report_type = models.CharField(max_length=80, db_index=True)
    title = models.CharField(max_length=200)
    params = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    ledger = models.ForeignKey(
        "PragmadinarLedger",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="market_reports",
    )
    cost_pragmadinar = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Market report"
        verbose_name_plural = "Market reports"
        indexes = [models.Index(fields=["supplier", "report_type"])]

    def __str__(self):
        return f"{self.title} — {self.supplier_id}"


class ConsentRecord(models.Model):
    """Buyer consent before any identity reveal (Pak-dating, contact sharing)."""

    class Purpose(models.TextChoices):
        PAK_DATING = "pak_dating", "Pak-dating"
        CONTACT_SHARE = "contact_share", "Partage de coordonnées"

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        GRANTED = "granted", "Accordé"
        REFUSED = "refused", "Refusé"
        EXPIRED = "expired", "Expiré"

    buyer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="consent_records"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="consent_records"
    )
    purpose = models.CharField(
        max_length=24, choices=Purpose.choices, default=Purpose.PAK_DATING
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    signal = models.CharField(
        max_length=120,
        blank=True,
        help_text="Signal d'intérêt détecté (favori, comparaison, visite répétée…).",
    )
    revealed_fields = models.JSONField(default=list, blank=True)
    hold_ledger = models.ForeignKey(
        "PragmadinarLedger",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consent_holds",
    )
    charge_ledger = models.ForeignKey(
        "PragmadinarLedger",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consent_charges",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = "Consent record"
        verbose_name_plural = "Consent records"
        indexes = [models.Index(fields=["supplier", "status"])]

    def __str__(self):
        return f"{self.get_purpose_display()} {self.buyer_id}→{self.supplier_id} ({self.status})"


# ========================================================================
# QUOTE DEMANDS + PRAGMADINAR ECONOMY
# ========================================================================


class PaknomikPlan(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=80)
    monthly_price_dzd = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["monthly_price_dzd", "code"]
        verbose_name = "Paknomik plan"
        verbose_name_plural = "Paknomik plans"

    def __str__(self):
        return self.name


class PragmadinarService(models.Model):
    WALLET_CATEGORY_CHOICES = (
        ("visibility", "Visibilité"),
        ("analytics", "Analytics"),
        ("promotion", "Promotion"),
    )

    code = models.CharField(max_length=80, unique=True)
    service_class = models.CharField(max_length=32)
    wallet_category = models.CharField(
        max_length=16, choices=WALLET_CATEGORY_CHOICES, default="visibility"
    )
    label = models.CharField(max_length=255)
    cost_pragmadinar = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unit = models.CharField(max_length=32, default="feature")
    unit_quantity = models.PositiveIntegerField(default=1)
    business_access = models.CharField(max_length=32, default="unlimited")
    genereux_access = models.CharField(max_length=32, default="unavailable")
    genereux_quota = models.PositiveIntegerField(null=True, blank=True)
    price_rank = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Rang tarifaire dans le listing (1 = le plus cher)."
    )
    unit_label = models.CharField(
        max_length=80, blank=True, help_text="Unité affichée dans le listing tarifaire."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["service_class", "code"]
        verbose_name = "Pragmadinar service"
        verbose_name_plural = "Pragmadinar services"

    def __str__(self):
        return self.label


class PragmadinarWallet(models.Model):
    """Pragmadinar wallet. Table name kept for migration compatibility."""

    class Category(models.TextChoices):
        LEGACY = "legacy", "Historique"
        VISIBILITY = "visibility", "Visibilité"
        ANALYTICS = "analytics", "Analytics"
        PROMOTION = "promotion", "Promotion"

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="pragmadinar_wallets"
    )
    category = models.CharField(
        max_length=16, choices=Category.choices, default=Category.LEGACY
    )
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    granted_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    purchased_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    spent_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    held_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Montant réservé (ex. Pak-dating en attente de consentement).",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pragmadinar wallet"
        verbose_name_plural = "Pragmadinar wallets"
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "category"], name="pragmadinar_wallet_supplier_category"
            )
        ]

    def __str__(self):
        return f"{self.supplier.company_name} — {self.balance} Pragmadinar"

    @property
    def available_balance(self):
        return self.balance - self.held_total


class Campaign(models.Model):
    """Sponsored visibility campaign funded in Pragmadinar."""

    class Format(models.TextChoices):
        VITRINE_EXTRA = "vitrine_extra", "Vitrine Extra"
        VITRINE_CENTRAL = "vitrine_central", "Vitrine Centrale"
        VITRINE_MEGA = "vitrine_mega", "Vitrine Mega"
        CARD_PASSPORT = "card_passport", "Carte Passport"
        CARD_HORIZONTAL = "card_horizontal", "Carte horizontale"
        BANNER = "banner", "Bannière horizontale (résultats de recherche)"
        BANNER_CONVERSATION = (
            "banner_conversation",
            "Bannière verticale (espace conversation)",
        )
        KEYWORD_RESERVATION = "keyword_reservation", "Réservation de mot-clé"
        PRIORITY_SUBCATEGORY = (
            "priority_subcategory",
            "Apparition prioritaire dans la sous-catégorie",
        )

    class Objective(models.TextChoices):
        DURATION = "duration", "Durée"
        VISITS = "visits", "Nombre de visites"
        VIEWS = "views", "Nombre de vues"

    class ContextScope(models.TextChoices):
        ALL = "all", "Toutes catégories"
        SUPPLIER_CATEGORIES = "supplier_categories", "Catégories du fournisseur"

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "En pause"
        EXHAUSTED = "exhausted", "Budget épuisé"
        COMPLETED = "completed", "Objectif atteint"
        ENDED = "ended", "Terminée"

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="campaigns"
    )
    name = models.CharField(max_length=200)
    campaign_format = models.CharField(max_length=32, choices=Format.choices)
    placement = models.CharField(max_length=64, blank=True)
    objective = models.CharField(max_length=120, blank=True)
    service = models.ForeignKey(
        PragmadinarService,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
    )
    budget_pragmadinar = models.DecimalField(max_digits=12, decimal_places=2)
    spent_pragmadinar = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    view_cap = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Objectif en vues ou visites (selon objective). Vide = pas de plafond.",
    )
    duration_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Objectif durée : nombre de jours à partir de l'activation.",
    )
    context_scope = models.CharField(
        max_length=32,
        choices=ContextScope.choices,
        default=ContextScope.ALL,
        help_text="Bannières : afficher partout ou seulement dans les catégories du fournisseur.",
    )
    keywords = models.JSONField(
        default=list,
        blank=True,
        help_text="Mots-clés réservés pour une campagne de recherche.",
    )
    subcategory = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="priority_campaigns",
        help_text="Sous-catégorie réservée pour une apparition prioritaire.",
    )
    views_consumed = models.PositiveIntegerField(default=0)
    visits_consumed = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Campaign"
        verbose_name_plural = "Campaigns"
        indexes = [models.Index(fields=["supplier", "status"])]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    @property
    def budget_remaining(self):
        return max(self.budget_pragmadinar - self.spent_pragmadinar, 0)


class PragmadinarLedger(models.Model):
    """Pragmadinar transaction. Table name kept for migration compatibility."""

    class EntryType(models.TextChoices):
        GRANT = "grant", "Crédit offert"
        PURCHASE = "purchase", "Achat de Pragmadinar"
        SPEND = "spend", "Consommation"
        REFUND = "refund", "Remboursement technique"
        HOLD = "hold", "Réservation"
        RELEASE = "release", "Libération de réservation"
        ADJUSTMENT = "adjustment", "Ajustement administrateur"
        TRANSFER = "transfer", "Transfert entre portefeuilles"

    class Status(models.TextChoices):
        POSTED = "posted", "Comptabilisée"
        HELD = "held", "Réservée"
        RELEASED = "released", "Libérée"
        REVERSED = "reversed", "Annulée"

    wallet = models.ForeignKey(
        PragmadinarWallet, on_delete=models.CASCADE, related_name="entries"
    )
    service = models.ForeignKey(
        PragmadinarService,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    entry_type = models.CharField(
        max_length=20, choices=EntryType.choices, default=EntryType.SPEND
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.POSTED
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, help_text="Négatif pour un débit."
    )
    balance_before = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Solde total du fournisseur avant l'écriture. Vide pour l'historique antérieur.",
    )
    balance_after = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Solde total du fournisseur après l'écriture.",
    )
    rate_dzd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=100,
        help_text="Taux Pragmadinar→DA appliqué au moment de l'opération.",
    )
    amount_dzd = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reference = models.CharField(max_length=64, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=160, blank=True)
    reason = models.CharField(max_length=255)
    ref_type = models.CharField(max_length=64, blank=True)
    ref_id = models.PositiveIntegerField(null=True, blank=True)
    refundable = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pragmadinar_entries_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Pragmadinar transaction"
        verbose_name_plural = "Pragmadinar transactions"
        constraints = [
            models.UniqueConstraint(
                fields=["wallet", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="pragmadinar_ledger_unique_idempotency",
            )
        ]

    def __str__(self):
        return f"{self.amount} Pragmadinar — {self.reason}"


class PragmadinarStrategyReservation(models.Model):
    class Period(models.TextChoices):
        DAY = "day", "Jour"
        WEEK = "week", "Semaine"
        MONTH = "month", "Mois"
        OPEN = "open", "Indéterminée"

    class Status(models.TextChoices):
        RESERVED = "reserved", "Réservée"
        RELEASED = "released", "Libérée"
        CONSUMED = "consumed", "Consommée"

    class Rhythm(models.TextChoices):
        DAILY_FIXED = "daily_fixed", "Quotidien fix"
        WEEKLY_FIXED = "weekly_fixed", "Hebdomadaire fix"
        VARIABLE = "variable", "Variable"
        WEEKDAYS = "weekdays", "Jours spécifiques de semaine"
        DAILY_LIMIT = "daily_limit", "Limite quotidienne"
        WEEKLY_LIMIT = "weekly_limit", "Limite hebdomadaire"

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="pragmadinar_strategy_reservations"
    )
    wallet = models.ForeignKey(
        PragmadinarWallet, on_delete=models.PROTECT, related_name="strategy_reservations"
    )
    service = models.ForeignKey(
        PragmadinarService, on_delete=models.PROTECT, related_name="strategy_reservations"
    )
    hold_ledger = models.OneToOneField(
        PragmadinarLedger,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="strategy_reservation",
    )
    target_quantity = models.PositiveIntegerField()
    period = models.CharField(max_length=8, choices=Period.choices)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    rhythm = models.CharField(max_length=16, choices=Rhythm.choices, default=Rhythm.VARIABLE)
    rhythm_detail = models.CharField(max_length=100, blank=True)
    reserved_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.RESERVED, db_index=True
    )
    ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Pragmadinar strategy reservation"
        verbose_name_plural = "Pragmadinar strategy reservations"

    def __str__(self):
        return f"{self.supplier.company_name} — {self.reserved_amount} PD"


class PragmadinarRechargeRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        APPROVED = "approved", "Approuvée"
        REJECTED = "rejected", "Refusée"

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="pragmadinar_recharges"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Pragmadinar recharge request"
        verbose_name_plural = "Pragmadinar recharge requests"

    def __str__(self):
        return f"Recharge {self.amount} — {self.supplier.company_name}"


class QuoteDemand(models.Model):
    """Demande de devis publique ou privée (modèle unifié)."""

    class Kind(models.TextChoices):
        PUBLIC = "public", "Publique"
        PRIVATE = "private", "Privée"

    class Status(models.TextChoices):
        OPEN = "open", "Ouverte"
        CLOSED = "closed", "Fermée"
        RESOLVED = "resolved", "Résolue"

    class MarketRemovedReason(models.TextChoices):
        SELECTED_SUPPLIER = "selected_supplier", "Fournisseur(s) sélectionné(s)"
        AGE_LIMIT = "age_limit", "Durée marché dépassée (7 j)"
        BUYER_RELAUNCHED = "buyer_relaunched", "Relancée sur le marché"

    buyer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="quote_demands"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="quote_demands"
    )
    subcategory = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="quote_demands_as_subcategory",
    )
    quantity = models.PositiveIntegerField()
    desired_lead_time_days = models.PositiveIntegerField(null=True, blank=True)
    indicative_budget = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    attachment = models.FileField(
        upload_to="quote_demands/", blank=True, null=True
    )
    description = models.TextField()
    catalog_type = models.ForeignKey(
        "ProductType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_demands",
        help_text="Type choisi dans le nouveau formulaire. La catégorie historique reste obligatoire.",
    )
    technical_characteristics = models.JSONField(
        default=dict,
        blank=True,
        help_text="Caractéristiques techniques préremplies depuis le gabarit du type.",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    wilaya = models.CharField(max_length=100, blank=True)
    trade_scope = models.CharField(
        max_length=8, choices=TradeScope.choices, default=TradeScope.LOCAL
    )
    destination_country = models.CharField(
        max_length=2, blank=True, help_text="Code pays ISO 3166-1 (demandes export)."
    )
    destination_city = models.CharField(max_length=160, blank=True)
    visible_on_market = models.BooleanField(default=True, db_index=True)
    market_published_at = models.DateTimeField(null=True, blank=True)
    admin_queue = models.BooleanField(default=False, db_index=True)
    market_removed_reason = models.CharField(
        max_length=32,
        choices=MarketRemovedReason.choices,
        blank=True,
    )
    packaging_project = models.ForeignKey(
        "buyer_space.PackagingProject",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_demands",
    )
    packaging_item = models.ForeignKey(
        "buyer_space.PackagingItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_demands",
    )
    service_request = models.ForeignKey(
        "buyer_space.ServiceRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_demands",
    )
    paksuites_component = models.ForeignKey(
        "buyer_space.PaksuitesComponent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_demands",
    )
    catalogue_line = models.ForeignKey(
        "buyer_space.SuiteComponentLine",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_demands",
    )
    packaging_spec_version = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Version de la fiche au moment du devis. Sert à signaler une revalidation.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Quote demand"
        verbose_name_plural = "Quote demands"
        indexes = [
            models.Index(fields=["kind", "status", "-created_at"]),
            models.Index(fields=["admin_queue", "status", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["paksuites_component"],
                condition=models.Q(paksuites_component__isnull=False),
                name="uniq_quote_demand_paksuites_component",
            ),
        ]

    def __str__(self):
        return f"Devis #{self.pk} — {self.get_kind_display()} ({self.status})"

    @property
    def engaged_count(self):
        return self.engagements.count()


class QuoteDemandPhoto(models.Model):
    """Photo jointe par l'acheteur à une demande de devis."""

    demand = models.ForeignKey(
        QuoteDemand, on_delete=models.CASCADE, related_name="photos"
    )
    image = models.ImageField(upload_to="quote_demands/photos/")
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"Photo #{self.pk} — devis #{self.demand_id}"


class QuoteDemandSelection(models.Model):
    demand = models.ForeignKey(
        QuoteDemand, on_delete=models.CASCADE, related_name="selections"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="quote_demand_selections"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["demand", "supplier"]]
        ordering = ["created_at"]


class QuoteDemandEngagement(models.Model):
    class Source(models.TextChoices):
        SELECTED_REPLIED = "selected_replied", "Sélectionné ayant répondu"
        PAID = "paid", "Non sélectionné ayant payé"
        PUBLIC_JOINED = "public_joined", "Participation publique gratuite"

    demand = models.ForeignKey(
        QuoteDemand, on_delete=models.CASCADE, related_name="engagements"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="quote_demand_engagements"
    )
    source = models.CharField(max_length=24, choices=Source.choices)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    ledger = models.ForeignKey(
        PragmadinarLedger,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_engagements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["demand", "supplier"]]
        ordering = ["created_at"]


class QuoteProposal(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        SENT = "sent", "Envoyée"
        REVISION_REQUESTED = "revision_requested", "Révision demandée"
        WON = "won", "Gagnée"
        LOST = "lost", "Perdue"
        EXPIRED = "expired", "Expirée"
        CANCELLED = "cancelled", "Annulée"

    demand = models.ForeignKey(
        QuoteDemand, on_delete=models.CASCADE, related_name="proposals"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, related_name="quote_proposals"
    )
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    confirmed_quantity = models.PositiveIntegerField()
    lead_time_days = models.PositiveIntegerField()
    moq = models.PositiveIntegerField()
    message = models.TextField(blank=True)
    currency = models.CharField(max_length=8, default="DZD")
    line_items = models.JSONField(default=list, blank=True)
    commercial_terms = models.JSONField(default=dict, blank=True)
    subtotal = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    discount_total = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    extra_fees = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    tax_total = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    total_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    content_snapshot = models.JSONField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    sent_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    refused_at = models.DateTimeField(null=True, blank=True)
    buyer_response_comment = models.TextField(blank=True)
    responded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_proposal_responses",
    )
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.SENT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["demand", "supplier"]]
        ordering = ["created_at"]

    def __str__(self):
        return f"Proposition #{self.pk} — devis #{self.demand_id}"


class QuoteProposalEvent(models.Model):
    """Version history and buyer/supplier actions on a quote proposal."""

    class Action(models.TextChoices):
        DRAFT_SAVED = "draft_saved", "Brouillon enregistré"
        SENT = "sent", "Envoyée"
        REVISED = "revised", "Nouvelle version envoyée"
        REVISION_REQUESTED = "revision_requested", "Révision demandée"
        ACCEPTED = "accepted", "Acceptée"
        REFUSED = "refused", "Refusée"
        EXPIRED = "expired", "Expirée"
        CANCELLED = "cancelled", "Annulée"
        CONVERTED = "converted", "Convertie en commande"

    proposal = models.ForeignKey(
        QuoteProposal, on_delete=models.CASCADE, related_name="events"
    )
    version = models.PositiveIntegerField(default=1)
    action = models.CharField(max_length=24, choices=Action.choices)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_proposal_events",
    )
    comment = models.TextField(blank=True)
    snapshot = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Quote proposal event"
        verbose_name_plural = "Quote proposal events"

    def __str__(self):
        return f"Proposition #{self.proposal_id} v{self.version} — {self.action}"


class AppNotification(models.Model):
    class Kind(models.TextChoices):
        QUOTE_INVITED = "quote_invited", "Fournisseur sélectionné"
        QUOTE_PROPOSAL = "quote_proposal", "Nouvelle proposition"
        QUOTE_LOST = "quote_lost", "Proposition perdue"
        RFQ_IN_REVIEW = "rfq_in_review", "Demande en cours de traitement"
        SAMPLE_REQUEST = "sample_request", "Demande d'échantillon"
        SERVICE_REQUEST = "service_request", "Demande de service"
        PILOTAGE_TASK_ASSIGNED = "pilotage_task_assigned", "Tâche de pilotage affectée"
        PILOTAGE_TASK_BLOCKED = "pilotage_task_blocked", "Tâche de pilotage bloquée"
        SUPPLIER_RATED = "supplier_rated", "Note acheteur"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="app_notifications"
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    link = models.CharField(max_length=255, blank=True)
    demand = models.ForeignKey(
        QuoteDemand,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "App notification"
        verbose_name_plural = "App notifications"

    def __str__(self):
        return f"{self.kind} → user {self.user_id}"


# ========================================================================
# ANALYTICS — EVENT LOG (see 04_event_dictionary.md)
# ========================================================================


class Event(models.Model):
    """Canonical platform event log (dictionary v0.2).

    Each row mirrors the JSON schema:
      event_type, actor { user_id, role }, context {}, timestamp
    """

    class ActorRole(models.TextChoices):
        BUYER = "buyer", "Buyer"
        SELLER = "seller", "Seller"
        VISITOR = "visitor", "Visitor"
        SYSTEM = "system", "System"
        ADMIN = "admin", "Admin"

    class EventType(models.TextChoices):
        # Buyer — authentication & session
        BUYER_LOGIN = "buyer.login", "Buyer login"
        BUYER_LOGOUT = "buyer.logout", "Buyer logout"
        BUYER_REGISTER = "buyer.register", "Buyer register"
        # Buyer — navigation & discovery
        BUYER_PAGE_VIEW = "buyer.page_view", "Buyer page view"
        BUYER_PAGE_SCROLL = "buyer.page_scroll", "Buyer page scroll"
        BUYER_SEARCH = "buyer.search", "Buyer search"
        BUYER_FILTER_APPLY = "buyer.filter_apply", "Buyer filter apply"
        BUYER_COMPARE = "buyer.compare", "Buyer compare"
        BUYER_CATEGORY_BROWSE = "buyer.category_browse", "Buyer category browse"
        # Buyer — supplier interaction
        BUYER_SUPPLIER_VIEW = "buyer.supplier_view", "Buyer supplier view"
        BUYER_PRODUCT_VIEW = "buyer.product_view", "Buyer product view"
        BUYER_SUPPLIER_SHORTLIST = "buyer.supplier_shortlist", "Buyer supplier shortlist"
        BUYER_FOLLOW_SUPPLIER = "buyer.follow_supplier", "Buyer follow supplier"
        BUYER_PRODUCT_FAVORITE = "buyer.product_favorite", "Buyer product favorite"
        BUYER_EXTERNAL_LINK_CLICK = "buyer.external_link_click", "Buyer external link click"
        # Buyer — messaging
        BUYER_MESSAGE_SEND = "buyer.message_send", "Buyer message send"
        BUYER_MESSAGE_READ = "buyer.message_read", "Buyer message read"
        # Buyer — commercial intent
        BUYER_QUOTATION_REQUEST = "buyer.quotation_request", "Buyer quotation request"
        BUYER_QUOTATION_RECEIVED = "buyer.quotation_received", "Buyer quotation received"
        BUYER_QUOTATION_ACCEPTED = "buyer.quotation_accepted", "Buyer quotation accepted"
        BUYER_QUOTATION_REJECTED = "buyer.quotation_rejected", "Buyer quotation rejected"
        BUYER_ORDER_CREATED = "buyer.order_created", "Buyer order created"
        # Buyer — content
        BUYER_ARTICLE_VIEW = "buyer.article_view", "Buyer article view"
        BUYER_GUIDE_DOWNLOAD = "buyer.guide_download", "Buyer guide download"
        # Seller — authentication & session
        SELLER_LOGIN = "seller.login", "Seller login"
        SELLER_LOGOUT = "seller.logout", "Seller logout"
        # Seller — profile
        SELLER_PROFILE_CREATED = "seller.profile_created", "Seller profile created"
        SELLER_PROFILE_UPDATED = "seller.profile_updated", "Seller profile updated"
        SELLER_DOCUMENT_UPLOADED = "seller.document_uploaded", "Seller document uploaded"
        # Seller — catalog
        SELLER_PRODUCT_ADDED = "seller.product_added", "Seller product added"
        SELLER_PRODUCT_UPDATED = "seller.product_updated", "Seller product updated"
        SELLER_PRODUCT_DELETED = "seller.product_deleted", "Seller product deleted"
        SELLER_CATALOG_PUBLISHED = "seller.catalog_published", "Seller catalog published"
        # Seller — messaging
        SELLER_MESSAGE_RECEIVE = "seller.message_receive", "Seller message receive"
        SELLER_MESSAGE_READ = "seller.message_read", "Seller message read"
        SELLER_MESSAGE_REPLY = "seller.message_reply", "Seller message reply"
        SELLER_MESSAGE_IGNORED = "seller.message_ignored", "Seller message ignored"
        # Seller — quotations
        SELLER_QUOTATION_RECEIVED = "seller.quotation_received", "Seller quotation received"
        SELLER_QUOTATION_REPLIED = "seller.quotation_replied", "Seller quotation replied"
        SELLER_QUOTATION_IGNORED = "seller.quotation_ignored", "Seller quotation ignored"
        # Seller — analytics
        SELLER_DASHBOARD_VIEW = "seller.dashboard_view", "Seller dashboard view"
        SELLER_MARKET_ANALYSIS_VIEW = (
            "seller.market_analysis_view",
            "Seller market analysis view",
        )
        SELLER_ANALYTICS_VIEW = "seller.analytics_view", "Seller analytics view"
        SELLER_PROFILE_VIEW_RECEIVED = "seller.profile_view_received", "Seller profile view received"
        # System
        SYSTEM_APPEAR_IN_SEARCH = "system.appear_in_search", "System appear in search"
        SYSTEM_SCORE_UPDATED = "system.score_updated", "System score updated"
        SYSTEM_INACTIVITY_WARNING = "system.inactivity_warning", "System inactivity warning"
        SYSTEM_INACTIVITY_PENALTY = "system.inactivity_penalty", "System inactivity penalty"
        SYSTEM_BADGE_AWARDED = "system.badge_awarded", "System badge awarded"
        SYSTEM_BADGE_REVOKED = "system.badge_revoked", "System badge revoked"
        SYSTEM_QUOTATION_EXPIRED = "system.quotation_expired", "System quotation expired"
        SYSTEM_MESSAGE_SLA_BREACH = "system.message_sla_breach", "System message SLA breach"

    event_type = models.CharField(max_length=80, choices=EventType.choices, db_index=True)
    actor_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    actor_role = models.CharField(max_length=20, choices=ActorRole.choices, db_index=True)
    context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Event"
        verbose_name_plural = "Events"
        indexes = [
            models.Index(fields=["event_type", "-created_at"]),
            models.Index(fields=["actor_role", "-created_at"]),
            models.Index(fields=["actor_user", "-created_at"]),
        ]

    def __str__(self):
        actor = self.actor_user_id or "system"
        return f"{self.event_type} ({self.actor_role}, user={actor})"

    @property
    def side(self):
        prefix = self.event_type.split(".", 1)[0]
        return prefix if prefix in {"buyer", "seller", "system"} else ""


# ========================================================================
# A/B EXPERIMENTS
# ========================================================================


class ExperimentConfig(models.Model):
    """Admin-controlled A/B experiment configuration."""

    class ForcedVariant(models.TextChoices):
        CONTROL = "control", "Control"
        V2 = "v2", "V2"

    key = models.CharField(max_length=80, unique=True, db_index=True)
    enabled = models.BooleanField(default=False)
    split_percent_v2 = models.PositiveSmallIntegerField(default=0)
    forced_variant = models.CharField(
        max_length=20,
        choices=ForcedVariant.choices,
        blank=True,
        null=True,
    )
    impressions_control = models.PositiveIntegerField(default=0)
    impressions_v2 = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="experiment_configs_updated",
    )

    class Meta:
        verbose_name = "Experiment config"
        verbose_name_plural = "Experiment configs"

    def __str__(self):
        return self.key


from user.digital_models import (  # noqa: E402,F401  (registers the models in the app)
    DigitalAuthorization,
    DigitalConnection,
    DigitalMetric,
    DigitalOAuthAttempt,
    DigitalPlatform,
)
from user.contact_models import ContactMessage  # noqa: E402,F401
from user.pilotage_models import (  # noqa: E402,F401
    PilotageBoardTask,
    PilotageDailyLog,
    PilotageGoalState,
    PilotageMarketContact,
    PilotageTaskEvent,
    PilotageWeek,
    PilotageWeekChecklistItem,
    PilotageWeeklyMetric,
)
