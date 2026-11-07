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

    def __str__(self) -> str:
        return self.display_name
