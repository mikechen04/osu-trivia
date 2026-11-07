from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand

from game import clock
from game.models import Question


class Command(BaseCommand):
    help = "Load the demo question set (5 questions) and schedule it for today. For local testing; replace with your own."

    def handle(self, *args, **options):
        today = clock.today()
        # Re-runnable: drop an earlier, still-unplayed demo set for today first.
        Question.objects.filter(date=today, responses__isnull=True).delete()
        path = Path(__file__).resolve().parents[2] / "fixtures" / "demo_questions.json"
        call_command("import_questions", str(path), schedule_from=today.isoformat())
        self.stdout.write(f"Demo set scheduled for {today}.")
