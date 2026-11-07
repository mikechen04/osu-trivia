from django.conf import settings
from django.db import models


class Suggestion(models.Model):
    """A player's question idea. Always lands in the moderation queue; nothing is auto-published."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="suggestions")
    text = models.CharField(max_length=300, verbose_name="question")
    examples = models.TextField(blank=True, help_text="Example answers from the player, one per line (optional).")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    admin_note = models.CharField(max_length=300, blank=True, help_text="Internal. Never shown to the player.")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_question = models.ForeignKey("game.Question", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.text[:80]

    def example_list(self) -> list[str]:
        return [line.strip() for line in self.examples.splitlines() if line.strip()]
