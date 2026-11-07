from django.contrib import admin, messages
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.conf import settings

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("osu_username", "osu_id", "country_code", "is_staff", "is_public", "last_login")
    list_filter = ("is_staff", "is_public", "is_active")
    search_fields = ("osu_username", "osu_id")
    fields = ("osu_username", "osu_id", "country_code", "avatar_url", "is_active", "is_staff", "is_superuser", "is_public")
    readonly_fields = ("osu_username", "osu_id", "country_code", "avatar_url")


def admin_login(request, extra_context=None):
    """Admins sign in with osu! like everyone else (there are no site passwords)."""
    if request.user.is_authenticated:
        return HttpResponseForbidden("This account is not an admin.")
    nxt = request.GET.get("next", f"/{settings.ADMIN_URL}")
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = f"/{settings.ADMIN_URL}"
    return redirect(f"{settings.LOGIN_URL}?next={nxt}")


admin.site.login = admin_login
admin.site.site_header = "osu! trivia admin"
admin.site.site_title = "osu! trivia admin"
admin.site.index_title = "Questions, answers & moderation"
