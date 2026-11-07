import json
from datetime import datetime, timezone
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from core.models import Suggestion
from game import services
from game.models import Attempt
from game.tests import T0, at, make_set, make_user

from . import osu

User = get_user_model()
OSU = override_settings(OSU_CLIENT_ID="cid-123", OSU_CLIENT_SECRET="s3cret-value", RATELIMIT_ENABLED=False)
PROFILE = {"osu_id": 777, "username": "Cookiezi", "avatar_url": "https://a.ppy.sh/777", "country_code": "KR"}


class ProfileParsingTests(TestCase):
    def test_clean_profile(self):
        p = osu.parse_profile({"id": 5, "username": " Bob ", "avatar_url": "https://a.ppy.sh/5?x=1", "country_code": "ca"})
        self.assertEqual(p, {"osu_id": 5, "username": "Bob", "avatar_url": "https://a.ppy.sh/5?x=1", "country_code": "CA"})

    def test_untrusted_fields_are_sanitised(self):
        p = osu.parse_profile({"id": 5, "username": "Bob", "avatar_url": "https://evil.example/x.png", "country_code": "<script>"})
        self.assertEqual((p["avatar_url"], p["country_code"]), ("", ""))
        self.assertEqual(osu.parse_profile({"id": 5, "username": "B", "avatar_url": "http://a.ppy.sh/5"})["avatar_url"], "")

    def test_bad_profiles_rejected(self):
        for bad in [None, {}, {"id": "5", "username": "x"}, {"id": True, "username": "x"}, {"id": 5, "username": ""}, {"id": -1, "username": "x"}]:
            with self.assertRaises(ValueError):
                osu.parse_profile(bad)


