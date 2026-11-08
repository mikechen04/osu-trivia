"""Staff dashboard that uses the main site chrome (not the Django admin skin)."""

from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts import roles
from core.models import Suggestion
from game.models import AcceptedAnswer, Question

from . import clock, scheduling

User = get_user_model()


def _admin_required(view):
    """Staff dashboard pages that only site admins may use."""

    @staff_member_required
    def wrapped(request, *args, **kwargs):
        if not roles.is_admin(request.user):
            messages.error(request, "Only admins can manage the team.")
            return redirect("staff_home")
        return view(request, *args, **kwargs)

    return wrapped


def _admin_change_url(question: Question) -> str:
    return reverse("admin:game_question_change", args=[question.pk])


def _enrich_slots(slots):
    out = []
    for slot in slots:
        q = slot["question"]
        if q is None:
            out.append({**slot, "rows": [], "plays": 0, "edit_url": None})
            continue
        rows, total = scheduling.answer_rarity_rows(q)
        out.append(
            {
                **slot,
                "rows": rows,
                "plays": total,
                "edit_url": _admin_change_url(q),
                "answer_count": q.answers.count(),
            }
        )
    return out


def _staff_context(**extra):
    return {
        "admin_root": "/" + settings.ADMIN_URL,
        "n": settings.QUESTIONS_PER_DAY,
        **extra,
    }


@staff_member_required
def staff_home(request):
    today = clock.today()
    tomorrow = today + timedelta(days=1)
    pending_qs = Suggestion.objects.filter(status=Suggestion.Status.PENDING).select_related("user")
    drafts = list(scheduling.draft_pool()[:40])
    draft_total = Question.objects.filter(date__isnull=True).count()
    today_slots = _enrich_slots(scheduling.day_slots(today))
    tomorrow_slots = _enrich_slots(scheduling.day_slots(tomorrow))
    return render(
        request,
        "staff/home.html",
        _staff_context(
            today=today,
            tomorrow=tomorrow,
            today_ready=scheduling.day_ready(today),
            tomorrow_ready=scheduling.day_ready(tomorrow),
            today_slots=today_slots,
            tomorrow_slots=tomorrow_slots,
            tomorrow_free=scheduling.free_positions(tomorrow),
            drafts=drafts,
            draft_total=draft_total,
            pending_suggestions=pending_qs.count(),
            pending_list=list(pending_qs[:15]),
            is_site_admin=roles.is_admin(request.user),
            role=roles.role_of(request.user),
        ),
    )


@staff_member_required
def staff_day(request, day: date):
    today = clock.today()
    tomorrow = today + timedelta(days=1)
    slots = _enrich_slots(scheduling.day_slots(day))
    filled = sum(1 for s in slots if s["question"])
    return render(
        request,
        "staff/day.html",
        _staff_context(
            day=day,
            today=today,
            tomorrow=tomorrow,
            prev_day=day - timedelta(days=1),
            next_day=day + timedelta(days=1),
            slots=slots,
            filled=filled,
            ready=filled >= settings.QUESTIONS_PER_DAY,
            free=scheduling.free_positions(day),
            drafts=list(scheduling.draft_pool()[:40]),
            is_site_admin=roles.is_admin(request.user),
            role=roles.role_of(request.user),
        ),
    )


@staff_member_required
@require_POST
def staff_fill_day(request, day: date):
    mode = request.POST.get("mode", "selected")
    if mode == "auto":
        placed, skipped = scheduling.fill_day_from_drafts(day, None)
    else:
        ids = [int(x) for x in request.POST.getlist("draft_ids") if str(x).isdigit()]
        if not ids:
            messages.warning(request, "Select at least one draft, or use auto-fill.")
            return redirect("staff_day", day=day)
        placed, skipped = scheduling.fill_day_from_drafts(day, ids)
    if placed:
        messages.success(request, f"Filled {placed} slot(s) on {day.isoformat()}.")
    else:
        messages.warning(request, "No slots filled. Need drafts with accepted answers and empty positions.")
    if skipped:
        messages.warning(request, f"Skipped {skipped} draft(s) with no accepted answers.")
    return redirect("staff_day", day=day)


