from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone
from django.utils.html import format_html

from game.models import AcceptedAnswer, Question

from .models import Suggestion


@admin.register(Suggestion)
class SuggestionAdmin(admin.ModelAdmin):
    """Approve → creates an unscheduled draft. Then add rarities/aliases and schedule it."""

    list_display = ("short_text", "from_who", "status_label", "created_at")
    list_filter = ("status",)
    search_fields = ("text", "user__osu_username")
    ordering = ("-created_at",)
    list_per_page = 30
    actions = ["approve", "reject"]
    readonly_fields = ("user", "created_at", "reviewed_at", "created_question")

    fieldsets = (
        (
            "Suggestion",
            {
                "fields": ("status", "text", "examples"),
                "description": "Approve creates a draft question. Reject closes it. Nothing goes live automatically.",
            },
        ),
        (
            "Staff note",
            {"fields": ("admin_note",), "classes": ("collapse",)},
        ),
        (
            "History",
            {
                "fields": ("user", "created_at", "reviewed_at", "created_question"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Suggestion", ordering="text")
    def short_text(self, obj):
        text = obj.text
        return text if len(text) <= 80 else text[:77] + "…"

    @admin.display(description="From", ordering="user__osu_username")
    def from_who(self, obj):
        return obj.user.display_name if obj.user_id else "—"

    @admin.display(description="Status", ordering="status")
    def status_label(self, obj):
        return format_html(
            '<span class="status status--{}">{}</span>',
            obj.status,
            obj.get_status_display().lower(),
        )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user", "created_question")

    @admin.action(description="Approve → make draft question")
    def approve(self, request, queryset):
        made = 0
        for suggestion in queryset.filter(status=Suggestion.Status.PENDING):
            with transaction.atomic():
                question = Question.objects.create(prompt=suggestion.text, notes=f"From suggestion #{suggestion.pk}")
                for line in suggestion.example_list():
                    AcceptedAnswer.objects.create(question=question, text=line, seed_tier=3)
                suggestion.status = Suggestion.Status.APPROVED
                suggestion.reviewed_at = timezone.now()
                suggestion.created_question = question
                suggestion.save()
            made += 1
        self.message_user(
            request,
            f"Approved {made}. Drafts created — add rarities if needed, then schedule under Questions or Staff.",
            messages.SUCCESS,
        )

    @admin.action(description="Reject")
    def reject(self, request, queryset):
        n = queryset.filter(status=Suggestion.Status.PENDING).update(
            status=Suggestion.Status.REJECTED, reviewed_at=timezone.now()
        )
        self.message_user(request, f"Rejected {n}.", messages.SUCCESS)

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data:
            obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)


Suggestion._meta.verbose_name_plural = "Suggestions"
