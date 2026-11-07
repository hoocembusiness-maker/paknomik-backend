"""
Create a supplier account from a company name, email, and optional PDF listing.

Used by `manage.py onboard_supplier` and the ultra-admin onboard API: the PDF
text is matched against Paknomik categories so catalogue rows can be created
without the supplier filling the product form by hand.
"""

from __future__ import annotations

import io
import re
import secrets
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from user.models import Category, Product, Supplier, User
from user.scoring import handle_product_added, handle_supplier_created
from user.search_normalize import SEARCH_STOP_WORDS, fold_text, tokenize_query
from user.search_tag_utils import apply_product_search_tags, resolve_search_tag_ids

MIN_CATEGORY_FOLD_LEN = 4
FUZZY_RATIO = 0.82
MAX_PRODUCTS = 80
MAX_NAME_LEN = 200
MAX_DESCRIPTION_LEN = 4000

NOISE_LINE = re.compile(
    r"^(page\s*\d+|\d+\s*/\s*\d+|tel\.?|fax|e-?mail|www\.|https?://|"
    r"catalogue|catalog|listing|sommaire|index|copyright|table des)",
    re.IGNORECASE,
)
MAKE_HINT = re.compile(
    r"\b(fabriq|fabrication|sur demande|sur mesure|custom|make to order)\b",
    re.IGNORECASE,
)
USERNAME_SAFE = re.compile(r"[^a-z0-9]+")


class OnboardError(ValueError):
    """User-facing validation error while creating a supplier from a listing."""


@dataclass
class ListingProduct:
    name: str
    description: str = ""
    listing_type: str = Product.ListingType.SELL
    category: Category | None = None
    subcategory: Category | None = None
    keywords: list[str] = field(default_factory=list)
    skip_reason: str = ""

    @property
    def is_usable(self) -> bool:
        return bool(self.name) and self.category is not None and not self.skip_reason


@dataclass
class OnboardResult:
    supplier: Supplier
    user: User
    created: bool
    password: str | None
    products_created: list[Product] = field(default_factory=list)
    products_skipped: list[ListingProduct] = field(default_factory=list)
    pdf_text: str = ""


@dataclass(frozen=True)
class _TaxonomyEntry:
    folded: str
    tokens: tuple[str, ...]
    node: Category
    parent: Category | None
    is_subcategory: bool


def normalize_email(raw: str) -> str:
    value = (raw or "").strip().lower()
    if not value:
        raise OnboardError("L'email du fournisseur est obligatoire.")
    try:
        validate_email(value)
    except ValidationError as exc:
        raise OnboardError("L'email du fournisseur n'est pas valide.") from exc
    return value


def username_from_identity(company_name: str, email: str) -> str:
    local = USERNAME_SAFE.sub("", fold_text(email.split("@", 1)[0]))[:24]
    company = USERNAME_SAFE.sub("", fold_text(company_name))[:24]
    base = local or company or "fournisseur"
    if not base[0].isalpha():
        base = f"f{base}"
    candidate = base
    suffix = 2
    while User.objects.filter(username__iexact=candidate).exists():
        candidate = f"{base}{suffix}"
        suffix += 1
    return candidate


