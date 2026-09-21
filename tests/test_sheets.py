from app.models import Enrichment, Lead
from app.sheets import build_row, row_values

HEADERS = ["Full name", "Email", "Phone number", "Website", "Linkedin Url", "Niche", "Primary service",
           "ICP Score", "ICP fit reason", "Professional Ice breaker"]

LEAD = Lead(full_name="Jane", email="jane@acme.com", phone="+34123456789", website="https://acme.com",
            linkedin_url="https://www.linkedin.com/company/acme")
ENRICH = Enrichment(niche_industry="Dental / Healthcare", primary_service="Implants", icp_score=8,
                    icp_fit_reason="Good fit.", icebreaker="Hello.")


def test_row_matches_real_headers():
    assert build_row(HEADERS, row_values(LEAD, ENRICH)) == [
        "Jane", "jane@acme.com", "+34123456789", "https://acme.com", "https://www.linkedin.com/company/acme",
        "Dental / Healthcare", "Implants", 8, "Good fit.", "Hello.",
    ]


def test_order_case_and_unknown_headers_dont_matter():
    headers = ["  ICP SCORE", "Notes", "LinkedIn URL", "email"]
    assert build_row(headers, row_values(LEAD, ENRICH)) == [
        8, "", "https://www.linkedin.com/company/acme", "jane@acme.com"
    ]


def test_live_sheet_headers():
    headers = ["Full-Name", "Email", "Phone Number", "Website", "Linkedin Url", "Niche", "Primary Service",
               "ICP Score(1-10)", "ICP Fit Reason", "Professional Ice breaker"]
    assert build_row(headers, row_values(LEAD, ENRICH)) == [
        "Jane", "jane@acme.com", "+34123456789", "https://acme.com", "https://www.linkedin.com/company/acme",
        "Dental / Healthcare", "Implants", 8, "Good fit.", "Hello.",
    ]


def test_flag_prefixes_reason_and_blank_score():
    values = row_values(LEAD, Enrichment(), "[ERROR: analysis failed]")
    assert values["icp_fit_reason"] == "[ERROR: analysis failed]"
    assert values["icp_score"] == ""
    assert row_values(LEAD, ENRICH, "[PARTIAL: website scrape failed]")["icp_fit_reason"] == (
        "[PARTIAL: website scrape failed] Good fit."
    )


def test_founder_column():
    lead = LEAD.model_copy(update={"founder_linkedin_url": "https://www.linkedin.com/in/jane"})
    assert build_row(["Founder LinkedIn URL"], row_values(lead, ENRICH)) == ["https://www.linkedin.com/in/jane"]
