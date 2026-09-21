import asyncio
from types import SimpleNamespace

import pytest

from app import analyze as analyze_mod
from app.analyze import AnalysisError, _Analysis, analyze, build_user_message
from app.models import Lead
from app.scrape import ScrapeResult

LEAD = Lead(full_name="Jane", email="jane@acme.com", website="https://acme.com",
            linkedin_url="https://www.linkedin.com/company/acme")


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.kwargs = None
        self.messages = SimpleNamespace(parse=self._parse)

    async def _parse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def response(parsed, stop_reason="end_turn"):
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=50))


@pytest.fixture(autouse=True)
def icp(monkeypatch):
    monkeypatch.setattr(analyze_mod, "load_icp", lambda: "We sell marketing to dental clinics.")
    monkeypatch.setattr(analyze_mod, "GEMINI_BACKOFF_SECONDS", 0)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")


def test_user_message_includes_data_and_problems():
    msg = build_user_message(LEAD, ScrapeResult(website_text="SITE", errors=["LinkedIn scrape failed"]))
    assert "SITE" in msg and "No LinkedIn data available." in msg
    assert "Data problems: LinkedIn scrape failed" in msg


def test_contact_details_not_sent_to_ai():
    lead = LEAD.model_copy(update={"phone": "+34123456789"})
    msg = build_user_message(lead, ScrapeResult())
    assert "jane@acme.com" not in msg and "+34123456789" not in msg


def test_analyze_maps_output_and_clamps_score():
    parsed = _Analysis(niche_industry=" Dental / Healthcare ", primary_service="Implants",
                       icp_score=14, icp_fit_reason="Dental clinic.", icebreaker="Hi.")
    client = FakeClient(response(parsed))
    result = asyncio.run(analyze(LEAD, ScrapeResult(), client=client))
    assert result.icp_score == 10
    assert result.niche_industry == "Dental / Healthcare"
    assert "We sell marketing to dental clinics." in client.kwargs["system"][0]["text"]
    assert client.kwargs["output_format"] is _Analysis


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_analyze_raises_on_unusable_response(stop_reason):
    with pytest.raises(AnalysisError):
        asyncio.run(analyze(LEAD, ScrapeResult(), client=FakeClient(response(None, stop_reason))))


# ---- Gemini ----

class FakeGemini:
    """Mimics genai.Client: client.aio.models.generate_content(...). Plays back `outcomes` in order."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._generate))

    async def _generate(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def gemini_response(parsed=None, text=""):
    return SimpleNamespace(parsed=parsed, text=text, candidates=[SimpleNamespace(finish_reason="STOP")],
                           usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=50))


GOOD = _Analysis(niche_industry="Executive coaching / Professional services", primary_service="Coaching",
                 icp_score=9, icp_fit_reason="Founder-led coach.", icebreaker="Hello.")


def test_gemini_happy_path(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    client = FakeGemini(gemini_response(GOOD))
    result = asyncio.run(analyze(LEAD, ScrapeResult(), client=client))
    assert result.icp_score == 9
    config = client.calls[0]["config"]
    assert config.response_schema is _Analysis and "dental clinics" in config.system_instruction


def test_gemini_falls_back_to_parsing_text(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    client = FakeGemini(gemini_response(None, GOOD.model_dump_json()))
    assert asyncio.run(analyze(LEAD, ScrapeResult(), client=client)).primary_service == "Coaching"


def test_gemini_retries_rate_limit_then_succeeds(monkeypatch):
    from google.genai import errors

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    rate_limited = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    client = FakeGemini(rate_limited, gemini_response(GOOD))
    assert asyncio.run(analyze(LEAD, ScrapeResult(), client=client)).icp_score == 9
    assert len(client.calls) == 2


def test_gemini_bad_request_not_retried(monkeypatch):
    from google.genai import errors

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    bad = errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})
    client = FakeGemini(bad)
    with pytest.raises(AnalysisError, match="Gemini API 400"):
        asyncio.run(analyze(LEAD, ScrapeResult(), client=client))
    assert len(client.calls) == 1


def _err(code, status):
    from google.genai import errors

    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "message": status, "status": status}})


def test_gemini_overloaded_falls_back_to_next_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_MODEL", "main")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "backup")
    busy = _err(503, "UNAVAILABLE")
    client = FakeGemini(busy, busy, busy, gemini_response(GOOD))
    assert asyncio.run(analyze(LEAD, ScrapeResult(), client=client)).icp_score == 9
    assert [c["model"] for c in client.calls] == ["main", "main", "main", "backup"]


def test_gemini_retired_model_skips_straight_to_next(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_MODEL", "old")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "new")
    client = FakeGemini(_err(404, "NOT_FOUND"), gemini_response(GOOD))
    asyncio.run(analyze(LEAD, ScrapeResult(), client=client))
    assert [c["model"] for c in client.calls] == ["old", "new"]


def test_gemini_all_models_down(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_MODEL", "a")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "b")
    busy = _err(503, "UNAVAILABLE")
    with pytest.raises(AnalysisError, match="Gemini unavailable: a 503.*; b 503"):
        asyncio.run(analyze(LEAD, ScrapeResult(), client=FakeGemini(*[busy] * 6)))


def test_gemini_garbage_output_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(AnalysisError, match="no structured output"):
        asyncio.run(analyze(LEAD, ScrapeResult(), client=FakeGemini(gemini_response(None, "not json"))))


def test_unknown_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(AnalysisError, match="unknown LLM_PROVIDER"):
        asyncio.run(analyze(LEAD, ScrapeResult()))
