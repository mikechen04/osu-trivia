from django.conf import settings

# No inline scripts or styles anywhere in the site templates, so the policy can be strict.
# osu! avatars are loaded straight from ppy's servers (disclosed in the privacy policy).
# form-action lists osu.ppy.sh because Chrome applies it to redirects after a form POST,
# and our sign-in form redirects to osu! to authorise.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: https://a.ppy.sh https://osu.ppy.sh https://assets.ppy.sh",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self' https://osu.ppy.sh",
    ]
)

PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), interest-cohort=()"


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Django's admin uses a few inline styles/scripts; it is staff-only, so it keeps Django's defaults.
        if not request.path.startswith(f"/{settings.ADMIN_URL}"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        if request.path.startswith(("/api/", "/account/")):
            response.headers["Cache-Control"] = "no-store"
        return response
