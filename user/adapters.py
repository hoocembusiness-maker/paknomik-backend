"""
Hooks into django-allauth for sign-up and social login (Google).
It tells Paknomik where to redirect users after login and how to
create or link accounts from Google.
"""

import logging
from urllib.parse import quote

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect

logger = logging.getLogger(__name__)


class PaknomikAccountAdapter(DefaultAccountAdapter):
    """Ensure SPA OAuth flows always return to the API callback."""

    def get_login_redirect_url(self, request):
        if request.session.get("oauth_frontend_redirect") is not None:
            return "/api/auth/google/callback/"
        if request.COOKIES.get("paknomik_oauth") == "1":
            return "/api/auth/google/callback/"
        return f"{settings.FRONTEND_URL.rstrip('/')}/"


class PaknomikSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Auto-link Google accounts to existing users and ensure usernames."""

    def on_authentication_error(
        self,
        request,
        provider,
        error=None,
        exception=None,
        extra_context=None,
    ):
        if provider.id == "google":
            frontend_redirect = request.session.get("oauth_frontend_redirect")
            if frontend_redirect is None:
                cookie_redirect = request.COOKIES.get("paknomik_oauth_redirect")
                if cookie_redirect and cookie_redirect.startswith("/"):
                    frontend_redirect = cookie_redirect
            redirect_to = frontend_redirect or "/"
            url = (
                f"{settings.FRONTEND_URL.rstrip('/')}/auth/callback"
                f"?error=google_auth_failed&redirect={quote(redirect_to, safe='')}"
            )
            raise ImmediateHttpResponse(HttpResponseRedirect(url))

        if exception is not None:
            logger.warning(
                "Google OAuth failed (%s): %s",
                error,
                exception,
                exc_info=exception,
            )

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        if not user.username:
            email = (data.get("email") or "").strip()
            if email:
                user.username = self._unique_username(email.split("@")[0])
        return user

    def is_auto_signup_allowed(self, request, sociallogin):
        return True

    def _unique_username(self, base: str) -> str:
        User = get_user_model()
        safe = "".join(ch for ch in base.lower() if ch.isalnum() or ch in "._-") or "user"
        candidate = safe[:40]
        suffix = 1
        while User.objects.filter(username=candidate).exists():
            candidate = f"{safe[:35]}{suffix}"
            suffix += 1
        return candidate
