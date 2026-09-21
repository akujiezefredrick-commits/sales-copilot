# Sales Copilot

Webhook service that enriches inbound Typeform leads for the sales team.

## What it does
1. A lead submits the Typeform: full name, email, phone, website, LinkedIn **company** URL.
2. Typeform POSTs to `POST /webhook/typeform`. We verify the signature, return 200 immediately, and process in a background task.
3. Apify actors run in parallel:
   - `harvestapi/linkedin-company` for the LinkedIn company page
   - `apify/website-content-crawler` for the company website
   - Optional, only if the lead gave their personal profile: `harvestapi/linkedin-profile-scraper` (the profile) plus `harvestapi/linkedin-profile-posts` (up to 5 posts from the last 3 months). The founder's posting activity is a key ICP signal.
4. The AI reads the scraped data plus `icp.md`. `LLM_PROVIDER` in `.env` picks the provider: `gemini` (free tier, `gemini-3.6-flash`, the current default) or `anthropic` (`claude-sonnet-5`, once the account has credits). It returns structured JSON:
   `niche_industry`, `primary_service`, `icp_score` (1–10), `icp_fit_reason`, `icebreaker` (professional, 1–2 sentences).
5. One row is appended to the Google Sheet.

## Google Sheet columns
Headers in row 1 (live sheet): Full-Name | Email | Phone Number | Website | Linkedin Url | Niche | Primary Service | ICP Score(1-10) | ICP Fit Reason | Professional Ice breaker | Founder LinkedIn Url

- `app/sheets.py` reads row 1 and writes each value under its matching header (case- and whitespace-insensitive). Column order doesn't matter. Unknown headers are left blank, and a missing expected header is logged as a warning.
- "Niche" holds `Niche / Industry`.
- The sheet has no status column. Partial or failed runs are flagged by prefixing ICP fit reason with `[PARTIAL: ...]` or `[ERROR: ...]`.

## Layout
- `app/main.py`: FastAPI app (`/webhook/typeform`, `/health`)
- `app/typeform.py`: signature verification + answer mapping by field `ref`
- `app/scrape.py`: Apify calls, retries, trimming
- `app/analyze.py`: Claude call with structured output
- `app/sheets.py`: gspread append
- `app/models.py`: `Lead`, `Enrichment` (pydantic)
- `app/pipeline.py`: orchestration + failure handling
- `icp.md`: the ideal customer profile, loaded verbatim into the system prompt
- `tests/`: pytest, with Apify/Claude/Sheets mocked
- `scripts/send_test_webhook.py`: sends a signed sample payload to the local server

## Rules
- Never drop a lead. If scraping fails after 2 retries, analyze with what we have. If Claude fails, still write the contact fields plus an `[ERROR: ...]` reason.
- The icebreaker must reference something specific from the scraped data. Never invent facts.
- Truncate each scraped source to about 15k chars before sending it to Claude.
- All secrets come from `.env` (see `.env.example`). Never commit `.env` or the service-account JSON.
- Keep modules small and typed. Log every stage with the lead email as context.

## Commands
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000     # run locally
ngrok http 8000                                # expose for Typeform
python scripts/send_test_webhook.py            # replay a signed test payload
pytest                                         # tests
```

## Helper scripts
```powershell
python scripts/run_lead.py --website <url> --linkedin <company url> --scrape-only   # Apify only
python scripts/run_lead.py --website <url> --linkedin <company url> --no-sheet      # + Claude, print result
python scripts/run_lead.py --website <url> --linkedin <company url>                 # full pipeline incl. Sheet
```
If the Sheet can't be written, the lead is saved to `failed_leads.jsonl`, so nothing is lost.

## Costs (per lead)
- LinkedIn company scraper: $0.004 per company (pay-per-event)
- Founder profile: $0.004. Founder posts: $0.002 per post, max 5, so about $0.01.
- Website crawler: platform usage. `cheerio` is cheap (about $0.004 on the Amazon test); `playwright:adaptive` is the fallback for JS-heavy sites and costs more.
- Measured on the Amazon test: about $0.023 per lead for Apify, all four scrapers.
- Gemini free tier: $0 (rate-limited, with automatic retries on 429). Google may use free-tier prompts, so lead email and phone are never sent to the AI.
- Claude Sonnet 5: $2 / $10 per 1M input/output tokens. About 5–10k input tokens per lead.

## Status
- Steps 1–6 written, 36 tests passing. The server runs locally.
- Google Sheet connected (service account `sales-co-pilot@sales-co-pilot-509310.iam.gserviceaccount.com`, tab `Sheet1`); a test write succeeded.
- `icp.md` written: a done-for-you content/lead-gen agency for founder-led coaches and consultants.
- Using Gemini's free tier because the Anthropic balance is $0. To switch to Claude, set `LLM_PROVIDER=anthropic`.
- Step 7 done (2026-09-21): Typeform webhook live via ngrok, with the secret set in Typeform, and leads reach the Sheet end to end. The winget ngrok (3.3.1) is too old, so run the current one from `%USERPROFILE%\Downloads\ngrok-v3-stable-windows-amd64\ngrok.exe http 8000`. The free ngrok URL can change on restart; update the Typeform webhook if it does.
- Next: add the optional founder LinkedIn question to Typeform (put its ref in `TF_REF_FOUNDER_LINKEDIN`).
- Local only for now (ngrok). Deploy target (Railway or Render) comes later.
