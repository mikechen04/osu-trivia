"""Helpers for scheduling daily sets and inspecting rarities (staff + admin)."""

from datetime import date, timedelta

from django.conf import settings
from django.db.models import Prefetch

from . import clock, scoring
from .models import AcceptedAnswer, Question
from .tiers import tier_info


def questions_for_day(day: date):
    return (
        Question.objects.filter(date=day)
        .prefetch_related(
            Prefetch("answers", queryset=AcceptedAnswer.objects.order_by("text")),
        )
        .order_by("position")
    )


def day_slots(day: date) -> list[dict]:
    """Five slots for a UTC day: filled question or empty placeholder."""
    by_pos = {q.position: q for q in questions_for_day(day)}
    n = settings.QUESTIONS_PER_DAY
    return [{"position": p, "question": by_pos.get(p)} for p in range(1, n + 1)]


def day_ready(day: date) -> bool:
    return Question.objects.filter(date=day).count() >= settings.QUESTIONS_PER_DAY


def free_positions(day: date) -> list[int]:
    used = set(Question.objects.filter(date=day).values_list("position", flat=True))
    return [p for p in range(1, settings.QUESTIONS_PER_DAY + 1) if p not in used]


def draft_pool():
    return (
        Question.objects.filter(date__isnull=True)
        .prefetch_related("answers")
        .order_by("id")
    )


def schedule_drafts(draft_ids, start_day: date | None = None) -> tuple[int, int]:
    """Place drafts (with at least one accepted answer) into the next open slots.
    Returns (placed, skipped)."""
    n = settings.QUESTIONS_PER_DAY
    drafts = list(
        Question.objects.filter(pk__in=draft_ids, date__isnull=True)
        .prefetch_related("answers")
        .order_by("id")
    )
    day = start_day or (clock.today() + timedelta(days=1))
    placed = 0
    skipped = 0
    for question in drafts:
        if not question.answers.exists():
            skipped += 1
            continue
        while True:
            free = free_positions(day)
            if free:
                question.date, question.position = day, free[0]
                question.save(update_fields=["date", "position"])
                placed += 1
                break
            day += timedelta(days=1)
    return placed, skipped


def fill_day_from_drafts(day: date, draft_ids: list[int] | None = None) -> tuple[int, int]:
    """Fill only empty slots on `day`. If draft_ids is None, take the oldest drafts."""
    free = free_positions(day)
    if not free:
        return 0, 0
    qs = Question.objects.filter(date__isnull=True).prefetch_related("answers").order_by("id")
    if draft_ids is not None:
        wanted = set(draft_ids)
        qs = [q for q in qs if q.pk in wanted]
    else:
        qs = list(qs)
    placed = 0
    skipped = 0
    for question in qs:
        if not free:
            break
        if not question.answers.exists():
            skipped += 1
            continue
        question.date, question.position = day, free.pop(0)
        question.save(update_fields=["date", "position"])
        placed += 1
    return placed, skipped


def answer_rarity_rows(question: Question) -> list[dict]:
    counts, total = scoring.answer_counts(question.pk)
    rows = []
    for answer in question.answers.all():
        plays = counts.get(answer.pk, 0)
        live = tier_info(scoring.effective_tier(answer.seed_tier, plays, total))
        seed = tier_info(answer.seed_tier)
        share = (plays / total) if total else None
        rows.append(
            {
                "answer": answer,
                "plays": plays,
                "share": share,
                "pct": round(100 * share) if share is not None else None,
                "seed": seed,
                "live": live,
            }
        )
    rows.sort(key=lambda r: (-r["live"].level, r["answer"].text.lower()))
    return rows, total
