"""Nominatim is optional infrastructure.

A pickup with no coordinates is a real pickup: it has an address a human can
read. Every failure below must still leave the resident with a posted
request. (The route-level version of that lives in test_flow.py; these hit
the geocode module directly.)
"""

import httpx2 as httpx
import pytest

from app import geocode, models


@pytest.fixture(autouse=True)
def geocoding_on(db_file, monkeypatch):
    monkeypatch.setenv("GEOCODE_ENABLED", "1")


def test_a_normal_lookup_returns_coordinates(monkeypatch):
    monkeypatch.setattr(
        geocode,
        "_request",
        lambda address: [
            {"lat": "-33.9891", "lon": "18.4712", "display_name": "14 Rosmead Ave"}
        ],
    )
    result = geocode.geocode("14 Rosmead Ave")
    assert result.ok
    assert result.lat == pytest.approx(-33.9891)
    assert result.note is None


def test_timeout_degrades_to_no_coordinates(monkeypatch):
    def timeout(address):
        raise httpx.ConnectTimeout("too slow")

    monkeypatch.setattr(geocode, "_request", timeout)

    result = geocode.geocode("14 Rosmead Ave")
    assert not result.ok
    assert result.lat is None
    assert "timed out" in result.note


def test_no_match_is_not_an_error(monkeypatch):
    monkeypatch.setattr(geocode, "_request", lambda address: [])
    result = geocode.geocode("Behind the koppie past the blue house")
    assert not result.ok
    assert "No match" in result.note


def test_a_500_from_nominatim_degrades(monkeypatch):
    def server_error(address):
        request = httpx.Request("GET", "https://nominatim.example/search")
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("down", request=request, response=response)

    monkeypatch.setattr(geocode, "_request", server_error)
    result = geocode.geocode("14 Rosmead Ave")
    assert not result.ok
    assert "503" in result.note


def test_garbage_json_degrades(monkeypatch):
    def nonsense(address):
        raise ValueError("expected a list from Nominatim, got dict")

    monkeypatch.setattr(geocode, "_request", nonsense)
    assert not geocode.geocode("14 Rosmead Ave").ok


def test_a_result_without_coordinates_degrades(monkeypatch):
    monkeypatch.setattr(
        geocode, "_request", lambda address: [{"display_name": "somewhere"}]
    )
    result = geocode.geocode("14 Rosmead Ave")
    assert not result.ok
    assert "coordinates" in result.note


def test_an_empty_address_is_not_looked_up(monkeypatch):
    def explode(address):
        raise AssertionError("should not have called Nominatim")

    monkeypatch.setattr(geocode, "_request", explode)
    assert not geocode.geocode("").ok
    assert not geocode.geocode(None).ok


def test_lookups_are_cached_so_one_address_is_one_request(monkeypatch):
    calls = []

    def counted(address):
        calls.append(address)
        return [{"lat": "-33.9", "lon": "18.4", "display_name": address}]

    monkeypatch.setattr(geocode, "_request", counted)

    geocode.geocode("14 Rosmead Ave")
    geocode.geocode("14 Rosmead Ave")
    assert len(calls) == 1


def test_a_timeout_is_not_cached(monkeypatch):
    """A timeout says nothing about the address, so don't remember it as a miss."""
    state = {"fail": True}

    def flaky(address):
        if state["fail"]:
            raise httpx.ReadTimeout("too slow")
        return [{"lat": "-33.9", "lon": "18.4", "display_name": address}]

    monkeypatch.setattr(geocode, "_request", flaky)

    assert not geocode.geocode("14 Rosmead Ave").ok
    state["fail"] = False
    assert geocode.geocode("14 Rosmead Ave").ok


def test_un_geocoded_jobs_sort_last_but_are_not_dropped(db_file):
    resident = models.create_user("Thandi", "resident")
    far = models.create_pickup(resident.id, "far", lat=-33.99, lng=18.47)
    near = models.create_pickup(resident.id, "near", lat=-33.925, lng=18.424)
    nowhere = models.create_pickup(resident.id, "no coords", address="unfindable")

    ordered = models.sort_by_distance(models.list_open_pickups(), -33.9249, 18.4241)
    assert [p["id"] for p in ordered] == [near, far, nowhere]
    assert ordered[-1]["distance_km"] is None


def test_without_a_collector_location_nothing_is_dropped(db_file):
    resident = models.create_user("Thandi", "resident")
    models.create_pickup(resident.id, "one", lat=-33.99, lng=18.47)
    models.create_pickup(resident.id, "two")

    ordered = models.sort_by_distance(models.list_open_pickups(), None, None)
    assert len(ordered) == 2
    assert all(p["distance_km"] is None for p in ordered)
