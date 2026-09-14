"""The photo is optional evidence, never a gate - same contract as every
other external input in this app. And the vision analyser is the text
parser's coerce() with an image in front, so all the garbage handling is
already tested; here we test the seams."""

import base64

from app import models, parse

# A real 1x1 transparent PNG.
PNG = base64.standard_b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _post_pickup(client, **extra):
    files = extra.pop("files", None)
    return client.post(
        "/pickups",
        data={"description": "Bags with evidence", **extra},
        files=files,
        follow_redirects=False,
    )


def test_a_photo_uploads_and_serves_back(client):
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    r = _post_pickup(client, files={"photo": ("pile.png", PNG, "image/png")})
    assert r.status_code == 303
    pid = models.list_open_pickups()[0]["id"]
    assert models.list_open_pickups()[0]["has_photo"]

    served = client.get(f"/pickups/{pid}/photo")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == PNG


def test_photos_require_being_signed_in(client, db_file):
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    _post_pickup(client, files={"photo": ("pile.png", PNG, "image/png")})
    pid = models.list_open_pickups()[0]["id"]

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as stranger:
        r = stranger.get(f"/pickups/{pid}/photo", follow_redirects=False)
        assert r.status_code == 303  # sent to /join, not handed the image


def test_an_oversized_photo_never_blocks_the_pickup(client):
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    big = b"x" * (models.PHOTO_MAX_BYTES + 1)
    r = _post_pickup(client, files={"photo": ("huge.png", big, "image/png")})
    assert r.status_code == 303
    pickup = models.list_open_pickups()[0]
    assert not pickup["has_photo"]  # posted, just without the photo


def test_a_non_image_never_blocks_the_pickup(client):
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    r = _post_pickup(client, files={"photo": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 303
    assert not models.list_open_pickups()[0]["has_photo"]


def test_posting_with_no_photo_still_works(client):
    """The multipart change must not break the plain form path."""
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    assert _post_pickup(client).status_code == 303


def test_parse_photo_degrades_without_a_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    r = client.post("/parse-photo", files={"photo": ("pile.png", PNG, "image/png")})
    assert r.status_code == 200
    assert r.json()["parsed"] is False
    assert "ANTHROPIC_API_KEY" in r.json()["parse_note"]


def test_parse_photo_fills_fields_and_never_invents_a_place_or_time(
    client, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    monkeypatch.setattr(
        parse,
        "_vision_request",
        lambda b64, mime: {
            "description": "About six black bags and a broken chair",
            "bag_count": 6,
            "size": "large",
            "notes": "Chair looks heavy",
            "address": "14 Hallucinated Street",   # the model must not know this
            "window_start": "2026-09-15T09:00",    # nor this
        },
    )
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    body = client.post(
        "/parse-photo", files={"photo": ("pile.png", PNG, "image/png")}
    ).json()
    assert body["parsed"] is True
    assert body["bag_count"] == 6 and body["size"] == "large"
    assert body["address"] is None
    assert body["window_start"] is None


def test_a_vision_explosion_degrades_like_the_text_parser(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")

    def explode(b64, mime):
        raise RuntimeError("model fell over")

    monkeypatch.setattr(parse, "_vision_request", explode)
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    body = client.post(
        "/parse-photo", files={"photo": ("pile.png", PNG, "image/png")}
    ).json()
    assert body["parsed"] is False
