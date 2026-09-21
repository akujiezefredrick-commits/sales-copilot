from pydantic import BaseModel, Field


class Lead(BaseModel):
    """One Typeform submission."""

    response_id: str = ""
    full_name: str = ""
    email: str = ""
    phone: str = ""
    website: str = ""
    linkedin_url: str = ""
    founder_linkedin_url: str = ""


class Enrichment(BaseModel):
    """What Claude extracts from the scraped LinkedIn page + website."""

    niche_industry: str = ""
    primary_service: str = ""
    icp_score: int | None = Field(default=None, ge=1, le=10)
    icp_fit_reason: str = ""
    icebreaker: str = ""
