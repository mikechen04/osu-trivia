from django.test import Client, TestCase, override_settings

from accounts import roles
from core.models import Suggestion
from game.tests import make_user


@override_settings(RATELIMIT_ENABLED=False)
class RoleTests(TestCase):
    def test_set_role_mod_and_admin(self):
        mod = make_user(10)
        roles.set_role(mod, roles.ROLE_MODERATOR)
        mod.refresh_from_db()
        self.assertTrue(mod.is_staff and not mod.is_superuser)
        self.assertEqual(mod.role, "moderator")
        self.assertTrue(mod.is_moderator)

        admin = make_user(11)
        roles.set_role(admin, roles.ROLE_ADMIN)
        admin.refresh_from_db()
        self.assertTrue(admin.is_site_admin)
        self.assertTrue(admin.is_moderator)

    def test_mod_can_use_staff_but_not_team(self):
        mod = make_user(12)
        roles.set_role(mod, roles.ROLE_MODERATOR)
        c = Client()
        c.force_login(mod)
        self.assertEqual(c.get("/staff/").status_code, 200)
        res = c.get("/staff/team/")
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res["Location"], "/staff/")

    def test_admin_can_promote_mod_and_mod_can_review(self):
        admin = make_user(13)
        roles.set_role(admin, roles.ROLE_ADMIN)
        player = make_user(14)
        c = Client()
        c.force_login(admin)
        res = c.post("/staff/team/role/", {"who": str(player.osu_id), "role": "moderator"})
        self.assertEqual(res.status_code, 302)
        player.refresh_from_db()
        self.assertEqual(player.role, "moderator")

        Suggestion.objects.create(user=make_user(15), text="Name a fruit in catch?")
        c2 = Client()
        c2.force_login(player)
        pk = Suggestion.objects.get().pk
        res = c2.post(f"/staff/suggestions/{pk}/", {"action": "approve"})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(Suggestion.objects.get().status, "approved")

    def test_bootstrap_admin_cannot_be_demoted(self):
        admin = make_user(16)
        roles.set_role(admin, roles.ROLE_ADMIN)
        bootstrap = make_user(9632648)
        roles.set_role(bootstrap, roles.ROLE_ADMIN)
        c = Client()
        c.force_login(admin)
        with override_settings(ADMIN_OSU_IDS={9632648}):
            res = c.post("/staff/team/role/", {"who": "9632648", "role": "player"})
        self.assertEqual(res.status_code, 302)
        bootstrap.refresh_from_db()
        self.assertTrue(bootstrap.is_site_admin)
