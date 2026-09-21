"""Google Sheets writer. Values are placed by matching the headers in row 1, not by position."""

import logging
import re
from functools import lru_cache

import gspread

from app.config import env
from app.models import Enrichment, Lead

log = logging.getLogger(__name__)

# Normalized header -> canonical field. Normalizing strips case, spaces and punctuation,
# so "Linkedin Url", "LinkedIn URL" and "linkedin_url" all match.
HEADER_ALIASES = {
    "fullname": "full_name",
    "name": "full_name",
    "firstname": "full_name",
    "email": "email",
    "phonenumber": "phone",
    "phone": "phone",
    "website": "website",
    "companywebsite": "website",
    "linkedinurl": "linkedin_url",
    "linkedin": "linkedin_url",
    "founderlinkedinurl": "founder_linkedin_url",
    "founderlinkedin": "founder_linkedin_url",
    "personallinkedinurl": "founder_linkedin_url",
    "personallinkedin": "founder_linkedin_url",
    "niche": "niche",
    "nicheindustry": "niche",
    "industry": "niche",
    "primaryservice": "primary_service",
    "icpscore": "icp_score",
    "icpfitscore": "icp_score",
    "icpfitreason": "icp_fit_reason",
    "professionalicebreaker": "icebreaker",
    "icebreaker": "icebreaker",
}
EXPECTED_FIELDS = set(HEADER_ALIASES.values())


def _normalize(header: str) -> str:
    # Drop bracketed hints like "(1-10)" first, so "ICP Score(1-10)" matches "icpscore".
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", header.lower()))


def row_values(lead: Lead, enrichment: Enrichment, flag: str = "") -> dict[str, str | int]:
    reason = f"{flag} {enrichment.icp_fit_reason}".strip() if flag else enrichment.icp_fit_reason
    return {
        "full_name": lead.full_name,
        "email": lead.email,
        "phone": lead.phone,
        "website": lead.website,
        "linkedin_url": lead.linkedin_url,
        "founder_linkedin_url": lead.founder_linkedin_url,
        "niche": enrichment.niche_industry,
        "primary_service": enrichment.primary_service,
        "icp_score": enrichment.icp_score if enrichment.icp_score is not None else "",
        "icp_fit_reason": reason,
        "icebreaker": enrichment.icebreaker,
    }


def build_row(headers: list[str], values: dict[str, str | int]) -> list[str | int]:
    fields = [HEADER_ALIASES.get(_normalize(h)) for h in headers]
    missing = EXPECTED_FIELDS - set(fields)
    if missing:
        log.warning("Sheet has no column for: %s", ", ".join(sorted(missing)))
    return [values.get(f, "") if f else "" for f in fields]


@lru_cache(maxsize=1)
def _worksheet() -> gspread.Worksheet:
    gc = gspread.service_account(filename=env("GOOGLE_SA_JSON", "./service-account.json"))
    sh = gc.open_by_key(env("GOOGLE_SHEET_ID"))
    tab = env("GOOGLE_SHEET_TAB")
    return sh.worksheet(tab) if tab else sh.sheet1


def append_lead(lead: Lead, enrichment: Enrichment, flag: str = "") -> None:
    """Blocking (gspread is sync); call via asyncio.to_thread from async code."""
    ws = _worksheet()
    headers = ws.row_values(1)
    if not headers:
        raise RuntimeError("Sheet row 1 is empty; add the column headers first")
    row = build_row(headers, row_values(lead, enrichment, flag))
    # RAW keeps "+34..." phone numbers as text and stops lead input from being evaluated as formulas.
    ws.append_row(row, value_input_option="RAW", table_range="A1")
    log.info("Wrote %s to sheet", lead.email)
