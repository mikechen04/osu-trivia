from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.db import models
from django.utils.html import format_html

from . import clock, scheduling, scoring
from .models import AcceptedAnswer, Question
from .tiers import tier_info

# Friendlier field labels in the admin forms
AcceptedAnswer._meta.get_field("text").verbose_name = "Answer"
AcceptedAnswer._meta.get_field("aliases").verbose_name = "Also accept (one per line)"
AcceptedAnswer._meta.get_field("seed_tier").verbose_name = "Starting rarity"


class AcceptedAnswerInline(admin.TabularInline):
    model = AcceptedAnswer
    extra = 2
    verbose_name = "accepted answer"
    verbose_name_plural = "Accepted answers — what players can type"
    fields = ("text", "aliases", "seed_tier", "live_plays", "live_tier")
    readonly_fields = ("live_plays", "live_tier")
    formfield_overrides = {models.TextField: {"widget": forms.Textarea(attrs={"rows": 2, "cols": 24})}}

    @admin.display(description="Plays")
    def live_plays(self, obj):
        if not obj.pk:
            return "–"
        counts, _ = scoring.answer_counts(obj.question_id)
        return counts.get(obj.pk, 0)

    @admin.display(description="Live rarity")
    def live_tier(self, obj):
        if not obj.pk:
            return "–"
        counts, total = scoring.answer_counts(obj.question_id)
        tier = tier_info(scoring.effective_tier(obj.seed_tier, counts.get(obj.pk, 0), total))
        return f"{tier.emoji} {tier.name} ({tier.points})"


class ScheduleFilter(admin.SimpleListFilter):
    title = "when"
    parameter_name = "when"

    def lookups(self, request, model_admin):
        return [
            ("today", "Today"),
            ("tomorrow", "Tomorrow"),
            ("upcoming", "Upcoming"),
            ("past", "Past"),
            ("draft", "Drafts (no date)"),
        ]

    def queryset(self, request, queryset):
        today = clock.today()
        return {
            "today": lambda: queryset.filter(date=today),
            "tomorrow": lambda: queryset.filter(date=today + timedelta(days=1)),
            "upcoming": lambda: queryset.filter(date__gt=today),
            "past": lambda: queryset.filter(date__lt=today),
            "draft": lambda: queryset.filter(date__isnull=True),
        }.get(self.value(), lambda: queryset)()


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("short_prompt", "schedule_label", "answer_count", "valid_plays")
    list_filter = (ScheduleFilter,)
    search_fields = ("prompt",)
    ordering = ("-date", "position", "-id")
    list_per_page = 25
    save_on_top = True
    inlines = [AcceptedAnswerInline]
    actions = ["schedule_next_open_slots"]

    fieldsets = (
        (
            "Question",
            {
                "fields": ("prompt", "hint"),
                "description": "Players see the prompt. Hint is optional small text under it.",
            },
        ),
        (
            "Schedule (UTC)",
            {
                "fields": ("date", "position"),
                "description": "Leave both empty to keep it as a draft. "
                "To go live, set the UTC date and a position 1–5. A day needs all 5 slots filled.",
            },
        ),
        (
            "Internal notes",
            {
                "fields": ("notes",),
                "classes": ("collapse",),
                "description": "Only staff see this. Never shown to players.",
            },
        ),
    )

    @admin.display(description="Question", ordering="prompt")
    def short_prompt(self, obj):
        text = obj.prompt
        return text if len(text) <= 72 else text[:69] + "…"

    @admin.display(description="Schedule", ordering="date")
    def schedule_label(self, obj):
        if not obj.date:
            return format_html('<span class="quiet">draft</span>')
        return f"{obj.date.isoformat()} · #{obj.position}"

    @admin.display(description="Answers")
    def answer_count(self, obj):
        return obj.answers.count()

    @admin.display(description="Plays")
    def valid_plays(self, obj):
        return scoring.answer_counts(obj.pk)[1]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["prompt"].label = "Prompt"
        form.base_fields["hint"].label = "Hint (optional)"
        form.base_fields["date"].label = "Date (UTC)"
        form.base_fields["position"].label = "Slot (1–5)"
        form.base_fields["notes"].label = "Notes"
        return form

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if formfield is None:
            return None
        if db_field.name == "prompt":
            formfield.widget.attrs["placeholder"] = "e.g. Name a mod you can turn on in osu!"
        if db_field.name == "hint":
            formfield.widget.attrs["placeholder"] = "Optional tip under the prompt"
        return formfield

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        question = form.instance
        dupes = question.duplicate_keys()
        if dupes:
            self.message_user(
                request,
                "These spellings appear under more than one answer (ambiguous): " + ", ".join(dupes),
                messages.WARNING,
            )
        if question.date and not question.answers.exists():
            self.message_user(request, "Scheduled, but it has no accepted answers yet.", messages.WARNING)
        elif question.date and question.position:
            n = Question.objects.filter(date=question.date).count()
            if n < settings.QUESTIONS_PER_DAY:
                self.message_user(
                    request,
                    f"Saved. {question.date} still needs {settings.QUESTIONS_PER_DAY - n} more question(s) to be playable.",
                    messages.INFO,
                )

    @admin.action(description="Schedule drafts into next open slots")
    def schedule_next_open_slots(self, request, queryset):
        drafts = list(queryset.filter(date__isnull=True).order_by("id"))
        placed, skipped = scheduling.schedule_drafts([q.pk for q in drafts])
        msg = f"Scheduled {placed} question(s)."
        if skipped:
            msg += f" Skipped {skipped} with no accepted answers."
        self.message_user(request, msg, messages.SUCCESS if placed else messages.WARNING)
