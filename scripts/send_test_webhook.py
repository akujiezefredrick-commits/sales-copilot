"""Send a signed Typeform-style webhook to the local server.

    python scripts/send_test_webhook.py --website https://acme.com --linkedin https://www.linkedin.com/company/acme
"""

import argparse
import base64
import copy
import hashlib
import hmac
import json
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import FieldRefs, env  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "typeform_sample.json"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000/webhook/typeform")
    p.add_argument("--name", default="Test Lead")
    p.add_argument("--email", default="test.lead@example.com")
    p.add_argument("--phone", default="+34123456789")
    p.add_argument("--website", default="https://example.com")
    p.add_argument("--linkedin", default="https://www.linkedin.com/company/example")
    p.add_argument("--founder-linkedin", default="")
    args = p.parse_args()

    payload = copy.deepcopy(json.loads(FIXTURE.read_text()))
    payload["form_response"]["token"] = uuid.uuid4().hex  # new id so it isn't treated as a duplicate
    refs = FieldRefs.from_env()
    values = {
        refs.full_name: args.name,
        refs.email: args.email,
        refs.phone: args.phone,
        refs.website: args.website,
        refs.linkedin: args.linkedin,
    }
    for answer in payload["form_response"]["answers"]:
        ref = answer["field"]["ref"]
        if ref in values:
            answer[answer["type"]] = values[ref]
    if refs.founder_linkedin and args.founder_linkedin:
        payload["form_response"]["answers"].append(
            {"type": "url", "url": args.founder_linkedin,
             "field": {"id": "founder", "type": "website", "ref": refs.founder_linkedin}}
        )

    body = json.dumps(payload).encode()
    digest = hmac.new(env("TYPEFORM_SECRET").encode(), body, hashlib.sha256).digest()
    headers = {
        "Content-Type": "application/json",
        "Typeform-Signature": "sha256=" + base64.b64encode(digest).decode(),
    }
    r = httpx.post(args.url, content=body, headers=headers, timeout=30)
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