def extract_pdf_text(source) -> str:
    """Read text from a PDF path, bytes, or file-like object."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise OnboardError(
            "pypdf est requis pour lire un listing PDF. Installez-le avec: pip install pypdf"
        ) from exc

    try:
        if hasattr(source, "read"):
            data = source.read()
            if hasattr(source, "seek"):
                try:
                    source.seek(0)
                except Exception:
                    pass
            reader = PdfReader(io.BytesIO(data))
        elif isinstance(source, (bytes, bytearray)):
            reader = PdfReader(io.BytesIO(source))
        else:
            path = Path(source)
            if not path.exists():
                raise OnboardError(f"Fichier PDF introuvable: {path}")
            reader = PdfReader(str(path))
    except OnboardError:
        raise
    except Exception as exc:
        raise OnboardError("Le PDF du listing n'a pas pu être lu.") from exc

    try:
        if getattr(reader, "is_encrypted", False):
            reader.decrypt("")
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        raise OnboardError("Le PDF du listing n'a pas pu être lu.") from exc
    return "\n".join(pages).strip()


def _taxonomy_catalog() -> list[_TaxonomyEntry]:
    entries: list[_TaxonomyEntry] = []
    categories = list(Category.objects.select_related("parent").all())
    for node in categories:
        names = [node.name_fr, node.name_en, node.name_ar]
        parent = node.parent if node.parent_id else None
        is_sub = parent is not None
        for raw in names:
            folded = fold_text(raw or "")
            if len(folded) < MIN_CATEGORY_FOLD_LEN:
                continue
            tokens = tuple(tokenize_query(raw or "", min_length=2))
            entries.append(
                _TaxonomyEntry(
                    folded=folded,
                    tokens=tokens,
                    node=node,
                    parent=parent,
                    is_subcategory=is_sub,
                )
            )
    entries.sort(key=lambda item: (not item.is_subcategory, -len(item.folded)))
    return entries


def _score_name_against_entry(folded_name: str, tokens: list[str], entry: _TaxonomyEntry) -> float:
    if not folded_name or not entry.folded:
        return 0.0
    if folded_name == entry.folded:
        return 1.0
    if entry.folded in folded_name or folded_name in entry.folded:
        shorter = min(len(folded_name), len(entry.folded))
        longer = max(len(folded_name), len(entry.folded))
        return 0.9 + (shorter / longer) * 0.08
    if entry.tokens and all(token in tokens for token in entry.tokens):
        return 0.88
    ratio = SequenceMatcher(None, folded_name, entry.folded).ratio()
    if entry.is_subcategory:
        ratio += 0.03
    return ratio


def match_category(name: str, catalog: list[_TaxonomyEntry] | None = None) -> tuple[Category | None, Category | None]:
    """Return (family, product type) for a listing line, or (None, None)."""
    catalog = catalog if catalog is not None else _taxonomy_catalog()
    folded = fold_text(name)
    tokens = tokenize_query(name)
    if not folded:
        return None, None

    best: _TaxonomyEntry | None = None
    best_score = 0.0
    for entry in catalog:
        score = _score_name_against_entry(folded, tokens, entry)
        if score > best_score:
            best = entry
            best_score = score
    if best is None or best_score < FUZZY_RATIO:
        return None, None
    if best.is_subcategory:
        return best.parent, best.node
    return best.node, None


def _is_noise_line(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 4 or len(stripped) > 120:
        return True
    if NOISE_LINE.match(stripped):
        return True
    if "@" in stripped or stripped.startswith("http"):
        return True
    folded = fold_text(stripped)
    tokens = [part for part in folded.split() if part not in SEARCH_STOP_WORDS]
    if not tokens:
        return True
    if re.fullmatch(r"[\d\s./-]+", stripped):
        return True
    return False


def _listing_type_for(text: str) -> str:
    if MAKE_HINT.search(text or ""):
        return Product.ListingType.MAKE
    return Product.ListingType.SELL


def parse_listing_products(text: str) -> list[ListingProduct]:
    """Turn PDF / listing text into product candidates matched to the taxonomy."""
    catalog = _taxonomy_catalog()
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]

    candidates: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_noise_line(line):
            index += 1
            continue
        description_parts = []
        look = index + 1
        while look < len(lines) and not _is_noise_line(lines[look]) and len(lines[look]) > 40:
            description_parts.append(lines[look])
            look += 1
            if len(description_parts) >= 3:
                break
        candidates.append((line, " ".join(description_parts)))
        index = look if look > index + 1 else index + 1

    seen: set[str] = set()
    products: list[ListingProduct] = []

    def add_product(name: str, description: str = "", keywords: list[str] | None = None) -> None:
        key = fold_text(name)
        if not key or key in seen:
            return
        family, product_type = match_category(name, catalog)
        if family is None and product_type is None and description:
            family, product_type = match_category(f"{name} {description}", catalog)
        item = ListingProduct(
            name=name[:MAX_NAME_LEN],
            description=(description or name)[:MAX_DESCRIPTION_LEN],
            listing_type=_listing_type_for(f"{name} {description}"),
            category=family,
            subcategory=product_type,
            keywords=keywords or [],
        )
        if item.category is None:
            item.skip_reason = "aucune catégorie Paknomik correspondante"
        seen.add(key)
        products.append(item)

    for name, description in candidates:
        add_product(name, description)

    body = fold_text(text)
    matched_subs = {item.subcategory.pk for item in products if item.subcategory}
    if body:
        for entry in catalog:
            if not entry.is_subcategory or entry.node.pk in matched_subs:
                continue
            if entry.folded in body and entry.folded not in seen:
                label = (entry.node.name_fr or entry.node.name_en).strip()
                add_product(label, label)
                last = products[-1] if products else None
                if last and last.subcategory:
                    matched_subs.add(last.subcategory.pk)

    usable = [item for item in products if item.is_usable]
    skipped = [item for item in products if not item.is_usable]
    return usable[:MAX_PRODUCTS] + skipped


def listing_products_from_specs(specs: list[dict]) -> list[ListingProduct]:
    """Build listing rows from a curated JSON list (name + optional category names)."""
    catalog = _taxonomy_catalog()
    products: list[ListingProduct] = []
    for raw in specs or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        description = str(raw.get("description") or "").strip()
        listing_type = str(raw.get("listingType") or raw.get("listing_type") or "").strip()
        if listing_type not in Product.ListingType.values:
            listing_type = _listing_type_for(f"{name} {description}")
        category = None
        subcategory = None
        category_id = raw.get("categoryId") or raw.get("category_id")
        subcategory_id = raw.get("subcategoryId") or raw.get("subcategory_id")
        if category_id:
            category = Category.objects.filter(pk=category_id, parent__isnull=True).first()
        if subcategory_id:
            subcategory = Category.objects.filter(pk=subcategory_id, parent__isnull=False).first()
            if subcategory and category is None:
                category = subcategory.parent
        if category is None:
            hint = " ".join(
                part
                for part in (
                    name,
                    description,
                    str(raw.get("category") or ""),
                    str(raw.get("subcategory") or ""),
                )
                if part
            )
            category, subcategory = match_category(hint, catalog)
        keywords = raw.get("keywords") or raw.get("customKeywords") or []
        if not isinstance(keywords, list):
            keywords = []
        item = ListingProduct(
            name=name[:MAX_NAME_LEN],
            description=(description or name)[:MAX_DESCRIPTION_LEN],
            listing_type=listing_type,
            category=category,
            subcategory=subcategory,
            keywords=[str(word).strip() for word in keywords if str(word).strip()],
        )
        if item.category is None:
            item.skip_reason = "aucune catégorie Paknomik correspondante"
        products.append(item)
    return products


def _create_listing_product(supplier: Supplier, spec: ListingProduct, *, actor: User) -> Product:
    product = Product.objects.create(
        supplier=supplier,
        category=spec.category,
        subcategory=spec.subcategory,
        name=spec.name,
        description=spec.description or spec.name,
        listing_type=spec.listing_type or Product.ListingType.SELL,
        min_order_quantity=1,
        lead_time_days=14,
        is_active=True,
    )
    tag_ids = resolve_search_tag_ids([], spec.keywords)
    if tag_ids:
        apply_product_search_tags(product, tag_ids)
    handle_product_added(supplier.pk, product.pk, actor_user=actor)
    return product


def _existing_product_names(supplier: Supplier) -> set[str]:
    return {fold_text(name) for name in supplier.products.values_list("name", flat=True)}


def _sync_interests(supplier: Supplier, products: list[Product]) -> None:
    families = {row.category_id for row in products if row.category_id}
    types = {row.subcategory_id for row in products if row.subcategory_id}
    if families:
        supplier.interested_categories.add(*families)
    if types:
        supplier.interested_subcategories.add(*types)


@transaction.atomic
def create_supplier_account(
    company_name: str,
    email: str,
    *,
    password: str | None = None,
    ghost: bool = False,
    actor: User | None = None,
) -> tuple[Supplier, User, bool, str | None]:
    """Create or reuse the User + Supplier pair for this email."""
    company_name = (company_name or "").strip()
    if not company_name:
        raise OnboardError("Le nom du fournisseur est obligatoire.")
    email = normalize_email(email)

    supplier = Supplier.objects.filter(email__iexact=email).first()
    user = User.objects.filter(email__iexact=email).first()
    created = False
    generated_password = None

    if user and user.supplier_id and supplier and user.supplier_id != supplier.pk:
        raise OnboardError(
            "Cet email est déjà lié à un autre compte fournisseur."
        )
    if user and user.supplier_id is None and supplier is None:
        raise OnboardError(
            "Cet email appartient déjà à un compte acheteur. Utilisez un autre email."
        )

    if supplier is None:
        supplier = Supplier.objects.create(
            company_name=company_name,
            email=email,
            is_active=True,
            is_ghost=ghost,
        )
        created = True
    else:
        updates = []
        if supplier.company_name != company_name:
            supplier.company_name = company_name
            updates.append("company_name")
        if supplier.is_ghost != ghost:
            supplier.is_ghost = ghost
            updates.append("is_ghost")
        if not supplier.is_active:
            supplier.is_active = True
            updates.append("is_active")
        if updates:
            updates.append("updated_at")
            supplier.save(update_fields=updates)

    if user is None:
        generated_password = password or secrets.token_urlsafe(12)
        user = User.objects.create_user(
            username=username_from_identity(company_name, email),
            email=email,
            password=generated_password,
            company_name=company_name,
            supplier=supplier,
        )
    elif user.supplier_id != supplier.pk:
        user.supplier = supplier
        user.company_name = user.company_name or company_name
        user.save(update_fields=["supplier", "company_name"])

    if created:
        handle_supplier_created(supplier, actor_user=actor or user)
    return supplier, user, created, generated_password


def onboard_supplier(
    company_name: str,
    email: str,
    *,
    pdf_source=None,
    product_specs: list[dict] | None = None,
    password: str | None = None,
    ghost: bool = False,
    actor: User | None = None,
) -> OnboardResult:
    """Create the supplier, then import products from curated specs and/or a PDF."""
    supplier, user, created, generated_password = create_supplier_account(
        company_name,
        email,
        password=password,
        ghost=ghost,
        actor=actor,
    )
    pdf_text = extract_pdf_text(pdf_source) if pdf_source is not None else ""
    if product_specs:
        listing = listing_products_from_specs(product_specs)
    elif pdf_text:
        listing = parse_listing_products(pdf_text)
    else:
        listing = []

    existing = _existing_product_names(supplier)
    created_products: list[Product] = []
    skipped: list[ListingProduct] = []
    actor_user = actor or user

    for spec in listing:
        if not spec.is_usable:
            skipped.append(spec)
            continue
        key = fold_text(spec.name)
        if key in existing:
            spec.skip_reason = "produit déjà présent sur ce compte"
            skipped.append(spec)
            continue
        product = _create_listing_product(supplier, spec, actor=actor_user)
        created_products.append(product)
        existing.add(key)

    if created_products:
        _sync_interests(supplier, created_products)

    return OnboardResult(
        supplier=supplier,
        user=user,
        created=created,
        password=generated_password,
        products_created=created_products,
        products_skipped=skipped,
        pdf_text=pdf_text,
    )
