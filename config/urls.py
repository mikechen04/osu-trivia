from datetime import date

from django.conf import settings
from django.contrib import admin
from django.urls import path, register_converter
from django.views.generic import TemplateView

import accounts.views as accounts
import core.views as core
import game.staff_views as staff
import game.views as game


class IsoDateConverter:
    regex = r"\d{4}-\d{2}-\d{2}"

    def to_python(self, value):
        return date.fromisoformat(value)  # ValueError -> 404

    def to_url(self, value):
        return value.isoformat()


register_converter(IsoDateConverter, "isodate")

page = lambda name: TemplateView.as_view(template_name=f"legal/{name}.html")  # noqa: E731

urlpatterns = [
    path("", core.home, name="home"),
    path("play/", game.play, name="play"),
    path("leaderboard/", game.leaderboard_page, name="leaderboard"),
    path("u/<str:username>/<isodate:day>/", game.share_page, name="share"),
    path("suggest/", core.suggest, name="suggest"),
    # JSON API used by the game screen
    path("api/game/state/", game.api_state, name="api_state"),
    path("api/game/next/", game.api_next, name="api_next"),
    path("api/game/answer/", game.api_answer, name="api_answer"),
    # Auth + account
    path("login/", accounts.login_view, name="login"),
    path("auth/callback/", accounts.callback, name="oauth_callback"),
    path("logout/", accounts.logout_view, name="logout"),
    path("account/", accounts.account, name="account"),
    path("account/export/", accounts.export_data, name="export_data"),
    path("account/delete/", accounts.delete_account, name="delete_account"),
    # Static pages
    path("faq/", TemplateView.as_view(template_name="core/faq.html"), name="faq"),
    path("privacy/", page("privacy"), name="privacy"),
    path("terms/", page("terms"), name="terms"),
    path("cookies/", page("cookies"), name="cookies"),
    path("robots.txt", core.robots),
    path("healthz", core.healthz),
    # Staff dashboard (main-site chrome) + classic Django admin for deep edits
    path("staff/", staff.staff_home, name="staff_home"),
    path("staff/day/<isodate:day>/", staff.staff_day, name="staff_day"),
    path("staff/day/<isodate:day>/fill/", staff.staff_fill_day, name="staff_fill_day"),
    path("staff/schedule-tomorrow/", staff.staff_schedule_tomorrow, name="staff_schedule_tomorrow"),
    path("staff/unschedule/<int:pk>/", staff.staff_unschedule, name="staff_unschedule"),
    path("staff/suggestions/<int:pk>/", staff.staff_review_suggestion, name="staff_review_suggestion"),
    path("staff/team/", staff.staff_team, name="staff_team"),
    path("staff/team/role/", staff.staff_set_role, name="staff_set_role"),
    path(settings.ADMIN_URL, admin.site.urls),
]

if settings.DEV_LOGIN:
    urlpatterns.insert(0, path("dev-login/", accounts.dev_login, name="dev_login"))

handler404 = "core.views.not_found"
