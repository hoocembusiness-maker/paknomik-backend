from django.db.utils import OperationalError, ProgrammingError
from django.db.models.signals import post_save
from django.dispatch import receiver

from buyer_space.wallet import ensure_buyer_account
from user.models import User


@receiver(post_save, sender=User)
def ensure_simple_user_dossier(sender, instance: User, raw=False, **kwargs):
    if raw or instance.supplier_id:
        return
    try:
        ensure_buyer_account(instance)
    except (OperationalError, ProgrammingError):
        return
