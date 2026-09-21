import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from app.config import FieldRefs
from app.typeform import is_complete_submission, normalize_url, parse_lead, verify_signature

FIXTURE = Path(__file__).parent / "fixtures" / "typeform_sample.json"

REFS = FieldRefs(
    full_name="da57594e-5f51-4c20-aad0-63052ac48469",
    email="fcbad6cc-bf39-475f-b3f5-03b4539dccfe",
    phone="d59ce916-f4a8-4be5-b109-8c86555d99f1",
    website="4ad25b57-a012-48c2-bb89-ac73fdf6a5b3",
    linkedin="11ef1c2f-1663-4382-a909-d757b4782193",
)


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


@pytest.fixture
def payload() -> dict:
    return json.loads(FIXTURE.read_text())


def test_signature_valid():
    body = FIXTURE.read_bytes()
    assert verify_signature(body, sign(body, "s3cret"), "s3cret")


@pytest.mark.parametrize("header,secret", [(None, "s3cret"), ("sha256=bogus", "s3cret"), ("x", "")])
def test_signature_rejected(header, secret):
    assert not verify_signature(FIXTURE.read_bytes(), header, secret)


def test_signature_rejects_tampered_body():
    body = FIXTURE.read_bytes()
    assert not verify_signature(body + b" ", sign(body, "s3cret"), "s3cret")


def test_parse_sample(payload):
    lead = parse_lead(payload, REFS)
    assert lead.response_id == "01M31NSK14S99JAT7GG4XN409S"
    assert lead.full_name == "Lorem ipsum dolor"
    assert lead.email == "an_account@example.com"
    assert lead.phone == "+34123456789"
    assert lead.website == "http://example-url.com"
    assert lead.linkedin_url == "http://example-url.com"


def test_missing_answers_become_blank(payload):
    payload["form_response"]["answers"] = payload["form_response"]["answers"][:2]
    lead = parse_lead(payload, REFS)
    assert lead.email == "an_account@example.com"
    assert lead.website == "" and lead.linkedin_url == "" and lead.phone == ""


def test_partial_responses_ignored(payload):
    assert is_complete_submission(payload)
    payload["event_type"] = "form_response_partial"
    assert not is_complete_submission(payload)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("acme.com", "https://acme.com"),
        ("  https://WWW.Acme.com/ ", "https://www.acme.com"),
        ("https://acme.com/about/?utm_source=x&id=7#top", "https://acme.com/about?id=7"),
        (
            "linkedin.com/company/acme/?trk=public_profile&originalSubdomain=uk",
            "https://linkedin.com/company/acme",
        ),
        ("", ""),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_optional_founder_question(payload):
    refs = FieldRefs(**{**REFS.__dict__, "founder_linkedin": "founder-ref"})
    assert parse_lead(payload, refs).founder_linkedin_url == ""
    payload["form_response"]["answers"].append(
        {"type": "url", "url": "linkedin.com/in/jane/", "field": {"ref": "founder-ref"}}
    )
    assert parse_lead(payload, refs).founder_linkedin_url == "https://linkedin.com/in/jane"
