import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from . import clock, leaderboard, scoring, services
from .matching import match_answer, normalize, osa_distance
from .models import AcceptedAnswer, Attempt, Question, Response

User = get_user_model()
T0 = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
DAY = T0.date()


def make_user(n, country="US", public=True):
    return User.objects.create(
        username=f"osu{n}", osu_id=n, osu_username=f"Player{n}", country_code=country, is_public=public
    )


def make_set(day=DAY):
    """Five questions; answers are deliberately recognisable so leaks are easy to detect."""
    specs = [
        ("Name a mod", [("Hidden", ["HD"], 1), ("Double Time", ["DT"], 2), ("Mirror", [], 5)]),
        ("Name a mode", [("Taiko", [], 2), ("Mania", [], 3)]),
        ("Name a grade", [("Silver S", ["SH"], 5), ("A", [], 1)]),
        ("Name a status", [("Ranked", [], 1), ("Loved", [], 2)]),
        ("Name a judgement", [("Great", ["300"], 1), ("Meh", ["50"], 4)]),
    ]
    questions = []
    for pos, (prompt, answers) in enumerate(specs, start=1):
        q = Question.objects.create(prompt=prompt, date=day, position=pos)
        for text, aliases, tier in answers:
            AcceptedAnswer.objects.create(question=q, text=text, aliases="\n".join(aliases), seed_tier=tier)
        questions.append(q)
    return questions


def at(moment):
    return mock.patch("game.clock.now", return_value=moment)


def play_all(user, answers, start=T0):
    """Answer every question for `user`. Returns final state."""
    moment = start
    for text in answers:
        with at(moment):
            state = services.start_next(user)
        q = state["current"]["question_id"]
        with at(moment + timedelta(seconds=5)):
            state, _ = services.submit_answer(user, q, text)
        moment += timedelta(seconds=30)
    return state


class MatchingTests(TestCase):
    def setUp(self):
        q = Question.objects.create(prompt="p")
        self.hd = AcceptedAnswer.objects.create(question=q, text="Hidden", aliases="HD")
        self.dt = AcceptedAnswer.objects.create(question=q, text="Double Time", aliases="DT")
        self.hr = AcceptedAnswer.objects.create(question=q, text="Hard Rock", aliases="HR")
        self.answers = [self.hd, self.dt, self.hr]

    def m(self, raw):
        return match_answer(raw, self.answers)[0]

    def test_normalize(self):
        self.assertEqual(normalize("  Half-Time!! "), "half time")
        self.assertEqual(normalize("Café"), "cafe")
        self.assertEqual(normalize("The  Hidden"), "hidden")

    def test_exact_alias_case_and_punctuation(self):
        self.assertEqual(self.m("hidden"), self.hd)
        self.assertEqual(self.m("HD"), self.hd)
        self.assertEqual(self.m("  h.d. "), self.hd)
        self.assertEqual(self.m("double-time"), self.dt)
        self.assertEqual(self.m("doubletime"), self.dt)

    def test_small_typos_forgiven(self):
        self.assertEqual(self.m("hiden"), self.hd)  # 1 deletion
        self.assertEqual(self.m("hidedn"), self.hd)  # swap counts as 1
        self.assertEqual(self.m("dobule time"), self.dt)

    def test_short_answers_must_be_exact(self):
        self.assertIsNone(self.m("hx"))
        self.assertIsNone(self.m("hdd"))

    def test_garbage_and_empty_are_invalid(self):
        self.assertIsNone(self.m(""))
        self.assertIsNone(self.m("   "))
        self.assertIsNone(self.m("!!!"))
        self.assertIsNone(self.m("completely wrong"))

    def test_ambiguous_typo_is_rejected(self):
        q = Question.objects.create(prompt="p2")
        a = AcceptedAnswer.objects.create(question=q, text="Cartel")
        b = AcceptedAnswer.objects.create(question=q, text="Carted")
        # "Cartes" is one edit from both, so we must not guess.
        self.assertIsNone(match_answer("Cartes", [a, b])[0])

    def test_numbers_are_exact_only(self):
        q = Question.objects.create(prompt="p3")
        a = AcceptedAnswer.objects.create(question=q, text="300")
        self.assertIsNone(match_answer("301", [a])[0])
        self.assertEqual(match_answer("300", [a])[0], a)

    def test_osa_distance_bounds(self):
        self.assertEqual(osa_distance("abc", "abc", 2), 0)
        self.assertEqual(osa_distance("ab", "ba", 2), 1)
        self.assertGreater(osa_distance("abcdef", "x", 2), 2)


