"""
Tells Django that the `user` app exists and loads signal handlers
when the server starts (see signals.py).
"""

from django.apps import AppConfig


class UserConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'user'

    def ready(self):
        import user.signals  # noqa: F401
