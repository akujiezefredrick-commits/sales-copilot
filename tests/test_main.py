import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

FIXTURE = Path(__file__).parent / "fixtures" / "typeform_sample.json"
SECRET = "test-secret"


def sign(body: bytes) -> str:
    return "sha256=" + base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TYPEFORM_SECRET", SECRET)
    processed = []

    async def fake_process(lead):
        processed.append(lead)

    monkeypatch.setattr(main, "process_lead", fake_process)
    main._seen_responses.clear()
    c = TestClient(main.app)
    c.processed = processed
    return c


def post(client, payload: dict, signature: str | None = None):
    body = json.dumps(payload).encode()
    headers = {"Typeform-Signature": signature or sign(body), "Content-Type": "application/json"}
    return client.post("/webhook/typeform", content=body, headers=headers)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_rejects_bad_signature(client):
    r = post(client, json.loads(FIXTURE.read_text()), signature="sha256=nope")
    assert r.status_code == 401 and client.processed == []


def test_accepts_and_processes_once(client):
    payload = json.loads(FIXTURE.read_text())
    assert post(client, payload).json() == {"status": "accepted"}
    assert post(client, payload).json() == {"status": "duplicate"}
    assert len(client.processed) == 1
    assert client.processed[0].email == "an_account@example.com"


def test_ignores_partial_responses(client):
    payload = json.loads(FIXTURE.read_text())
    payload["event_type"] = "form_response_partial"
    assert post(client, payload).json()["status"] == "ignored"
    assert client.processed == []
