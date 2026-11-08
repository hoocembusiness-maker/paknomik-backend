from smtplib import SMTPException
from unittest.mock import patch

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from user.models import User
from user.password_reset import encode_uid, make_reset_token

REQUEST_URL = reverse("api_password_reset")
VALIDATE_URL = reverse("api_password_reset_validate")
CONFIRM_URL = reverse("api_password_reset_confirm")
GENERIC = (
    "Si un compte correspond à cette adresse e-mail, vous recevrez un lien "
    "de réinitialisation dans quelques instants."
)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PASSWORD_RESET_TIMEOUT=1800,
    PASSWORD_RESET_EMAIL_COOLDOWN=120,
    FRONTEND_URL="https://paknomik.com",
)
class PasswordResetApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.password = "OldPassphrase123"
        self.user = User.objects.create_user(
            username="reset.user",
            email="reset.user@example.com",
            password=self.password,
        )

    def _request(self, email):
        return self.client.post(REQUEST_URL, {"email": email}, format="json")

    def _token_pair(self, user=None):
        target = user or self.user
        return encode_uid(target), make_reset_token(target)

    def test_registered_email_sends_reset_mail(self):
        response = self._request(self.user.email)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["message"], GENERIC)
        self.assertNotIn("reset.user@example.com", str(response.data))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, "Réinitialisation de votre mot de passe Paknomik")
        self.assertEqual(message.to, [self.user.email])
        uid, _ = self._token_pair()
        self.assertIn(f"https://paknomik.com/reset-password/{uid}/", message.body)
        self.assertTrue(message.alternatives)

    def test_unknown_email_returns_same_success_without_mail(self):
        response = self._request("nobody@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["message"], GENERIC)
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_email_is_rejected(self):
        response = self._request("not-an-email")
        self.assertEqual(response.status_code, 400)
        self.assertIn("e-mail", response.data["error"])
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_user_is_treated_as_unknown(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self._request(self.user.email)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_repeated_request_does_not_send_a_second_email(self):
        self.assertEqual(self._request(self.user.email).status_code, 200)
        self.assertEqual(self._request(self.user.email).status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    def test_valid_token(self):
        uid, token = self._token_pair()
        response = self.client.post(
            VALIDATE_URL, {"uid": uid, "token": token}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["valid"])
        self.assertNotIn("email", response.data)

    def test_invalid_token(self):
        uid, _ = self._token_pair()
        response = self.client.post(
            VALIDATE_URL, {"uid": uid, "token": "not-a-real-token"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["valid"])

    def test_expired_token(self):
        uid, token = self._token_pair()
        real_seconds = default_token_generator._num_seconds
        with patch.object(
            default_token_generator,
            "_num_seconds",
            side_effect=lambda dt: real_seconds(dt) + 10_000,
        ):
            response = self.client.post(
                VALIDATE_URL, {"uid": uid, "token": token}, format="json"
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["valid"])

    def test_password_mismatch(self):
        uid, token = self._token_pair()
        response = self.client.post(
            CONFIRM_URL,
            {
                "uid": uid,
                "token": token,
                "new_password": "NewPassphrase123",
                "confirm_password": "OtherPassphrase123",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_weak_password(self):
        uid, token = self._token_pair()
        response = self.client.post(
            CONFIRM_URL,
            {
                "uid": uid,
                "token": token,
                "new_password": "123",
                "confirm_password": "123",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_successful_password_change_invalidates_token(self):
        uid, token = self._token_pair()
        new_password = "NewPassphrase123"
        response = self.client.post(
            CONFIRM_URL,
            {
                "uid": uid,
                "token": token,
                "new_password": new_password,
                "confirm_password": new_password,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))
        self.assertFalse(self.user.check_password(self.password))
        self.assertFalse(default_token_generator.check_token(self.user, token))

        reused = self.client.post(
            CONFIRM_URL,
            {
                "uid": uid,
                "token": token,
                "new_password": "AnotherPassphrase123",
                "confirm_password": "AnotherPassphrase123",
            },
            format="json",
        )
        self.assertEqual(reused.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))

        login = self.client.post(
            reverse("api_login"),
            {"login": self.user.email, "password": new_password},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        self.assertIn("token", login.data)

    def test_smtp_failure_does_not_claim_success(self):
        with patch(
            "user.password_reset.EmailMultiAlternatives.send",
            side_effect=SMTPException("smtp down"),
        ):
            response = self._request(self.user.email)
        self.assertEqual(response.status_code, 503)
        self.assertIn("temporairement", response.data["error"])
        self.assertEqual(len(mail.outbox), 0)

    def test_request_rate_limiting(self):
        cache.clear()
        statuses = [
            self._request(f"limit{i}@example.com").status_code for i in range(6)
        ]
        self.assertEqual(statuses[:5], [200, 200, 200, 200, 200])
        self.assertEqual(statuses[5], 429)
