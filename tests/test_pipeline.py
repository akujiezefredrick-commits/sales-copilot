import asyncio
import json

import pytest

from app import pipeline
from app.models import Enrichment, Lead
from app.scrape import ScrapeResult

LEAD = Lead(full_name="Jane", email="jane@acme.com", website="https://acme.com",
            linkedin_url="https://www.linkedin.com/company/acme")
GOOD = Enrichment(niche_industry="Dental / Healthcare", primary_service="Implants", icp_score=8,
                  icp_fit_reason="Good fit.", icebreaker="Hello.")


@pytest.fixture
def written(monkeypatch, tmp_path):
    rows = []
    monkeypatch.setattr(pipeline, "append_lead", lambda lead, enr, flag: rows.append((lead, enr, flag)))
    monkeypatch.setattr(pipeline, "FAILED_LEADS", tmp_path / "failed.jsonl")
    return rows


def patch(monkeypatch, scraped, analysis):
    async def fake_scrape(lead):
        return scraped

    async def fake_analyze(lead, s):
        if isinstance(analysis, Exception):
            raise analysis
        return analysis

    monkeypatch.setattr(pipeline, "scrape_all", fake_scrape)
    monkeypatch.setattr(pipeline, "analyze", fake_analyze)


def test_happy_path(monkeypatch, written):
    patch(monkeypatch, ScrapeResult(linkedin_text="x", website_text="y"), GOOD)
    asyncio.run(pipeline.process_lead(LEAD))
    assert written == [(LEAD, GOOD, "")]


def test_partial_scrape_is_flagged(monkeypatch, written):
    patch(monkeypatch, ScrapeResult(linkedin_text="x", errors=["website scrape failed"]), GOOD)
    asyncio.run(pipeline.process_lead(LEAD))
    assert written[0][2] == "[PARTIAL: website scrape failed]"


def test_analysis_failure_still_writes_contact(monkeypatch, written):
    patch(monkeypatch, ScrapeResult(), RuntimeError("boom"))
    asyncio.run(pipeline.process_lead(LEAD))
    lead, enr, flag = written[0]
    assert lead == LEAD and enr == Enrichment()
    assert flag == "[ERROR: analysis failed: boom]"


def test_sheet_failure_saves_locally(monkeypatch, tmp_path):
    patch(monkeypatch, ScrapeResult(linkedin_text="x", website_text="y"), GOOD)
    failed = tmp_path / "failed.jsonl"
    monkeypatch.setattr(pipeline, "FAILED_LEADS", failed)

    def broken(*a):
        raise PermissionError("sheet not shared")

    monkeypatch.setattr(pipeline, "append_lead", broken)
    asyncio.run(pipeline.process_lead(LEAD))
    record = json.loads(failed.read_text().splitlines()[0])
    assert record["lead"]["email"] == "jane@acme.com" and record["enrichment"]["icp_score"] == 8
