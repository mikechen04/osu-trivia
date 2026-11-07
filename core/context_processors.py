from django.conf import settings
from django.utils import timezone

from game import clock


def site(request):
    now = timezone.now()
    return {
        "SITE_URL": settings.SITE_URL,
        "USE_TORUS": settings.USE_TORUS,
        "ADMIN_URL": "/" + settings.ADMIN_URL,
        "SERVER_NOW_MS": int(now.timestamp() * 1000),
        "NEXT_RESET_MS": int(clock.next_reset(now).timestamp() * 1000),
        "TODAY": clock.today(now),
    }
