"""The free-text parser is a convenience, never a gate.

`coerce` is the pure half - it takes whatever came back from the model and
has to produce the six fields without raising, no matter what it's handed.
These are the shapes we could think of. (The route-level "submission still
works after a failed parse" test lives in test_flow.py.)
"""

import pytest

from app import parse

RAW = "3 bags of garden cuttings and a broken chair, 14 Rosmead Ave, tomorrow morning"


def test_a_good_response_becomes_fields():
    result = parse.coerce(
        {
            "description": "Garden cuttings and a broken chair",
            "bag_count": 3,
            "size": "medium",
            "address": "14 Rosmead Ave",
            "when_text": "tomorrow morning",
            "notes": None,
        },
        RAW,
    )
    assert result["parsed"] is True
    assert result["description"] == "Garden cuttings and a broken chair"
    assert result["bag_count"] == 3
    assert result["size"] == "medium"
    assert result["notes"] is None


def test_prose_instead_of_json_falls_back_to_the_raw_text():
    result = parse.coerce(
        "Sure! It sounds like you have three bags of garden cuttings to collect.",
        RAW,
    )
    assert result["parsed"] is False
    assert result["description"] == RAW
    assert result["address"] is None
    assert "prose" in result["parse_note"]


def test_json_wrapped_in_prose_is_still_read():
    result = parse.coerce(
        'Here you go:\n```json\n{"description": "Broken chair", "bag_count": 1}\n```\nHope that helps!',
        RAW,
    )
    assert result["parsed"] is True
    assert result["description"] == "Broken chair"
    assert result["bag_count"] == 1


def test_a_json_string_rather_than_an_object_is_read():
    result = parse.coerce('{"description": "Broken chair"}', RAW)
    assert result["parsed"] is True
    assert result["description"] == "Broken chair"


def test_invented_fields_are_ignored_not_stored():
    result = parse.coerce(
        {
            "description": "Broken chair",
            "urgency": "critical",
            "price_estimate_zar": 250,
            "resident_mood": "cheerful",
        },
        RAW,
    )
    assert result["parsed"] is True
    assert set(result) == {
        "description", "bag_count", "size", "address", "when_text",
        "notes", "window_start", "window_end", "raw_text", "parsed", "parse_note",
    }
    assert "urgency" not in result


def test_a_missing_description_falls_back():
    """Every other field is optional; this one is the pickup."""
    result = parse.coerce({"address": "14 Rosmead Ave", "bag_count": 3}, RAW)
    assert result["parsed"] is False
    assert result["description"] == RAW


@pytest.mark.parametrize(
    "payload",
    [None, [], 42, True, "", "   ", {"description": ""}, {"description": None}, {}],
)
def test_empty_and_nonsense_payloads_fall_back(payload):
    result = parse.coerce(payload, RAW)
    assert result["parsed"] is False
    assert result["description"] == RAW


def test_a_list_wrapper_is_unwrapped():
    result = parse.coerce([{"description": "Broken chair"}], RAW)
    assert result["parsed"] is True
    assert result["description"] == "Broken chair"


@pytest.mark.parametrize(
    "value,expected",
    [
        (3, 3), ("3", 3), ("three bags", None), ("about 4", 4), (4.0, 4),
        (0, None), (-2, None), (9999, None), (True, None), (None, None),
        ({"count": 3}, None),
    ],
)
def test_bag_count_only_survives_if_it_is_a_number(value, expected):
    result = parse.coerce({"description": "Bags", "bag_count": value}, RAW)
    assert result["bag_count"] == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("large", "large"), ("Large", "large"), ("quite large", "large"),
        ("enormous", None), ("", None), (None, None), (7, None),
    ],
)
def test_size_is_clamped_to_the_three_we_offer(value, expected):
    result = parse.coerce({"description": "Stuff", "size": value}, RAW)
    assert result["size"] == expected


