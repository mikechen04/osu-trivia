from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.db import models

from . import clock, scoring
from .models import AcceptedAnswer, Question
from .tiers import tier_info


class AcceptedAnswerInline(admin.TabularInline):
    model = AcceptedAnswer
    extra = 3
    fields = ("text", "aliases", "seed_tier", "live_plays", "live_tier")
    readonly_fields = ("live_plays", "live_tier")
    formfield_overrides = {models.TextField: {"widget": forms.Textarea(attrs={"rows": 2, "cols": 28})}}

    @admin.display(description="Times given")
    def live_plays(self, obj):
        if not obj.pk:
            return "–"
        counts, _ = scoring.answer_counts(obj.question_id)
        return counts.get(obj.pk, 0)

    @admin.display(description="Current tier")
    def live_tier(self, obj):
        if not obj.pk:
            return "–"
        counts, total = scoring.answer_counts(obj.question_id)
        tier = tier_info(scoring.effective_tier(obj.seed_tier, counts.get(obj.pk, 0), total))
        return f"{tier.emoji} {tier.level} · {tier.name} ({tier.points})"


class ScheduleFilter(admin.SimpleListFilter):
    title = "schedule"
    parameter_name = "when"

    def lookups(self, request, model_admin):
        return [("today", "Today"), ("upcoming", "Upcoming"), ("past", "Past"), ("draft", "Unscheduled drafts")]

    def queryset(self, request, queryset):
        today = clock.today()
        return {
            "today": lambda: queryset.filter(date=today),
            "upcoming": lambda: queryset.filter(date__gt=today),
            "past": lambda: queryset.filter(date__lt=today),
            "draft": lambda: queryset.filter(date__isnull=True),
        }.get(self.value(), lambda: queryset)()


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("prompt", "date", "position", "answer_count", "valid_plays")
    list_filter = (ScheduleFilter,)
    search_fields = ("prompt",)
    ordering = ("-date", "position")
    inlines = [AcceptedAnswerInline]
    actions = ["schedule_next_open_slots"]

    @admin.display(description="Accepted answers")
    def answer_count(self, obj):
        return obj.answers.count()

    @admin.display(description="Valid plays")
    def valid_plays(self, obj):
        return scoring.answer_counts(obj.pk)[1]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        question = form.instance
        dupes = question.duplicate_keys()
        if dupes:
            self.message_user(
                request,
                "These spellings appear under more than one answer, so matching is ambiguous: " + ", ".join(dupes),
                messages.WARNING,
            )
        if question.date and not question.answers.exists():
            self.message_user(request, "This question is scheduled but has no accepted answers yet.", messages.WARNING)

    @admin.action(description="Schedule selected drafts into the next open slots")
    def schedule_next_open_slots(self, request, queryset):
        n = settings.QUESTIONS_PER_DAY
        drafts = list(queryset.filter(date__isnull=True).order_by("id"))
        day = clock.today() + timedelta(days=1)
        placed = 0
        for question in drafts:
            if not question.answers.exists():
                continue
            while True:
                used = set(Question.objects.filter(date=day).values_list("position", flat=True))
                free = next((p for p in range(1, n + 1) if p not in used), None)
                if free:
                    break
                day += timedelta(days=1)
            question.date, question.position = day, free
            question.save(update_fields=["date", "position"])
            placed += 1
        skipped = len(drafts) - placed
        msg = f"Scheduled {placed} question(s)."
        if skipped:
            msg += f" Skipped {skipped} with no accepted answers."
        self.message_user(request, msg, messages.SUCCESS if placed else messages.WARNING)
