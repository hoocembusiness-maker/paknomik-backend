"""
Google OAuth redirect handlers for the API login flow.
"""

from django.views import View

from allauth.socialaccount.providers.google.views import oauth2_login


class GoogleOAuthLoginView(View):
    """Wrap allauth Google login so OAuth state is always stored in the session."""

    def get(self, request):
        if not request.session.session_key:
            request.session.create()
        request.session.modified = True
        return oauth2_login(request)
