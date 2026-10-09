"""
Generates PDF documents related to products (catalog sheets).
"""

from __future__ import annotations

import html
import re

from django.template.loader import render_to_string
from django.utils.safestring import SafeString, mark_safe

from user.proposal_pdf import (
    BODY_TEXT,
    PAGE_BG,
    _normalize_brand_color,
    fitted_image_from_field,
    html_to_pdf_bytes,
    mark_last_list_items,
    paknomik_hero_html,
    paknomik_page_chrome_context,
    paknomik_pill_html,
    paknomik_rect_assets,
)
from user.rich_text import sanitize_rich_html


HERO_TITLE = "Fiche technique"


def _slugify_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned.lower() or "produit"


_TITLE_RE = re.compile(r"^#\s+(.*)$")
_SUBTITLE_RE = re.compile(r"^##\s+(.*)$")
_OL_RE = re.compile(r"^\d+[.)]\s+(.*)$")
_UL_RE = re.compile(r"^[-•*]\s+(.*)$")


def description_to_html(text: str) -> SafeString:
    """Render the product description markers (#, ##, 1., -) as escaped HTML blocks."""
    parts: list[str] = []
    list_tag = None
    paragraph: list[str] = []

    def flush_paragraph():
        if paragraph:
            parts.append(f'<p class="block-text">{"<br/>".join(paragraph)}</p>')
            paragraph.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            parts.append(f"</{list_tag}>")
            list_tag = None

    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush_paragraph()
            continue
        heading = _SUBTITLE_RE.match(line) or _TITLE_RE.match(line)
        if heading:
            flush_paragraph()
            close_list()
            css = "desc-subtitle" if line.startswith("##") else "desc-title"
            parts.append(f'<p class="{css}">{html.escape(heading.group(1).strip())}</p>')
            continue
        item = _OL_RE.match(line)
        tag = "ol" if item else None
        if not item:
            item = _UL_RE.match(line)
            tag = "ul" if item else None
        if item:
            flush_paragraph()
            if list_tag != tag:
                close_list()
                parts.append(f'<{tag} class="desc-list">')
                list_tag = tag
            parts.append(f"<li>{html.escape(item.group(1).strip())}</li>")
            continue
        close_list()
        paragraph.append(html.escape(line))

    flush_paragraph()
    close_list()
    return mark_safe("".join(parts))


def _product_description_html(product) -> SafeString:
    rich = sanitize_rich_html(getattr(product, "description_html", ""))
    if rich:
        markup = f'<div class="block-text rich-text">{rich}</div>'
    else:
        markup = description_to_html((product.description or "").strip())
    return mark_safe(mark_last_list_items(markup))


def build_product_datasheet_context(product) -> dict:
    from user.api.serializers import _product_technical_sheet_rows

    supplier = product.supplier
    brand_color = _normalize_brand_color(
        getattr(supplier, "brand_color", None) or getattr(supplier, "cover_color", None)
    )
    rects = paknomik_rect_assets(brand_color)
    rows = _product_technical_sheet_rows(product)
    display_image = product.get_display_image()
    category_name = ""
    if product.subcategory_id and product.subcategory:
        category_name = product.subcategory.name_fr or product.subcategory.name_en
    elif product.category_id and product.category:
        category_name = product.category.name_fr or product.category.name_en

    markets = []
    for raw in product.target_markets or []:
        label = str(raw).strip()
        if label:
            markets.append(html.escape(label))

    spec_rows = [
        {"label": html.escape(row.get("label") or ""), "value": html.escape(row.get("value") or "")}
        for row in rows
        if row.get("label") or row.get("value")
    ]

    return {
        "hero_title": HERO_TITLE,
        "hero_html": paknomik_hero_html(HERO_TITLE, rects["hero_left_uri"], rects["hero_right_uri"]),
        "product_name": html.escape(product.name or ""),
        "supplier_name": html.escape(supplier.company_name or ""),
        "supplier_logo": fitted_image_from_field(getattr(supplier, "logo", None), 150, 90),
        "product_image": fitted_image_from_field(display_image, 250, 230),
        "category_name": html.escape(category_name),
        "description": _product_description_html(product),
        "spec_rows": spec_rows,
        "markets": markets,
        "supplier_phone": html.escape((supplier.whatsapp_number or "").strip()),
        "supplier_email": html.escape((supplier.email or "").strip()),
        "supplier_website": html.escape((supplier.website or "").strip()),
        "brand_color": brand_color,
        "body_text_color": BODY_TEXT,
        "page_bg": PAGE_BG,
        **paknomik_page_chrome_context(),
        "pill_html_specs": paknomik_pill_html(
            "Caractéristiques",
            rects["pill_left_uri"],
            rects["pill_right_uri"],
        ),
        **rects,
        "filename": f"fiche-technique-{_slugify_filename(product.name)}.pdf",
    }


def render_product_datasheet_html(**context) -> str:
    return render_to_string("user/pdf/product_datasheet.html", context)


def product_datasheet_pdf_bytes(product) -> tuple[bytes, str]:
    context = build_product_datasheet_context(product)
    html_content = render_product_datasheet_html(**context)
    return html_to_pdf_bytes(html_content), context["filename"]
