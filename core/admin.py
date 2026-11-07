from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone

from game.models import AcceptedAnswer, Question

from .models import Suggestion


@admin.register(Suggestion)
class SuggestionAdmin(admin.ModelAdmin):
    """Moderation queue. Approving creates an *unscheduled draft* question; a human still
    has to add tiers/aliases and give it a date before anyone can play it."""

    list_display = ("text", "user", "status", "created_at", "draft")
    list_filter = ("status",)
    search_fields = ("text", "user__osu_username")
    readonly_fields = ("user", "created_at", "reviewed_at", "created_question")
    fields = ("status", "text", "examples", "admin_note", "user", "created_at", "reviewed_at", "created_question")
    actions = ["approve", "reject"]

    @admin.display(description="Draft question")
    def draft(self, obj):
        return "yes" if obj.created_question_id else ""

    @admin.action(description="Approve → create an unscheduled draft question")
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
            f"Approved {made}. Draft questions were created (not scheduled). Review them under Questions.",
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
