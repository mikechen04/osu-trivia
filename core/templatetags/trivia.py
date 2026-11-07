from django import template
from django.conf import settings
from django.utils.html import format_html

register = template.Library()

PLACEHOLDERS = {
    "contact_email": ("CONTACT_EMAIL", "[CONTACT EMAIL]"),
    "jurisdiction": ("JURISDICTION", "[JURISDICTION]"),
    "effective_date": ("EFFECTIVE_DATE", "[EFFECTIVE DATE]"),
}


@register.simple_tag
def placeholder(name: str):
    """Renders the configured value, or a clearly highlighted [PLACEHOLDER] until it is set."""
    setting_name, fallback = PLACEHOLDERS[name]
    value = getattr(settings, setting_name, "")
    if value:
        return value
    return format_html('<mark class="placeholder">{}</mark>', fallback)


@register.filter
def flag(code):
    """Country code -> flag emoji (rendered with a bundled flag font so it also works on Windows)."""
    if isinstance(code, str) and len(code) == 2 and code.isalpha() and code.isascii():
        return "".join(chr(0x1F1E6 + ord(c) - 65) for c in code.upper())
    return ""


@register.filter
def pct(value):
    try:
        return f"{float(value):.1f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return ""
