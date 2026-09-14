"""The ordinary path, the role boundaries, and the route-level versions of
the ugly paths: the claim race over real HTTP with sessions, a pickup that
posts despite a dead geocoder, and a submission that survives a parser that
answered in prose.
"""

import threading

import httpx2 as httpx

from app import geocode, models, parse

RAW = "3 bags of garden cuttings and a broken chair, 14 Rosmead Ave, tomorrow morning"


def test_a_stranger_is_sent_to_the_join_page(client):
    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/join"

    response = client.get("/jobs", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/join"


def test_a_resident_lands_on_their_requests(client):
    response = client.post(
        "/join", data={"name": "Thandi", "role": "resident"}, follow_redirects=False
    )
    assert response.headers["location"] == "/requests"
    assert "My requests" in client.get("/requests").text


def test_a_collector_lands_on_the_job_board(client):
    response = client.post(
        "/join", data={"name": "Sipho", "role": "collector"}, follow_redirects=False
    )
    assert response.headers["location"] == "/jobs"
    assert "Open jobs" in client.get("/jobs").text


def test_a_collector_cannot_post_a_pickup(client):
    client.post("/join", data={"name": "Sipho", "role": "collector"})
    assert client.get("/new").status_code == 403
    assert client.post("/pickups", data={"description": "x"}).status_code == 403


def test_a_resident_cannot_claim(client):
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    client.post("/pickups", data={"description": "Broken chair"})
    pickup_id = models.list_open_pickups()[0]["id"]

    assert client.get("/jobs").status_code == 403
    assert client.post(f"/pickups/{pickup_id}/claim").status_code == 403


def test_a_blank_description_is_rejected_and_the_draft_survives(client):
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    response = client.post(
        "/pickups", data={"description": "   ", "address": "14 Rosmead Ave"}
    )
    assert response.status_code == 200
    assert "Say what needs collecting." in response.text
    assert "14 Rosmead Ave" in response.text  # they don't have to retype it
    assert models.list_open_pickups() == []


def test_the_whole_lifecycle(client, db_file):
    from app.main import app

    from .conftest import make_client

    client.post("/join", data={"name": "Thandi", "role": "resident"})
    client.post(
        "/pickups",
        data={
            "description": "Four bags after a braai",
            "bag_count": "4",
            "size": "medium",
            "address": "14 Rosmead Ave",
            "when_text": "tomorrow morning",
        },
    )
    pickup_id = models.list_open_pickups()[0]["id"]

    collector = make_client(app, "Sipho", "collector")
    assert "Four bags after a braai" in collector.get("/jobs").text

    collector.post(f"/pickups/{pickup_id}/claim")
    assert models.get_pickup(pickup_id)["status"] == "claimed"

    # The resident sees who has it, and can no longer cancel.
    page = client.get("/requests").text
    assert "Sipho is collecting this." in page
    assert "cancel" not in page

    collector.post(f"/pickups/{pickup_id}/done")
    done = models.get_pickup(pickup_id)
    assert done["status"] == "done"
    assert done["completed_at"] is not None

    # And it's off the open board.
    board = collector.get("/jobs").text.split("<h1>Open jobs</h1>")[1]
    assert "Four bags after a braai" not in board


def test_two_collectors_racing_over_http(client, db_file):
    """The same race as test_claim_race, but through the routes - sessions,
    form handling, redirects and all."""
    from app.main import app

    from .conftest import make_client

    client.post("/join", data={"name": "Thandi", "role": "resident"})
    client.post(
        "/pickups",
        data={"description": "Broken chair", "address": "14 Rosmead Ave"},
        follow_redirects=False,
    )
    pickup_id = models.list_open_pickups()[0]["id"]

    one = make_client(app, "Sipho", "collector")
    two = make_client(app, "Nadia", "collector")

    outcomes = []
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    def race(c):
        barrier.wait()
        r = c.post(f"/pickups/{pickup_id}/claim", follow_redirects=False)
        with lock:
            outcomes.append(r.headers["location"])

    threads = [threading.Thread(target=race, args=(c,)) for c in (one, two)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # The winner is redirected to the job; the loser back to the board.
    assert sorted(outcomes) == sorted([f"/pickups/{pickup_id}", "/jobs"])
    assert models.get_pickup(pickup_id)["status"] == "claimed"


def test_the_pickup_is_created_even_when_geocoding_fails(client, monkeypatch):
    monkeypatch.setenv("GEOCODE_ENABLED", "1")

    def timeout(address):
        raise httpx.ConnectTimeout("too slow")

    monkeypatch.setattr(geocode, "_request", timeout)

    client.post("/join", data={"name": "Thandi", "role": "resident"})
    response = client.post(
        "/pickups",
        data={
            "description": "Four bags after a braai",
            "address": "Somewhere Nominatim has never heard of",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    pickups = models.list_open_pickups()
    assert len(pickups) == 1
    assert pickups[0]["geocoded"] == 0
    assert pickups[0]["address"] == "Somewhere Nominatim has never heard of"
    assert "timed out" in pickups[0]["geocode_note"]


def test_the_parse_endpoint_returns_200_even_when_the_model_misbehaves(
    client, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    monkeypatch.setattr(
        parse, "_request", lambda text: "I'm afraid I can't do that, Dave."
    )

    client.post("/join", data={"name": "Thandi", "role": "resident"})
    response = client.post("/parse", json={"text": RAW})

    assert response.status_code == 200
    body = response.json()
    assert body["parsed"] is False
    assert body["description"] == RAW


def test_a_submission_still_works_after_a_failed_parse(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    monkeypatch.setattr(parse, "_request", lambda text: "nope")

    client.post("/join", data={"name": "Thandi", "role": "resident"})
    parsed = client.post("/parse", json={"text": RAW}).json()

    response = client.post(
        "/pickups",
        data={
            "description": parsed["description"],
            "address": parsed["address"] or "",
            "raw_text": RAW,
            "parsed": "1" if parsed["parsed"] else "0",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    pickup = models.list_open_pickups()[0]
    assert pickup["description"] == RAW
    assert pickup["parsed"] == 0
    assert pickup["raw_text"] == RAW


def test_a_collector_can_set_their_location_and_jobs_resort(client, db_file):
    from app.main import app

    from .conftest import make_client

    client.post("/join", data={"name": "Thandi", "role": "resident"})
    thandi = models.get_user(1)
    far = models.create_pickup(thandi.id, "far one", lat=-34.10, lng=18.85)
    near = models.create_pickup(thandi.id, "near one", lat=-33.93, lng=18.43)

    collector = make_client(app, "Sipho", "collector")
    response = collector.post("/me/location", json={"lat": -33.9249, "lng": 18.4241})
    assert response.json()["ok"] is True

    order = [j["id"] for j in collector.get("/api/jobs.json").json()["jobs"]]
    assert order == [near, far]


def test_nonsense_coordinates_are_refused(client, db_file):
    from app.main import app

    from .conftest import make_client

    collector = make_client(app, "Sipho", "collector")
    assert collector.post("/me/location", json={"lat": 900, "lng": 0}).status_code == 400
    assert collector.post("/me/location", json={"lat": "here"}).status_code == 400


def test_a_missing_pickup_is_a_404(client):
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    assert client.get("/pickups/9999").status_code == 404


def test_a_stale_session_does_not_crash(client):
    """The cookie can outlive the database row; the app should just re-ask."""
    from app import db

    client.post("/join", data={"name": "Thandi", "role": "resident"})
    with db.cursor() as cur:
        cur.execute("DELETE FROM users")

    response = client.get("/requests", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/join"


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


# --- the friday polish: ticks, polling snapshot, access log -----------------


def test_the_status_snapshot_matches_reality(client, db_file):
    from app.main import app

    from .conftest import make_client

    client.post("/join", data={"name": "Thandi", "role": "resident"})
    client.post("/pickups", data={"description": "Broken chair"})
    pickup_id = models.list_open_pickups()[0]["id"]

    snap = client.get("/api/requests.json").json()["requests"]
    assert snap == [{"id": pickup_id, "status": "open"}]

    collector = make_client(app, "Sipho", "collector")
    collector.post(f"/pickups/{pickup_id}/claim")

    # This changing is exactly what makes the requests page reload itself.
    snap = client.get("/api/requests.json").json()["requests"]
    assert snap == [{"id": pickup_id, "status": "claimed"}]


def test_the_snapshot_is_resident_only_and_private(client, db_file):
    from app.main import app

    from .conftest import make_client

    client.post("/join", data={"name": "Thandi", "role": "resident"})
    client.post("/pickups", data={"description": "Broken chair"})

    collector = make_client(app, "Sipho", "collector")
    assert collector.get("/api/requests.json").status_code == 403

    other = make_client(app, "Zanele", "resident")
    assert other.get("/api/requests.json").json()["requests"] == []


def test_status_ticks_render(client, db_file):
    from app.main import app

    from .conftest import make_client

    client.post("/join", data={"name": "Thandi", "role": "resident"})
    client.post("/pickups", data={"description": "Broken chair"})
    pickup_id = models.list_open_pickups()[0]["id"]

    assert "ticks-open" in client.get("/requests").text

    collector = make_client(app, "Sipho", "collector")
    collector.post(f"/pickups/{pickup_id}/claim")
    collector.post(f"/pickups/{pickup_id}/done")
    assert "ticks-done" in client.get("/requests").text


def test_requests_are_logged_with_status_and_timing(client, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="binrun.access"):
        client.get("/join")
    lines = [r.getMessage() for r in caplog.records if r.name == "binrun.access"]
    assert any("GET /join -> 200 in" in line for line in lines)

    # The healthcheck stays quiet at INFO so a log tail is mostly signal.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="binrun.access"):
        client.get("/healthz")
    assert not [r for r in caplog.records if r.name == "binrun.access"]


def test_tampered_window_fields_are_cleaned_not_stored(client):
    """The hidden inputs come from the parser, but any browser can edit them."""
    client.post("/join", data={"name": "Thandi", "role": "resident"})
    client.post(
        "/pickups",
        data={
            "description": "Bags",
            "window_start": "'); DROP TABLE pickups;--",
            "window_end": "2026-09-15T12:00",
        },
    )
    p = models.list_open_pickups()[0]
    assert p["window_start"] is None
    assert p["window_end"] == "2026-09-15 12:00"
