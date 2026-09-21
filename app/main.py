"""FastAPI entry point: Typeform webhook -> background pipeline."""

import json
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from app.config import FieldRefs, env
from app.pipeline import process_lead
from app.typeform import is_complete_submission, parse_lead, verify_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sales_copilot")

app = FastAPI(title="Sales Copilot")

# Typeform retries deliveries it thinks failed; skip responses already accepted this process lifetime.
_seen_responses: set[str] = set()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook/typeform")
async def typeform_webhook(request: Request, background: BackgroundTasks) -> dict:
    body = await request.body()
    if not verify_signature(body, request.headers.get("Typeform-Signature"), env("TYPEFORM_SECRET")):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON")

    if not is_complete_submission(payload):
        return {"status": "ignored", "reason": "partial response"}

    lead = parse_lead(payload, FieldRefs.from_env())
    if lead.response_id and lead.response_id in _seen_responses:
        return {"status": "duplicate"}
    _seen_responses.add(lead.response_id)

    log.info("Accepted lead %s", lead.email)
    background.add_task(process_lead, lead)
    return {"status": "accepted"}
