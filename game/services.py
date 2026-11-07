"""The game state machine. Everything that matters (timer, locking, scoring) happens here, server-side.

Flow for one player on one UTC day:
  start_next()  -> server creates the Response row and stamps `served_at`; that starts the 25s timer
  submit_answer -> server checks its own clock, matches, scores, locks the row
A question left unanswered is closed as a timeout the next time anything looks at it.
"""

from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum

from . import clock, scoring
from .matching import MAX_ANSWER_LEN, match_answer
from .models import Attempt, Question, Response
from .tiers import MAX_POINTS, tier_info


class GameError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# --- helpers -----------------------------------------------------------------

def todays_questions(day):
    """The day's full set, or None if the admins haven't scheduled all of it."""
    n = settings.QUESTIONS_PER_DAY
    questions = list(Question.objects.filter(date=day).order_by("position"))
    if [q.position for q in questions] != list(range(1, n + 1)):
        return None
    return questions


def _deadline(response: Response):
    return response.served_at + timedelta(seconds=settings.QUESTION_SECONDS)


def _close(response: Response, *, answered_at, raw="", normalized="", matched=None, tier=0, points=0, timed_out=False):
    """Lock a response. The `answered_at IS NULL` filter makes this a compare-and-set, so two
    simultaneous submits can never both win. Returns True if this call did the locking."""
    updated = Response.objects.filter(pk=response.pk, answered_at__isnull=True).update(
        answered_at=answered_at,
        raw_answer=raw,
        normalized=normalized,
        matched=matched,
        tier=tier,
        points=points,
        timed_out=timed_out,
    )
    return updated == 1


def _refresh_attempt(attempt: Attempt):
    agg = attempt.responses.filter(answered_at__isnull=False).aggregate(score=Sum("points"), n=Count("id"))
    fields = {"score": agg["score"] or 0}
    if agg["n"] == settings.QUESTIONS_PER_DAY and attempt.finished_at is None:
        fields["finished_at"] = clock.now()
    Attempt.objects.filter(pk=attempt.pk).update(**fields)


def _settle(attempt: Attempt, at):
    """Close any question whose time (plus network grace) has run out."""
    grace = timedelta(seconds=settings.ANSWER_GRACE_SECONDS)
    changed = False
    for response in attempt.responses.filter(answered_at__isnull=True):
        if at > _deadline(response) + grace:
            changed |= _close(response, answered_at=_deadline(response), timed_out=True)
    if changed:
        _refresh_attempt(attempt)


# --- public API --------------------------------------------------------------

def start_next(user) -> dict:
    """Begin the next unanswered question (idempotent: if one is already running it just returns it)."""
    now = clock.now()
    day = clock.today(now)
    questions = todays_questions(day)
    if questions is None:
        raise GameError("not_ready", "Today's questions aren't ready yet. Check back soon.", 409)

    with transaction.atomic():
        attempt, _ = Attempt.objects.get_or_create(user=user, date=day)
        _settle(attempt, now)
        taken = {r.position: r for r in attempt.responses.all()}
        if any(r.answered_at is None for r in taken.values()):
            return get_state(user)
        upcoming = next((q for q in questions if q.position not in taken), None)
        if upcoming is not None:
            try:
                with transaction.atomic():
                    Response.objects.get_or_create(
                        attempt=attempt,
                        question=upcoming,
                        defaults={"position": upcoming.position, "served_at": now},
                    )
            except IntegrityError:
                pass  # a parallel request created it first; same outcome
    return get_state(user)


def submit_answer(user, question_id: int, raw) -> tuple[dict, dict]:
    if not isinstance(raw, str):
        raise GameError("bad_answer", "Answer must be text.")
    raw = " ".join(raw.split())  # collapse whitespace/newlines/control gaps
    if len(raw) > MAX_ANSWER_LEN:
        raise GameError("too_long", f"Answers can be at most {MAX_ANSWER_LEN} characters.")
    now = clock.now()

    with transaction.atomic():
        response = (
            Response.objects.select_for_update(of=("self",))
            .select_related("attempt", "question")
            .filter(attempt__user=user, question_id=question_id)
            .first()
        )
        if response is None:
            raise GameError("not_started", "That question hasn't been started.", 409)
        if response.answered_at is not None:
            raise GameError("already_answered", "You've already answered this question.", 409)

        grace = timedelta(seconds=settings.ANSWER_GRACE_SECONDS)
        if now > _deadline(response) + grace:
            _close(response, answered_at=_deadline(response), timed_out=True)
        else:
            answers = list(response.question.answers.all())
            matched, normalized = match_answer(raw, answers)
            tier = points = 0
            if matched is not None:
                counts, total = scoring.answer_counts(response.question_id)
                tier = scoring.effective_tier(matched.seed_tier, counts.get(matched.pk, 0), total)
                points = tier_info(tier).points
            if not _close(
                response,
                answered_at=now,
                raw=raw,
                normalized=normalized,
                matched=matched,
                tier=tier,
                points=points,
            ):
                raise GameError("already_answered", "You've already answered this question.", 409)
        _refresh_attempt(response.attempt)

    state = get_state(user)
    result = next((a for a in state["answered"] if a["question_id"] == question_id), None)
    return state, result


