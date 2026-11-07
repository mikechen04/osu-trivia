import json
import re
from datetime import date, timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from core.ratelimit import ratelimit

from . import clock, leaderboard, services
from .models import Attempt
from .tiers import MAX_POINTS, tier_info


# --- JSON API ---------------------------------------------------------------

def _error(code: str, message: str, status: int):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def api(method: str):
    """Shared plumbing for the game endpoints: auth, method, error format."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method != method:
                return _error("method_not_allowed", f"Use {method}.", 405)
            if not request.user.is_authenticated:
                return _error("auth_required", "Sign in with osu! to play.", 401)
            try:
                return view(request, *args, **kwargs)
            except services.GameError as exc:
                return _error(exc.code, exc.message, exc.status)

        return wrapped

    return decorator


@api("GET")
@ratelimit("api_state", user=120, ip=300)
def api_state(request):
    return JsonResponse(services.get_state(request.user))


@api("POST")
@ratelimit("api_next", user=30, ip=90)
def api_next(request):
    return JsonResponse(services.start_next(request.user))


@api("POST")
@ratelimit("api_answer", user=30, ip=90)
def api_answer(request):
    if len(request.body) > 2048:
        return _error("too_large", "Request too large.", 413)
    try:
        payload = json.loads(request.body or b"{}")
        question_id = payload["question_id"]
        answer = payload.get("answer", "")
    except (ValueError, KeyError, TypeError, AttributeError):
        return _error("bad_request", "Malformed request.", 400)
    if not isinstance(question_id, int) or isinstance(question_id, bool):
        return _error("bad_request", "Malformed request.", 400)
    state, result = services.submit_answer(request.user, question_id, answer)
    return JsonResponse({**state, "result": result})


# --- Pages ------------------------------------------------------------------

@login_required
def play(request):
    return render(request, "game/play.html")


COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


@require_GET
def leaderboard_page(request):
    today = clock.today()
    period = request.GET.get("tab", "daily")
    if period not in leaderboard.PERIODS:
        period = "daily"
    country = request.GET.get("country", "")
    country = country.upper() if COUNTRY_RE.match(country) else ""

    day = today
    if period == "daily":
        try:
            day = date.fromisoformat(request.GET.get("date", ""))
        except ValueError:
            day = today
        day = min(day, today)

    data = leaderboard.build(period, day=day, country=country, me=request.user)
    context = {
        "period": period,
        "country": country,
        "countries": leaderboard.countries(),
        "day": day,
        "prev_day": day - timedelta(days=1),
        "next_day": day + timedelta(days=1) if day < today else None,
        "week": clock.week_bounds(today) if period == "weekly" else None,
        "max_score": settings.QUESTIONS_PER_DAY * MAX_POINTS,
        **data,
    }
    return render(request, "game/leaderboard.html", context)


def share_page(request, username: str, day: date):
    """Public, spoiler-free result page. Shows the score and tier row only, never answers."""
    User = get_user_model()
    user = User.objects.filter(osu_username__iexact=username, is_public=True).order_by("-last_login").first()
    if user is None and username.isdigit():
        user = User.objects.filter(osu_id=int(username), is_public=True).first()
    if user is None:
        raise Http404
    attempt = Attempt.objects.complete().filter(user=user, date=day).first()
    if attempt is None:
        raise Http404

    by_position = {r.position: r.tier for r in attempt.responses.exclude(answered_at=None)}
    tiers = [tier_info(by_position.get(p, 0)) for p in range(1, settings.QUESTIONS_PER_DAY + 1)]
    max_score = settings.QUESTIONS_PER_DAY * MAX_POINTS
    return render(
        request,
        "game/share.html",
        {
            "player": user,
            "attempt": attempt,
            "tiers": tiers,
            "emoji_row": "".join(t.emoji for t in tiers),
            "max_score": max_score,
            "page_url": f"{settings.SITE_URL}{request.path}",
        },
    )
