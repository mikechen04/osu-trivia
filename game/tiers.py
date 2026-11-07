"""Rarity tiers. Higher tier = rarer answer = more points. Tier 0 means "not on the list"."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Tier:
    level: int
    name: str
    points: int
    emoji: str


TIERS = {
    0: Tier(0, "Miss", 0, "⬛"),
    1: Tier(1, "Osu Chud", 10, "🟦"),
    2: Tier(2, "Average Player", 30, "🟩"),
    3: Tier(3, "Mechanically Gifted", 60, "🟨"),
    4: Tier(4, "Genetics Abuser", 85, "🟧"),
    5: Tier(5, "Veiny Zany", 100, "🟪"),
}
MAX_TIER = 5
MAX_POINTS = TIERS[MAX_TIER].points

TIER_CHOICES = [(t.level, f"{t.level} · {t.name} ({t.points} pts)") for t in TIERS.values() if t.level > 0]


def tier_info(level: int) -> Tier:
    return TIERS[max(0, min(MAX_TIER, int(level)))]
