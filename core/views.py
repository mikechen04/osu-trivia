from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from game import clock, leaderboard, services
from game.models import Attempt

from .forms import SuggestionForm
from .models import Suggestion
from .ratelimit import allow, client_ip


def home(request):
    today = clock.today()
    ready = services.todays_questions(today) is not None
    cta = "login"
    if request.user.is_authenticated:
        attempt = Attempt.objects.filter(user=request.user, date=today).first()
        if attempt is None:
            cta = "play"
        elif attempt.responses.filter(answered_at__isnull=False).count() >= settings.QUESTIONS_PER_DAY:
            cta = "done"
        else:
            cta = "continue"
        my_attempt = attempt
    else:
        my_attempt = None
    top = leaderboard.build("daily", day=today, me=request.user)["rows"][:5]
    return render(request, "core/home.html", {"ready": ready, "cta": cta, "my_attempt": my_attempt, "top": top})


@login_required
def suggest(request):
    form = SuggestionForm(request.POST or None)
    recent = Suggestion.objects.filter(user=request.user, created_at__gte=timezone.now() - timedelta(days=1)).count()
    left = max(0, settings.SUGGESTIONS_PER_DAY - recent)

    if request.method == "POST":
        if not allow("suggest:ip", client_ip(request), 10, 3600):
            messages.error(request, "Too many suggestions from this network. Please try again later.")
        elif left == 0:
            messages.error(request, f"You can send {settings.SUGGESTIONS_PER_DAY} suggestions per day. Try again tomorrow.")
        elif form.is_valid():
            Suggestion.objects.create(
                user=request.user,
                text=form.cleaned_data["text"],
                examples=form.cleaned_data["examples"],
            )
            messages.success(request, "Thanks! Your suggestion is in the review queue. Nothing is published automatically.")
            return redirect("suggest")

    mine = Suggestion.objects.filter(user=request.user)[:10]
    return render(request, "core/suggest.html", {"form": form, "left": left, "mine": mine})


def not_found(request, exception=None):
    return render(request, "404.html", status=404)


@require_GET
def robots(request):
    body = f"User-agent: *\nDisallow: /{settings.ADMIN_URL}\nDisallow: /staff/\nDisallow: /api/\nDisallow: /account/\n"
    return HttpResponse(body, content_type="text/plain")


@require_GET
def healthz(request):
    return HttpResponse("ok", content_type="text/plain")
