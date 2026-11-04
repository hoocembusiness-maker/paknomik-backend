from django.apps import AppConfig


class BuyerSpaceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "buyer_space"
    verbose_name = "Espace acheteur"

    def ready(self):
        import buyer_space.signals  # noqa: F401
