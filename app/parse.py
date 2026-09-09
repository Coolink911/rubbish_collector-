"""Free text -> the fields a pickup form wants.

A resident types "3 bags of garden stuff and a broken chair, 14 Rosmead Ave,
any time tomorrow morning" and this turns it into structured fields they can
correct before submitting.

The design rule: this is a convenience, never a gate. `parse_free_text` is
total - it always returns a dict with the right keys. If the API key is
missing, the network is down, the model answers in prose, or it invents a
field nobody asked for, the fallback is the raw text in the description and
`parsed: False`. The submission goes through either way.

That is why the model call (`_request`) and the sanitising (`coerce`) are
separate functions: `coerce` is pure and gets tested directly against every
shape of bad output we could think of, with no key and no network.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import config

log = logging.getLogger(__name__)

FIELDS = ("description", "bag_count", "size", "address", "when_text", "notes")
SIZES = ("small", "medium", "large")

MAX_INPUT_CHARS = 2000
MAX_FIELD_CHARS = 500

SYSTEM = """You turn a resident's rubbish-collection request into structured fields.

Rules:
- Only use information present in the text. Never invent an address, a time, or a bag count.
- description: what is being collected, in a few words. Required.
- bag_count: number of bags or bin-sized items, if stated. Null otherwise.
- size: small (fits in one bag), medium (a few bags), or large (furniture, rubble, a bakkie load). Null if unclear.
- address: the street address, if stated. Null otherwise.
- when_text: when they want it collected, copied in their own words. Null otherwise.
- notes: access details a collector needs (gate codes, dogs, "behind the wall"). Null otherwise."""


class ParseResult(dict):
    """A plain dict with the six fields plus `parsed` and `parse_note`."""


def fallback(raw_text: str, note: str) -> ParseResult:
    """What every failure returns: the resident's own words, unharmed."""
    return ParseResult(
        description=(raw_text or "").strip()[:MAX_FIELD_CHARS],
        bag_count=None,
        size=None,
        address=None,
        when_text=None,
        notes=None,
        raw_text=raw_text,
        parsed=False,
        parse_note=note,
    )


def _clean_str(value: Any) -> str | None:
    """Anything that isn't a usable string becomes None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower() in ("null", "none", "n/a", "unknown", "unspecified"):
        return None
    return value[:MAX_FIELD_CHARS]


def _clean_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if 0 < value <= 200 else None
    if isinstance(value, float):
        return int(value) if 0 < value <= 200 else None
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            n = int(match.group())
            return n if 0 < n <= 200 else None
    return None


def _clean_size(value: Any) -> str | None:
    text = _clean_str(value)
    if text is None:
        return None
    text = text.lower()
    for size in SIZES:
        if size in text:
            return size
    return None


def coerce(payload: Any, raw_text: str) -> ParseResult:
    """Turn whatever the model produced into the six fields. Never raises.

    Structured outputs constrain the *shape* of a response, not its sense -
    and only when the call succeeds at all. So this treats every payload as
    untrusted. Handles, in order: a JSON string instead of an object, prose
    instead of JSON, a list wrapper, missing keys, invented keys, and wrong
    types in the keys that are there. Unknown keys are simply never read, so
    an invented field cannot reach the database.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            # Prose. Models like wrapping JSON in a fenced block, so try the
            # outermost braces before giving up.
            match = re.search(r"\{.*\}", payload, re.DOTALL)
            if not match:
                return fallback(raw_text, "The parser answered in prose, so I kept your text as-is.")
            try:
                payload = json.loads(match.group())
            except (json.JSONDecodeError, ValueError):
                return fallback(raw_text, "The parser answered in prose, so I kept your text as-is.")

    if isinstance(payload, list):
        payload = payload[0] if payload else None

    if not isinstance(payload, dict):
        return fallback(raw_text, "The parser didn't return usable fields, so I kept your text as-is.")

    description = _clean_str(payload.get("description"))
    if description is None:
        return fallback(raw_text, "The parser left out what to collect, so I kept your text as-is.")

    return ParseResult(
        description=description,
        bag_count=_clean_int(payload.get("bag_count")),
        size=_clean_size(payload.get("size")),
        address=_clean_str(payload.get("address")),
        when_text=_clean_str(payload.get("when_text")),
        notes=_clean_str(payload.get("notes")),
        raw_text=raw_text,
        parsed=True,
        parse_note=None,
    )


def _request(text: str) -> Any:
    """Ask Claude for the fields. Raises on any failure; the caller degrades.

    Uses structured outputs (messages.parse with a pydantic schema) so the
    response is schema-valid JSON rather than something fished out of prose -
    but `coerce` still treats the result as untrusted.
    """
    import anthropic
    from pydantic import BaseModel

    class PickupFields(BaseModel):
        description: str
        bag_count: int | None = None
        size: str | None = None
        address: str | None = None
        when_text: str | None = None
        notes: str | None = None

    client = anthropic.Anthropic(api_key=config.anthropic_api_key())
    response = client.with_options(timeout=20.0).messages.parse(
        model=config.anthropic_model(),
        max_tokens=2048,
        system=SYSTEM,
        messages=[{"role": "user", "content": text}],
        output_config={"effort": "low"},
        output_format=PickupFields,
    )
    parsed = response.parsed_output
    return parsed.model_dump() if parsed is not None else None


def parse_free_text(text: str) -> ParseResult:
    """Structure a resident's free text. Total function - never raises."""
    raw = (text or "").strip()
    if not raw:
        return fallback(raw, "Nothing to parse.")
    if len(raw) > MAX_INPUT_CHARS:
        raw = raw[:MAX_INPUT_CHARS]

    if not config.anthropic_api_key():
        return fallback(raw, "No ANTHROPIC_API_KEY set - fill the fields in yourself.")

    try:
        payload = _request(raw)
    except ImportError:
        log.info("anthropic package not installed; skipping parse")
        return fallback(raw, "The parser isn't installed here - fill the fields in yourself.")
    except Exception as exc:  # noqa: BLE001 - deliberately total
        # _describe_error has the specific chain; nothing about a parser
        # failure may stop a pickup being requested.
        log.warning("free-text parse failed: %s", exc, exc_info=True)
        return fallback(raw, _describe_error(exc))

    return coerce(payload, raw)


def _describe_error(exc: Exception) -> str:
    """Turn an SDK exception into something a resident can read."""
    try:
        import anthropic
    except ImportError:
        return "The parser is unavailable - fill the fields in yourself."

    if isinstance(exc, anthropic.AuthenticationError):
        return "The parser's API key was rejected - fill the fields in yourself."
    if isinstance(exc, anthropic.NotFoundError):
        return "The parser is misconfigured (unknown model) - fill the fields in yourself."
    if isinstance(exc, anthropic.RateLimitError):
        return "The parser is rate-limited right now - fill the fields in yourself."
    if isinstance(exc, anthropic.APIStatusError):
        return f"The parser returned {exc.status_code} - fill the fields in yourself."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Couldn't reach the parser - fill the fields in yourself."
    return "The parser failed - fill the fields in yourself."
