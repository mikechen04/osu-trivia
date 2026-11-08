import json
import logging
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Max, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.ratelimit import ratelimit
from game.models import Attempt

from . import osu, roles
from .models import User

log = logging.getLogger(__name__)
BACKEND = "django.contrib.auth.backends.ModelBackend"


def _safe_next(request, value) -> str:
    if value and url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return value
    return "/play/"


def _sign_in(request, profile: dict, *, accepted_terms: bool) -> User:
    user, created = User.objects.get_or_create(osu_id=profile["osu_id"], defaults={"username": f"osu{profile['osu_id']}"})
    if created:
        user.set_unusable_password()
    user.osu_username = profile["username"]
    user.avatar_url = profile["avatar_url"]
    user.country_code = profile["country_code"]
    if accepted_terms:
        user.tos_accepted_at = timezone.now()
    user.save()
    roles.apply_bootstrap_admin(user)
    login(request, user, backend=BACKEND)
    return user


@require_http_methods(["GET", "POST"])
@ratelimit("login", ip=30)
def login_view(request):
    nxt = _safe_next(request, request.POST.get("next") or request.GET.get("next"))
    if request.user.is_authenticated:
        return redirect(nxt)

    error = None
    if request.method == "POST":
        if request.POST.get("agree") != "on":
            error = "Please confirm you're at least 13 and accept the Terms and Privacy Policy."
        elif not osu.is_configured():
            error = "osu! sign-in isn't configured on this server yet."
        else:
            state = secrets.token_urlsafe(32)
            request.session["osu_state"] = state
            request.session["login_next"] = nxt
            request.session["tos_ok"] = True
            return redirect(osu.build_authorize_url(state))
    return render(request, "accounts/login.html", {"next": nxt, "error": error, "dev_login": settings.DEV_LOGIN})


@require_GET
@ratelimit("oauth_callback", ip=30)
def callback(request):
    expected = request.session.pop("osu_state", None)
    nxt = request.session.pop("login_next", "/play/")
    accepted = request.session.pop("tos_ok", False)

    if not expected or not constant_time_compare(expected, request.GET.get("state", "")):
        messages.error(request, "That sign-in link expired. Please try again.")
        return redirect("login")
    if request.GET.get("error"):
        messages.info(request, "Sign-in was cancelled.")
        return redirect("login")
    code = request.GET.get("code", "")
    if not code or len(code) > 2048:
        messages.error(request, "osu! didn't send a sign-in code. Please try again.")
        return redirect("login")

    try:
        profile = osu.fetch_profile(osu.exchange_code(code))  # the token is discarded right after this
    except (osu.OsuError, ValueError):
        log.warning("osu! sign-in failed", exc_info=True)
        messages.error(request, "Couldn't complete sign-in with osu!. Please try again in a moment.")
        return redirect("login")

    existing = User.objects.filter(osu_id=profile["osu_id"]).first()
    if existing is not None and not existing.is_active:
        messages.error(request, "This account is disabled.")
        return redirect("login")

    _sign_in(request, profile, accepted_terms=accepted)
    return redirect(_safe_next(request, nxt))


@require_http_methods(["GET", "POST"])
def dev_login(request):
    """Local development only: pick a fake osu! identity. Returns 404 unless DEBUG and DEV_LOGIN are on."""
    from django.http import Http404

    if not settings.DEV_LOGIN:
        raise Http404
    if request.method == "POST":
        try:
            osu_id = int(request.POST.get("osu_id", ""))
        except ValueError:
            osu_id = 0
        name = request.POST.get("username", "").strip()[:20]
        if osu_id > 0 and name:
            country = request.POST.get("country", "")[:2].upper()
            profile = osu.parse_profile({"id": osu_id, "username": name, "avatar_url": "", "country_code": country})
            user = _sign_in(request, profile, accepted_terms=True)
            role = request.POST.get("role", "player")
            if role in {roles.ROLE_MODERATOR, roles.ROLE_ADMIN}:
                roles.set_role(user, role)
            elif role == roles.ROLE_PLAYER and user.osu_id not in settings.ADMIN_OSU_IDS:
                roles.set_role(user, roles.ROLE_PLAYER)
            return redirect(_safe_next(request, request.POST.get("next")))
    return render(request, "accounts/dev_login.html", {"next": request.GET.get("next", "/play/")})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("home")


# --- Account page: privacy controls ---------------------------------------------

@login_required
@require_http_methods(["GET", "POST"])
def account(request):
    if request.method == "POST":
        request.user.is_public = request.POST.get("is_public") == "on"
        request.user.save(update_fields=["is_public"])
        messages.success(request, "Saved.")
        return redirect("account")
    attempts = Attempt.objects.complete().filter(user=request.user)
    stats = attempts.aggregate(total=Sum("score"), best=Max("score"))
    return render(
        request,
        "accounts/account.html",
        {"days_played": attempts.count(), "total": stats["total"] or 0, "best": stats["best"] or 0},
    )


@login_required
@require_GET
@ratelimit("export", user=5, ip=10, window=3600)
def export_data(request):
    user = request.user
    attempts = []
    for attempt in Attempt.objects.filter(user=user).prefetch_related("responses__question", "responses__matched"):
        attempts.append(
            {
                "date": attempt.date.isoformat(),
                "score": attempt.score,
                "started_at": attempt.created_at.isoformat(),
                "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
                "responses": [
                    {
                        "position": r.position,
                        "question": r.question.prompt,
                        "your_answer": r.raw_answer,
                        "counted_as": r.matched.text if r.matched else None,
                        "tier": r.tier,
                        "points": r.points,
                        "timed_out": r.timed_out,
                        "answered_at": r.answered_at.isoformat() if r.answered_at else None,
                    }
                    for r in sorted(attempt.responses.all(), key=lambda r: r.position)
                ],
            }
        )
    data = {
        "exported_at": timezone.now().isoformat(),
        "profile": {
            "osu_id": user.osu_id,
            "username": user.osu_username,
            "avatar_url": user.avatar_url,
            "country_code": user.country_code,
            "show_on_leaderboards_and_share_pages": user.is_public,
            "first_signed_in": user.date_joined.isoformat(),
            "terms_accepted_at": user.tos_accepted_at.isoformat() if user.tos_accepted_at else None,
        },
        "attempts": attempts,
        "suggestions": [
            {"question": s.text, "examples": s.example_list(), "status": s.status, "submitted_at": s.created_at.isoformat()}
            for s in user.suggestions.all()
        ],
    }
    response = HttpResponse(json.dumps(data, indent=2, ensure_ascii=False), content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="osu-trivia-my-data.json"'
    return response


@login_required
@require_POST
@ratelimit("delete", user=5, ip=10, window=3600)
def delete_account(request):
    if request.POST.get("confirm", "").strip() != "DELETE":
        messages.error(request, "Type DELETE to confirm.")
        return redirect("account")
    user = request.user
    with transaction.atomic():
        # Unreviewed or rejected suggestions go with the account. Approved ones may already be
        # part of the question pool, so they stay but are detached from the user (anonymised).
        user.suggestions.exclude(status="approved").delete()
        logout(request)
        user.delete()  # cascades to attempts and responses
    messages.success(request, "Your account and play history have been deleted.")
    return redirect("home")
