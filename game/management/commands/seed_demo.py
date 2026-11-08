"""Load placeholder questions for local testing.

Examples:
  python manage.py seed_demo
  python manage.py seed_demo --days 4 --drafts 8 --force
  python manage.py seed_demo --force          # wipe plays for those days and reseed so you can replay
"""

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from core.models import Suggestion
from game import clock
from game.models import AcceptedAnswer, Attempt, Question


class Command(BaseCommand):
    help = "Load placeholder question packs for continuous local testing."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=4, help="How many UTC days to fill starting today (default 4).")
        parser.add_argument("--drafts", type=int, default=6, help="Extra unscheduled drafts to keep around (default 6).")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Delete attempts/responses for those days and reseed (lets you replay today).",
        )

    @transaction.atomic
    def handle(self, *args, days, drafts, force, **options):
        days = max(1, days)
        drafts = max(0, drafts)
        today = clock.today()
        end = today + timedelta(days=days)
        n = settings.QUESTIONS_PER_DAY
        need = days * n

        path = Path(__file__).resolve().parents[2] / "fixtures" / "demo_questions.json"

        if force:
            Attempt.objects.filter(date__gte=today, date__lt=end).delete()
            Question.objects.filter(date__gte=today, date__lt=end).delete()
            # Clear leftover demo drafts tagged in notes so re-runs stay tidy
            Question.objects.filter(date__isnull=True, notes="demo draft").delete()
            Suggestion.objects.filter(admin_note="demo seed").delete()
        else:
            Question.objects.filter(date__gte=today, date__lt=end, responses__isnull=True).delete()

        # Import enough scheduled questions, then leftover items as drafts.
        import json

        with open(path, encoding="utf-8") as fh:
            pack = json.load(fh)

        if len(pack) < need + drafts:
            # Repeat the pack with numbered prompts so we always have enough days + drafts.
            base = list(pack)
            i = 1
            while len(pack) < need + drafts:
                for item in base:
                    clone = {
                        "prompt": f"{item['prompt']} (pack {i})",
                        "hint": item.get("hint", ""),
                        "answers": item.get("answers", []),
                    }
                    pack.append(clone)
                    if len(pack) >= need + drafts:
                        break
                i += 1

        scheduled = pack[:need]
        draft_items = pack[need : need + drafts]

        tmp = path.with_name("_seed_scheduled.json")
        tmp.write_text(json.dumps(scheduled), encoding="utf-8")
        try:
            call_command("import_questions", str(tmp), schedule_from=today.isoformat())
        finally:
            tmp.unlink(missing_ok=True)

        made_drafts = 0
        for item in draft_items:
            q = Question.objects.create(
                prompt=item["prompt"],
                hint=item.get("hint", ""),
                notes="demo draft",
            )
            for answer in item.get("answers", []):
                AcceptedAnswer.objects.create(
                    question=q,
                    text=answer["text"],
                    aliases="\n".join(answer.get("aliases", [])),
                    seed_tier=int(answer.get("tier", 3)),
                )
            made_drafts += 1

        # One pending suggestion so the staff review queue isn't empty.
        suggester, _ = User.objects.get_or_create(
            osu_id=900001,
            defaults={"username": "osu900001", "osu_username": "DemoSuggester"},
        )
        if not Suggestion.objects.filter(status=Suggestion.Status.PENDING, admin_note="demo seed").exists():
            Suggestion.objects.create(
                user=suggester,
                text="Name a fruit that appears in osu!catch",
                examples="apple\norange\nbanana\ngrape",
                admin_note="demo seed",
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {days} day(s) from {today} ({need} questions), {made_drafts} draft(s)"
                + (" [force]" if force else "")
                + "."
            )
        )
        self.stdout.write("Replay tip: python manage.py seed_demo --force")
