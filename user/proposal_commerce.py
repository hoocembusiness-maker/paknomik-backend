"""Line items and totals for QuoteDemand commercial proposals."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from rest_framework.exceptions import ValidationError

MONEY_QUANT = Decimal("0.01")
DEFAULT_CURRENCY = "DZD"


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _parse_line(raw: dict, index: int) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError(
            {"error": f"Ligne {index + 1} invalide.", "code": "invalid_line"}
        )
    description = (raw.get("description") or raw.get("label") or "").strip()
    product_id = raw.get("productId") or raw.get("product_id")
    reference = (raw.get("reference") or raw.get("sku") or "").strip()
    unit = (raw.get("unit") or "u").strip() or "u"
    try:
        quantity = int(raw.get("quantity") or 0)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            {"error": f"Quantité invalide (ligne {index + 1}).", "code": "invalid_quantity"}
        ) from exc
    try:
        unit_price = _money(raw.get("unitPrice") or raw.get("unit_price"))
    except Exception as exc:
        raise ValidationError(
            {"error": f"Prix unitaire invalide (ligne {index + 1}).", "code": "invalid_price"}
        ) from exc
    discount_pct = raw.get("discountPercent") or raw.get("discount_percent") or 0
    try:
        discount_pct = _money(discount_pct)
    except Exception as exc:
        raise ValidationError(
            {"error": f"Remise invalide (ligne {index + 1}).", "code": "invalid_discount"}
        ) from exc
    if discount_pct < 0 or discount_pct > 100:
        raise ValidationError(
            {
                "error": f"La remise doit être comprise entre 0 et 100 % (ligne {index + 1}).",
                "code": "invalid_discount",
            }
        )

    if quantity <= 0:
        raise ValidationError(
            {"error": f"Quantité requise (ligne {index + 1}).", "code": "invalid_quantity"}
        )
    if unit_price < 0:
        raise ValidationError(
            {"error": f"Prix unitaire requis (ligne {index + 1}).", "code": "invalid_price"}
        )
    if not description and not product_id:
        raise ValidationError(
            {
                "error": f"Description ou produit requis (ligne {index + 1}).",
                "code": "line_description_required",
            }
        )

    gross = _money(unit_price * quantity)
    discount_amount = _money(gross * discount_pct / Decimal("100"))
    line_total = _money(gross - discount_amount)

    line = {
        "description": description or f"Produit #{product_id}",
        "productId": int(product_id) if product_id not in (None, "") else None,
        "reference": reference,
        "unit": unit,
        "quantity": quantity,
        "unitPrice": str(unit_price),
        "discountPercent": str(discount_pct),
        "lineTotal": str(line_total),
    }
    return line


def parse_line_items(raw_lines, *, require_at_least_one: bool = True) -> list[dict]:
    if raw_lines is None:
        raw_lines = []
    if not isinstance(raw_lines, list):
        raise ValidationError({"error": "lines must be a list.", "code": "invalid_lines"})
    lines = [_parse_line(row, i) for i, row in enumerate(raw_lines)]
    if require_at_least_one and not lines:
        raise ValidationError(
            {"error": "Ajoutez au moins une ligne commerciale.", "code": "lines_required"}
        )
    return lines


COMMON_TERM_KEYS = (
    "productionLeadDays",
    "validUntil",
    "paymentTerms",
    "deliveryTerms",
    "deliveryLocation",
    "availability",
    "warrantyNotes",
    "buyerNotes",
    "extraFees",
    "orderDiscountPercent",
    "taxRatePercent",
)
#: Only kept on export proposals so a local proposal stays short.
EXPORT_TERM_KEYS = (
    "destinationCountry",
    "incoterm",
    "incotermPlace",
    "departurePlace",
    "shippingLeadDays",
    "transportMode",
    "forwarderName",
    "forwarderContact",
    "freightCost",
    "freightIncluded",
)
TEXT_TERM_MAX_LENGTH = 500


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "oui")


def _parse_export_terms(cleaned: dict, *, require_complete: bool) -> None:
    from user.export_terms import (
        TRANSPORT_MODES,
        clean_export_country,
        clean_incoterm,
    )

    if "destinationCountry" in cleaned:
        cleaned["destinationCountry"] = clean_export_country(
            cleaned["destinationCountry"], required=False
        )
    incoterm = clean_incoterm(cleaned.get("incoterm"))
    if incoterm:
        cleaned["incoterm"] = incoterm
    else:
        cleaned.pop("incoterm", None)
        cleaned.pop("incotermPlace", None)
    if incoterm and require_complete and not cleaned.get("incotermPlace"):
        raise ValidationError(
            {
                "error": f"Précisez le lieu nommé associé à l'Incoterm {incoterm}.",
                "code": "incoterm_place_required",
            }
        )
    if "shippingLeadDays" in cleaned:
        try:
            days = int(cleaned["shippingLeadDays"])
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                {
                    "error": "Le délai d'expédition doit être un nombre de jours.",
                    "code": "invalid_shipping_days",
                }
            ) from exc
        if days < 0:
            raise ValidationError(
                {
                    "error": "Le délai d'expédition doit être positif.",
                    "code": "invalid_shipping_days",
                }
            )
        cleaned["shippingLeadDays"] = days
    if "transportMode" in cleaned and cleaned["transportMode"] not in TRANSPORT_MODES:
        raise ValidationError(
            {"error": "Mode de transport inconnu.", "code": "invalid_transport_mode"}
        )
    if "freightCost" in cleaned:
        try:
            cost = _money(cleaned["freightCost"])
        except Exception as exc:
            raise ValidationError(
                {"error": "Le coût de transport est invalide.", "code": "invalid_freight_cost"}
            ) from exc
        if cost < 0:
            raise ValidationError(
                {"error": "Le coût de transport doit être positif.", "code": "invalid_freight_cost"}
            )
        cleaned["freightCost"] = str(cost)
        cleaned["freightIncluded"] = _truthy(cleaned.get("freightIncluded", False))
    else:
        cleaned.pop("freightIncluded", None)


def parse_commercial_terms(raw, *, require_complete: bool = False, default_trade_scope="local"):
    """Validate proposal terms. ``require_complete`` applies the checks needed to
    send (e.g. the named place of a chosen Incoterm); drafts may be partial."""
    from user.export_terms import TRADE_EXPORT, clean_trade_scope

    if not raw or not isinstance(raw, dict):
        raw = {}
    trade_scope = clean_trade_scope(raw.get("tradeScope") or default_trade_scope)
    allowed_keys = COMMON_TERM_KEYS + (EXPORT_TERM_KEYS if trade_scope == TRADE_EXPORT else ())
    cleaned = {}
    for key in allowed_keys:
        if key not in raw:
            continue
        value = raw.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, str):
            value = value.strip()[:TEXT_TERM_MAX_LENGTH]
        cleaned[key] = value
    if trade_scope == TRADE_EXPORT:
        cleaned["tradeScope"] = TRADE_EXPORT
        _parse_export_terms(cleaned, require_complete=require_complete)

    for key, label, upper in (
        ("orderDiscountPercent", "La remise globale", Decimal("100")),
        ("taxRatePercent", "Le taux de taxe", Decimal("100")),
        ("extraFees", "Les frais", None),
    ):
        if key not in cleaned:
            continue
        try:
            amount = _money(cleaned[key])
        except Exception as exc:
            raise ValidationError(
                {"error": f"{label} est invalide.", "code": f"invalid_{key}"}
            ) from exc
        if amount < 0 or (upper is not None and amount > upper):
            bounds = "entre 0 et 100 %" if upper is not None else "positifs"
            raise ValidationError(
                {"error": f"{label} doit être {bounds}.", "code": f"invalid_{key}"}
            )
        cleaned[key] = str(amount)

    if "productionLeadDays" in cleaned:
        try:
            days = int(cleaned["productionLeadDays"])
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                {"error": "Le délai doit être un nombre de jours.", "code": "invalid_lead_days"}
            ) from exc
        if days < 0:
            raise ValidationError(
                {"error": "Le délai doit être positif.", "code": "invalid_lead_days"}
            )
        cleaned["productionLeadDays"] = days

    if "validUntil" in cleaned:
        try:
            date.fromisoformat(str(cleaned["validUntil"])[:10])
        except ValueError as exc:
            raise ValidationError(
                {"error": "Date de validité invalide.", "code": "invalid_valid_until"}
            ) from exc
        cleaned["validUntil"] = str(cleaned["validUntil"])[:10]
    return cleaned


def compute_totals(
    lines: list[dict],
    commercial_terms: dict | None = None,
) -> dict:
    terms = commercial_terms or {}
    subtotal = _money(sum(_money(line["lineTotal"]) for line in lines))
    order_discount_pct = _money(terms.get("orderDiscountPercent") or 0)
    discount_total = _money(subtotal * order_discount_pct / Decimal("100"))
    after_discount = _money(subtotal - discount_total)
    extra_fees = _money(terms.get("extraFees") or 0)
    tax_rate = _money(terms.get("taxRatePercent") or 0)
    taxable = _money(after_discount + extra_fees)
    tax_total = _money(taxable * tax_rate / Decimal("100")) if tax_rate > 0 else Decimal("0.00")
    freight = _money(freight_total(terms) or 0)
    total = _money(taxable + tax_total + freight)
    return {
        "subtotal": str(subtotal),
        "discountTotal": str(discount_total),
        "extraFees": str(extra_fees),
        "taxTotal": str(tax_total),
        "freightTotal": str(freight),
        "totalAmount": str(total),
    }


def freight_total(terms) -> str | None:
    """Transport/forwarder cost added to the total — only when the supplier
    declared it included. An excluded cost stays informative."""
    terms = terms or {}
    if not terms.get("freightCost") or not _truthy(terms.get("freightIncluded", False)):
        return None
    return str(_money(terms["freightCost"]))


def legacy_line_from_scalar(
    *,
    unit_price,
    quantity,
    description: str = "",
    product_id=None,
) -> list[dict]:
    """Single-line proposal from legacy scalar fields."""
    line = _parse_line(
        {
            "description": description or "Prestation / produit proposé",
            "productId": product_id,
            "quantity": quantity,
            "unitPrice": unit_price,
            "unit": "u",
            "discountPercent": 0,
        },
        0,
    )
    return [line]


def stored_totals(proposal) -> dict | None:
    """Totals persisted when the proposal was saved, or None for legacy rows."""
    if proposal.total_amount is None:
        return None
    return {
        "subtotal": str(_money(proposal.subtotal)),
        "discountTotal": str(_money(proposal.discount_total)),
        "extraFees": str(_money(proposal.extra_fees)),
        "taxTotal": str(_money(proposal.tax_total)),
        "freightTotal": freight_total(proposal.commercial_terms) or "0.00",
        "totalAmount": str(_money(proposal.total_amount)),
    }


def _party_supplier(supplier) -> dict:
    return {
        "id": supplier.id,
        "companyName": supplier.company_name or "",
        "wilaya": supplier.wilaya or "",
        "address": getattr(supplier, "address", "") or "",
        "phone": getattr(supplier, "whatsapp_number", "") or "",
    }


def _party_buyer(buyer) -> dict | None:
    if buyer is None:
        return None
    return {
        "id": buyer.id,
        "companyName": getattr(buyer, "company_name", "") or "",
        "contactName": buyer.get_full_name() or buyer.username or "",
        "email": buyer.email or "",
        "phone": getattr(buyer, "phone_number", "") or "",
        "wilaya": getattr(buyer, "wilaya", "") or "",
    }


def proposal_snapshot(proposal) -> dict:
    """Frozen payload of the agreement, reused verbatim by the order.

    Totals are the ones persisted on the proposal: a sent proposal is never
    silently recomputed.
    """
    lines = list(proposal.line_items or [])
    if not lines:
        lines = legacy_line_from_scalar(
            unit_price=proposal.unit_price,
            quantity=proposal.confirmed_quantity,
            description=(proposal.message or "")[:500],
        )
    terms = dict(proposal.commercial_terms or {})
    totals = stored_totals(proposal) or compute_totals(lines, terms)
    demand = proposal.demand
    return {
        "source": "quote_proposal",
        "proposalId": proposal.id,
        "proposalReference": f"DEV-{demand.id}-P{proposal.id}",
        "demandId": demand.id,
        "version": proposal.version,
        "supplier": _party_supplier(proposal.supplier),
        "buyer": _party_buyer(demand.buyer),
        "currency": proposal.currency or DEFAULT_CURRENCY,
        "lines": lines,
        "commercialTerms": terms,
        "totals": totals,
        "leadTimeDays": proposal.lead_time_days,
        "moq": proposal.moq,
        "confirmedQuantity": proposal.confirmed_quantity,
        "unitPrice": str(proposal.unit_price),
        "message": proposal.message or "",
        "sentAt": proposal.sent_at.isoformat() if proposal.sent_at else None,
        "acceptedAt": proposal.accepted_at.isoformat() if proposal.accepted_at else None,
    }


def quote_response_snapshot(response_obj, *, buyer, quantity, unit_price) -> dict:
    """Agreement snapshot for an order born from a storefront RFQ quote."""
    line = _parse_line(
        {
            "description": (
                (response_obj.quote_request.title or "").strip()
                or (response_obj.message or "")[:200]
                or "Prestation / produit proposé"
            ),
            "productId": response_obj.quote_request.product_id,
            "quantity": quantity,
            "unitPrice": unit_price,
            "unit": "u",
        },
        0,
    )
    totals = compute_totals([line], {})
    return {
        "source": "quote_response",
        "quoteResponseId": response_obj.id,
        "rfqId": response_obj.quote_request_id,
        "proposalReference": f"RFQ-{response_obj.quote_request_id}-R{response_obj.id}",
        "version": 1,
        "supplier": _party_supplier(response_obj.supplier),
        "buyer": _party_buyer(buyer),
        "currency": getattr(response_obj, "currency", "") or DEFAULT_CURRENCY,
        "lines": [line],
        "commercialTerms": {},
        "totals": totals,
        "leadTimeDays": response_obj.lead_time_days,
        "confirmedQuantity": quantity,
        "unitPrice": str(_money(unit_price)),
        "message": response_obj.message or "",
        "sentAt": response_obj.created_at.isoformat() if response_obj.created_at else None,
        "acceptedAt": None,
    }
