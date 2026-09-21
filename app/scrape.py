"""Apify scraping: LinkedIn company page + company website, run in parallel with retries."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from apify_client import ApifyClientAsync

from app.config import env
from app.models import Lead

log = logging.getLogger(__name__)

MAX_CHARS = 15_000
RETRIES = 2  # on top of the first attempt
BACKOFF_SECONDS = 3

WEBSITE_MAX_PAGES = 8
# Pages worth reading first when trimming the website text.
_PRIORITY_PATHS = ("about", "service", "solution", "what-we-do", "product", "industr", "client", "case")
# LinkedIn fields that are noise for the analysis (substring match, case-insensitive).
_NOISE_KEYS = ("logo", "image", "picture", "photo", "banner", "background", "urn", "tracking")
# Exact keys to drop: big lists about *other* pages/people, and scraper bookkeeping.
# (Amazon's affiliatedPages alone was 104k chars and pushed the real data past the truncation limit.)
_DROP_KEYS = {
    "affiliatedpages", "affiliatedorganizations", "similarorganizations", "moreprofiles", "interests",
    "profiletopeducation", "author", "query", "originalquery", "requestid", "socialcontent",
    "reactionids", "commentids", "entityid", "contentattributes", "header",
}


class ScrapeError(Exception):
    pass


FOUNDER_MAX_POSTS = 5
FOUNDER_POSTS_WINDOW = "3months"


@dataclass
class ScrapeResult:
    linkedin_text: str = ""
    website_text: str = ""
    founder_text: str = ""
    errors: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


def _client() -> ApifyClientAsync:
    return ApifyClientAsync(env("APIFY_TOKEN"))


async def _run_actor(
    client: ApifyClientAsync, actor_id: str, run_input: dict, timeout_s: int, max_charge_usd: str
) -> tuple[list[dict], float]:
    run = await client.actor(actor_id).call(
        run_input=run_input,
        run_timeout=timedelta(seconds=timeout_s),
        max_total_charge_usd=Decimal(max_charge_usd),
        logger=None,
    )
    if run is None:
        raise ScrapeError(f"{actor_id}: run did not start")
    status = str(getattr(run.status, "value", run.status))
    if status != "SUCCEEDED":
        raise ScrapeError(f"{actor_id}: run {status} ({run.status_message or 'no message'})")
    page = await client.dataset(run.default_dataset_id).list_items(clean=True)
    # The run returned by call() can carry a not-yet-final usage figure; re-read it.
    final = await client.run(run.id).get()
    return list(page.items), float((final or run).usage_total_usd or 0)


async def _with_retries(label: str, fn):
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            return await fn()
        except ValueError:
            raise  # bad input, retrying won't help
        except Exception as e:  # network, actor failure, empty result
            last = e
            log.warning("%s attempt %d/%d failed: %s", label, attempt + 1, RETRIES + 1, e)
            if attempt < RETRIES:
                await asyncio.sleep(BACKOFF_SECONDS * 2**attempt)
    raise ScrapeError(f"{label} failed after {RETRIES + 1} attempts: {last}")


def _compact(value):
    """Drop empty values and image/tracking fields so the JSON stays readable."""
    if isinstance(value, dict):
        out = {
            k: _compact(v)
            for k, v in value.items()
            if k.lower() not in _DROP_KEYS and not any(n in k.lower() for n in _NOISE_KEYS)
        }
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [c for c in (_compact(v) for v in value) if c not in (None, "", [], {})]
    return value


def _truncate(text: str, label: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    log.info("%s text truncated from %d to %d chars", label, len(text), MAX_CHARS)
    return text[:MAX_CHARS] + "\n[...truncated]"


def format_linkedin(items: list[dict]) -> str:
    if not items:
        return ""
    return _truncate(json.dumps(_compact(items[0]), ensure_ascii=False, indent=1), "LinkedIn")


def _page_rank(item: dict, start_url: str) -> int:
    url = (item.get("url") or "").lower().rstrip("/")
    if url == start_url.lower().rstrip("/"):
        return 0
    return 1 if any(p in url for p in _PRIORITY_PATHS) else 2


def format_website(items: list[dict], start_url: str) -> str:
    pages = []
    for item in sorted(items, key=lambda i: _page_rank(i, start_url)):
        body = (item.get("markdown") or item.get("text") or "").strip()
        if not body:
            continue
        title = (item.get("metadata") or {}).get("title") or ""
        pages.append(f"## {title} ({item.get('url', '')})\n{body}")
    return _truncate("\n\n".join(pages), "Website")


async def scrape_linkedin(client: ApifyClientAsync, url: str) -> tuple[str, float]:
    if "linkedin.com/company/" not in url and "linkedin.com/school/" not in url:
        raise ValueError(f"not a LinkedIn company URL: {url!r}")

    async def attempt():
        items, cost = await _run_actor(
            client, env("APIFY_LINKEDIN_ACTOR"), {"companies": [url]}, timeout_s=180, max_charge_usd="0.05"
        )
        text = format_linkedin(items)
        if not text:
            raise ScrapeError("LinkedIn returned no data")
        return text, cost

    return await _with_retries("LinkedIn", attempt)


def _website_input(url: str, crawler_type: str) -> dict:
    return {
        "startUrls": [{"url": url}],
        "crawlerType": crawler_type,
        "maxCrawlPages": WEBSITE_MAX_PAGES,
        "maxResults": WEBSITE_MAX_PAGES,
        "maxCrawlDepth": 1,
        "proxyConfiguration": {"useApifyProxy": True},
        "removeCookieWarnings": True,
        "blockMedia": True,
        "saveMarkdown": True,
    }


async def scrape_website(client: ApifyClientAsync, url: str) -> tuple[str, float]:
    if not url:
        raise ValueError("no website URL")

    async def attempt():
        total = 0.0
        # Plain HTTP crawl is fast and cheap; fall back to a browser for JS-rendered sites.
        for crawler in ("cheerio", "playwright:adaptive"):
            items, cost = await _run_actor(
                client, env("APIFY_WEBSITE_ACTOR"), _website_input(url, crawler), timeout_s=300, max_charge_usd="0.50"
            )
            total += cost
            text = format_website(items, url)
            if len(text) >= 200:
                return text, total
            log.info("Website crawl with %s returned too little text, trying next crawler", crawler)
        raise ScrapeError("website returned no readable text")

    return await _with_retries("Website", attempt)


def format_founder(profile_items: list[dict], post_items: list[dict]) -> str:
    profile = _compact(profile_items[0]) if profile_items else {}
    # Posting activity is the key buying signal, so state the count explicitly.
    header = (
        f"Posts in the last 3 months (up to {FOUNDER_MAX_POSTS} fetched): {len(post_items)}"
        + (" - NOT POSTING RECENTLY" if not post_items else "")
    )
    body = json.dumps({"profile": profile, "recent_posts": _compact(post_items)}, ensure_ascii=False, indent=1)
    return _truncate(f"{header}\n{body}", "Founder")


async def scrape_founder(client: ApifyClientAsync, url: str) -> tuple[str, float]:
    if "linkedin.com/in/" not in url:
        raise ValueError(f"not a LinkedIn personal profile URL: {url!r}")

    async def attempt():
        (profile, c1), (posts, c2) = await asyncio.gather(
            _run_actor(client, env("APIFY_PROFILE_ACTOR", "harvestapi/linkedin-profile-scraper"),
                       {"queries": [url]}, timeout_s=180, max_charge_usd="0.05"),
            _run_actor(client, env("APIFY_POSTS_ACTOR", "harvestapi/linkedin-profile-posts"),
                       {"targetUrls": [url], "maxPosts": FOUNDER_MAX_POSTS, "postedLimit": FOUNDER_POSTS_WINDOW,
                        "scrapeReactions": False, "scrapeComments": False},
                       timeout_s=180, max_charge_usd="0.05"),
        )
        if not profile:
            raise ScrapeError("founder profile returned no data")
        return format_founder(profile, posts), c1 + c2

    return await _with_retries("Founder LinkedIn", attempt)


async def _skip() -> None:
    return None


async def scrape_all(lead: Lead, client: ApifyClientAsync | None = None) -> ScrapeResult:
    client = client or _client()
    result = ScrapeResult()
    # The founder profile is an optional form question; skip it quietly when blank.
    founder_task = scrape_founder(client, lead.founder_linkedin_url) if lead.founder_linkedin_url else _skip()
    outcomes = await asyncio.gather(
        scrape_linkedin(client, lead.linkedin_url),
        scrape_website(client, lead.website),
        founder_task,
        return_exceptions=True,
    )
    for (label, attr), outcome in zip(
        (("LinkedIn", "linkedin_text"), ("website", "website_text"), ("founder LinkedIn", "founder_text")), outcomes
    ):
        if outcome is None:
            continue
        if isinstance(outcome, BaseException):
            log.error("%s scrape failed for %s: %s", label, lead.email, outcome)
            result.errors.append(f"{label} scrape failed")
            continue
        text, cost = outcome
        result.cost_usd += cost
        setattr(result, attr, text)
    return result
