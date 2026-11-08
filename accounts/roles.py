"""Site roles: player · moderator · admin.

- Moderators (is_staff, not superuser): schedule questions, review suggestions, use /staff/
  and the classic admin for Questions / Suggestions.
- Admins (is_superuser): everything mods can do, plus manage the team and all Django admin.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db.models import Q

ROLE_PLAYER = "player"
ROLE_MODERATOR = "moderator"
ROLE_ADMIN = "admin"

MODERATOR_GROUP = "Moderators"

# Content mods can curate the question pool and the suggestion queue.
MODERATOR_PERM_CODES = (
    ("game", "view_question"),
    ("game", "add_question"),
    ("game", "change_question"),
    ("game", "delete_question"),
    ("game", "view_acceptedanswer"),
    ("game", "add_acceptedanswer"),
    ("game", "change_acceptedanswer"),
    ("game", "delete_acceptedanswer"),
    ("core", "view_suggestion"),
    ("core", "change_suggestion"),
)


def role_of(user) -> str:
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return ROLE_PLAYER
    if user.is_superuser:
        return ROLE_ADMIN
    if user.is_staff:
        return ROLE_MODERATOR
    return ROLE_PLAYER


def is_admin(user) -> bool:
    return role_of(user) == ROLE_ADMIN


def is_moderator(user) -> bool:
    """True for mods and admins (admins inherit mod powers)."""
    return role_of(user) in {ROLE_MODERATOR, ROLE_ADMIN}


def ensure_moderator_group() -> Group:
    group, _ = Group.objects.get_or_create(name=MODERATOR_GROUP)
    wanted = []
    for app_label, codename in MODERATOR_PERM_CODES:
        try:
            wanted.append(Permission.objects.get(content_type__app_label=app_label, codename=codename))
        except Permission.DoesNotExist:
            continue
    group.permissions.set(wanted)
    return group


def set_role(user, role: str) -> None:
    """Assign player / moderator / admin. Admins always get staff + superuser."""
    role = (role or ROLE_PLAYER).lower()
    group = ensure_moderator_group()
    if role == ROLE_ADMIN:
        user.is_staff = True
        user.is_superuser = True
        user.groups.add(group)
    elif role == ROLE_MODERATOR:
        user.is_staff = True
        user.is_superuser = False
        user.groups.add(group)
    else:
        user.is_staff = False
        user.is_superuser = False
        user.groups.remove(group)
    user.save(update_fields=["is_staff", "is_superuser"])


def apply_bootstrap_admin(user) -> bool:
    """Promote users listed in ADMIN_OSU_IDS (includes the hard-coded default)."""
    from django.conf import settings

    if user.osu_id and user.osu_id in settings.ADMIN_OSU_IDS:
        set_role(user, ROLE_ADMIN)
        return True
    return False


def team_queryset():
    User = get_user_model()
    return User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).order_by("-is_superuser", "osu_username")
