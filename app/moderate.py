"""A moderation pass over what residents type, before other people see it.

Descriptions, notes and addresses are free text from one stranger shown to
other strangers. Following the Claude docs' content-moderation guide, this
is a classification with risk levels rather than a binary:

    clear  -> post normally
    review -> post, but keep a flag and a reason on the row. There is no
              admin screen yet, so "review" is a paper trail, not a queue -
              an honest limitation, recorded here and in the docs.
    block  -> refuse the submission, tell the resident why, keep their draft

And one overriding rule, same as geocoding and parsing: infrastructure
failure is not a verdict. No key, network down, model returns nonsense -
the post goes through as 'skipped'. Moderation is a filter, never an
outage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import config

log = logging.getLogger(__name__)

CLEAR, REVIEW, BLOCK, SKIPPED = "clear", "review", "block", "skipped"

SYSTEM = """You moderate postings for a neighbourhood rubbish-collection app.
Residents describe rubbish; collectors read it and drive to the address.

Classify the posting:
- clear: an ordinary rubbish pickup, even a strange one.
- review: probably fine but a human should glance at it - possible personal
  data about someone else, veiled hostility, a transaction that isn't
  rubbish collection.
- block: clearly not a rubbish pickup in good faith - abuse or threats,
  doxxing, solicitation of anything illegal, or using the app to send
  people to an address for another purpose.

risk: one of clear/review/block. reason: one short sentence, written to be
shown to the resident when blocking. Ordinary rubbish is clear - do not
over-flag."""


@dataclass(frozen=True)
class Verdict:
    risk: str
    reason: str | None = None


def _request(text: str) -> dict:
    """The model call. Raises on any failure; moderate() fails open."""
    import anthropic
    from pydantic import BaseModel

    class Moderation(BaseModel):
        risk: str
        reason: str | None = None

    client = anthropic.Anthropic(api_key=config.anthropic_api_key())
    response = client.with_options(timeout=15.0).messages.parse(
        model=config.anthropic_model(),
        max_tokens=1024,
        system=SYSTEM,
        messages=[{"role": "user", "content": text[:3000]}],
        output_config={"effort": "low"},
        output_format=Moderation,
    )
    parsed = response.parsed_output
    return parsed.model_dump() if parsed is not None else {}


def moderate(*parts: str | None) -> Verdict:
    """Classify a posting's combined free text. Never raises."""
    text = "\n".join(p.strip() for p in parts if p and p.strip())
    if not text:
        return Verdict(CLEAR)
    if not config.anthropic_api_key():
        return Verdict(SKIPPED, "no ANTHROPIC_API_KEY - moderation not run")

    try:
        payload = _request(text)
    except Exception as exc:  # noqa: BLE001 - fail open, always
        log.warning("moderation failed open: %s", exc, exc_info=True)
        return Verdict(SKIPPED, "moderation errored - posted unchecked")

    risk = payload.get("risk") if isinstance(payload, dict) else None
    risk = risk.strip().lower() if isinstance(risk, str) else ""
    if risk not in (CLEAR, REVIEW, BLOCK):
        # The model answered outside its own schema's intent; that is the
        # model's failure, not the resident's. Fail open.
        return Verdict(SKIPPED, f"moderation returned {risk!r} - posted unchecked")

    reason = payload.get("reason")
    reason = reason.strip()[:300] if isinstance(reason, str) and reason.strip() else None
    return Verdict(risk, reason)