@staff_member_required
@require_POST
def staff_schedule_tomorrow(request):
    tomorrow = clock.today() + timedelta(days=1)
    ids = [int(x) for x in request.POST.getlist("draft_ids") if str(x).isdigit()]
    if not ids:
        messages.warning(request, "Select at least one draft.")
        return redirect("staff_home")
    placed, skipped = scheduling.fill_day_from_drafts(tomorrow, ids)
    leftover = [i for i in ids if Question.objects.filter(pk=i, date__isnull=True).exists()]
    extra = 0
    if leftover:
        extra, skip2 = scheduling.schedule_drafts(leftover, start_day=tomorrow + timedelta(days=1))
        skipped += skip2
    total = placed + extra
    if total:
        messages.success(request, f"Scheduled {total} question(s) starting {tomorrow.isoformat()}.")
    else:
        messages.warning(request, "Nothing scheduled.")
    if skipped:
        messages.warning(request, f"Skipped {skipped} with no accepted answers.")
    return redirect("staff_home")


@staff_member_required
@require_http_methods(["GET", "POST"])
def staff_unschedule(request, pk: int):
    """Turn a scheduled question back into a draft."""
    try:
        q = Question.objects.get(pk=pk)
    except Question.DoesNotExist:
        messages.error(request, "Question not found.")
        return redirect("staff_home")
    day = q.date
    if request.method == "POST":
        q.date = None
        q.position = None
        q.save(update_fields=["date", "position"])
        messages.success(request, "Moved back to drafts.")
        if day:
            return redirect("staff_day", day=day)
        return redirect("staff_home")
    return redirect("staff_day", day=day) if day else redirect("staff_home")


@staff_member_required
@require_POST
def staff_review_suggestion(request, pk: int):
    suggestion = get_object_or_404(Suggestion, pk=pk, status=Suggestion.Status.PENDING)
    action = request.POST.get("action")
    if action == "approve":
        with transaction.atomic():
            question = Question.objects.create(prompt=suggestion.text, notes=f"From suggestion #{suggestion.pk}")
            for line in suggestion.example_list():
                AcceptedAnswer.objects.create(question=question, text=line, seed_tier=3)
            suggestion.status = Suggestion.Status.APPROVED
            suggestion.reviewed_at = timezone.now()
            suggestion.created_question = question
            suggestion.save()
        messages.success(request, "Approved → unscheduled draft created. Add tiers/aliases, then schedule it.")
    elif action == "reject":
        suggestion.status = Suggestion.Status.REJECTED
        suggestion.reviewed_at = timezone.now()
        suggestion.save(update_fields=["status", "reviewed_at"])
        messages.success(request, "Suggestion rejected.")
    else:
        messages.error(request, "Unknown action.")
    return redirect("staff_home")


@_admin_required
def staff_team(request):
    return render(
        request,
        "staff/team.html",
        _staff_context(
            team=list(roles.team_queryset()),
            today=clock.today(),
            tomorrow=clock.today() + timedelta(days=1),
            is_site_admin=True,
            role=roles.ROLE_ADMIN,
            bootstrap_ids=sorted(settings.ADMIN_OSU_IDS),
        ),
    )


@_admin_required
@require_POST
def staff_set_role(request):
    who = (request.POST.get("who") or "").strip()
    role = (request.POST.get("role") or "").strip().lower()
    if role not in {roles.ROLE_PLAYER, roles.ROLE_MODERATOR, roles.ROLE_ADMIN}:
        messages.error(request, "Invalid role.")
        return redirect("staff_team")

    user = None
    if who.isdigit():
        user = User.objects.filter(osu_id=int(who)).first()
    if user is None:
        user = User.objects.filter(osu_username__iexact=who).first()
    if user is None:
        messages.error(request, "No such player. They need to sign in once first.")
        return redirect("staff_team")

    if user.pk == request.user.pk and role != roles.ROLE_ADMIN:
        messages.error(request, "You can’t demote yourself.")
        return redirect("staff_team")

    if user.osu_id in settings.ADMIN_OSU_IDS and role != roles.ROLE_ADMIN:
        messages.error(request, "That osu! id is a bootstrap admin and stays admin.")
        return redirect("staff_team")

    roles.set_role(user, role)
    messages.success(request, f"{user.display_name} is now {role}.")
    return redirect("staff_team")
