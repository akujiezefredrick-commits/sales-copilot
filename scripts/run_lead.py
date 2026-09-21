"""Run one lead through the pipeline from the command line (no webhook, no server).

    python scripts/run_lead.py --website https://acme.com --linkedin https://www.linkedin.com/company/acme
    python scripts/run_lead.py ... --scrape-only     # Apify only, prints what was scraped
    python scripts/run_lead.py ... --no-sheet        # scrape + Claude, print result, don't write the sheet
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.analyze import analyze  # noqa: E402
from app.models import Lead  # noqa: E402
from app.pipeline import process_lead  # noqa: E402
from app.scrape import scrape_all  # noqa: E402
from app.typeform import normalize_url  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--website", required=True)
    p.add_argument("--linkedin", required=True)
    p.add_argument("--founder-linkedin", default="", help="optional personal profile (linkedin.com/in/...)")
    p.add_argument("--name", default="Test Lead")
    p.add_argument("--email", default="test.lead@example.com")
    p.add_argument("--phone", default="")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--scrape-only", action="store_true")
    mode.add_argument("--no-sheet", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    lead = Lead(
        response_id="cli",
        full_name=args.name,
        email=args.email,
        phone=args.phone,
        website=normalize_url(args.website),
        linkedin_url=normalize_url(args.linkedin),
        founder_linkedin_url=normalize_url(args.founder_linkedin),
    )

    if not (args.scrape_only or args.no_sheet):
        print(json.dumps((await process_lead(lead)).model_dump(), indent=2, ensure_ascii=False))
        return

    scraped = await scrape_all(lead)
    print(f"\nApify cost: ${scraped.cost_usd:.4f}   errors: {scraped.errors or 'none'}")
    print(f"LinkedIn: {len(scraped.linkedin_text)} chars   Website: {len(scraped.website_text)} chars   "
          f"Founder: {len(scraped.founder_text)} chars")
    if args.scrape_only:
        print("\n===== LINKEDIN =====\n" + scraped.linkedin_text[:3000])
        print("\n===== FOUNDER =====\n" + scraped.founder_text[:3000])
        print("\n===== WEBSITE =====\n" + scraped.website_text[:3000])
        return
    enrichment = await analyze(lead, scraped)
    print(json.dumps(enrichment.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
