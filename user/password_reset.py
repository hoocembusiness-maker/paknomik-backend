"""Password-reset tokens, emails and eligibility — no account enumeration."""

from __future__ import annotations

import hashlib
import logging
from smtplib import SMTPException

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

logger = logging.getLogger(__name__)

User = get_user_model()

GENERIC_REQUEST_MESSAGE = (
    "Si un compte correspond à cette adresse e-mail, vous recevrez un lien "
    "de réinitialisation dans quelques instants."
)
INVALID_LINK_MESSAGE = (
    "Ce lien de réinitialisation est invalide ou a expiré."
)
SUCCESS_CONFIRM_MESSAGE = (
    "Votre mot de passe a été modifié avec succès. "
    "Vous pouvez maintenant vous connecter."
)
SMTP_UNAVAILABLE_MESSAGE = (
    "Le service d'e-mail est temporairement indisponible. Réessayez plus tard."
)


class PasswordResetError(Exception):
    """User-facing reset error that is safe to return in JSON."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def validate_email_format(value: str) -> str:
    email = normalize_email(value)
    if not email:
        raise PasswordResetError("Indiquez une adresse e-mail valide.")
    try:
        validate_email(email)
    except ValidationError as exc:
        raise PasswordResetError("Indiquez une adresse e-mail valide.") from exc
    return email


def eligible_user_for_email(email: str):
    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if user is None or not user.email:
        return None
    return user


def encode_uid(user) -> str:
    return urlsafe_base64_encode(force_bytes(user.pk))


def user_from_uid(uid: str):
    if not uid or not str(uid).strip():
        return None
    try:
        decoded = force_str(urlsafe_base64_decode(str(uid).strip()))
        user = User.objects.get(pk=decoded, is_active=True)
    except (ValueError, TypeError, OverflowError, User.DoesNotExist):
        return None
    return user


def make_reset_token(user) -> str:
    return default_token_generator.make_token(user)


def token_is_valid(user, token: str) -> bool:
    if user is None or not token:
        return False
    return bool(default_token_generator.check_token(user, token))


def resolve_reset_user(uid: str, token: str):
    user = user_from_uid(uid)
    if not token_is_valid(user, token):
        return None
    return user


def reset_link(uid: str, token: str) -> str:
    base = (getattr(settings, "FRONTEND_URL", "") or "https://paknomik.com").rstrip("/")
    if not getattr(settings, "DEBUG", False) and base.startswith("http://"):
        logger.warning("FRONTEND_URL is not HTTPS; password-reset links should use HTTPS.")
    return f"{base}/reset-password/{uid}/{token}"


def _email_cooldown_key(email: str) -> str:
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
    return f"pwdreset:cooldown:{digest}"


def _within_email_cooldown(email: str) -> bool:
    return bool(cache.get(_email_cooldown_key(email)))


def _mark_email_sent(email: str) -> None:
    ttl = int(getattr(settings, "PASSWORD_RESET_EMAIL_COOLDOWN", 120) or 120)
    cache.set(_email_cooldown_key(email), 1, timeout=max(ttl, 1))


def send_reset_email(user, uid: str, token: str) -> None:
    link = reset_link(uid, token)
    minutes = max(1, int(getattr(settings, "PASSWORD_RESET_TIMEOUT", 1800) or 1800) // 60)
    context = {
        "reset_link": link,
        "minutes": minutes,
        "frontend_url": (getattr(settings, "FRONTEND_URL", "") or "https://paknomik.com").rstrip("/"),
    }
    subject = render_to_string("user/email/password_reset_subject.txt", context).strip()
    text_body = render_to_string("user/email/password_reset.txt", context)
    html_body = render_to_string("user/email/password_reset.html", context)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "Paknomik <no_reply@paknomik.com>")
    message = EmailMultiAlternatives(subject, text_body, from_email, [user.email])
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)


def request_password_reset(email: str) -> None:
    """Send a reset mail when eligible. Raises PasswordResetError only for SMTP/format."""
    normalized = validate_email_format(email)
    user = eligible_user_for_email(normalized)
    if user is None:
        return
    if _within_email_cooldown(normalized):
        return
    uid = encode_uid(user)
    token = make_reset_token(user)
    try:
        send_reset_email(user, uid, token)
    except (SMTPException, OSError) as exc:
        logger.error(
            "password_reset_email_failed: %s %s",
            exc.__class__.__name__,
            str(exc)[:200],
        )
        raise PasswordResetError(SMTP_UNAVAILABLE_MESSAGE, status=503) from exc
    _mark_email_sent(normalized)
    logger.info("password_reset_email_sent")


def confirm_new_password(uid: str, token: str, new_password: str, confirm_password: str):
    if new_password != confirm_password:
        raise PasswordResetError("Les mots de passe ne correspondent pas.")
    if not new_password:
        raise PasswordResetError("Indiquez un nouveau mot de passe.")
    user = resolve_reset_user(uid, token)
    if user is None:
        raise PasswordResetError(INVALID_LINK_MESSAGE)
    try:
        validate_password(new_password, user)
    except ValidationError as exc:
        raise PasswordResetError(" ".join(exc.messages)) from exc
    user.set_password(new_password)
    user.save(update_fields=["password"])
    logger.info("password_reset_completed")
    return user
