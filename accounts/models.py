from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """A player. Identity is the osu! user id; Django's `username` is just a stable key
    (`osu<id>`) so osu! name changes can never collide with another row."""

    osu_id = models.BigIntegerField(unique=True, null=True, blank=True)
    osu_username = models.CharField(max_length=64, blank=True, db_index=True)
    avatar_url = models.URLField(max_length=300, blank=True)
    country_code = models.CharField(max_length=2, blank=True)
    # Players can opt out of leaderboards and public share pages.
    is_public = models.BooleanField(
        default=True,
        help_text="Show this player on leaderboards and on public result pages.",
    )
    tos_accepted_at = models.DateTimeField(null=True, blank=True)

    @property
    def display_name(self) -> str:
        return self.osu_username or self.username

    @property
    def role(self) -> str:
        from .roles import role_of

        return role_of(self)

    @property
    def is_site_admin(self) -> bool:
        from .roles import is_admin

        return is_admin(self)

    @property
    def is_moderator(self) -> bool:
        from .roles import is_moderator

        return is_moderator(self)

    def __str__(self) -> str:
        return self.display_name
