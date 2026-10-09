"""
Automatic actions when something is saved or deleted
(for example updating a score when a product changes).
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from user.models import User
from user.scoring import handle_associated_user_added, ensure_supplier_score


@receiver(post_save, sender=User)
def user_supplier_linked(sender, instance: User, created: bool, **kwargs) -> None:
    if not instance.supplier_id:
        return
    ensure_supplier_score(instance.supplier_id)
    if created or kwargs.get("update_fields") is None or "supplier" in (
        kwargs.get("update_fields") or []
    ):
        handle_associated_user_added(instance.supplier_id, instance.id)
