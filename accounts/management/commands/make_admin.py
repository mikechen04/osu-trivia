from django.core.management.base import BaseCommand, CommandError

from accounts import roles
from accounts.models import User


class Command(BaseCommand):
    help = "Set a player's role. Usage: make_admin <osu id or username> [--role admin|moderator|player]"

    def add_arguments(self, parser):
        parser.add_argument("who")
        parser.add_argument(
            "--role",
            choices=[roles.ROLE_ADMIN, roles.ROLE_MODERATOR, roles.ROLE_PLAYER],
            default=roles.ROLE_ADMIN,
        )
        parser.add_argument("--revoke", action="store_true", help="Shortcut for --role player")

    def handle(self, *args, who, role, revoke, **options):
        qs = User.objects.filter(osu_id=int(who)) if who.isdigit() else User.objects.filter(osu_username__iexact=who)
        user = qs.first()
        if user is None:
            raise CommandError("No such player. They need to sign in once first.")
        if revoke:
            role = roles.ROLE_PLAYER
        roles.set_role(user, role)
        self.stdout.write(f"{user.display_name}: role set to {role}.")