@OSU
class LoginFlowTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_login_requires_age_and_terms_checkbox(self):
        res = self.client.post("/login/", {"next": "/play/"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "at least 13")
        self.assertNotIn("osu_state", self.client.session)

    def test_login_redirects_to_osu_with_identify_scope_and_state(self):
        res = self.client.post("/login/", {"agree": "on", "next": "/leaderboard/"})
        self.assertEqual(res.status_code, 302)
        url = urlparse(res["Location"])
        q = parse_qs(url.query)
        self.assertEqual((url.netloc, url.path), ("osu.ppy.sh", "/oauth/authorize"))
        self.assertEqual(q["scope"], ["identify"])
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["client_id"], ["cid-123"])
        self.assertTrue(q["redirect_uri"][0].endswith("/auth/callback/"))
        self.assertEqual(q["state"], [self.client.session["osu_state"]])
        self.assertNotIn("s3cret-value", res["Location"])  # secret never goes to the browser

    def test_client_secret_never_rendered(self):
        for path in ["/", "/login/", "/faq/", "/privacy/"]:
            self.assertNotContains(self.client.get(path), "s3cret-value")

    def _begin(self, nxt="/play/"):
        self.client.post("/login/", {"agree": "on", "next": nxt})
        return self.client.session["osu_state"]

    @mock.patch("accounts.osu.fetch_profile", return_value=PROFILE)
    @mock.patch("accounts.osu.exchange_code", return_value="tok")
    def test_callback_creates_user_and_logs_in(self, exchange, fetch):
        state = self._begin("/leaderboard/")
        res = self.client.get("/auth/callback/", {"code": "abc", "state": state})
        self.assertRedirects(res, "/leaderboard/", fetch_redirect_response=False)
        user = User.objects.get(osu_id=777)
        self.assertEqual((user.osu_username, user.country_code, user.avatar_url), ("Cookiezi", "KR", "https://a.ppy.sh/777"))
        self.assertFalse(user.has_usable_password())
        self.assertIsNotNone(user.tos_accepted_at)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        exchange.assert_called_once_with("abc")
        # the state is one-time use
        res = self.client.get("/auth/callback/", {"code": "abc", "state": state})
        self.assertRedirects(res, "/login/", fetch_redirect_response=False)

    @mock.patch("accounts.osu.exchange_code")
    def test_state_mismatch_is_rejected(self, exchange):
        self._begin()
        res = self.client.get("/auth/callback/", {"code": "abc", "state": "forged"})
        self.assertRedirects(res, "/login/", fetch_redirect_response=False)
        self.assertFalse(User.objects.exists())
        exchange.assert_not_called()

    @mock.patch("accounts.osu.exchange_code")
    def test_callback_without_prior_login_attempt_is_rejected(self, exchange):
        res = self.client.get("/auth/callback/", {"code": "abc", "state": "anything"})
        self.assertRedirects(res, "/login/", fetch_redirect_response=False)
        exchange.assert_not_called()

    def test_user_denied_access(self):
        state = self._begin()
        res = self.client.get("/auth/callback/", {"error": "access_denied", "state": state})
        self.assertRedirects(res, "/login/", fetch_redirect_response=False)
        self.assertFalse(User.objects.exists())

    @mock.patch("accounts.osu.exchange_code", side_effect=osu.OsuError("boom"))
    def test_osu_failure_is_handled(self, _):
        state = self._begin()
        res = self.client.get("/auth/callback/", {"code": "abc", "state": state})
        self.assertRedirects(res, "/login/", fetch_redirect_response=False)
        self.assertFalse(User.objects.exists())

    @mock.patch("accounts.osu.fetch_profile", return_value=PROFILE)
    @mock.patch("accounts.osu.exchange_code", return_value="tok")
    def test_open_redirect_is_blocked(self, *_):
        state = self._begin("https://evil.example/phish")
        res = self.client.get("/auth/callback/", {"code": "abc", "state": state})
        self.assertRedirects(res, "/play/", fetch_redirect_response=False)

    @mock.patch("accounts.osu.fetch_profile", return_value=PROFILE)
    @mock.patch("accounts.osu.exchange_code", return_value="tok")
    def test_returning_user_profile_is_refreshed_and_admin_ids_work(self, *_):
        User.objects.create(username="osu777", osu_id=777, osu_username="OldName", country_code="US")
        with override_settings(ADMIN_OSU_IDS={777}):
            state = self._begin()
            self.client.get("/auth/callback/", {"code": "abc", "state": state})
        user = User.objects.get(osu_id=777)
        self.assertEqual((user.osu_username, user.country_code), ("Cookiezi", "KR"))
        self.assertTrue(user.is_staff and user.is_superuser)
        self.assertEqual(User.objects.count(), 1)

    def test_play_requires_login(self):
        res = self.client.get("/play/")
        self.assertRedirects(res, "/login/?next=/play/", fetch_redirect_response=False)

    def test_logout_is_post_only(self):
        user = make_user(1)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        self.client.post("/logout/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_dev_login_does_not_exist_outside_debug(self):
        self.assertEqual(self.client.get("/dev-login/").status_code, 404)

    def test_admin_requires_staff_and_uses_osu_login(self):
        res = self.client.get("/admin/", follow=False)
        self.assertEqual(res.status_code, 302)
        res = self.client.get(res["Location"])
        self.assertEqual(res.status_code, 302)
        self.assertTrue(res["Location"].startswith("/login/"))
        user = make_user(1)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin/login/").status_code, 403)  # not an infinite redirect loop


@override_settings(RATELIMIT_ENABLED=False)
class AccountPrivacyTests(TestCase):
    def setUp(self):
        make_set()
        self.user = make_user(1)
        self.client = Client()
        self.client.force_login(self.user)

    def _play(self):
        from game.tests import play_all

        play_all(self.user, ["hd", "taiko", "SH", "ranked", "300"])

    def test_export_contains_my_data(self):
        self._play()
        Suggestion.objects.create(user=self.user, text="Name a skin element", examples="cursor")
        res = self.client.get("/account/export/")
        self.assertEqual(res["Content-Type"], "application/json")
        self.assertIn("attachment", res["Content-Disposition"])
        data = json.loads(res.content)
        self.assertEqual(data["profile"]["osu_id"], 1)
        self.assertEqual(data["attempts"][0]["score"], 160)
        self.assertEqual(data["attempts"][0]["responses"][0]["your_answer"], "hd")
        self.assertEqual(data["suggestions"][0]["examples"], ["cursor"])

    def test_export_requires_login(self):
        self.assertEqual(Client().get("/account/export/").status_code, 302)

    def test_delete_requires_confirmation_then_removes_everything(self):
        self._play()
        approved = Suggestion.objects.create(user=self.user, text="Approved one", status="approved")
        pending = Suggestion.objects.create(user=self.user, text="Pending one")
        self.client.post("/account/delete/", {"confirm": "nope"})
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

        res = self.client.post("/account/delete/", {"confirm": "DELETE"})
        self.assertEqual(res.status_code, 302)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Attempt.objects.exists())
        self.assertFalse(Suggestion.objects.filter(pk=pending.pk).exists())
        approved.refresh_from_db()
        self.assertIsNone(approved.user)  # anonymised, not deleted
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_delete_is_post_only(self):
        self.assertEqual(self.client.get("/account/delete/").status_code, 405)

    def test_visibility_toggle_hides_from_leaderboards_and_share_pages(self):
        self._play()
        with at(T0.replace(hour=13)):
            self.assertEqual(Client().get("/u/Player1/2026-11-07/").status_code, 200)
            self.client.post("/account/", {})  # unchecked checkbox => hidden
            self.assertEqual(Client().get("/u/Player1/2026-11-07/").status_code, 404)
            self.assertNotContains(Client().get("/leaderboard/"), "Player1")
