from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Give a player admin access. Usage: make_admin <osu id or exact osu! username> [--revoke]"

    def add_arguments(self, parser):
        parser.add_argument("who")
        parser.add_argument("--revoke", action="store_true")

    def handle(self, *args, who, revoke, **options):
        qs = User.objects.filter(osu_id=int(who)) if who.isdigit() else User.objects.filter(osu_username__iexact=who)
        user = qs.first()
        if user is None:
            raise CommandError("No such player. They need to sign in once first.")
        user.is_staff = user.is_superuser = not revoke
        user.save(update_fields=["is_staff", "is_superuser"])
        self.stdout.write(f"{user.display_name}: admin {'revoked' if revoke else 'granted'}.")
