"""Product taxonomy rules: schema, offer mode, ranking, alternatives.

Constants live here so search, import and the admin share one configuration.
The historical catalogue (Category, listing_type, orders, Pragmadinar) is not
replaced by this module.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

MAX_CARDS_PER_SUPPLIER_PAGE1 = 2
SCORE_TIE_BAND = 0.10
ALTERNATIVES_MAX = 4
ALTERNATIVES_MIN_RESULTS = 6
LEVEL_WEIGHT = {"DIRECT": 1.0, "PROCHE": 0.6}
ALTERNATIVES_BILLABLE = False

PAGE_SIZE = 12
MAX_VARIANT_AXES = 3
MIN_ATTRIBUTES = 5
MAX_ATTRIBUTES = 8
MIN_REQUIRED_ATTRIBUTES = 3
MAX_REQUIRED_ATTRIBUTES = 4
PIVOT_KEYS = ("capacite", "materiau", "contact_alimentaire", "dimensions")
ATTRIBUTE_KINDS = ("number", "enum", "multi", "text", "bool")
OFFER_STOCK = "STOCK"
OFFER_SUR_MESURE = "SUR_MESURE"
RELEVANCE_WEIGHT = 0.50
QUALITY_WEIGHT = 0.30
REACTIVITY_WEIGHT = 0.20
SESSION_SLICE_HOURS = 4
TEXT_MAX_LENGTH = 500

KEY_RE_MESSAGE = "Clé d'attribut invalide."


class SchemaError(ValueError):
    def __init__(self, messages):
        self.messages = list(messages)
        super().__init__("; ".join(self.messages))


def public_config() -> dict[str, Any]:
    return {
        "maxCardsPerSupplierPage1": MAX_CARDS_PER_SUPPLIER_PAGE1,
        "scoreTieBand": SCORE_TIE_BAND,
        "alternativesMax": ALTERNATIVES_MAX,
        "alternativesMinResults": ALTERNATIVES_MIN_RESULTS,
        "levelWeight": dict(LEVEL_WEIGHT),
        "alternativesBillable": ALTERNATIVES_BILLABLE,
        "pageSize": PAGE_SIZE,
        "maxVariantAxes": MAX_VARIANT_AXES,
        "pivotKeys": list(PIVOT_KEYS),
        "relevanceWeight": RELEVANCE_WEIGHT,
        "qualityWeight": QUALITY_WEIGHT,
        "reactivityWeight": REACTIVITY_WEIGHT,
        "sessionSliceHours": SESSION_SLICE_HOURS,
    }


def default_offer_mode(kind: str) -> str:
    if kind == "MANUFACTURER":
        return OFFER_SUR_MESURE
    return OFFER_STOCK


def session_slice_for(moment: datetime) -> int:
    return int(moment.hour) // SESSION_SLICE_HOURS


def rotation_rank(supplier_id: int, on_date: date, session_slice: int) -> int:
    payload = f"{supplier_id}|{on_date.isoformat()}|{int(session_slice)}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _is_key(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if not ("a" <= value[0] <= "z"):
        return False
    return all(char.islower() or char.isdigit() or char == "_" for char in value)


def validate_attribute_schema(schema: Any, *, strict_size: bool = True) -> list[dict]:
    if not isinstance(schema, list):
        raise SchemaError(["Le gabarit doit être une liste d'attributs."])
    if strict_size and not (MIN_ATTRIBUTES <= len(schema) <= MAX_ATTRIBUTES):
        raise SchemaError(
            [f"Un type porte entre {MIN_ATTRIBUTES} et {MAX_ATTRIBUTES} attributs."]
        )
    cleaned: list[dict] = []
    seen: set[str] = set()
    required_count = 0
    errors: list[str] = []
    for raw in schema:
        if not isinstance(raw, dict):
            errors.append("Chaque attribut est un objet.")
            continue
        key = raw.get("key")
        if not _is_key(key):
            errors.append(KEY_RE_MESSAGE)
            continue
        if key in seen:
            errors.append(f"Attribut en double : {key}.")
            continue
        seen.add(key)
        kind = raw.get("kind")
        if kind not in ATTRIBUTE_KINDS:
            errors.append(f"{key} : kind inconnu.")
            continue
        label = str(raw.get("label") or "").strip()
        if not label:
            errors.append(f"{key} : libellé manquant.")
            continue
        options = raw.get("options") or []
        if kind in {"enum", "multi"}:
            if not isinstance(options, list) or not options:
                errors.append(f"{key} : options requises.")
                continue
            options = [str(item) for item in options]
        else:
            options = []
        pivot = raw.get("pivot_key") or ""
        if pivot and pivot not in PIVOT_KEYS:
            errors.append(f"{key} : pivot_key inconnu.")
            continue
        required = bool(raw.get("required"))
        if required:
            required_count += 1
        item = {
            "key": key,
            "label": label,
            "kind": kind,
            "unit": str(raw.get("unit") or ""),
            "required": required,
            "filterable": bool(raw.get("filterable")),
            "options": options,
        }
        if pivot:
            item["pivot_key"] = pivot
        for extra in ("label_en", "label_ar"):
            if raw.get(extra):
                item[extra] = str(raw[extra])
        cleaned.append(item)
    if strict_size and not (
        MIN_REQUIRED_ATTRIBUTES <= required_count <= MAX_REQUIRED_ATTRIBUTES
    ):
        errors.append(
            f"Un type a {MIN_REQUIRED_ATTRIBUTES} ou {MAX_REQUIRED_ATTRIBUTES} "
            "attributs obligatoires."
        )
    if errors:
        raise SchemaError(errors)
    return cleaned


def _schema_map(schema: list[dict]) -> dict[str, dict]:
    return {item["key"]: item for item in schema}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_number(value: Any, *, allow_range: bool) -> str | None:
    if _is_number(value):
        return None
    if allow_range and isinstance(value, dict):
        low = value.get("min")
        high = value.get("max")
        if low is None and high is None:
            return "Indiquez un minimum ou un maximum."
        if low is not None and not _is_number(low):
            return "Minimum invalide."
        if high is not None and not _is_number(high):
            return "Maximum invalide."
        if low is not None and high is not None and low > high:
            return "Le minimum dépasse le maximum."
        return None
    if allow_range:
        return "Nombre ou plage attendu."
    return "Valeur fixe attendue (pas une plage)."


def _check_value(spec: dict, value: Any, *, allow_range: bool) -> str | None:
    kind = spec["kind"]
    options = set(spec.get("options") or [])
    if kind == "number":
        return _check_number(value, allow_range=allow_range)
    if kind == "bool":
        if isinstance(value, bool):
            return None
        return "Oui ou non attendu."
    if kind == "text":
        if isinstance(value, str) and len(value) <= TEXT_MAX_LENGTH:
            return None
        return "Texte attendu."
    if kind == "enum":
        if isinstance(value, str) and value in options:
            return None
        if allow_range and isinstance(value, list) and value:
            if all(isinstance(item, str) and item in options for item in value):
                return None
        return "Option inconnue."
    if isinstance(value, list) and value:
        if all(isinstance(item, str) and item in options for item in value):
            return None
    return "Liste d'options attendue."


def validate_attributes(
    schema: list[dict],
    attributes: Any,
    *,
    offer_mode: str,
    publishing: bool,
) -> dict:
    if attributes in (None, ""):
        attributes = {}
    if not isinstance(attributes, dict):
        raise SchemaError(["Les caractéristiques doivent être un objet."])
    specs = _schema_map(schema)
    errors: list[str] = []
    cleaned: dict[str, Any] = {}
    allow_range = offer_mode == OFFER_SUR_MESURE
    for key, value in attributes.items():
        spec = specs.get(key)
        if spec is None:
            errors.append(f"Attribut inconnu : {key}.")
            continue
        if value is None or value == "" or value == []:
            continue
        problem = _check_value(spec, value, allow_range=allow_range)
        if problem:
            errors.append(f"{spec['label']} : {problem}")
        else:
            cleaned[key] = value
    if publishing:
        for spec in schema:
            if spec.get("required") and spec["key"] not in cleaned:
                errors.append(f"{spec['label']} est obligatoire.")
    if errors:
        raise SchemaError(errors)
    return cleaned


def validate_axes(schema: list[dict], axes: Any, *, offer_mode: str) -> dict:
    if axes in (None, ""):
        return {}
    if not isinstance(axes, dict):
        raise SchemaError(["Les axes de variante doivent être un objet."])
    if len(axes) > MAX_VARIANT_AXES:
        raise SchemaError([f"Une variante a au plus {MAX_VARIANT_AXES} axes."])
    return validate_attributes(schema, axes, offer_mode=offer_mode, publishing=False)


def stock_publish_error(variants: list[dict]) -> str | None:
    for variant in variants:
        if not variant.get("is_available", True):
            continue
        if variant.get("price_from") is not None and variant.get("moq"):
            return None
    return (
        "Pour publier un produit à vendre, au moins une variante doit avoir "
        "un prix de départ et une quantité minimum. Sinon, passez le produit en sur mesure."
    )


def required_covered(schema: list[dict], attributes: dict, variants: list[dict]) -> list[str]:
    """Required keys may sit on the gamme or on every available variant."""
    missing = []
    available = [item for item in variants if item.get("is_available", True)] or [{}]
    for spec in schema:
        if not spec.get("required"):
            continue
        key = spec["key"]
        if key in attributes:
            continue
        if available and all(key in (item.get("axes") or {}) for item in available):
            continue
        missing.append(spec["label"])
    return missing


def order_is_allowed(product) -> bool:
    mode = getattr(product, "offer_mode", "") or ""
    if mode == OFFER_SUR_MESURE:
        return bool(getattr(product, "is_mixed", False))
    if mode == OFFER_STOCK:
        return True
    return getattr(product, "listing_type", "sell") != "make"


def primary_action(offer_mode: str) -> str:
    if offer_mode == OFFER_SUR_MESURE:
        return "quote"
    return "order"


def secondary_action(offer_mode: str, is_mixed: bool) -> str | None:
    if not is_mixed:
        return None
    if offer_mode == OFFER_SUR_MESURE:
        return "order"
    return "quote"


def badge_label(offer_mode: str) -> str:
    if offer_mode == OFFER_SUR_MESURE:
        return "Sur mesure"
    return "Disponible"


def action_label(action: str | None) -> str | None:
    if action == "order":
        return "Commander"
    if action == "quote":
        return "Demande de devis"
    return None


def _plural_label(label: str, count: int) -> str:
    text = label.lower()
    if count > 1 and not text.endswith("s"):
        text = f"{text}s"
    return text


def _format_amount(amount: Decimal | int | float) -> str:
    number = Decimal(str(amount))
    if number == number.to_integral():
        return str(int(number))
    return f"{number:.2f}"


def variant_summary(variants: list[dict], schema: list[dict]) -> str:
    labels = {item["key"]: item.get("label") or item["key"] for item in schema}
    buckets: dict[str, set[str]] = {}
    prices: list[Decimal] = []
    for variant in variants:
        if not variant.get("is_available", True):
            continue
        for key, value in (variant.get("axes") or {}).items():
            buckets.setdefault(key, set()).add(str(value))
        price = variant.get("price_from")
        if price is not None:
            prices.append(Decimal(str(price)))
    parts = [
        f"{len(values)} {_plural_label(labels.get(key, key), len(values))}"
        for key, values in buckets.items()
        if values
    ]
    if prices:
        parts.append(f"dès {_format_amount(min(prices))} DA")
    return ", ".join(parts)


def gamme_score(*, relevance: float, quality: float, reactivity: float) -> float:
    return (
        RELEVANCE_WEIGHT * relevance
        + QUALITY_WEIGHT * quality
        + REACTIVITY_WEIGHT * reactivity
    )


def relevance_score(
    *,
    name: str,
    description: str,
    query: str,
    filters_matched: int,
    filters_total: int,
    type_match: bool,
) -> float:
    text = 1.0
    tokens = [part for part in (query or "").lower().split() if part]
    if tokens:
        blob = f"{name} {description}".lower()
        text = sum(1 for token in tokens if token in blob) / len(tokens)
    filters = 1.0 if filters_total == 0 else filters_matched / filters_total
    type_part = 1.0 if type_match else 0.4
    return 0.5 * text + 0.3 * filters + 0.2 * type_part


def quality_score(
    *,
    required_filled: int,
    required_total: int,
    description_length: int,
    has_photo: bool,
    has_sheet: bool,
) -> float:
    base = 1.0 if required_total == 0 else required_filled / required_total
    bonus = 0.0
    if description_length >= 40:
        bonus += 0.15
    if has_photo:
        bonus += 0.15
    if has_sheet:
        bonus += 0.10
    return min(1.0, 0.60 * base + bonus)


def reactivity_score(raw: int | None) -> float:
    if raw is None:
        return 0.5
    return max(0.0, min(float(raw), 100.0)) / 100.0


def value_matches(stored: Any, wanted: Any, kind: str) -> bool:
    if kind == "number":
        if isinstance(stored, dict):
            low = stored.get("min")
            high = stored.get("max")
            try:
                number = float(wanted)
            except (TypeError, ValueError):
                return False
            if low is not None and number < float(low):
                return False
            if high is not None and number > float(high):
                return False
            return True
        try:
            return float(stored) == float(wanted)
        except (TypeError, ValueError):
            return False
    if kind == "bool":
        return bool(stored) is bool(wanted)
    if kind == "enum":
        if isinstance(stored, list):
            return str(wanted) in {str(item) for item in stored}
        return str(stored) == str(wanted)
    if kind == "multi":
        stored_set = {str(item) for item in stored} if isinstance(stored, list) else {str(stored)}
        if isinstance(wanted, list):
            return any(str(item) in stored_set for item in wanted)
        return str(wanted) in stored_set
    return str(stored).lower() == str(wanted).lower()


def attribute_value(attributes: dict, axes: dict, key: str) -> Any:
    if key in axes:
        return axes[key]
    return attributes.get(key)


def filters_match_count(
    schema: list[dict],
    attributes: dict,
    variants: list[dict],
    filters: dict,
) -> tuple[int, int]:
    specs = _schema_map(schema)
    matched = 0
    considered = 0
    usable = [item for item in variants if item.get("is_available", True)]
    for key, wanted in filters.items():
        spec = specs.get(key)
        if spec is None:
            continue
        considered += 1
        if any(
            value_matches(
                attribute_value(attributes, item.get("axes") or {}, key),
                wanted,
                spec["kind"],
            )
            for item in (usable or [{"axes": {}}])
            if attribute_value(attributes, item.get("axes") or {}, key) is not None
        ):
            matched += 1
    return matched, considered


@dataclass(frozen=True)
class TranslateResult:
    filters: dict
    blocked: bool


def translate_filters(
    filters: dict,
    attribute_map: dict,
    source_schema: list[dict],
    target_schema: list[dict],
) -> TranslateResult:
    source = _schema_map(source_schema)
    target = _schema_map(target_schema)
    required = {item["key"] for item in source_schema if item.get("required")}
    pivot_to_target: dict[str, str] = {}
    for item in target_schema:
        pivot = item.get("pivot_key") or (item["key"] if item["key"] in PIVOT_KEYS else "")
        if pivot and pivot not in pivot_to_target:
            pivot_to_target[pivot] = item["key"]
    translated: dict[str, Any] = {}
    blocked = False
    for key, value in (filters or {}).items():
        mapped = (attribute_map or {}).get(key)
        if not mapped:
            spec = source.get(key, {})
            pivot = spec.get("pivot_key") or (key if key in PIVOT_KEYS else "")
            mapped = pivot_to_target.get(pivot or "")
        if not mapped or mapped not in target:
            if key in required:
                blocked = True
            continue
        translated[mapped] = value
    return TranslateResult(translated, blocked)


@dataclass(frozen=True)
class GammeCard:
    product_id: int
    supplier_id: int
    type_id: int
    score: float
    is_flagship: bool


@dataclass
class CollapsedCard:
    card: GammeCard
    other_ids: list[int] = field(default_factory=list)

    @property
    def product_id(self) -> int:
        return self.card.product_id

    @property
    def supplier_id(self) -> int:
        return self.card.supplier_id

    @property
    def score(self) -> float:
        return self.card.score


def collapse_supplier_types(cards: list[GammeCard]) -> list[CollapsedCard]:
    groups: dict[tuple[int, int], list[GammeCard]] = {}
    for card in cards:
        groups.setdefault((card.supplier_id, card.type_id), []).append(card)
    collapsed: list[CollapsedCard] = []
    for group in groups.values():
        flagged = [item for item in group if item.is_flagship]
        pool = flagged or group
        chosen = sorted(pool, key=lambda item: (-item.score, item.product_id))[0]
        others = [item.product_id for item in group if item.product_id != chosen.product_id]
        collapsed.append(CollapsedCard(chosen, others))
    return collapsed


def _in_band(score: float, top: float) -> bool:
    return (top - score) <= SCORE_TIE_BAND + 1e-9


def _round_robin(
    band: list[CollapsedCard],
    on_date: date,
    session_slice: int,
) -> list[CollapsedCard]:
    by_supplier: dict[int, list[CollapsedCard]] = {}
    for card in band:
        by_supplier.setdefault(card.supplier_id, []).append(card)
    for queue in by_supplier.values():
        queue.sort(key=lambda item: (-item.score, item.product_id))
    order = sorted(
        by_supplier,
        key=lambda supplier_id: (
            rotation_rank(supplier_id, on_date, session_slice),
            supplier_id,
        ),
    )
    ranked: list[CollapsedCard] = []
    while any(by_supplier[supplier_id] for supplier_id in order):
        for supplier_id in order:
            queue = by_supplier[supplier_id]
            if queue:
                ranked.append(queue.pop(0))
    return ranked


def order_by_score_and_rotation(
    cards: list[CollapsedCard],
    *,
    on_date: date,
    session_slice: int,
) -> list[CollapsedCard]:
    remaining = sorted(cards, key=lambda item: (-item.score, item.product_id))
    ordered: list[CollapsedCard] = []
    while remaining:
        top = remaining[0].score
        band = [item for item in remaining if _in_band(item.score, top)]
        band_ids = {item.product_id for item in band}
        ordered.extend(_round_robin(band, on_date, session_slice))
        remaining = [item for item in remaining if item.product_id not in band_ids]
    return ordered


def split_page1(
    ordered: list[CollapsedCard],
    *,
    page_size: int,
    cap: int = MAX_CARDS_PER_SUPPLIER_PAGE1,
) -> tuple[list[CollapsedCard], list[CollapsedCard]]:
    """Page 1 keeps at most `cap` cards per supplier, even when the page is not full."""
    page1: list[CollapsedCard] = []
    overflow: list[CollapsedCard] = []
    counts: dict[int, int] = {}
    for card in ordered:
        used = counts.get(card.supplier_id, 0)
        if len(page1) < page_size and used < cap:
            page1.append(card)
            counts[card.supplier_id] = used + 1
        else:
            overflow.append(card)
    return page1, overflow


def rank_organic(
    cards: list[GammeCard],
    *,
    page: int,
    page_size: int,
    on_date: date,
    session_slice: int,
) -> tuple[list[CollapsedCard], int]:
    collapsed = collapse_supplier_types(cards)
    ordered = order_by_score_and_rotation(
        collapsed, on_date=on_date, session_slice=session_slice
    )
    page1, overflow = split_page1(ordered, page_size=page_size)
    total = len(page1) + len(overflow)
    if page <= 1:
        return page1, total
    start = (page - 2) * page_size
    return overflow[start : start + page_size], total


def format_variant_axes(axes: dict | None) -> str:
    if not axes:
        return ""
    return ", ".join(f"{key} : {value}" for key, value in axes.items())
