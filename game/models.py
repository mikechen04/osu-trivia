from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from . import clock
from .matching import compact
from .tiers import TIER_CHOICES, tier_info


class Question(models.Model):
    prompt = models.CharField(max_length=300)
    hint = models.CharField(max_length=200, blank=True, help_text="Optional small line under the prompt.")
    date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text="UTC day this question is played. Leave empty to keep it as an unscheduled draft.",
    )
    position = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1-5, order within the day.")
    notes = models.TextField(blank=True, help_text="Internal notes. Never shown to players.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["date", "position"],
                condition=Q(date__isnull=False),
                name="one_question_per_slot",
            ),
        ]

    def __str__(self) -> str:
        return self.prompt

    def clean(self):
        if self.date and not self.position:
            raise ValidationError({"position": "A scheduled question needs a position (1-5)."})
        if self.position and not (1 <= self.position <= settings.QUESTIONS_PER_DAY):
            raise ValidationError({"position": f"Position must be 1-{settings.QUESTIONS_PER_DAY}."})
        if self.position and not self.date:
            raise ValidationError({"date": "Set a date, or clear the position to keep this as a draft."})

    def duplicate_keys(self) -> list[str]:
        """Answer spellings that appear under more than one accepted answer (would be ambiguous)."""
        seen: dict[str, int] = {}
        dupes = set()
        for answer in self.answers.all():
            for key in answer.match_keys:
                if key in seen and seen[key] != answer.pk:
                    dupes.add(key)
                seen[key] = answer.pk
        return sorted(dupes)


class AcceptedAnswer(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    text = models.CharField(max_length=100, help_text="How the answer is shown to players, e.g. “Hidden”.")
    aliases = models.TextField(
        blank=True,
        help_text="Other accepted spellings, one per line, e.g. “HD”. Case, punctuation and spaces are ignored; "
        "small typos are forgiven automatically.",
    )
    seed_tier = models.PositiveSmallIntegerField(
        choices=TIER_CHOICES,
        default=3,
        help_text="Starting rarity. Replaced by real player data as answers come in.",
    )
    match_keys = models.JSONField(default=list, editable=False)

    class Meta:
        ordering = ["question_id", "text"]

    def __str__(self) -> str:
        return self.text

    def alias_list(self) -> list[str]:
        return [line.strip() for line in self.aliases.splitlines() if line.strip()]

    def save(self, *args, **kwargs):
        keys = {compact(t) for t in [self.text, *self.alias_list()]}
        self.match_keys = sorted(k for k in keys if k)
        super().save(*args, **kwargs)


class AttemptQuerySet(models.QuerySet):
    def complete(self):
        """Attempts that count for leaderboards/share pages: finished, or from a day that has ended."""
        return self.filter(Q(finished_at__isnull=False) | Q(date__lt=clock.today()))


class Attempt(models.Model):
    """One player's go at one day's set. Exactly one per player per day."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attempts")
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    score = models.PositiveSmallIntegerField(default=0)

    objects = AttemptQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "date"], name="one_attempt_per_user_per_day")]
        indexes = [models.Index(fields=["date", "-score"])]

    def __str__(self) -> str:
        return f"{self.user} {self.date} ({self.score})"


class Response(models.Model):
    """A player's answer to one question. Locked once `answered_at` is set."""

    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="responses")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="responses")
    position = models.PositiveSmallIntegerField()
    served_at = models.DateTimeField(help_text="When the server started this question's timer.")
    answered_at = models.DateTimeField(null=True, blank=True)
    raw_answer = models.CharField(max_length=100, blank=True)
    normalized = models.CharField(max_length=120, blank=True)
    matched = models.ForeignKey(AcceptedAnswer, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tier = models.PositiveSmallIntegerField(default=0)
    points = models.PositiveSmallIntegerField(default=0)
    timed_out = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["attempt", "question"], name="one_response_per_question"),
            models.UniqueConstraint(fields=["attempt", "position"], name="one_response_per_position"),
        ]
        indexes = [models.Index(fields=["question", "matched"])]

    @property
    def tier_info(self):
        return tier_info(self.tier)
