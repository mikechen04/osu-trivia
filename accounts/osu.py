"""Minimal osu! OAuth2 client (authorization code flow, `identify` scope only).

The access token is used once to read the profile and then thrown away.
"""

import re
from urllib.parse import urlencode, urlparse

import requests
from django.conf import settings

AUTHORIZE_URL = "https://osu.ppy.sh/oauth/authorize"
TOKEN_URL = "https://osu.ppy.sh/oauth/token"
ME_URL = "https://osu.ppy.sh/api/v2/me"
TIMEOUT = 10
AVATAR_HOSTS = {"a.ppy.sh", "osu.ppy.sh", "assets.ppy.sh"}


class OsuError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.OSU_CLIENT_ID and settings.OSU_CLIENT_SECRET)


def build_authorize_url(state: str) -> str:
    params = {
        "client_id": settings.OSU_CLIENT_ID,
        "redirect_uri": settings.OSU_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str) -> str:
    try:
        res = requests.post(
            TOKEN_URL,
            data={
                "client_id": settings.OSU_CLIENT_ID,
                "client_secret": settings.OSU_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.OSU_REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        res.raise_for_status()
        token = res.json().get("access_token")
    except (requests.RequestException, ValueError) as exc:
        raise OsuError("token exchange failed") from exc
    if not token or not isinstance(token, str):
        raise OsuError("no access token in response")
    return token


def fetch_profile(access_token: str) -> dict:
    try:
        res = requests.get(
            ME_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        res.raise_for_status()
        return parse_profile(res.json())
    except (requests.RequestException, ValueError) as exc:
        raise OsuError("profile fetch failed") from exc


def parse_profile(data: dict) -> dict:
    """Validate and clean what osu! sends us; we never trust remote data blindly."""
    if not isinstance(data, dict):
        raise ValueError("bad profile")
    osu_id = data.get("id")
    username = data.get("username")
    if not isinstance(osu_id, int) or isinstance(osu_id, bool) or osu_id <= 0:
        raise ValueError("bad id")
    if not isinstance(username, str) or not (1 <= len(username.strip()) <= 64):
        raise ValueError("bad username")

    avatar = data.get("avatar_url") or ""
    parsed = urlparse(avatar) if isinstance(avatar, str) else None
    if not (parsed and parsed.scheme == "https" and parsed.hostname in AVATAR_HOSTS and len(avatar) <= 300):
        avatar = ""

    country = data.get("country_code") or ""
    country = country.upper() if isinstance(country, str) and re.fullmatch(r"[A-Za-z]{2}", country) else ""

    return {"osu_id": osu_id, "username": username.strip(), "avatar_url": avatar, "country_code": country}