@pytest.mark.parametrize("null_ish", ["null", "None", "N/A", "unknown", "unspecified"])
def test_the_model_writing_null_as_a_word_is_treated_as_null(null_ish):
    result = parse.coerce({"description": "Stuff", "address": null_ish}, RAW)
    assert result["address"] is None


def test_absurdly_long_fields_are_trimmed():
    result = parse.coerce({"description": "x" * 5000}, RAW)
    assert len(result["description"]) == parse.MAX_FIELD_CHARS


# --- the full function, with the model call stubbed out ---------------------


def test_no_api_key_means_no_call_and_a_clean_fallback(db_file, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        parse, "_request", lambda text: pytest.fail("should not have called the API")
    )

    result = parse.parse_free_text(RAW)
    assert result["parsed"] is False
    assert result["description"] == RAW
    assert "ANTHROPIC_API_KEY" in result["parse_note"]


def test_an_exploding_api_call_never_reaches_the_caller(db_file, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")

    def explode(text):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(parse, "_request", explode)

    result = parse.parse_free_text(RAW)
    assert result["parsed"] is False
    assert result["description"] == RAW
    assert result["parse_note"]


def test_empty_input_is_handled_without_calling_anything(db_file):
    result = parse.parse_free_text("   ")
    assert result["parsed"] is False
    assert result["description"] == ""


def test_over_long_input_is_truncated_before_it_is_sent(db_file, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    seen = {}

    def capture(text):
        seen["text"] = text
        return {"description": "Lots of bags"}

    monkeypatch.setattr(parse, "_request", capture)

    parse.parse_free_text("bags " * 2000)
    assert len(seen["text"]) <= parse.MAX_INPUT_CHARS


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-09-15T09:00", "2026-09-15 09:00"),
        ("2026-09-15 09:00:30", "2026-09-15 09:00"),
        ("tomorrow morning", None),
        ("whenever suits", None),
        ("2026-09-15", None),  # date without a time is not a window
        ("20260915", None),    # basic-ISO trap: fromisoformat parses this
        (20260915, None), (None, None),
    ],
)
def test_windows_only_survive_if_datetime_shaped(value, expected):
    result = parse.coerce({"description": "Bags", "window_start": value}, RAW)
    assert result["window_start"] == expected


def test_a_backwards_window_keeps_start_and_drops_end():
    result = parse.coerce(
        {"description": "Bags",
         "window_start": "2026-09-15T12:00", "window_end": "2026-09-15T09:00"},
        RAW,
    )
    assert result["window_start"] == "2026-09-15 12:00"
    assert result["window_end"] is None


# --- the local rung of the ladder: Ollama ----------------------------------


def test_with_no_key_a_local_model_answers(db_file, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_ENABLED", "1")
    monkeypatch.setattr(
        parse, "_ollama_request",
        lambda text: '{"description": "Garden cuttings", "bag_count": 3}',
    )
    result = parse.parse_free_text(RAW)
    assert result["parsed"] is True
    assert result["bag_count"] == 3
    assert "Ollama" in result["parse_note"]  # transparency about which brain


def test_a_local_model_answering_in_prose_falls_back(db_file, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_ENABLED", "1")
    monkeypatch.setattr(
        parse, "_ollama_request",
        lambda text: "Sounds like you have some garden cuttings there!",
    )
    result = parse.parse_free_text(RAW)
    assert result["parsed"] is False
    assert result["description"] == RAW


def test_ollama_down_degrades_to_type_it_yourself(db_file, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_ENABLED", "1")

    def refused(text):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(parse, "_ollama_request", refused)
    result = parse.parse_free_text(RAW)
    assert result["parsed"] is False
    assert "no local model answered" in result["parse_note"]


def test_the_anthropic_key_outranks_the_local_model(db_file, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    monkeypatch.setenv("OLLAMA_ENABLED", "1")
    monkeypatch.setattr(parse, "_request", lambda text: {"description": "From the API"})
    monkeypatch.setattr(
        parse, "_ollama_request",
        lambda text: pytest.fail("must not fall through to Ollama when a key exists"),
    )
    assert parse.parse_free_text(RAW)["description"] == "From the API"