def share_text(attempt: Attempt, responses) -> str:
    """Spoiler-free: score, one emoji per question tier, link. Never any answer text."""
    by_position = {r.position: r.tier for r in responses if r.answered_at}
    row = "".join(tier_info(by_position.get(p, 0)).emoji for p in range(1, settings.QUESTIONS_PER_DAY + 1))
    max_score = settings.QUESTIONS_PER_DAY * MAX_POINTS
    return f"osu! trivia · {attempt.date.isoformat()}\n{attempt.score}/{max_score}\n{row}\n{share_url(attempt)}"


def share_url(attempt: Attempt) -> str:
    name = quote(attempt.user.display_name, safe="")
    return f"{settings.SITE_URL}/u/{name}/{attempt.date.isoformat()}/"


def _entry(response: Response) -> dict:
    tier = tier_info(response.tier)
    return {
        "question_id": response.question_id,
        "position": response.position,
        "prompt": response.question.prompt,
        "answer": response.raw_answer,
        "matched": response.matched.text if response.matched else None,
        "tier": tier.level,
        "tier_name": tier.name,
        "emoji": tier.emoji,
        "points": response.points,
        "timed_out": response.timed_out,
    }


def _breakdown(responses) -> list[dict]:
    """Most popular answers per question. Only shown once the player has finished."""
    out = []
    for response in responses:
        question = response.question
        counts, total = scoring.answer_counts(question.pk)
        answers = list(question.answers.all())
        rows = []
        for answer in sorted(answers, key=lambda a: (-counts.get(a.pk, 0), a.text)):
            plays = counts.get(answer.pk, 0)
            mine = response.matched_id == answer.pk
            if plays == 0 and not mine:
                continue
            tier = tier_info(scoring.effective_tier(answer.seed_tier, plays, total))
            rows.append(
                {
                    "text": answer.text,
                    "plays": plays,
                    "share": round(100 * plays / total, 1) if total else 0,
                    "tier_name": tier.name,
                    "emoji": tier.emoji,
                    "mine": mine,
                }
            )
        out.append({"position": response.position, "prompt": question.prompt, "total": total, "answers": rows[:8]})
    return out


def get_state(user) -> dict:
    now = clock.now()
    day = clock.today(now)
    n = settings.QUESTIONS_PER_DAY
    state = {
        "date": day.isoformat(),
        "total_questions": n,
        "question_seconds": settings.QUESTION_SECONDS,
        "max_score": n * MAX_POINTS,
        "server_now_ms": int(now.timestamp() * 1000),
        "next_reset_ms": int(clock.next_reset(now).timestamp() * 1000),
        "ready": todays_questions(day) is not None,
        "status": "not_started",
        "answered": [],
        "current": None,
        "score": 0,
        "finished": False,
    }

    attempt = Attempt.objects.select_related("user").filter(user=user, date=day).first()
    if attempt is None:
        return state
    _settle(attempt, now)

    responses = list(attempt.responses.select_related("question", "matched").order_by("position"))
    answered = [r for r in responses if r.answered_at is not None]
    active = next((r for r in responses if r.answered_at is None), None)
    state["answered"] = [_entry(r) for r in answered]
    state["score"] = sum(r.points for r in answered)
    state["ready"] = True

    if active is not None:
        state["status"] = "active"
        left = (_deadline(active) - now).total_seconds()
        state["current"] = {
            "question_id": active.question_id,
            "position": active.position,
            "prompt": active.question.prompt,
            "hint": active.question.hint,
            "seconds_left": round(max(0.0, left), 2),
        }
    elif len(answered) >= n:
        state["status"] = "finished"
        state["finished"] = True
        state["share"] = {"text": share_text(attempt, responses), "url": share_url(attempt)}
        state["breakdown"] = _breakdown(answered)
    else:
        state["status"] = "between"
    return state
