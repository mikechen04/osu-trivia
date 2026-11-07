import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader so local dev needs no extra dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


DEBUG = env_bool("DEBUG")
TESTING = "test" in sys.argv
PROD = not DEBUG and not TESTING

SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000").rstrip("/")

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "insecure-dev-only-key"
    else:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"] if not PROD else [urlparse(SITE_URL).hostname]
CSRF_TRUSTED_ORIGINS = [SITE_URL]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "game",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site",
            ],
        },
    },
]

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,
    )
}

AUTH_USER_MODEL = "accounts.User"
# Nobody signs in with a password (osu! OAuth only), but keep the validators for
# any admin-created accounts.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]
if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
LOGIN_URL = "/login/"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

# --- Static files ----------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if (DEBUG or TESTING)
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# --- Cache (rate limiting) ---------------------------------------------------
# Locmem is per-process. In production we use a DB-backed cache so limits are
# shared between Cloud Run instances (run `manage.py createcachetable`).
if PROD:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "cache_table",
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

RATELIMIT_ENABLED = True
# How many reverse proxies sit in front of the app. Cloud Run alone = 1.
# 0 means "trust REMOTE_ADDR only" (local dev).
TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "1" if PROD else "0"))

# --- Security ----------------------------------------------------------------
DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"  # Lax is required so the osu! redirect back to us keeps the session
CSRF_COOKIE_HTTPONLY = True  # the JS reads the token from a <meta> tag instead
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
if PROD:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]

ADMIN_URL = os.environ.get("ADMIN_URL", "admin/").lstrip("/")
if not ADMIN_URL.endswith("/"):
    ADMIN_URL += "/"

# --- osu! OAuth2 -------------------------------------------------------------
OSU_CLIENT_ID = os.environ.get("OSU_CLIENT_ID", "")
OSU_CLIENT_SECRET = os.environ.get("OSU_CLIENT_SECRET", "")  # server-side only, never rendered
OSU_REDIRECT_URI = f"{SITE_URL}/auth/callback/"
ADMIN_OSU_IDS = {int(x) for x in env_list("ADMIN_OSU_IDS") if x.isdigit()}
# Fake sign-in for local development. Hard-disabled unless DEBUG is on.
DEV_LOGIN = DEBUG and env_bool("DEV_LOGIN", True)

# --- Game rules ----------------------------------------------------------------
QUESTIONS_PER_DAY = 5
QUESTION_SECONDS = 25
ANSWER_GRACE_SECONDS = 1.5  # network slack; the server clock is the only clock that counts
BLEND_MIN_PLAYS = 30  # below this many valid answers the seeded tier is used as-is
BLEND_FULL_PLAYS = 300  # at/above this the live answer share fully decides the tier
SUGGESTIONS_PER_DAY = 3

# --- Site text / legal placeholders ------------------------------------------
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")
JURISDICTION = os.environ.get("JURISDICTION", "")
EFFECTIVE_DATE = os.environ.get("EFFECTIVE_DATE", "")
USE_TORUS = env_bool("USE_TORUS")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
