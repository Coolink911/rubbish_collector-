"""Address -> coordinates, via OpenStreetMap's Nominatim.

Geocoding is the part of this app most likely to be unavailable, slow, or
simply wrong about a South African street name. So the contract is: this
module never raises at the caller. It returns a Geocode with coordinates, or
one without them plus a note explaining what happened. A pickup with no
coordinates is still a pickup - it sorts to the bottom of the job list and
shows its address as text instead of a pin.

Nominatim's usage policy: identify yourself (NOMINATIM_USER_AGENT) and stay
under one request per second - the cache below exists mostly for that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx2 as httpx  # the httpx successor; same API. One HTTP lib in the venv.

from . import config, db

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Geocode:
    lat: float | None = None
    lng: float | None = None
    label: str | None = None
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.lat is not None and self.lng is not None


def _cache_get(query: str) -> Geocode | None:
    try:
        with db.cursor() as cur:
            row = cur.execute(
                "SELECT lat, lng, label FROM geocode_cache WHERE query = %s",
                (query,),
            ).fetchone()
    except Exception:  # a broken cache and a cache miss are the same to us
        return None
    if row is None:
        return None
    if row["lat"] is None:
        return Geocode(note="No match for that address (remembered from earlier).")
    return Geocode(lat=row["lat"], lng=row["lng"], label=row["label"])


def _cache_put(query: str, result: Geocode) -> None:
    try:
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO geocode_cache (query, lat, lng, label)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(query) DO UPDATE SET
                    lat = excluded.lat, lng = excluded.lng, label = excluded.label
                """,
                (query, result.lat, result.lng, result.label),
            )
    except Exception:
        log.debug("could not cache geocode for %r", query, exc_info=True)


def _request(address: str) -> list[dict]:
    """The actual network call. Patched out in tests."""
    response = httpx.get(
        config.nominatim_url(),
        params={
            "q": address,
            "format": "jsonv2",
            "limit": 1,
            "countrycodes": "za",
            "addressdetails": 0,
        },
        headers={"User-Agent": config.nominatim_user_agent()},
        timeout=config.geocode_timeout(),
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"expected a list from Nominatim, got {type(payload).__name__}")
    return payload


def geocode(address: str | None, *, use_cache: bool = True) -> Geocode:
    """Best-effort geocode. Never raises."""
    query = (address or "").strip()
    if not query:
        return Geocode(note="No address given.")

    if not config.geocode_enabled():
        return Geocode(note="Geocoding is switched off; address kept as text.")

    if use_cache:
        cached = _cache_get(query)
        if cached is not None:
            return cached

    try:
        results = _request(query)
    except httpx.TimeoutException:
        # Deliberately not cached: a timeout says nothing about the address.
        log.warning("Nominatim timed out for %r", query)
        return Geocode(note="The map service timed out. Address saved as text.")
    except httpx.HTTPStatusError as exc:
        log.warning("Nominatim returned %s for %r", exc.response.status_code, query)
        return Geocode(
            note=f"The map service returned {exc.response.status_code}. "
            "Address saved as text."
        )
    except httpx.HTTPError as exc:
        log.warning("Nominatim unreachable for %r: %s", query, exc)
        return Geocode(note="Couldn't reach the map service. Address saved as text.")
    except (ValueError, TypeError) as exc:
        log.warning("Nominatim sent something unreadable for %r: %s", query, exc)
        return Geocode(note="The map service sent back nonsense. Address saved as text.")

    if not results:
        miss = Geocode(note="No match for that address. It'll show as text on the job.")
        if use_cache:
            _cache_put(query, miss)
        return miss

    top = results[0]
    try:
        found = Geocode(
            lat=float(top["lat"]),
            lng=float(top["lon"]),
            label=top.get("display_name") or query,
        )
    except (KeyError, TypeError, ValueError):
        log.warning("Nominatim result had no usable coordinates: %r", top)
        return Geocode(note="The map service didn't give coordinates for that address.")

    if use_cache:
        _cache_put(query, found)
    return found