class ScoringTests(TestCase):
    def test_seed_tier_used_until_enough_plays(self):
        self.assertEqual(scoring.effective_tier(4, 0, 10), 4)
        self.assertEqual(scoring.effective_tier(2, 9, 29), 2)

    def test_live_data_takes_over(self):
        # Seeded "very rare" but 60% of 500 players said it: becomes tier 1.
        self.assertEqual(scoring.effective_tier(5, 300, 500), 1)
        # Seeded "very common" but only 1% said it: becomes tier 5.
        self.assertEqual(scoring.effective_tier(1, 5, 500), 5)

    def test_blend_is_gradual(self):
        # Halfway up the ramp (165 plays): seed 5 and a ~48% share (live ≈ tier 1) meet in the middle.
        self.assertEqual(scoring.effective_tier(5, 80, 165), 3)

    def test_rarer_share_never_lowers_tier(self):
        tiers = [scoring.effective_tier(3, c, 1000) for c in (600, 200, 80, 30, 10, 2)]
        self.assertEqual(tiers, sorted(tiers))


@override_settings(RATELIMIT_ENABLED=False)
class GameFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        make_set()
        self.user = make_user(1)

    def test_not_ready_without_full_set(self):
        Question.objects.filter(position=5).delete()
        with at(T0):
            with self.assertRaises(services.GameError) as ctx:
                services.start_next(self.user)
        self.assertEqual(ctx.exception.code, "not_ready")

    def test_full_game_scores_and_finishes(self):
        state = play_all(self.user, ["hd", "taiko", "SH", "ranked", "300"])
        # seed tiers 1,2,5,1,1 -> 10+30+100+10+10
        self.assertEqual(state["score"], 160)
        self.assertTrue(state["finished"])
        attempt = Attempt.objects.get(user=self.user)
        self.assertEqual(attempt.score, 160)
        self.assertIsNotNone(attempt.finished_at)

    def test_invalid_and_empty_answers_score_zero(self):
        state = play_all(self.user, ["", "zzz", "A", "   ", "Meh"])
        self.assertEqual([a["points"] for a in state["answered"]], [0, 0, 10, 0, 85])
        self.assertEqual(state["answered"][0]["tier_name"], "Miss")

    def test_timer_is_enforced_by_server(self):
        with at(T0):
            state = services.start_next(self.user)
        qid = state["current"]["question_id"]
        # 26.0s later: inside 25s + 1.5s grace -> counts.
        with at(T0 + timedelta(seconds=26)):
            _, result = services.submit_answer(self.user, qid, "hidden")
        self.assertEqual(result["points"], 10)
        self.assertFalse(result["timed_out"])

        with at(T0 + timedelta(seconds=40)):
            state = services.start_next(self.user)
        qid2 = state["current"]["question_id"]
        # 27s later: too late. Correct answer, still zero.
        with at(T0 + timedelta(seconds=40 + 27)):
            _, result = services.submit_answer(self.user, qid2, "taiko")
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["points"], 0)
        self.assertEqual(result["answer"], "")  # a late answer is discarded, not stored

    def test_refresh_does_not_reset_timer(self):
        with at(T0):
            services.start_next(self.user)
        with at(T0 + timedelta(seconds=10)):
            state = services.start_next(self.user)  # e.g. page reload hits "next" again
            self.assertEqual(state["current"]["position"], 1)
            self.assertAlmostEqual(state["current"]["seconds_left"], 15, delta=0.1)
        self.assertEqual(Response.objects.count(), 1)

    def test_abandoned_question_times_out_when_looked_at(self):
        with at(T0):
            services.start_next(self.user)
        with at(T0 + timedelta(seconds=120)):
            state = services.get_state(self.user)
        self.assertEqual(state["status"], "between")
        self.assertTrue(state["answered"][0]["timed_out"])

    def test_answer_locks_and_cannot_be_replayed(self):
        with at(T0):
            state = services.start_next(self.user)
        qid = state["current"]["question_id"]
        with at(T0 + timedelta(seconds=3)):
            services.submit_answer(self.user, qid, "Mirror")
            with self.assertRaises(services.GameError) as ctx:
                services.submit_answer(self.user, qid, "Hidden")
        self.assertEqual(ctx.exception.status, 409)
        r = Response.objects.get(question_id=qid)
        self.assertEqual(r.raw_answer, "Mirror")
        self.assertEqual(r.points, 100)

    def test_cannot_answer_unstarted_question(self):
        last_q = Question.objects.get(position=5)
        with at(T0):
            services.start_next(self.user)
            with self.assertRaises(services.GameError) as ctx:
                services.submit_answer(self.user, last_q.pk, "Great")
        self.assertEqual(ctx.exception.code, "not_started")
        self.assertFalse(Response.objects.filter(question=last_q).exists())

    def test_one_attempt_per_user_per_day(self):
        play_all(self.user, ["hd"] * 5)
        with at(T0 + timedelta(minutes=30)):
            state = services.start_next(self.user)
        self.assertEqual(state["status"], "finished")
        self.assertEqual(Attempt.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Response.objects.count(), 5)

    def test_new_day_is_a_new_attempt(self):
        play_all(self.user, ["hd"] * 5)
        tomorrow = DAY + timedelta(days=1)
        make_set(tomorrow)
        with at(datetime(2026, 11, 8, 0, 0, 5, tzinfo=timezone.utc)):
            state = services.start_next(self.user)
        self.assertEqual(state["date"], tomorrow.isoformat())
        self.assertEqual(state["current"]["position"], 1)
        self.assertEqual(Attempt.objects.filter(user=self.user).count(), 2)

    def test_answer_length_limit(self):
        with at(T0):
            state = services.start_next(self.user)
        with at(T0 + timedelta(seconds=1)):
            with self.assertRaises(services.GameError):
                services.submit_answer(self.user, state["current"]["question_id"], "x" * 101)

    def test_reset_countdown_is_midnight_utc(self):
        self.assertEqual(clock.next_reset(T0), datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc))

    def test_live_tier_changes_with_player_data(self):
        """Once enough people said 'Hidden', it stops being a premium answer."""
        q = Question.objects.get(position=1)
        hidden = q.answers.get(text="Hidden")
        mirror = q.answers.get(text="Mirror")
        for i in range(100, 100 + 320):
            u = make_user(i)
            a = Attempt.objects.create(user=u, date=DAY - timedelta(days=3))
            Response.objects.create(
                attempt=a, question=q, position=1, served_at=T0, answered_at=T0,
                matched=hidden if i % 10 else mirror, tier=1, points=10,
            )
        counts, total = scoring.answer_counts(q.pk)
        self.assertEqual(total, 320)
        # Seeded tier 1 stays 1 (90% share); seeded-rare Mirror (10%) lands mid-tier, not 5.
        self.assertEqual(scoring.effective_tier(1, counts[hidden.pk], total), 1)
        self.assertLess(scoring.effective_tier(5, counts[mirror.pk], total), 5)


