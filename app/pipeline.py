"""Lead -> scrape -> analyze -> sheet. Never drops a lead."""

import asyncio
import json
import logging
from pathlib import Path

from app.analyze import analyze
from app.models import Enrichment, Lead
from app.scrape import ScrapeResult, scrape_all
from app.sheets import append_lead

log = logging.getLogger(__name__)

# Last resort when the Sheet itself can't be written.
FAILED_LEADS = Path(__file__).resolve().parent.parent / "failed_leads.jsonl"


def build_flag(scraped: ScrapeResult, analysis_error: str | None) -> str:
    if analysis_error:
        return f"[ERROR: {analysis_error}]"
    if scraped.errors:
        return f"[PARTIAL: {'; '.join(scraped.errors)}]"
    return ""


async def process_lead(lead: Lead) -> Enrichment:
    log.info("Processing lead %s (%s)", lead.email, lead.response_id)

    scraped = await scrape_all(lead)
    # Apify settles pay-per-event charges after a run ends, so this can under-report; check the Apify console for exact figures.
    log.info("Scraped %s: errors=%s, apify cost so far=$%.4f", lead.email, scraped.errors or "none", scraped.cost_usd)

    analysis_error = None
    try:
        enrichment = await analyze(lead, scraped)
    except Exception as e:
        log.exception("Analysis failed for %s", lead.email)
        analysis_error = f"analysis failed: {e}"
        enrichment = Enrichment()

    flag = build_flag(scraped, analysis_error)
    try:
        await asyncio.to_thread(append_lead, lead, enrichment, flag)
    except Exception:
        log.exception("Sheet write failed for %s; saving to %s", lead.email, FAILED_LEADS.name)
        record = {"lead": lead.model_dump(), "enrichment": enrichment.model_dump(), "flag": flag}
        with FAILED_LEADS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return enrichment
