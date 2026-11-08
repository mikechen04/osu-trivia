from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import Group
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from . import roles
from .models import User

# Hide Django's built-in Groups page — roles are managed on Staff → Team.
try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("osu_username", "osu_id", "country_code", "role_label", "is_public", "last_login")
    list_filter = ("is_staff", "is_superuser", "is_public")
    search_fields = ("osu_username", "osu_id")
    ordering = ("osu_username",)
    list_per_page = 50
    actions = None

    fieldsets = (
        (
            "osu! profile",
            {
                "fields": ("osu_username", "osu_id", "country_code", "avatar_url"),
                "description": "Pulled from osu! on sign-in. Not editable here.",
            },
        ),
        (
            "Access",
            {
                "fields": ("is_active", "is_staff", "is_superuser"),
                "description": "Prefer Staff → Team to set moderator/admin. "
                "Staff = can open admin. Superuser = full admin (can manage players).",
            },
        ),
        ("Privacy", {"fields": ("is_public",)}),
    )
    readonly_fields = ("osu_username", "osu_id", "country_code", "avatar_url")

    @admin.display(description="Role")
    def role_label(self, obj):
        return roles.role_of(obj)

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.is_superuser or obj.is_staff:
            obj.groups.add(roles.ensure_moderator_group())
        else:
            obj.groups.remove(roles.ensure_moderator_group())


def admin_login(request, extra_context=None):
    """Staff sign in with osu! like everyone else (no site passwords)."""
    if request.user.is_authenticated:
        return HttpResponseForbidden("This account is not staff.")
    nxt = request.GET.get("next", f"/{settings.ADMIN_URL}")
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = f"/{settings.ADMIN_URL}"
    return redirect(f"{settings.LOGIN_URL}?next={nxt}")


admin.site.login = admin_login
admin.site.site_header = "osu! trivia"
admin.site.site_title = "osu! trivia admin"
admin.site.index_title = "Home"
admin.site.enable_nav_sidebar = False