@override_settings(RATELIMIT_ENABLED=False)
class LeakTests(TestCase):
    """Accepted answers and tiers must never reach the client before it answers."""

    def setUp(self):
        cache.clear()
        make_set()
        self.user = make_user(1)
        self.client = Client()
        self.client.force_login(self.user)

    def test_api_never_exposes_answer_list_or_tiers_before_answering(self):
        with at(T0):
            self.client.post("/api/game/next/", content_type="application/json", data="{}")
            res = self.client.get("/api/game/state/")
        body = res.content.decode()
        for secret in ["Hidden", "Double Time", "Mirror", "seed_tier", "aliases", "match_keys", '"HD"']:
            self.assertNotIn(secret, body)
        data = res.json()
        self.assertEqual(set(data["current"]), {"question_id", "position", "prompt", "hint", "seconds_left"})
        self.assertEqual(data["answered"], [])
        self.assertNotIn("breakdown", data)

    def test_next_question_prompt_is_not_sent_until_started(self):
        with at(T0):
            self.client.post("/api/game/next/", content_type="application/json", data="{}")
            body = self.client.get("/api/game/state/").content.decode()
        self.assertIn("Name a mod", body)
        self.assertNotIn("Name a mode", body)

    def test_breakdown_only_after_finishing(self):
        with at(T0):
            self.client.post("/api/game/next/", content_type="application/json", data="{}")
            qid = self.client.get("/api/game/state/").json()["current"]["question_id"]
        with at(T0 + timedelta(seconds=2)):
            res = self.client.post("/api/game/answer/", data=json.dumps({"question_id": qid, "answer": "hd"}), content_type="application/json")
        self.assertNotIn("breakdown", res.json())
        self.assertEqual(res.json()["result"]["matched"], "Hidden")

    def test_share_text_has_no_answers(self):
        state = play_all(self.user, ["hd", "taiko", "SH", "ranked", "300"])
        text = state["share"]["text"]
        self.assertIn("160/500", text)
        self.assertIn("🟦🟩🟪🟦🟦", text)
        self.assertIn("/u/Player1/2026-11-07/", text)
        for word in ["Hidden", "Taiko", "Silver", "Ranked", "Great", "hd", "300"]:
            self.assertNotIn(word, text)


class ApiSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        make_set()
        self.user = make_user(1)

    def test_requires_login(self):
        for method, url in [("get", "/api/game/state/"), ("post", "/api/game/next/"), ("post", "/api/game/answer/")]:
            res = getattr(Client(), method)(url)
            self.assertEqual(res.status_code, 401, url)

    def test_post_endpoints_enforce_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post("/api/game/next/", data="{}", content_type="application/json").status_code, 403)

    def test_method_checks_and_malformed_bodies(self):
        client = Client()
        client.force_login(self.user)
        self.assertEqual(client.get("/api/game/next/").status_code, 405)
        with at(T0):
            client.post("/api/game/next/", data="{}", content_type="application/json")
            self.assertEqual(client.post("/api/game/answer/", data="not json", content_type="application/json").status_code, 400)
            self.assertEqual(client.post("/api/game/answer/", data=json.dumps({"question_id": "1"}), content_type="application/json").status_code, 400)
            self.assertEqual(client.post("/api/game/answer/", data=json.dumps({"question_id": 1, "answer": ["x"]}), content_type="application/json").status_code, 400)
            self.assertEqual(client.post("/api/game/answer/", data="x" * 5000, content_type="application/json").status_code, 413)

    def test_rate_limited_per_user(self):
        client = Client()
        client.force_login(self.user)
        codes = []
        with at(T0):
            for _ in range(35):
                codes.append(client.post("/api/game/next/", data="{}", content_type="application/json").status_code)
        self.assertIn(429, codes)
        self.assertEqual(codes[0], 200)

    def test_rate_limited_per_ip_even_across_users(self):
        codes = []
        with at(T0):
            for i in range(95):
                u = make_user(500 + i)
                c = Client()
                c.force_login(u)
                codes.append(c.post("/api/game/next/", data="{}", content_type="application/json").status_code)
        self.assertIn(429, codes)


