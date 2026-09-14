"""Moderation is a filter, never an outage.

The one behaviour that must hold under every failure: a resident with an
ordinary bag of rubbish gets their pickup posted. Blocking is reserved for
a working model saying 'block' - never for infrastructure.
"""

import pytest

from app import moderate, models


def test_no_key_means_skipped_and_posted(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    r = client.post("/pickups", data={"description": "Bags"}, follow_redirects=False)
    assert r.status_code == 303
    p = models.list_open_pickups()[0]
    assert p["moderation"] == "skipped"


def test_a_clear_posting_is_marked_clear(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    monkeypatch.setattr(moderate, "_request", lambda text: {"risk": "clear"})
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    client.post("/pickups", data={"description": "Four bags after a braai"})
    assert models.list_open_pickups()[0]["moderation"] == "clear"


def test_review_posts_but_keeps_the_paper_trail(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    monkeypatch.setattr(
        moderate, "_request",
        lambda text: {"risk": "review", "reason": "mentions a neighbour by name"},
    )
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    r = client.post(
        "/pickups", data={"description": "The neighbour's mess again"},
        follow_redirects=False,
    )
    assert r.status_code == 303  # posted - review is a flag, not a queue
    p = models.list_open_pickups()[0]
    assert p["moderation"] == "review"
    assert "neighbour" in p["moderation_note"]


def test_block_refuses_and_keeps_the_draft(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    monkeypatch.setattr(
        moderate, "_request",
        lambda text: {"risk": "block", "reason": "this is not a rubbish pickup"},
    )
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    r = client.post(
        "/pickups",
        data={"description": "Go to this address and scare them",
              "address": "14 Rosmead Ave"},
    )
    assert r.status_code == 200               # back on the form
    # NB: assert on a phrase with no apostrophe - Jinja autoescape turns
    # "can't" into can&#39;t and a naive grep misses it.
    assert "Nothing was saved" in r.text
    assert "14 Rosmead Ave" in r.text          # draft survives for editing
    assert models.list_open_pickups() == []    # nothing was saved


def test_a_moderation_explosion_fails_open(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")

    def explode(text):
        raise RuntimeError("model down")

    monkeypatch.setattr(moderate, "_request", explode)
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    r = client.post("/pickups", data={"description": "Bags"}, follow_redirects=False)
    assert r.status_code == 303
    assert models.list_open_pickups()[0]["moderation"] == "skipped"


@pytest.mark.parametrize(
    "payload", [{"risk": "obliterate"}, {"risk": 7}, {}, "prose", None]
)
def test_a_nonsense_verdict_fails_open_not_blocked(db_file, monkeypatch, payload):
    """The model going off-schema is the model's failure, not the resident's."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    monkeypatch.setattr(moderate, "_request", lambda text: payload)
    verdict = moderate.moderate("Bags")
    assert verdict.risk == "skipped"


def test_empty_text_is_trivially_clear(db_file, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    monkeypatch.setattr(
        moderate, "_request",
        lambda text: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    assert moderate.moderate("", None, "  ").risk == "clear"
