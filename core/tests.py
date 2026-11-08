import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings

from game.models import AcceptedAnswer, Question
from game.tests import T0, at, make_set, make_user

from .models import Suggestion
from .ratelimit import client_ip

User = get_user_model()
PAGES = ["/", "/faq/", "/privacy/", "/terms/", "/cookies/", "/leaderboard/", "/login/"]


@override_settings(RATELIMIT_ENABLED=False)
class PageTests(TestCase):
    def setUp(self):
        make_set()

    def test_public_pages_render_and_are_linked_in_footer(self):
        home = Client().get("/")
        for url in ["/faq/", "/privacy/", "/terms/", "/cookies/"]:
            self.assertContains(home, f'href="{url}"')
            self.assertEqual(Client().get(url).status_code, 200)

    def test_legal_pages_show_marked_placeholders(self):
        for url in ["/privacy/", "/terms/", "/cookies/"]:
            html = Client().get(url).content.decode()
            self.assertIn('<mark class="placeholder">[EFFECTIVE DATE]</mark>', html)
        self.assertIn('<mark class="placeholder">[CONTACT EMAIL]</mark>', Client().get("/privacy/").content.decode())
        self.assertIn('<mark class="placeholder">[JURISDICTION]</mark>', Client().get("/terms/").content.decode())
        self.assertIn("[CONTACT EMAIL]", Client().get("/faq/").content.decode())

    @override_settings(CONTACT_EMAIL="hi@example.com", JURISDICTION="Ontario, Canada", EFFECTIVE_DATE="2026-12-01")
    def test_placeholders_can_be_filled_from_settings(self):
        html = Client().get("/terms/").content.decode()
        self.assertIn("Ontario, Canada", html)
        self.assertNotIn("[JURISDICTION]", html)

    def test_legal_pages_cover_required_topics(self):
        privacy = Client().get("/privacy/").content.decode().lower()
        for topic in ["gdpr", "ccpa", "export my data", "delete my account", "at least 13", "how long we keep", "osu! user id"]:
            self.assertIn(topic, privacy)
        terms = Client().get("/terms/").content.decode().lower()
        for topic in ["not affiliated", "acceptable use", "licence", "as is", "limitation of liability", "governing law", "13"]:
            self.assertIn(topic, terms)

    def test_cookie_banner_and_no_third_party_resources(self):
        html = Client().get("/").content.decode()
        self.assertIn('id="cookie-banner"', html)
        hosts = set(re.findall(r'(?:src|href)="(https?://[^/"]+)', html))
        self.assertEqual(hosts - {"https://osu.ppy.sh"}, set())

    def test_security_headers(self):
        res = Client().get("/")
        csp = res["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(res["X-Frame-Options"], "DENY")
        self.assertEqual(res["X-Content-Type-Options"], "nosniff")

    def test_templates_are_csp_clean(self):
        """No inline <script>, inline event handlers or style="" attributes (the CSP would block them)."""
        user = make_user(1)
        c = Client()
        c.force_login(user)
        for url in PAGES + ["/play/", "/suggest/", "/account/"]:
            html = c.get(url).content.decode()
            self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)", url)
            self.assertNotRegex(html, r"\son[a-z]+=\"", url)
            self.assertNotRegex(html, r"\sstyle=\"", url)

    def test_404_page(self):
        res = Client().get("/nope/")
        self.assertEqual(res.status_code, 404)
        self.assertContains(res, "Page not found", status_code=404)

    def test_robots_hides_admin_and_api(self):
        body = Client().get("/robots.txt").content.decode()
        self.assertIn("Disallow: /admin/", body)
        self.assertIn("Disallow: /staff/", body)
        self.assertIn("Disallow: /api/", body)

    def test_home_cta_follows_state(self):
        with at(T0):
            self.assertContains(Client().get("/"), "Sign in with osu! to play")
            c = Client()
            c.force_login(make_user(1))
            self.assertContains(c.get("/"), "Play today’s set")


class SuggestionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user(1)
        self.client = Client()
        self.client.force_login(self.user)

    def post(self, text="Name a thing you can do in the osu! editor", examples="timing\nbookmarks"):
        return self.client.post("/suggest/", {"text": text, "examples": examples})

    def test_requires_login(self):
        self.assertEqual(Client().get("/suggest/").status_code, 302)
        self.assertEqual(Client().post("/suggest/", {"text": "x" * 20}).status_code, 302)

    def test_goes_to_pending_queue_and_never_publishes(self):
        self.assertEqual(self.post().status_code, 302)
        s = Suggestion.objects.get()
        self.assertEqual((s.status, s.user), ("pending", self.user))
        self.assertEqual(s.example_list(), ["timing", "bookmarks"])
        self.assertFalse(Question.objects.exists())

    def test_validation(self):
        self.assertEqual(self.post(text="too short").status_code, 200)
        self.assertEqual(self.post(examples="\n".join(f"a{i}" for i in range(11))).status_code, 200)
        self.assertEqual(self.post(examples="x" * 81).status_code, 200)
        self.assertFalse(Suggestion.objects.exists())

    def test_daily_limit_per_user(self):
        for i in range(3):
            self.assertEqual(self.post(text=f"Name a thing number {i} in osu!").status_code, 302)
        res = self.post(text="Name a fourth thing in osu!")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Suggestion.objects.count(), 3)
        # ...and it resets after 24h
        Suggestion.objects.update(created_at=T0 - timedelta(days=2))
        self.assertEqual(self.post(text="Name a fifth thing in osu!").status_code, 302)

    @override_settings(SUGGESTIONS_PER_DAY=1000)
    def test_ip_rate_limit(self):
        codes = [self.post(text=f"Name osu! thing number {i}").status_code for i in range(12)]
        self.assertEqual(Suggestion.objects.count(), 10)
        self.assertEqual(codes[:10], [302] * 10)

    def test_html_is_escaped_on_display(self):
        self.post(text="<script>alert(1)</script> is this a question?")
        self.assertNotContains(self.client.get("/suggest/"), "<script>alert(1)")


class ModerationTests(TestCase):
    def setUp(self):
        self.staff = make_user(9)
        self.staff.is_staff = self.staff.is_superuser = True
        self.staff.save()
        self.client = Client()
        self.client.force_login(self.staff)
        self.s1 = Suggestion.objects.create(user=make_user(1), text="Name a skin element in osu!", examples="cursor\nhitcircle")
        self.s2 = Suggestion.objects.create(user=make_user(2), text="Name a thing nobody likes?")

    def test_approve_creates_unscheduled_draft_only(self):
        res = self.client.post("/admin/core/suggestion/", {"action": "approve", "_selected_action": [self.s1.pk]}, follow=True)
        self.assertEqual(res.status_code, 200)
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.status, "approved")
        q = self.s1.created_question
        self.assertIsNone(q.date)  # never auto-published
        self.assertIsNone(q.position)
        self.assertEqual(sorted(a.text for a in q.answers.all()), ["cursor", "hitcircle"])

    def test_reject(self):
        self.client.post("/admin/core/suggestion/", {"action": "reject", "_selected_action": [self.s2.pk]})
        self.s2.refresh_from_db()
        self.assertEqual(self.s2.status, "rejected")
        self.assertEqual(Question.objects.count(), 0)

    def test_edit_in_admin(self):
        res = self.client.get(f"/admin/core/suggestion/{self.s1.pk}/change/")
        self.assertEqual(res.status_code, 200)
        res = self.client.post(
            f"/admin/core/suggestion/{self.s1.pk}/change/",
            {"status": "pending", "text": "Reworded question for osu!", "examples": "cursor", "admin_note": "ok"},
        )
        self.assertEqual(res.status_code, 302)
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.text, "Reworded question for osu!")

    def test_non_staff_cannot_moderate(self):
        c = Client()
        c.force_login(make_user(5))
        self.assertEqual(c.get("/admin/core/suggestion/", follow=True).status_code, 403)
        res = c.post("/admin/core/suggestion/", {"action": "approve", "_selected_action": [self.s1.pk]}, follow=True)
        self.assertEqual(res.status_code, 403)
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.status, "pending")