@override_settings(RATELIMIT_ENABLED=False)
class LeaderboardTests(TestCase):
    def setUp(self):
        cache.clear()
        make_set()
        make_set(DAY - timedelta(days=1))
        make_set(DAY - timedelta(days=10))

    def finish(self, user, score_answers, when):
        play_all(user, score_answers, start=when)

    def test_ranking_ties_country_visibility_and_unfinished(self):
        a, b, c, hidden = make_user(1, "US"), make_user(2, "CA"), make_user(3, "US"), make_user(4, "US", public=False)
        best = ["Mirror", "Mania", "SH", "Loved", "Meh"]  # 100+60+100+30+85 = 375
        ok = ["hd", "taiko", "SH", "ranked", "300"]  # 160
        self.finish(a, best, T0)
        self.finish(b, ok, T0)
        self.finish(c, ok, T0)
        self.finish(hidden, best, T0)
        # d starts but never finishes today
        d = make_user(5)
        with at(T0):
            services.start_next(d)

        with at(T0 + timedelta(hours=1)):
            rows = leaderboard.build("daily", day=DAY, me=None)["rows"]
            self.assertEqual([r.user.osu_id for r in rows], [1, 2, 3])
            self.assertEqual([r.rank for r in rows], [1, 2, 2])  # ties share a rank

            only_ca = leaderboard.build("daily", day=DAY, country="CA")["rows"]
            self.assertEqual([r.user.osu_id for r in only_ca], [2])
            self.assertEqual(leaderboard.countries(), ["CA", "US"])

            mine = leaderboard.build("daily", day=DAY, me=c)
            self.assertTrue([r for r in mine["rows"] if r.is_me])  # highlighted
            self.assertIsNone(mine["my_row"])

    def test_weekly_and_alltime_sum_days(self):
        a = make_user(1)
        ok = ["hd", "taiko", "SH", "ranked", "300"]
        self.finish(a, ok, T0)  # today, 160
        self.finish(a, ok, T0 - timedelta(days=1))  # yesterday (same ISO week: Nov 7 2026 is a Saturday)
        self.finish(a, ok, T0 - timedelta(days=10))  # previous week
        with at(T0 + timedelta(hours=1)):
            weekly = leaderboard.build("weekly", day=DAY)["rows"]
            alltime = leaderboard.build("alltime", day=DAY)["rows"]
            self.assertEqual(weekly[0].score, 320)
            self.assertEqual(alltime[0].score, 480)
            self.assertEqual(alltime[0].days, 3)

    def test_users_outside_top_get_their_own_row(self):
        me = make_user(1)
        ok = ["hd", "taiko", "SH", "ranked", "300"]
        self.finish(me, ok, T0)
        with mock.patch.object(leaderboard, "LIMIT", 1):
            for i in range(2, 5):
                self.finish(make_user(i), ["Mirror", "Mania", "SH", "Loved", "Meh"], T0)
            with at(T0 + timedelta(hours=1)):
                data = leaderboard.build("daily", day=DAY, me=me)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["my_row"].rank, 4)
        self.assertTrue(data["my_row"].is_me)

    def test_leaderboard_page_renders_and_highlights(self):
        me = make_user(1, "US")
        self.finish(me, ["hd"] * 5, T0)
        client = Client()
        client.force_login(me)
        with at(T0 + timedelta(hours=1)):
            res = client.get("/leaderboard/?tab=daily&country=us")
        self.assertContains(res, "is-me")
        self.assertContains(res, "Player1")
        with at(T0 + timedelta(hours=1)):
            self.assertEqual(client.get("/leaderboard/?tab=bogus&country=%3Cscript%3E&date=nope").status_code, 200)


@override_settings(RATELIMIT_ENABLED=False)
class SharePageTests(TestCase):
    def setUp(self):
        make_set()
        self.user = make_user(1, "US")

    def test_share_page_has_og_tags_and_no_answers(self):
        play_all(self.user, ["hd", "taiko", "SH", "ranked", "300"])
        with at(T0 + timedelta(hours=1)):
            res = Client().get("/u/Player1/2026-11-07/")
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn('property="og:title" content="Player1 scored 160/500 on osu! trivia"', html)
        self.assertIn("🟦🟩🟪🟦🟦", html)
        self.assertIn("/u/Player1/2026-11-07/", html)
        html = html.replace("Veiny Zany", "")  # a tier name, not an answer
        for word in ["Hidden", "Taiko", "Silver S", "Ranked", "Great"]:
            self.assertNotIn(word, html)

    def test_lookup_is_case_insensitive_and_falls_back_to_id(self):
        play_all(self.user, ["hd"] * 5)
        with at(T0 + timedelta(hours=1)):
            self.assertEqual(Client().get("/u/player1/2026-11-07/").status_code, 200)
            self.assertEqual(Client().get("/u/1/2026-11-07/").status_code, 200)

    def test_404s(self):
        with at(T0):
            services.start_next(self.user)  # in progress today
            self.assertEqual(Client().get("/u/Player1/2026-11-07/").status_code, 404)  # not finished
            self.assertEqual(Client().get("/u/Nobody/2026-11-07/").status_code, 404)
            self.assertEqual(Client().get("/u/Player1/2026-13-45/").status_code, 404)  # bad date

    def test_hidden_players_have_no_public_page(self):
        play_all(self.user, ["hd"] * 5)
        self.user.is_public = False
        self.user.save()
        with at(T0 + timedelta(hours=1)):
            self.assertEqual(Client().get("/u/Player1/2026-11-07/").status_code, 404)

    def test_abandoned_attempt_counts_after_the_day_ends(self):
        with at(T0):
            services.start_next(self.user)
        with at(T0 + timedelta(days=1)):
            self.assertEqual(Client().get("/u/Player1/2026-11-07/").status_code, 200)
