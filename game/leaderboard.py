from dataclasses import dataclass
from datetime import date

from django.contrib.auth import get_user_model
from django.db.models import Count, Max, Sum

from . import clock
from .models import Attempt

PERIODS = ("daily", "weekly", "alltime")
LIMIT = 100


@dataclass
class Row:
    rank: int
    user: object
    score: int
    days: int
    is_me: bool = False


def _base(period: str, day: date, country: str):
    qs = Attempt.objects.complete().filter(user__is_public=True)
    if period == "daily":
        qs = qs.filter(date=day)
    elif period == "weekly":
        start, end = clock.week_bounds(day)
        qs = qs.filter(date__range=(start, end))
    if country:
        qs = qs.filter(user__country_code=country)
    return qs


def build(period: str, *, day: date, country: str = "", me=None) -> dict:
    """Top rows (ties share a rank) plus the signed-in player's own row if they're outside the top."""
    qs = _base(period, day, country)
    totals = (
        qs.values("user")
        .annotate(total=Sum("score"), days=Count("id"), last=Max("finished_at"))
        .order_by("-total", "last", "user")
    )
    page = list(totals[:LIMIT])
    users = get_user_model().objects.in_bulk([r["user"] for r in page])

    rows, previous_total, previous_rank = [], None, 0
    for index, r in enumerate(page, start=1):
        rank = previous_rank if r["total"] == previous_total else index
        previous_total, previous_rank = r["total"], rank
        rows.append(Row(rank, users[r["user"]], r["total"], r["days"], is_me=bool(me and r["user"] == me.pk)))

    my_row = None
    if me is not None and me.is_authenticated and me.is_public and not any(r.is_me for r in rows):
        mine = qs.filter(user=me).aggregate(total=Sum("score"), days=Count("id"))
        if mine["days"]:
            better = qs.values("user").annotate(total=Sum("score")).filter(total__gt=mine["total"]).count()
            my_row = Row(better + 1, me, mine["total"], mine["days"], is_me=True)

    return {"rows": rows, "my_row": my_row}


def countries() -> list[str]:
    User = get_user_model()
    return sorted(
        User.objects.filter(is_public=True, attempts__isnull=False)
        .exclude(country_code="")
        .values_list("country_code", flat=True)
        .distinct()
    )
