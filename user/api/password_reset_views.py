"""API views for requesting, validating and confirming a password reset."""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from user.password_reset import (
    GENERIC_REQUEST_MESSAGE,
    INVALID_LINK_MESSAGE,
    SUCCESS_CONFIRM_MESSAGE,
    PasswordResetError,
    confirm_new_password,
    request_password_reset,
    resolve_reset_user,
)


class PasswordResetRequestThrottle(AnonRateThrottle):
    scope = "password_reset"


class PasswordResetValidateThrottle(AnonRateThrottle):
    scope = "password_reset_validate"


class PasswordResetConfirmThrottle(AnonRateThrottle):
    scope = "password_reset_confirm"


def _payload(request):
    if hasattr(request.data, "get"):
        return request.data
    return {}


class PasswordResetRequestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRequestThrottle]

    def post(self, request):
        email = _payload(request).get("email") or ""
        try:
            request_password_reset(email)
        except PasswordResetError as exc:
            return Response({"error": exc.message}, status=exc.status)
        return Response({"success": True, "message": GENERIC_REQUEST_MESSAGE})


class PasswordResetValidateView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetValidateThrottle]

    def post(self, request):
        data = _payload(request)
        uid = data.get("uid") or ""
        token = data.get("token") or ""
        user = resolve_reset_user(uid, token)
        if user is None:
            return Response({"valid": False, "error": INVALID_LINK_MESSAGE})
        return Response({"valid": True})


class PasswordResetConfirmView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetConfirmThrottle]

    def post(self, request):
        data = _payload(request)
        uid = data.get("uid") or ""
        token = data.get("token") or ""
        new_password = data.get("new_password") or ""
        confirm_password = data.get("confirm_password") or ""
        try:
            confirm_new_password(uid, token, new_password, confirm_password)
        except PasswordResetError as exc:
            return Response({"error": exc.message}, status=exc.status)
        return Response({"success": True, "message": SUCCESS_CONFIRM_MESSAGE})
