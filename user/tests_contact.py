from django.test import TestCase
from rest_framework.test import APIClient

from user.contact_models import ContactMessage

URL = "/api/contact/"
VALID = {"contact": "client@example.com", "subject": "Question", "message": "Bonjour, j'ai une question."}


class PublicContactTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_email_message_is_stored(self):
        response = self.client.post(URL, VALID, format="json")
        self.assertEqual(response.status_code, 201)
        msg = ContactMessage.objects.get()
        self.assertEqual(msg.contact_type, ContactMessage.ContactType.EMAIL)
        self.assertIsNone(msg.user)
        self.assertEqual(len(msg.client_hash), 64)

    def test_phone_is_accepted(self):
        response = self.client.post(URL, {**VALID, "contact": "+213 555 12 34 56"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ContactMessage.objects.get().contact_type, ContactMessage.ContactType.PHONE)

    def test_invalid_fields_are_reported(self):
        response = self.client.post(URL, {"contact": "abc", "subject": "x", "message": "court"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.data["fields"]), {"contact", "subject", "message"})
        self.assertFalse(ContactMessage.objects.exists())

    def test_honeypot_silently_drops(self):
        response = self.client.post(URL, {**VALID, "website": "http://spam"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertFalse(ContactMessage.objects.exists())

    def test_rate_limited_after_five_messages(self):
        for _ in range(5):
            self.assertEqual(self.client.post(URL, VALID, format="json").status_code, 201)
        response = self.client.post(URL, VALID, format="json")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(ContactMessage.objects.count(), 5)

    def test_invalid_token_does_not_block_submission(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")
        response = self.client.post(URL, VALID, format="json")
        self.assertEqual(response.status_code, 201)


class SocialLinksTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.admin = User.objects.create_superuser("root", "root@example.com", "pw-123456")
        self.staff = User.objects.create_user("staff", "staff@example.com", "pw-123456", is_staff=True)
        self.client = APIClient()

    def test_normalization_and_rejections(self):
        from user.social_links import SocialLinkError, normalize_social_url

        self.assertEqual(normalize_social_url("facebook", "facebook.com/paknomik"), "https://facebook.com/paknomik")
        self.assertEqual(normalize_social_url("whatsapp", "+213 555 12 34 56"), "https://wa.me/213555123456")
        self.assertEqual(normalize_social_url("instagram", ""), "")
        for platform, raw in [
            ("facebook", "javascript:alert(1)"),
            ("facebook", "http://facebook.com/paknomik"),
            ("instagram", "https://evil.com/instagram.com"),
            ("whatsapp", "https://wa.me.evil.com/1"),
        ]:
            with self.assertRaises(SocialLinkError):
                normalize_social_url(platform, raw)

    def test_admin_requires_settings_permission(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get("/api/admin/social-links/").status_code, 403)
        response = self.client.put("/api/admin/social-links/", {"facebook": "https://facebook.com/x"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_admin_saves_and_public_reads(self):
        from user.models import AuditLog

        self.client.force_authenticate(self.admin)
        response = self.client.put(
            "/api/admin/social-links/",
            {"facebook": "facebook.com/paknomik", "whatsapp": "+213555123456", "instagram": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="settings.social_links.update").exists())

        public = APIClient().get("/api/social-links/")
        self.assertEqual(public.status_code, 200)
        self.assertEqual(
            public.data["links"],
            [
                {"platform": "facebook", "url": "https://facebook.com/paknomik"},
                {"platform": "whatsapp", "url": "https://wa.me/213555123456"},
            ],
        )

    def test_invalid_link_is_rejected_and_nothing_saved(self):
        self.client.force_authenticate(self.admin)
        response = self.client.put(
            "/api/admin/social-links/",
            {"facebook": "https://facebook.com/ok", "instagram": "https://evil.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("instagram", response.data["fields"])
        self.assertEqual(APIClient().get("/api/social-links/").data["links"], [])

    def test_unsafe_value_saved_elsewhere_is_not_served(self):
        from user.platform_config import set_setting

        set_setting("social_facebook_url", "javascript:alert(1)")
        self.assertEqual(APIClient().get("/api/social-links/").data["links"], [])


class AdminContactMessagesTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.admin = User.objects.create_superuser("root2", "root2@example.com", "pw-123456")
        self.staff = User.objects.create_user("staff2", "staff2@example.com", "pw-123456", is_staff=True)
        self.client = APIClient()
        self.msg = ContactMessage.objects.create(
            contact="a@example.com", contact_type="email", subject="Devis carton", message="Bonjour, besoin de devis."
        )
        ContactMessage.objects.create(
            contact="+213555000000", contact_type="phone", subject="Autre", message="Rappel svp merci", status="handled"
        )

    def test_requires_support_permission(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get("/api/admin/contact-messages/").status_code, 403)
        response = self.client.patch(f"/api/admin/contact-messages/{self.msg.pk}/", {"status": "handled"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_list_filters_and_counts(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/admin/contact-messages/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m["id"] for m in response.data["messages"]], [self.msg.pk])
        self.assertEqual(response.data["counts"], {"new": 1, "handled": 1})
        self.assertNotIn("client_hash", response.data["messages"][0])
        self.assertEqual(len(self.client.get("/api/admin/contact-messages/?status=all").data["messages"]), 2)
        self.assertEqual(len(self.client.get("/api/admin/contact-messages/?status=all&q=carton").data["messages"]), 1)

    def test_mark_handled_is_audited(self):
        from user.models import AuditLog

        self.client.force_authenticate(self.admin)
        response = self.client.patch(f"/api/admin/contact-messages/{self.msg.pk}/", {"status": "handled"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.status, "handled")
        self.assertTrue(AuditLog.objects.filter(action="contact_message.status", entity_id=self.msg.pk).exists())
        bad = self.client.patch(f"/api/admin/contact-messages/{self.msg.pk}/", {"status": "nope"}, format="json")
        self.assertEqual(bad.status_code, 400)
