"""
Builds HTML and PDF files for commercial proposals
(after a supplier answers a quote or order).
"""

from __future__ import annotations

import base64
import html
import re
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string

from PIL import Image, ImageDraw

from user.api.serializers import render_proposal_blocks_to_text

DEFAULT_BRAND = "#6c3b9e"
BODY_TEXT = "#1a1a1a"
PAGE_BG = "#ebe4f7"
PAKNOMIK_ACCENT = "#6c3b9e"
PAKNOMIK_DARK = "#1a003a"
PAKNOMIK_PHONE = "00 213 699 39 89 88"
PAKNOMIK_EMAIL = "paknomik@gmail.com"
HERO_TITLE = "Proposition commerciale"
PAKNOMIK_LOGO_REL = Path("branding") / "paknomik-logo.png"


def _normalize_brand_color(value: str | None) -> str:
    raw = (value or "").strip()
    if raw and raw.startswith("#") and len(raw) in (4, 7):
        return raw
    return DEFAULT_BRAND


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = _normalize_brand_color(value).lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))


def _relative_luminance(color: str) -> float:
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = _hex_to_rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def readable_text_color(background: str) -> str:
    """White or Paknomik dark, whichever contrasts more with the background."""
    if _contrast_ratio("#ffffff", background) >= _contrast_ratio(PAKNOMIK_DARK, background):
        return "#ffffff"
    return PAKNOMIK_DARK


def brand_ink_color(brand_color: str, background: str = PAGE_BG, minimum: float = 3.0) -> str:
    """Brand color for text, darkened just enough to stay legible on the page."""
    color = _normalize_brand_color(brand_color)
    r, g, b = _hex_to_rgb(color)
    for step in range(11):
        factor = 1 - step / 10
        candidate = "#{:02x}{:02x}{:02x}".format(
            round(r * factor), round(g * factor), round(b * factor)
        )
        if _contrast_ratio(candidate, background) >= minimum:
            return candidate
    return PAKNOMIK_DARK


def _fitted_image(image: Image.Image, max_width: int, max_height: int) -> dict | None:
    """
    Downscale an image for the PDF and return its data URI with explicit
    display dimensions in CSS px: xhtml2pdf ignores max-width/max-height on
    <img> and otherwise draws the file at its intrinsic size.
    """
    if not image.width or not image.height:
        return None
    ratio = min(max_width / image.width, max_height / image.height, 1.0)
    width = max(1, round(image.width * ratio))
    height = max(1, round(image.height * ratio))
    raster = image.copy()
    raster.thumbnail((width * 2, height * 2), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    if raster.mode in ("RGBA", "LA", "P"):
        raster.convert("RGBA").save(buffer, format="PNG", optimize=True)
        mime = "image/png"
    else:
        raster.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True)
        mime = "image/jpeg"
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {"uri": f"data:{mime};base64,{encoded}", "width": width, "height": height}


def fitted_image_from_path(path: Path, max_width: int, max_height: int) -> dict | None:
    if not path.is_file():
        return None
    try:
        with Image.open(path) as image:
            image.load()
            return _fitted_image(image, max_width, max_height)
    except (OSError, ValueError):
        return None


def fitted_image_from_field(file_field, max_width: int, max_height: int) -> dict | None:
    if not file_field:
        return None
    try:
        path = Path(file_field.path)
    except (ValueError, NotImplementedError):
        return None
    return fitted_image_from_path(path, max_width, max_height)


