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

FIELDS = (
    "description", "bag_count", "size", "address", "when_text", "notes",
    "window_start", "window_end",
)
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
- window_start / window_end: that same wish as concrete local datetimes, format
  YYYY-MM-DDTHH:MM, computed from the "today is" line in the message ("tomorrow
  morning" -> next day 08:00 to 12:00). Null when they gave no time at all.
  Never invent a window they didn't imply.
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
        window_start=None,
        window_end=None,
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


def _clean_datetime(value: Any) -> str | None:
    """Accept only something datetime-shaped; normalise to YYYY-MM-DD HH:MM.

    The model is asked for ISO 8601, but this also survives a space instead
    of the T, trailing seconds, and of course prose like "tomorrow-ish".
    """
    from datetime import datetime

    if not isinstance(value, str):
        return None  # a bare number like 20260915 must not become a date
    text = _clean_str(value)
    # Demand date AND time in extended form: fromisoformat alone happily
    # parses "20260915" (basic ISO) into midnight on a real day.
    if text is None or not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text):
        return None
    try:
        return datetime.fromisoformat(text.replace(" ", "T")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
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

    window_start = _clean_datetime(payload.get("window_start"))
    window_end = _clean_datetime(payload.get("window_end"))
    if window_start and window_end and window_end <= window_start:
        window_end = None  # a backwards window is no window

    return ParseResult(
        description=description,
        bag_count=_clean_int(payload.get("bag_count")),
        size=_clean_size(payload.get("size")),
        address=_clean_str(payload.get("address")),
        when_text=_clean_str(payload.get("when_text")),
        notes=_clean_str(payload.get("notes")),
        window_start=window_start,
        window_end=window_end,
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
        window_start: str | None = None
        window_end: str | None = None

    from datetime import datetime

    today = datetime.now().strftime("%A %Y-%m-%d %H:%M")
    client = anthropic.Anthropic(api_key=config.anthropic_api_key())
    response = client.with_options(timeout=20.0).messages.parse(
        model=config.anthropic_model(),
        max_tokens=2048,
        system=SYSTEM,
        messages=[{"role": "user", "content": f"(Today is {today}.)\n\n{text}"}],
        output_config={"effort": "low"},
        output_format=PickupFields,
    )
    parsed = response.parsed_output
    return parsed.model_dump() if parsed is not None else None


VISION_PROMPT = """This photo shows rubbish a resident wants collected. Fill the
fields from what you can actually see: description (what the pile is), bag_count
(bags/bin-sized items you can count), size (small: fits one bag; medium: a few
bags; large: furniture, rubble, a bakkie load), notes (anything a collector
should know before driving out - heavy items, hazards, access). Leave address
and the time fields null - a photo doesn't know those. Never invent."""


def _vision_request(image_b64: str, media_type: str) -> Any:
    """One vision call with the same structured-output schema. Raises on any
    failure; analyze_photo degrades."""
    import anthropic
    from pydantic import BaseModel

    class PickupFields(BaseModel):
        description: str
        bag_count: int | None = None
        size: str | None = None
        notes: str | None = None

    client = anthropic.Anthropic(api_key=config.anthropic_api_key())
    response = client.with_options(timeout=30.0).messages.parse(
        model=config.anthropic_model(),
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": media_type,
                            "data": image_b64}},
                {"type": "text", "text": VISION_PROMPT},
            ],
        }],
        output_config={"effort": "low"},
        output_format=PickupFields,
    )
    parsed = response.parsed_output
    return parsed.model_dump() if parsed is not None else None


def analyze_photo(image_b64: str, media_type: str) -> ParseResult:
    """Photo -> the same six fields. Total function - never raises.

    Runs through the same coerce() as the text parser, so an invented field
    or a bag count of "several" dies in exactly the same place.
    """
    if not config.anthropic_api_key():
        return fallback("", "No ANTHROPIC_API_KEY set - describe it yourself.")
    try:
        payload = _vision_request(image_b64, media_type)
    except Exception as exc:  # noqa: BLE001 - deliberately total
        log.warning("photo analysis failed: %s", exc, exc_info=True)
        return fallback("", _describe_error(exc))
    result = coerce(payload, raw_text="")
    # A photo cannot know where or when; never let the model claim it does.
    result["address"] = None
    result["when_text"] = None
    result["window_start"] = None
    result["window_end"] = None
    return result


def _ollama_request(text: str) -> Any:
    """The same extraction, via a local model on Ollama. Raises on failure.

    Returns whatever the model wrote as its message content - usually a JSON
    string, since we ask for format=json - and coerce() treats it with the
    same suspicion as everything else. Local models answer in prose or wrap
    JSON in fences far more often than the API does; that is exactly the
    garbage path the coerce tests already cover.
    """
    import json as _json
    from datetime import datetime

    import httpx2 as httpx

    today = datetime.now().strftime("%A %Y-%m-%d %H:%M")
    response = httpx.post(
        config.ollama_url().rstrip("/") + "/api/chat",
        json={
            "model": config.ollama_model(),
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM
                 + "\nAnswer ONLY with a JSON object holding exactly these keys: "
                 + ", ".join(FIELDS) + "."},
                {"role": "user", "content": f"(Today is {today}.)\n\n{text}"},
            ],
        },
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("message", {}).get("content")


def parse_free_text(text: str) -> ParseResult:
    """Structure a resident's free text. Total function - never raises.

    Backend chain: Anthropic API when a key is set; otherwise a local model
    via Ollama when one answers; otherwise the resident types it themselves.
    Rule 8 in miniature - the paid path is optional, the free path is real,
    and no path can block the submission. (Photos skip the local rung: the
    default local model has no vision.)
    """
    raw = (text or "").strip()
    if not raw:
        return fallback(raw, "Nothing to parse.")
    if len(raw) > MAX_INPUT_CHARS:
        raw = raw[:MAX_INPUT_CHARS]

    if config.anthropic_api_key():
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

    if config.ollama_enabled():
        try:
            payload = _ollama_request(raw)
        except Exception as exc:  # noqa: BLE001 - deliberately total
            log.info("local parse unavailable (%s)", exc)
            return fallback(
                raw,
                "No ANTHROPIC_API_KEY set and no local model answered - "
                "fill the fields in yourself.",
            )
        result = coerce(payload, raw)
        if result["parsed"]:
            result["parse_note"] = f"Parsed locally by {config.ollama_model()} via Ollama."
        return result

    return fallback(raw, "No ANTHROPIC_API_KEY set - fill the fields in yourself.")


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