class AdminQuestionTests(TestCase):
    def setUp(self):
        self.staff = make_user(9)
        self.staff.is_staff = self.staff.is_superuser = True
        self.staff.save()
        self.client = Client()
        self.client.force_login(self.staff)

    def test_add_question_with_answers_aliases_and_tiers(self):
        data = {
            "prompt": "Name a game mode", "hint": "", "date": "2026-12-01", "position": "1", "notes": "",
            "answers-TOTAL_FORMS": "2", "answers-INITIAL_FORMS": "0", "answers-MIN_NUM_FORMS": "0", "answers-MAX_NUM_FORMS": "1000",
            "answers-0-text": "Taiko", "answers-0-aliases": "drums\nosu!taiko", "answers-0-seed_tier": "2",
            "answers-1-text": "Mania", "answers-1-aliases": "", "answers-1-seed_tier": "4",
        }
        res = self.client.post("/admin/game/question/add/", data)
        self.assertEqual(res.status_code, 302, getattr(res, "context", None) and res.context["adminform"].errors)
        q = Question.objects.get()
        taiko = q.answers.get(text="Taiko")
        self.assertEqual(taiko.seed_tier, 2)
        self.assertEqual(taiko.alias_list(), ["drums", "osu!taiko"])
        self.assertIn("osutaiko", taiko.match_keys)
        self.assertIn("taiko", taiko.match_keys)

    def test_position_required_when_scheduled(self):
        res = self.client.post("/admin/game/question/add/", {
            "prompt": "x question", "date": "2026-12-01", "position": "", "hint": "", "notes": "",
            "answers-TOTAL_FORMS": "0", "answers-INITIAL_FORMS": "0", "answers-MIN_NUM_FORMS": "0", "answers-MAX_NUM_FORMS": "1000",
        })
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Question.objects.exists())

    def test_one_question_per_slot(self):
        Question.objects.create(prompt="a", date="2026-12-01", position=1)
        res = self.client.post("/admin/game/question/add/", {
            "prompt": "b question", "date": "2026-12-01", "position": "1", "hint": "", "notes": "",
            "answers-TOTAL_FORMS": "0", "answers-INITIAL_FORMS": "0", "answers-MIN_NUM_FORMS": "0", "answers-MAX_NUM_FORMS": "1000",
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Question.objects.count(), 1)

    def test_schedule_action_fills_next_open_slots(self):
        drafts = []
        for i in range(7):
            q = Question.objects.create(prompt=f"draft {i}")
            AcceptedAnswer.objects.create(question=q, text="x")
            drafts.append(q.pk)
        empty = Question.objects.create(prompt="no answers")  # skipped: nothing accepted yet
        self.client.post("/admin/game/question/", {"action": "schedule_next_open_slots", "_selected_action": drafts + [empty.pk]})
        slots = sorted(Question.objects.filter(date__isnull=False).values_list("date", "position"))
        self.assertEqual(len(slots), 7)
        self.assertEqual([p for _, p in slots[:5]], [1, 2, 3, 4, 5])
        self.assertEqual(len({d for d, _ in slots}), 2)
        self.assertIsNone(Question.objects.get(pk=empty.pk).date)


class ClientIpTests(TestCase):
    def req(self, xff=None, addr="10.0.0.1"):
        meta = {"REMOTE_ADDR": addr}
        if xff:
            meta["HTTP_X_FORWARDED_FOR"] = xff
        return RequestFactory().get("/", **meta)

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_no_proxy_ignores_forwarded_header(self):
        self.assertEqual(client_ip(self.req("1.2.3.4")), "10.0.0.1")

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_one_proxy_uses_last_entry_so_spoofing_does_not_work(self):
        self.assertEqual(client_ip(self.req("6.6.6.6, 203.0.113.9")), "203.0.113.9")

    @override_settings(TRUSTED_PROXY_COUNT=2)
    def test_two_proxies(self):
        self.assertEqual(client_ip(self.req("6.6.6.6, 203.0.113.9, 35.1.1.1")), "203.0.113.9")

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_missing_header_falls_back(self):
        self.assertEqual(client_ip(self.req()), "10.0.0.1")
