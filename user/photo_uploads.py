"""Buyer photo uploads: server-side validation and metadata stripping.

Shared by public quote demands, boutique quote requests and sample requests.
Photos are re-encoded with Pillow so EXIF (GPS position, device, date) is never stored.
"""

from __future__ import annotations

import os
from io import BytesIO

from django.core.files.base import ContentFile

MAX_PHOTOS = 5
MAX_PHOTO_BYTES = 5 * 1024 * 1024
ALLOWED_PHOTO_FORMATS = {"JPEG", "PNG", "WEBP"}


def clean_photos(files) -> str:
    """Error message for invalid uploads, "" when all are real JPEG/PNG/WebP images."""
    from PIL import Image, UnidentifiedImageError

    if len(files) > MAX_PHOTOS:
        return f"{MAX_PHOTOS} photos maximum par demande."
    for upload in files:
        if upload.size > MAX_PHOTO_BYTES:
            return "Une photo dépasse 5 Mo."
        try:
            with Image.open(upload) as image:
                image_format = image.format
                image.verify()
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
            return "Une photo n'est pas une image valide."
        finally:
            upload.seek(0)
        if image_format not in ALLOWED_PHOTO_FORMATS:
            return "Format de photo non pris en charge (JPEG, PNG ou WebP)."
    return ""


def strip_metadata(file_obj, name: str | None = None) -> ContentFile | None:
    """Re-encoded copy of an image without EXIF, or None when it is not a supported image.

    The EXIF orientation is applied to the pixels first so the photo keeps its rotation.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        file_obj.seek(0)
        with Image.open(file_obj) as source:
            image_format = source.format
            if image_format not in ALLOWED_PHOTO_FORMATS:
                return None
            icc_profile = source.info.get("icc_profile")
            image = ImageOps.exif_transpose(source)
            params = {}
            if icc_profile:
                params["icc_profile"] = icc_profile
            if image_format == "JPEG":
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                params.update(quality=90, optimize=True)
            elif image_format == "WEBP":
                params.update(quality=90)
            buffer = BytesIO()
            image.save(buffer, format=image_format, **params)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        return None
    finally:
        file_obj.seek(0)
    filename = os.path.basename(name or getattr(file_obj, "name", "") or "photo")
    return ContentFile(buffer.getvalue(), name=filename)


def has_metadata(file_obj) -> bool:
    """True when the image still carries EXIF data."""
    from PIL import Image, UnidentifiedImageError

    try:
        file_obj.seek(0)
        with Image.open(file_obj) as image:
            return bool(image.getexif()) or "exif" in image.info
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        return False
    finally:
        file_obj.seek(0)


def sanitized_photos(files) -> list[ContentFile]:
    """Validated uploads re-encoded without metadata (call ``clean_photos`` first)."""
    cleaned = []
    for upload in files:
        content = strip_metadata(upload)
        if content is not None:
            cleaned.append(content)
    return cleaned


def save_photos(model, parent_field: str, parent, photos: list[ContentFile]) -> None:
    """Create one ``model`` row per photo, in order, linked to ``parent``."""
    for position, content in enumerate(photos):
        content.seek(0)
        model.objects.create(
            **{parent_field: parent},
            image=ContentFile(content.read(), name=content.name),
            position=position,
        )
