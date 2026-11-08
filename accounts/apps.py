from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _ensure_roles(sender, **kwargs):
    if sender.name != "accounts":
        return
    from .roles import ensure_moderator_group

    ensure_moderator_group()


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        post_migrate.connect(_ensure_roles, sender=self)
