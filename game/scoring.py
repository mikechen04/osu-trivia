"""Turning "how often did people give this answer" into a tier.

Every accepted answer starts with a hand/LLM-seeded tier (1-5). Once a question has
enough valid answers (BLEND_MIN_PLAYS) the real share of players who gave an answer is
blended in, and from BLEND_FULL_PLAYS on it decides the tier alone.
Rarer answer -> higher tier -> more points.
"""

import math

from django.conf import settings
from django.db.models import Count

from .tiers import MAX_TIER

# Share of valid answers at/above which an answer is "very common" (tier 1)
# and at/below which it is "very rare" (tier 5). In between we interpolate on a log scale.
COMMON_SHARE = 0.5
RARE_SHARE = 0.01


def live_tier_value(share: float) -> float:
    clamped = min(max(share, RARE_SHARE), COMMON_SHARE)
    return 1 + (MAX_TIER - 1) * math.log(COMMON_SHARE / clamped) / math.log(COMMON_SHARE / RARE_SHARE)


def blend_weight(total_valid: int) -> float:
    low, high = settings.BLEND_MIN_PLAYS, settings.BLEND_FULL_PLAYS
    if total_valid < low:
        return 0.0
    if total_valid >= high:
        return 1.0
    return (total_valid - low) / (high - low)


def effective_tier(seed_tier: int, count: int, total_valid: int) -> int:
    """`count` = valid answers equal to this one, `total_valid` = all valid answers to the question."""
    weight = blend_weight(total_valid)
    if weight == 0:
        return seed_tier
    share = max(count, 0.5) / total_valid  # 0.5 so a never-seen answer still gets a finite share
    value = (1 - weight) * seed_tier + weight * live_tier_value(share)
    return max(1, min(MAX_TIER, int(value + 0.5)))


def answer_counts(question_id: int) -> tuple[dict[int, int], int]:
    """({accepted_answer_id: times given}, total valid answers) for a question, finished answers only."""
    from .models import Response

    rows = (
        Response.objects.filter(question_id=question_id, matched__isnull=False, answered_at__isnull=False)
        .values("matched")
        .annotate(n=Count("id"))
    )
    counts = {row["matched"]: row["n"] for row in rows}
    return counts, sum(counts.values())
