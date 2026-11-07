"""Bulk-load questions from JSON (handy for LLM-drafted question packs).

File format:
[
  {"prompt": "Name a game mode in osu!",
   "hint": "optional",
   "date": "2026-12-01",        # optional; with "position" 1-5
   "position": 1,
   "answers": [{"text": "Taiko", "aliases": ["drums"], "tier": 3}, ...]}
]

Without --schedule-from, entries that have no date are imported as unscheduled drafts.
With --schedule-from YYYY-MM-DD they are placed 5 per day, in file order, starting that day.
"""

import json
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from game.models import AcceptedAnswer, Question


class Command(BaseCommand):
    help = "Import questions from a JSON file."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--schedule-from", help="YYYY-MM-DD")

    @transaction.atomic
    def handle(self, *args, path, schedule_from, **options):
        try:
            with open(path, encoding="utf-8") as fh:
                items = json.load(fh)
        except (OSError, ValueError) as exc:
            raise CommandError(f"Can't read {path}: {exc}")

        n = settings.QUESTIONS_PER_DAY
        cursor = date.fromisoformat(schedule_from) if schedule_from else None
        slot = 0
        made = 0
        for item in items:
            when, position = item.get("date"), item.get("position")
            if when is None and cursor is not None:
                when, position = cursor.isoformat(), slot + 1
                slot += 1
                if slot == n:
                    slot, cursor = 0, cursor + timedelta(days=1)
            question = Question(
                prompt=item["prompt"], hint=item.get("hint", ""), date=date.fromisoformat(when) if when else None, position=position
            )
            question.full_clean()
            question.save()
            for answer in item.get("answers", []):
                tier = int(answer.get("tier", 3))
                if not 1 <= tier <= 5:
                    raise CommandError(f"Tier must be 1-5 ({answer})")
                AcceptedAnswer.objects.create(
                    question=question, text=answer["text"], aliases="\n".join(answer.get("aliases", [])), seed_tier=tier
                )
            made += 1
        self.stdout.write(self.style.SUCCESS(f"Imported {made} question(s)."))
