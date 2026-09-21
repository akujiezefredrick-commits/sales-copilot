import asyncio

import pytest

from app import scrape
from app.models import Lead
from app.scrape import format_linkedin, format_website, scrape_all


def test_format_linkedin_drops_noise_and_empties():
    items = [{"name": "Acme", "logo": "x.png", "about": "We build", "specialties": [], "employeeCount": None}]
    text = format_linkedin(items)
    assert '"name": "Acme"' in text and '"about": "We build"' in text
    assert "logo" not in text and "specialties" not in text and "employeeCount" not in text


def test_format_linkedin_empty():
    assert format_linkedin([]) == ""


def test_format_website_puts_home_and_about_first():
    items = [
        {"url": "https://acme.com/blog/post", "markdown": "blog", "metadata": {"title": "Blog"}},
        {"url": "https://acme.com/about", "markdown": "about us", "metadata": {"title": "About"}},
        {"url": "https://acme.com/", "markdown": "home", "metadata": {"title": "Home"}},
        {"url": "https://acme.com/empty", "markdown": ""},
    ]
    text = format_website(items, "https://acme.com")
    assert text.index("home") < text.index("about us") < text.index("blog")
    assert "/empty" not in text


def test_truncation():
    text = format_website([{"url": "https://a.com", "markdown": "x" * 20_000}], "https://a.com")
    assert len(text) < 15_100 and text.endswith("[...truncated]")


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(scrape, "BACKOFF_SECONDS", 0)


def test_scrape_all_success(monkeypatch):
    async def fake_run(client, actor_id, run_input, timeout_s, max_charge_usd):
        if "companies" in run_input:
            return [{"name": "Acme", "description": "Dental marketing"}], 0.004
        return [{"url": "https://acme.com", "markdown": "We help dentists grow. " * 20}], 0.01

    monkeypatch.setattr(scrape, "_run_actor", fake_run)
    lead = Lead(website="https://acme.com", linkedin_url="https://www.linkedin.com/company/acme")
    result = asyncio.run(scrape_all(lead, client=object()))
    assert result.errors == []
    assert "Acme" in result.linkedin_text and "dentists" in result.website_text
    assert result.cost_usd == pytest.approx(0.014)


def test_scrape_all_retries_then_reports_partial(monkeypatch):
    calls = {"linkedin": 0}

    async def fake_run(client, actor_id, run_input, timeout_s, max_charge_usd):
        if "companies" in run_input:
            calls["linkedin"] += 1
            raise scrape.ScrapeError("actor FAILED")
        return [{"url": "https://acme.com", "markdown": "We help dentists grow. " * 20}], 0.01

    monkeypatch.setattr(scrape, "_run_actor", fake_run)
    lead = Lead(website="https://acme.com", linkedin_url="https://www.linkedin.com/company/acme")
    result = asyncio.run(scrape_all(lead, client=object()))
    assert calls["linkedin"] == scrape.RETRIES + 1
    assert result.errors == ["LinkedIn scrape failed"]
    assert result.linkedin_text == "" and result.website_text


def test_bad_inputs_are_not_retried(monkeypatch):
    async def fake_run(*a, **k):
        raise AssertionError("should not be called")

    monkeypatch.setattr(scrape, "_run_actor", fake_run)
    lead = Lead(website="", linkedin_url="https://www.linkedin.com/in/some-person")
    result = asyncio.run(scrape_all(lead, client=object()))
    assert result.errors == ["LinkedIn scrape failed", "website scrape failed"]


def test_website_falls_back_to_browser_crawler(monkeypatch):
    crawlers = []

    async def fake_run(client, actor_id, run_input, timeout_s, max_charge_usd):
        if "companies" in run_input:
            return [{"name": "Acme"}], 0.004
        crawlers.append(run_input["crawlerType"])
        if run_input["crawlerType"] == "cheerio":
            return [{"url": "https://acme.com", "markdown": "Loading..."}], 0.001
        return [{"url": "https://acme.com", "markdown": "Real rendered content. " * 20}], 0.02

    monkeypatch.setattr(scrape, "_run_actor", fake_run)
    lead = Lead(website="https://acme.com", linkedin_url="https://www.linkedin.com/company/acme")
    result = asyncio.run(scrape_all(lead, client=object()))
    assert crawlers == ["cheerio", "playwright:adaptive"]
    assert "rendered" in result.website_text


def test_founder_profile_and_posts(monkeypatch):
    async def fake_run(client, actor_id, run_input, timeout_s, max_charge_usd):
        if "companies" in run_input:
            return [{"name": "Acme"}], 0.004
        if "queries" in run_input:
            return [{"headline": "Executive coach for founders"}], 0.004
        if "targetUrls" in run_input:
            assert run_input["postedLimit"] == "3months"
            return [{"content": "3 lessons from coaching 100 CEOs"}], 0.002
        return [{"url": "https://acme.com", "markdown": "Coaching programs. " * 20}], 0.01

    monkeypatch.setattr(scrape, "_run_actor", fake_run)
    lead = Lead(website="https://acme.com", linkedin_url="https://www.linkedin.com/company/acme",
                founder_linkedin_url="https://www.linkedin.com/in/jane-coach")
    result = asyncio.run(scrape_all(lead, client=object()))
    assert result.errors == []
    assert "Posts in the last 3 months (up to 5 fetched): 1" in result.founder_text
    assert "Executive coach" in result.founder_text and "100 CEOs" in result.founder_text
    assert result.cost_usd == pytest.approx(0.02)


def test_founder_not_posting_is_called_out():
    assert "NOT POSTING RECENTLY" in scrape.format_founder([{"headline": "Coach"}], [])


def test_founder_blank_is_skipped_and_company_url_rejected(monkeypatch):
    async def fake_run(client, actor_id, run_input, timeout_s, max_charge_usd):
        assert "queries" not in run_input and "targetUrls" not in run_input
        if "companies" in run_input:
            return [{"name": "Acme"}], 0.004
        return [{"url": "https://acme.com", "markdown": "Coaching programs. " * 20}], 0.01

    monkeypatch.setattr(scrape, "_run_actor", fake_run)
    base = dict(website="https://acme.com", linkedin_url="https://www.linkedin.com/company/acme")
    assert asyncio.run(scrape_all(Lead(**base), client=object())).errors == []
    wrong = Lead(**base, founder_linkedin_url="https://www.linkedin.com/company/acme")
    assert asyncio.run(scrape_all(wrong, client=object())).errors == ["founder LinkedIn scrape failed"]
