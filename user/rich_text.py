"""Allow-list sanitizer for the WordPad-like rich text (product description,
supplier presentation).

Only basic formatting survives: paragraphs, headings, bold/italic/underline/
strike, colour, highlight, font size, alignment, lists and indentation. Links,
images, scripts, event handlers and any CSS outside the allow-list are dropped,
so the stored HTML is safe to render as-is.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

RICH_HTML_MAX_LENGTH = 30000

ALLOWED_TAGS = {
    "p", "div", "br", "span", "strong", "em", "u", "s", "sub", "sup",
    "h3", "h4", "ul", "ol", "li", "blockquote",
}
TAG_ALIASES = {
    "b": "strong",
    "i": "em",
    "strike": "s",
    "del": "s",
    "ins": "u",
    "h1": "h3",
    "h2": "h3",
    "h5": "h4",
    "h6": "h4",
    "font": "span",
}
# Content of these elements is discarded entirely, not just the tag.
DROP_WITH_CONTENT = {
    "script", "style", "iframe", "object", "embed", "template", "noscript",
    "svg", "math", "head", "title", "textarea", "select", "button", "form",
    "frameset", "frame", "canvas", "audio", "video",
}
BLOCK_TAGS = {"p", "div", "h3", "h4", "ul", "ol", "li", "blockquote"}
ALIGNABLE_TAGS = {"p", "div", "h3", "h4", "li", "blockquote"}

_COLOR_RE = re.compile(
    r"^(#[0-9a-f]{3,8}"
    r"|rgba?\(\s*\d{1,3}%?\s*,\s*\d{1,3}%?\s*,\s*\d{1,3}%?\s*(,\s*(0|1|0?\.\d+)\s*)?\)"
    r"|[a-z]{3,20})$"
)
_FONT_SIZE_KEYWORDS = {
    "xx-small", "x-small", "small", "medium", "large", "x-large", "xx-large",
    "xxx-large", "smaller", "larger",
}
_FONT_SIZE_RE = re.compile(r"^(\d{1,3}(?:\.\d+)?)(px|pt|em|rem|%)$")
_FONT_SIZE_BOUNDS = {
    "px": (8, 48), "pt": (6, 36), "em": (0.5, 3), "rem": (0.5, 3), "%": (50, 300),
}
_LENGTH_RE = re.compile(r"^(0|\d{1,3}(?:\.\d+)?(px|em|rem))$")
FONT_FAMILIES = {"arial", "georgia", "verdana", "tahoma", "times new roman", "courier new", "inherit"}
_FONT_TAG_SIZES = {
    "1": "x-small", "2": "small", "3": "medium", "4": "large",
    "5": "x-large", "6": "xx-large", "7": "xxx-large",
}


def _valid_color(value: str) -> bool:
    return bool(_COLOR_RE.match(value)) and "expression" not in value


def _valid_font_size(value: str) -> bool:
    if value in _FONT_SIZE_KEYWORDS:
        return True
    match = _FONT_SIZE_RE.match(value)
    if not match:
        return False
    low, high = _FONT_SIZE_BOUNDS[match.group(2)]
    return low <= float(match.group(1)) <= high


def _valid_indent(value: str) -> bool:
    if not _LENGTH_RE.match(value):
        return False
    number = float(re.sub(r"[a-z%]+$", "", value) or 0)
    return number <= (200 if value.endswith("px") else 12)


def _valid_margin(value: str) -> bool:
    parts = value.split()
    return 1 <= len(parts) <= 4 and all(_valid_indent(part) for part in parts)


def _valid_decoration(value: str) -> bool:
    tokens = value.split()
    return bool(tokens) and all(t in {"underline", "line-through", "none"} for t in tokens)


_STYLE_RULES = {
    "color": _valid_color,
    "background-color": _valid_color,
    "font-size": _valid_font_size,
    "text-align": lambda v: v in {"left", "right", "center", "justify", "start", "end"},
    "font-weight": lambda v: v in {"bold", "bolder", "normal"} or bool(re.match(r"^[1-9]00$", v)),
    "font-style": lambda v: v in {"italic", "normal", "oblique"},
    "text-decoration": _valid_decoration,
    "text-decoration-line": _valid_decoration,
    "margin-left": _valid_indent,
    "padding-left": _valid_indent,
    "margin": _valid_margin,
}


def _font_family(value: str) -> str | None:
    first = value.split(",")[0].strip().strip("'\"").strip().lower()
    return first if first in FONT_FAMILIES else None


def _clean_style(raw: str, tag: str) -> list[tuple[str, str]]:
    kept: list[tuple[str, str]] = []
    for declaration in (raw or "").split(";"):
        if ":" not in declaration:
            continue
        prop, _, value = declaration.partition(":")
        prop = prop.strip().lower()
        value = value.strip().lower().replace("!important", "").strip()
        if not prop or not value or "\\" in value or "/*" in value or "url" in value:
            continue
        if prop == "text-align" and tag not in ALIGNABLE_TAGS:
            continue
        if prop == "font-family":
            family = _font_family(value)
            if family:
                kept.append((prop, family))
            continue
        validator = _STYLE_RULES.get(prop)
        if validator and validator(value):
            kept.append((prop, value))
    return kept


def _clean_attrs(original_tag: str, tag: str, attrs: list[tuple[str, str | None]]) -> str:
    styles: list[tuple[str, str]] = []
    for name, value in attrs:
        name = (name or "").lower()
        value = (value or "").strip()
        if name == "style":
            styles.extend(_clean_style(value, tag))
        elif name == "align" and tag in ALIGNABLE_TAGS:
            align = value.lower()
            if align in {"left", "right", "center", "justify"}:
                styles.append(("text-align", align))
        elif original_tag == "font" and name == "size" and value in _FONT_TAG_SIZES:
            styles.append(("font-size", _FONT_TAG_SIZES[value]))
        elif original_tag == "font" and name == "color" and _valid_color(value.lower()):
            styles.append(("color", value.lower()))
        elif original_tag == "font" and name == "face" and _font_family(value):
            styles.append(("font-family", _font_family(value)))
    if not styles:
        return ""
    seen: dict[str, str] = {}
    for prop, value in styles:
        seen[prop] = value
    css = "; ".join(f"{prop}: {value}" for prop, value in seen.items())
    return f' style="{escape(css, quote=True)}"'


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.drop_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self.drop_depth += 1
            return
        if self.drop_depth:
            return
        mapped = TAG_ALIASES.get(tag, tag)
        if mapped not in ALLOWED_TAGS:
            return
        if mapped == "br":
            self.out.append("<br>")
            return
        self.out.append(f"<{mapped}{_clean_attrs(tag, mapped, attrs)}>")
        self.stack.append(mapped)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT or self.drop_depth:
            return
        mapped = TAG_ALIASES.get(tag, tag)
        if mapped == "br":
            self.out.append("<br>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self.drop_depth = max(0, self.drop_depth - 1)
            return
        if self.drop_depth:
            return
        mapped = TAG_ALIASES.get(tag, tag)
        if mapped not in self.stack:
            return
        while self.stack:
            current = self.stack.pop()
            self.out.append(f"</{current}>")
            if current == mapped:
                break

    def handle_data(self, data):
        if not self.drop_depth:
            self.out.append(escape(data, quote=False))

    def result(self) -> str:
        self.close()
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_rich_html(value: str | None) -> str:
    """Return the allow-listed HTML, or "" when nothing readable is left."""
    raw = (value or "").strip()
    if not raw:
        return ""
    parser = _Sanitizer()
    parser.feed(raw)
    cleaned = parser.result().strip()
    return cleaned if rich_html_to_text(cleaned) else ""


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.current: list[str] = []
        self.lists: list[list] = []
        self.drop_depth = 0
        self.has_content = False

    def _break(self):
        line = re.sub(r"\s+", " ", "".join(self.current)).strip()
        if line:
            self.lines.append(line)
        self.current = []

    def handle_starttag(self, tag, attrs):
        tag = TAG_ALIASES.get(tag.lower(), tag.lower())
        if tag in DROP_WITH_CONTENT:
            self.drop_depth += 1
            return
        if tag == "br":
            self._break()
        elif tag in {"ul", "ol"}:
            self._break()
            self.lists.append([tag, 0])
        elif tag == "li":
            self._break()
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                self.current.append(f"{self.lists[-1][1]}. ")
            else:
                self.current.append("- ")
        elif tag in BLOCK_TAGS:
            self._break()

    def handle_startendtag(self, tag, attrs):
        if TAG_ALIASES.get(tag.lower(), tag.lower()) == "br":
            self._break()

    def handle_endtag(self, tag):
        tag = TAG_ALIASES.get(tag.lower(), tag.lower())
        if tag in DROP_WITH_CONTENT:
            self.drop_depth = max(0, self.drop_depth - 1)
            return
        if tag in {"ul", "ol"} and self.lists:
            self._break()
            self.lists.pop()
        elif tag in BLOCK_TAGS:
            self._break()

    def handle_data(self, data):
        if not self.drop_depth:
            self.current.append(data)
            if data.strip():
                self.has_content = True

    def text(self) -> str:
        self.close()
        self._break()
        return "\n".join(self.lines) if self.has_content else ""


def sync_rich_field(instance, html_attr: str, text_attr: str, update_fields=None) -> None:
    """Sanitize ``html_attr`` and derive ``text_attr`` from it before saving.

    The plain-text field stays the source for search, scoring and previews; it
    is only overwritten when the rich version holds readable text.
    """
    if update_fields is not None and html_attr not in update_fields:
        return
    cleaned = sanitize_rich_html(getattr(instance, html_attr, ""))
    setattr(instance, html_attr, cleaned)
    if cleaned:
        setattr(instance, text_attr, rich_html_to_text(cleaned))


def rich_html_to_text(value: str | None) -> str:
    """Plain-text version (one line per block, list markers kept) for search,
    completeness scoring and previews."""
    if not (value or "").strip():
        return ""
    extractor = _TextExtractor()
    extractor.feed(value)
    return extractor.text()
