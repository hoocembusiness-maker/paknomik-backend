from django.conf import settings
from django.db import models


class ContactMessage(models.Model):
    """Message sent from the public "Contactez-nous" form (visitors may be anonymous)."""

    class ContactType(models.TextChoices):
        EMAIL = "email", "Email"
        PHONE = "phone", "Téléphone"

    class Status(models.TextChoices):
        NEW = "new", "Nouveau"
        HANDLED = "handled", "Traité"

    contact = models.CharField(max_length=254)
    contact_type = models.CharField(max_length=10, choices=ContactType.choices)
    subject = models.CharField(max_length=150)
    message = models.TextField(max_length=3000)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_messages",
    )
    source = models.CharField(max_length=40, default="home")
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.NEW, db_index=True
    )
    client_hash = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Contact message"
        verbose_name_plural = "Contact messages"

    def __str__(self):
        return f"Contact #{self.pk} — {self.subject}"