def _png_data_uri(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _draw_paknomik_rect(width: int, height: int, fill: str, r_top: int, r_bot: int) -> Image.Image:
    """Rounded rectangle: larger top corners, smaller bottom corners."""
    scale = 4
    w, h = width * scale, height * scale
    rt, rb = r_top * scale, r_bot * scale
    rt = max(1, min(rt, w // 2, h // 2))
    rb = max(1, min(rb, w // 2, h // 2))
    color = _hex_to_rgb(fill)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.pieslice((0, 0, 2 * rt, 2 * rt), 180, 270, fill=color)
    draw.pieslice((w - 2 * rt, 0, w, 2 * rt), 270, 360, fill=color)
    draw.pieslice((0, h - 2 * rb, 2 * rb, h), 90, 180, fill=color)
    draw.pieslice((w - 2 * rb, h - 2 * rb, w, h), 0, 90, fill=color)
    draw.rectangle((rt, 0, w - rt, rt), fill=color)
    draw.rectangle((0, rt, w, h - rb), fill=color)
    draw.rectangle((rb, h - rb, w - rb, h), fill=color)
    return img.resize((width, height), Image.Resampling.LANCZOS)


HERO_CAP_WIDTH = 30
HERO_CAP_HEIGHT = 44


def _rect_caps(color: str, cap_w: int, cap_h: int, r_top: int, r_bot: int) -> tuple[str, str]:
    full = _draw_paknomik_rect(cap_w * 2 + 8, cap_h, color, r_top=r_top, r_bot=r_bot)
    left = full.crop((0, 0, cap_w, cap_h))
    right = full.crop((full.width - cap_w, 0, full.width, cap_h))
    return _png_data_uri(left), _png_data_uri(right)


def paknomik_rect_assets(brand_color: str) -> dict[str, str]:
    """
    Rounded caps drawn as images because xhtml2pdf ignores per-corner CSS radii
    and the HTML `background` attribute on cells; the middle of each shape is a
    plain background-color cell, which xhtml2pdf does paint.
    """
    color = _normalize_brand_color(brand_color)
    pill_left, pill_right = _rect_caps(color, 24, 30, r_top=22, r_bot=8)
    hero_left, hero_right = _rect_caps(
        color, HERO_CAP_WIDTH, HERO_CAP_HEIGHT, r_top=26, r_bot=10
    )
    return {
        "pill_left_uri": pill_left,
        "pill_right_uri": pill_right,
        "hero_left_uri": hero_left,
        "hero_right_uri": hero_right,
        "brand_text_color": readable_text_color(color),
        "brand_ink_color": brand_ink_color(color),
    }


def paknomik_pill_html(title: str, left_uri: str, right_uri: str) -> str:
    return (
        '<table class="pak-pill" cellpadding="0" cellspacing="0">'
        "<tr>"
        f'<td class="pak-pill-cap"><img src="{left_uri}" width="22" height="28" alt=""/></td>'
        f'<td class="pak-pill-mid">{title}</td>'
        f'<td class="pak-pill-cap"><img src="{right_uri}" width="22" height="28" alt=""/></td>'
        "</tr></table>"
    )


def paknomik_hero_html(title: str, left_uri: str, right_uri: str) -> str:
    return (
        '<table class="pak-hero" cellpadding="0" cellspacing="0" width="100%">'
        "<tr>"
        f'<td class="pak-hero-cap"><img src="{left_uri}" width="{HERO_CAP_WIDTH}"'
        f' height="{HERO_CAP_HEIGHT}" alt=""/></td>'
        f'<td class="pak-hero-mid">{html.escape(title)}</td>'
        f'<td class="pak-hero-cap"><img src="{right_uri}" width="{HERO_CAP_WIDTH}"'
        f' height="{HERO_CAP_HEIGHT}" alt=""/></td>'
        "</tr></table>"
    )


_LIST_TOKEN_RE = re.compile(r"<(/?)(ul|ol|li)\b[^>]*>", re.IGNORECASE)


def mark_last_list_items(markup: str) -> str:
    """
    Tag the last <li> of every list with class "pdf-li-last".

    The PDF CSS chains list items with -pdf-keep-with-next so a list moves to
    the next page as a block instead of leaving one item stranded; xhtml2pdf
    has no :last-child, so the chain has to be broken explicitly.
    """
    if "<li" not in markup.lower():
        return markup
    stack: list[int | None] = []
    last_items: list[int] = []
    for match in _LIST_TOKEN_RE.finditer(markup):
        closing, tag = match.group(1), match.group(2).lower()
        if tag in ("ul", "ol"):
            if not closing:
                stack.append(None)
            elif stack:
                position = stack.pop()
                if position is not None:
                    last_items.append(position)
        elif not closing and stack:
            stack[-1] = match.start()
    for position in sorted(last_items, reverse=True):
        insert_at = position + 3
        markup = f'{markup[:insert_at]} class="pdf-li-last"{markup[insert_at:]}'
    return markup


def paknomik_page_chrome_context() -> dict:
    return {
        "paknomik_accent": PAKNOMIK_ACCENT,
        "paknomik_phone": PAKNOMIK_PHONE,
        "paknomik_email": PAKNOMIK_EMAIL,
        "paknomik_footer_logo": paknomik_footer_logo(),
    }


def paknomik_footer_logo() -> dict | None:
    static_root = Path(getattr(settings, "STATIC_ROOT", "") or "")
    for candidate in (
        Path(settings.BASE_DIR) / "static" / PAKNOMIK_LOGO_REL,
        static_root / PAKNOMIK_LOGO_REL,
    ):
        fitted = fitted_image_from_path(candidate, 76, 20)
        if fitted:
            return fitted
    return None


def render_proposal_blocks_to_html(
    blocks,
    pill_left_uri: str = "",
    pill_right_uri: str = "",
) -> str:
    if not isinstance(blocks, list):
        return ""
    chunks = []
    for block in blocks:
        if not isinstance(block, dict) or not block.get("enabled", True):
            continue
        title = html.escape((block.get("title") or "").strip())
        btype = block.get("type")
        section = ['<div class="pdf-section">']
        if title:
            section.append(paknomik_pill_html(title, pill_left_uri, pill_right_uri))
        section.append('<div class="section-body">')
        if btype == "header":
            content = html.escape((block.get("content") or "").strip())
            if content:
                section.append(f'<p class="block-text">{content.replace(chr(10), "<br/>")}</p>')
        elif btype == "product":
            for item in block.get("items") or []:
                if not isinstance(item, dict):
                    continue
                label = html.escape((item.get("label") or "").strip())
                value = html.escape((item.get("value") or "").strip())
                if label or value:
                    if label:
                        section.append(
                            f'<p class="field-line">'
                            f'<span class="field-label">{label} :</span> {value}'
                            f"</p>"
                        )
                    else:
                        section.append(f'<p class="field-line">{value}</p>')
        elif btype == "process":
            section.append('<ol class="block-list">')
            for step in block.get("steps") or []:
                step_text = html.escape((step or "").strip())
                if step_text:
                    section.append(f"<li>{step_text}</li>")
            section.append("</ol>")
        elif btype == "faq":
            for item in block.get("faqItems") or []:
                if not isinstance(item, dict):
                    continue
                question = html.escape((item.get("question") or "").strip())
                answer = html.escape((item.get("answer") or "").strip())
                if question and answer:
                    section.append(
                        f'<p class="faq-q">{question}</p>'
                        f'<p class="faq-a">{answer.replace(chr(10), "<br/>")}</p>'
                    )
        elif btype in ("pricing", "terms"):
            content = html.escape((block.get("content") or "").strip())
            if content:
                section.append(f'<p class="block-text">{content.replace(chr(10), "<br/>")}</p>')
        section.append("</div></div>")
        chunks.append("".join(section))
    if chunks:
        return "".join(chunks)
    fallback = html.escape(render_proposal_blocks_to_text(blocks))
    return f'<p class="block-text">{fallback.replace(chr(10), "<br/>")}</p>' if fallback else ""


def build_proposal_pdf_context(
    *,
    supplier,
    document_title: str,
    subtitle: str,
    blocks,
    message: str,
    proposed_price,
    lead_time_days,
    currency: str = "DZD",
    client_name: str = "",
) -> dict:
    brand = _normalize_brand_color(getattr(supplier, "brand_color", None))
    rects = paknomik_rect_assets(brand)
    body_html = render_proposal_blocks_to_html(
        blocks,
        pill_left_uri=rects["pill_left_uri"],
        pill_right_uri=rects["pill_right_uri"],
    )
    if not body_html.strip() and (message or "").strip():
        body_html = (
            '<div class="pdf-section">'
            f'{paknomik_pill_html("Détails", rects["pill_left_uri"], rects["pill_right_uri"])}'
            '<div class="section-body">'
            f'<p class="block-text">{html.escape(message.strip()).replace(chr(10), "<br/>")}</p>'
            "</div></div>"
        )
    price_line = ""
    if proposed_price is not None:
        price_line = f"{proposed_price} {currency or 'DZD'}"
    lead_line = ""
    if lead_time_days:
        lead_line = f"{lead_time_days} jours"
    client = (client_name or "").strip()
    if not client and subtitle:
        client = subtitle.strip()
    return {
        "document_title": document_title,
        "subtitle": subtitle,
        "hero_title": HERO_TITLE,
        "hero_html": paknomik_hero_html(
            HERO_TITLE, rects["hero_left_uri"], rects["hero_right_uri"]
        ),
        "client_name": client,
        "brand_color": brand,
        "body_text_color": BODY_TEXT,
        "page_bg": PAGE_BG,
        "body_html": mark_last_list_items(body_html),
        "price_line": price_line,
        "lead_line": lead_line,
        "supplier_logo": fitted_image_from_field(getattr(supplier, "logo", None), 150, 90),
        "supplier_name": (getattr(supplier, "company_name", None) or "").strip(),
        **paknomik_page_chrome_context(),
        "pill_html_conditions": paknomik_pill_html(
            "Conditions commerciales",
            rects["pill_left_uri"],
            rects["pill_right_uri"],
        ),
        **rects,
    }


def render_proposal_pdf_html(**context) -> str:
    return render_to_string("user/pdf/proposal_document.html", context)


def html_to_pdf_bytes(html_content: str) -> bytes:
    from xhtml2pdf import pisa

    result = BytesIO()
    pdf = pisa.CreatePDF(html_content, dest=result, encoding="utf-8")
    if pdf.err:
        raise RuntimeError("PDF generation failed")
    return result.getvalue()


class PDFRenderer:
    """Lets DRF accept Accept: application/pdf instead of returning 406."""

    media_type = "application/pdf"
    format = "pdf"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, (bytes, bytearray, memoryview)):
            return bytes(data)
        return data
