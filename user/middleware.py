"""HTTP middleware for Paknomik."""

from __future__ import annotations

from django.conf import settings

from user.visitor_tracking import VISITOR_SESSION_COOKIE, _COOKIE_MAX_AGE, ensure_visitor_session_id


class VisitorSessionMiddleware:
    """Assign a first-party anonymous session id (cookie) for analytics."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ensure_visitor_session_id(request)
        response = self.get_response(request)
        new_sid = getattr(request, "_set_visitor_session_cookie", None)
        if new_sid and VISITOR_SESSION_COOKIE not in request.COOKIES:
            samesite = getattr(settings, "VISITOR_COOKIE_SAMESITE", "Lax")
            secure = getattr(settings, "SESSION_COOKIE_SECURE", False) or samesite == "None"
            response.set_cookie(
                VISITOR_SESSION_COOKIE,
                new_sid,
                max_age=_COOKIE_MAX_AGE,
                httponly=True,
                samesite=samesite,
                secure=secure,
            )
        return response
