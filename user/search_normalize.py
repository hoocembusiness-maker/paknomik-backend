"""
Cleans search text (accents, Arabic/French normalization).
"""

from __future__ import annotations

import re
import unicodedata

# Arabic diacritics (tashkeel) and Quranic annotation marks.
ARABIC_DIACRITICS = re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]')
TATWEEL = '\u0640'
ARABIC_LETTER = re.compile(r'[\u0600-\u06FF]')
HAS_ARABIC = re.compile(r'[\u0600-\u06FF]')

APOSTROPHE_CHARS = "'\u2019\u02BC\u0060\u00B4"
PUNCTUATION = re.compile(r'[^\w\s\u0600-\u06FF]', re.UNICODE)
TOKEN_SPLIT = re.compile(r'[\s,;|/\\.+_\-–—\'\u2019]+')

SEARCH_STOP_WORDS: frozenset[str] = frozenset({
    'a', 'an', 'and', 'de', 'des', 'du', 'en', 'et', 'for', 'in', 'la', 'le',
    'les', 'of', 'or', 'pour', 'the', 'to', 'un', 'une', 'with',
    'في', 'من', 'على', 'الى', 'إلى', 'عن', 'مع', 'هذا', 'هذه', 'التي', 'الذي',
})

ALEF_MAP = str.maketrans({
    '\u0623': '\u0627',  # أ → ا
    '\u0625': '\u0627',  # إ → ا
    '\u0622': '\u0627',  # آ → ا
    '\u0671': '\u0627',  # ٱ → ا
    '\u0649': '\u064A',  # ى → ي
    '\u0624': '\u0648',  # ؤ → و
    '\u0626': '\u064A',  # ئ → ي
})

# Ta marbuta: searchable as both ه and ة
TA_MARBUTA = '\u0629'
HA = '\u0647'


def _strip_combining(value: str) -> str:
    normalized = unicodedata.normalize('NFKD', value)
    return ''.join(char for char in normalized if not unicodedata.combining(char))


def normalize_arabic_letters(value: str) -> str:
    if not value:
        return ''
    text = ARABIC_DIACRITICS.sub('', value)
    text = text.replace(TATWEEL, '')
    text = text.translate(ALEF_MAP)
    return text


def normalize_arabic(value: str) -> str:
    """Normalize Arabic script for matching (not for display)."""
    if not value:
        return ''
    text = normalize_arabic_letters(value)
    text = text.replace(TA_MARBUTA, HA)
    return ' '.join(text.split())


def arabic_ta_marbuta_variant(value: str) -> str:
    """Alternate form keeping ta marbuta instead of ha."""
    if not value:
        return ''
    text = normalize_arabic_letters(value)
    text = text.replace(HA, TA_MARBUTA)
    return ' '.join(text.split())


def strip_arabic_definite_article(token: str) -> str:
    if token.startswith('\u0627\u0644') and len(token) > 3:
        return token[2:]
    return token


def normalize_french(value: str) -> str:
    """Lowercase, strip accents, normalize apostrophes and hyphens."""
    if not value:
        return ''
    text = value.strip().lower()
    for apostrophe in APOSTROPHE_CHARS:
        text = text.replace(apostrophe, ' ')
    text = text.replace('-', ' ')
    text = PUNCTUATION.sub(' ', text)
    text = _strip_combining(text)
    return ' '.join(text.split())


def french_icontains_variants(token: str) -> set[str]:
    """Accent variants for SQL icontains against raw French stored text."""
    if not token or HAS_ARABIC.search(token):
        return {token} if token else set()
    variants = {token}
    for src, dst in (
        ('e', 'é'),
        ('e', 'è'),
        ('e', 'ê'),
        ('a', 'à'),
        ('a', 'â'),
        ('u', 'û'),
        ('i', 'î'),
        ('c', 'ç'),
        ('o', 'ô'),
    ):
        if src in token:
            variants.add(token.replace(src, dst, 1))
    return variants


