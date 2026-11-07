"""Answer normalising and matching (aliases + small-typo forgiveness)."""

import re
import unicodedata

MAX_ANSWER_LEN = 100
_LEADING_ARTICLE = re.compile(r"^(the|a|an)\s+")


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace.
    "  Half-Time!! " -> "half time"."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = "".join(c if c.isalnum() else " " for c in text)
    text = " ".join(text.split())
    return _LEADING_ARTICLE.sub("", text)


def compact(text: str) -> str:
    """Normalised and with spaces removed, so "double time" == "doubletime"."""
    return normalize(text).replace(" ", "")


def osa_distance(a: str, b: str, limit: int) -> int:
    """Edit distance where a swap of two neighbours counts as one edit.
    Returns limit + 1 as soon as it is clear the distance exceeds `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2 = None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def allowed_typos(target: str) -> int:
    """Short answers must be exact ("hd" is not "hr"); longer ones get 1-2 slips."""
    if target.isdigit() or len(target) <= 4:
        return 0
    return 1 if len(target) <= 8 else 2


def match_answer(raw: str, answers):
    """Return (matched_answer_or_None, normalised_text).

    `answers` need `.pk` and `.match_keys` (compact forms of the answer and its aliases).
    Exact matches win. Otherwise a typo match only counts if exactly one answer is
    the closest, so a vague guess can never be credited to the wrong answer."""
    normalized = normalize(raw)
    key = normalized.replace(" ", "")
    if not key:
        return None, normalized

    for answer in answers:
        if key in answer.match_keys:
            return answer, normalized

    best = None
    best_answers = set()
    by_pk = {}
    for answer in answers:
        by_pk[answer.pk] = answer
        for target in answer.match_keys:
            limit = allowed_typos(target)
            if limit == 0:
                continue
            dist = osa_distance(key, target, limit)
            if dist > limit:
                continue
            if best is None or dist < best:
                best, best_answers = dist, {answer.pk}
            elif dist == best:
                best_answers.add(answer.pk)

    if best is not None and len(best_answers) == 1:
        return by_pk[next(iter(best_answers))], normalized
    return None, normalized
