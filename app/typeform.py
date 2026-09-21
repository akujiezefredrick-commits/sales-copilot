"""Typeform webhook helpers: signature check, submission filter, answer mapping."""

import base64
import hashlib
import hmac
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config import FieldRefs
from app.models import Lead

_TRACKING_PARAMS = {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "trk", "originalsubdomain"}


def verify_signature(body: bytes, header: str | None, secret: str) -> bool:
    """Typeform signs the raw body: 'sha256=' + base64(HMAC-SHA256(secret, body))."""
    if not secret or not header:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    expected = "sha256=" + base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, header)


def is_complete_submission(payload: dict) -> bool:
    """The form sends partial responses too; only finished ones are processed."""
    return payload.get("event_type") == "form_response"


def normalize_url(raw: str) -> str:
    """Add a scheme, lowercase the host, drop tracking params and fragments."""
    url = (raw or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") if parts.path not in ("", "/") else ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def _answer_value(answer: dict) -> str:
    # Each answer stores its value under a key named after its type: text, email, phone_number, url...
    value = answer.get(answer.get("type", ""), "")
    return str(value).strip() if value is not None else ""


def parse_lead(payload: dict, refs: FieldRefs) -> Lead:
    form = payload.get("form_response", {})
    by_ref = {a.get("field", {}).get("ref"): _answer_value(a) for a in form.get("answers", [])}
    return Lead(
        response_id=form.get("token", ""),
        full_name=by_ref.get(refs.full_name, ""),
        email=by_ref.get(refs.email, "").lower(),
        phone=by_ref.get(refs.phone, ""),
        website=normalize_url(by_ref.get(refs.website, "")),
        linkedin_url=normalize_url(by_ref.get(refs.linkedin, "")),
        founder_linkedin_url=normalize_url(by_ref.get(refs.founder_linkedin, "")) if refs.founder_linkedin else "",
    )
