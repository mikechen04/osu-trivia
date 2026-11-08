from datetime import timedelta

from django.test import Client, TestCase, override_settings

from core.models import Suggestion

from . import clock, scheduling
from .models import AcceptedAnswer, Question
from .tests import T0, at, make_set, make_user


@override_settings(RATELIMIT_ENABLED=False)
class StaffDashboardTests(TestCase):
    def setUp(self):
        self.staff = make_user(9)
        self.staff.is_staff = self.staff.is_superuser = True
        self.staff.save()
        self.client = Client()
        self.client.force_login(self.staff)
        make_set()  # today's 5 questions

    def test_non_staff_redirected(self):
        c = Client()
        c.force_login(make_user(2))
        res = c.get("/staff/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login/", res["Location"])

    def test_home_shows_today_and_drafts(self):
        with at(T0):
            draft = Question.objects.create(prompt="draft prompt for staff")
            AcceptedAnswer.objects.create(question=draft, text="Answer", seed_tier=2)
            Suggestion.objects.create(user=make_user(3), text="pending idea")
            res = self.client.get("/staff/")
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn("Staff", html)
        self.assertIn("draft prompt for staff", html)
        self.assertIn("Name a mod", html)

    def test_day_shows_rarities(self):
        with at(T0):
            day = clock.today()
            res = self.client.get(f"/staff/day/{day.isoformat()}/")
        self.assertEqual(res.status_code, 200)
        html = res.content.decode().lower()
        self.assertIn("seed", html)
        self.assertIn("live", html)

    def test_schedule_tomorrow_from_drafts(self):
        with at(T0):
            tomorrow = clock.today() + timedelta(days=1)
            # Clear any questions already on tomorrow
            Question.objects.filter(date=tomorrow).update(date=None, position=None)
            drafts = []
            for i in range(3):
                q = Question.objects.create(prompt=f"tmrw draft {i}")
                AcceptedAnswer.objects.create(question=q, text=f"A{i}", seed_tier=3)
                drafts.append(q.pk)
            res = self.client.post("/staff/schedule-tomorrow/", {"draft_ids": drafts})
            self.assertEqual(res.status_code, 302)
            scheduled = list(Question.objects.filter(date=tomorrow).order_by("position"))
            self.assertEqual(len(scheduled), 3)
            self.assertEqual([q.position for q in scheduled], [1, 2, 3])

    def test_fill_day_auto(self):
        with at(T0):
            day = clock.today() + timedelta(days=2)
            for i in range(5):
                q = Question.objects.create(prompt=f"auto {i}")
                AcceptedAnswer.objects.create(question=q, text=f"X{i}")
            res = self.client.post(f"/staff/day/{day.isoformat()}/fill/", {"mode": "auto"})
            self.assertEqual(res.status_code, 302)
            self.assertTrue(scheduling.day_ready(day))

    def test_unschedule(self):
        with at(T0):
            day = clock.today()
            q = Question.objects.filter(date=day, position=1).get()
            res = self.client.post(f"/staff/unschedule/{q.pk}/")
            self.assertEqual(res.status_code, 302)
            q.refresh_from_db()
            self.assertIsNone(q.date)
            self.assertIsNone(q.position)

    def test_csp_clean(self):
        with at(T0):
            html = self.client.get("/staff/").content.decode()
            day = clock.today()
            html += self.client.get(f"/staff/day/{day.isoformat()}/").content.decode()
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
        self.assertNotRegex(html, r"\son[a-z]+=\"")
        self.assertNotRegex(html, r"\sstyle=\"")