def arabic_plural_variants(token: str) -> set[str]:
    """Lightweight singular/plural variants for Arabic packaging terms."""
    if not token or not HAS_ARABIC.search(token):
        return set()
    variants = {token}
    if token.endswith('ات') and len(token) > 3:
        stem = token[:-2]
        variants.add(stem)
        variants.add(f'{stem}ة')
        variants.add(f'{stem}ه')
    if token.endswith('ة') or token.endswith('ه'):
        stem = token[:-1]
        variants.add(f'{stem}ات')
    return {item for item in variants if len(item) >= 2}


def french_plural_variants(token: str) -> set[str]:
    """Common French singular/plural variants for search expansion."""
    if not token or HAS_ARABIC.search(token):
        return set()
    variants = {token}
    if len(token) > 3 and token.endswith('s'):
        variants.add(token[:-1])
        if token.endswith('es') and len(token) > 4:
            variants.add(token[:-2])
        if token.endswith('aux') and len(token) > 4:
            variants.add(token[:-3] + 'al')
    else:
        variants.add(f'{token}s')
        if token.endswith('e'):
            variants.add(f'{token}s')
        elif token.endswith('al'):
            variants.add(token[:-2] + 'aux')
    return {item for item in variants if len(item) >= 2}


def arabic_icontains_variants(token: str) -> set[str]:
    """Spelling variants for SQL icontains against raw Arabic stored text."""
    if not token or not HAS_ARABIC.search(token):
        return {token} if token else set()

    variants: set[str] = {token}
    normalized = normalize_arabic(token)
    variants.add(normalized)
    variants.add(arabic_ta_marbuta_variant(normalized))

    alef_chars = ('\u0627', '\u0623', '\u0625', '\u0622')
    for alef in alef_chars:
        if normalized.startswith('\u0627'):
            variants.add(alef + normalized[1:])
        if token.startswith(alef):
            variants.add(token)
            variants.add('\u0627' + token[1:])

    stripped = strip_arabic_definite_article(normalized)
    if stripped != normalized:
        variants.add(stripped)
        variants.add(f'\u0627\u0644{stripped}')
    return {item for item in variants if item}


def arabic_article_variants(token: str) -> set[str]:
    if not token or not ARABIC_LETTER.search(token):
        return set()
    variants = {token}
    stripped = strip_arabic_definite_article(token)
    if stripped != token:
        variants.add(stripped)
    elif len(token) >= 2:
        variants.add(f'\u0627\u0644{token}')
    return variants


def fold_text(value: str) -> str:
    """Unified fold for French, English, and Arabic search matching."""
    if not value:
        return ''
    if HAS_ARABIC.search(value):
        return normalize_arabic(value)
    return normalize_french(value)


def normalize_phrase(value: str) -> str:
    """Normalize a full query phrase while preserving word order."""
    if not value:
        return ''
    parts = []
    for raw in TOKEN_SPLIT.split(value.strip()):
        if not raw:
            continue
        folded = fold_text(raw)
        if folded:
            parts.append(folded)
    return ' '.join(parts)


def tokenize_query(query: str, *, min_length: int = 2) -> list[str]:
    """Split a query into normalized unique tokens."""
    if not query:
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in TOKEN_SPLIT.split(query.strip()):
        token = fold_text(raw)
        if HAS_ARABIC.search(token):
            stripped = strip_arabic_definite_article(token)
            if len(stripped) >= min_length:
                token = stripped
        if len(token) < min_length or token in seen or token in SEARCH_STOP_WORDS:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    prev = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            current.append(
                min(
                    current[-1] + 1,
                    prev[j] + 1,
                    prev[j - 1] + cost,
                )
            )
        prev = current
    return prev[-1]


def corpus_words(text: str) -> list[str]:
    """Tokenize normalized free text for in-memory matching."""
    if not text:
        return []
    if HAS_ARABIC.search(text):
        normalized = normalize_phrase(text)
    else:
        normalized = normalize_french(text)
    return [word for word in normalized.split() if len(word) >= 2]
