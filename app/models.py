"""Domain operations. Every write that can race lives in this file.

The rule: no read-then-write across two statements where two users could
interleave. Every state change is a single conditional UPDATE whose WHERE
clause names the state the caller expects to find - and, where it matters,
who the caller is - and the caller decides what to do when zero rows match.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from . import db

ROLES = ("resident", "collector")
OPEN, CLAIMED, DONE, CANCELLED = "open", "claimed", "done", "cancelled"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class ClaimFailed(Exception):
    """Someone else got there first, or the pickup is no longer claimable."""


class NotAllowed(Exception):
    """The user is not the one who gets to do this."""


# --- users ------------------------------------------------------------------


@dataclass(frozen=True)
class User:
    id: int
    name: str
    role: str
    lat: float | None = None
    lng: float | None = None

    @property
    def is_collector(self) -> bool:
        return self.role == "collector"


def create_user(name: str, role: str) -> User:
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    with db.cursor() as cur:
        row = cur.execute(
            "INSERT INTO users (name, role, created_at) VALUES (%s, %s, %s) "
            "RETURNING id",
            (name, role, _now()),
        ).fetchone()
        return User(id=row["id"], name=name, role=role)


def get_user(user_id: int) -> User | None:
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT id, name, role, lat, lng FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    return User(**row) if row else None


def set_user_location(user_id: int, lat: float, lng: float) -> None:
    with db.cursor() as cur:
        cur.execute(
            "UPDATE users SET lat = %s, lng = %s WHERE id = %s", (lat, lng, user_id)
        )


# --- pickups ----------------------------------------------------------------


def create_pickup(
    resident_id: int,
    description: str,
    *,
    bag_count: int | None = None,
    size: str | None = None,
    address: str | None = None,
    when_text: str | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
    notes: str | None = None,
    raw_text: str | None = None,
    parsed: bool = False,
    lat: float | None = None,
    lng: float | None = None,
    geocode_note: str | None = None,
) -> int:
    description = (description or "").strip()
    if not description:
        raise ValueError("description is required")
    geocoded = 1 if (lat is not None and lng is not None) else 0
    with db.cursor() as cur:
        row = cur.execute(
            """
            INSERT INTO pickups (
                resident_id, description, bag_count, size, address, when_text,
                window_start, window_end,
                notes, raw_text, parsed, lat, lng, geocoded, geocode_note,
                status, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, 'open', %s)
            RETURNING id
            """,
            (
                resident_id, description, bag_count, size, address, when_text,
                window_start, window_end,
                notes, raw_text, 1 if parsed else 0, lat, lng, geocoded,
                geocode_note, _now(),
            ),
        ).fetchone()
        return int(row["id"])


_PICKUP_SELECT = """
    SELECT p.*,
           r.name AS resident_name,
           c.name AS collector_name,
           EXISTS(SELECT 1 FROM photos ph WHERE ph.pickup_id = p.id) AS has_photo
    FROM pickups p
    JOIN users r ON r.id = p.resident_id
    LEFT JOIN users c ON c.id = p.collector_id
"""


def get_pickup(pickup_id: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        return cur.execute(
            _PICKUP_SELECT + " WHERE p.id = %s", (pickup_id,)
        ).fetchone()


def list_open_pickups() -> list[dict[str, Any]]:
    with db.cursor() as cur:
        return cur.execute(
            _PICKUP_SELECT + " WHERE p.status = 'open' ORDER BY p.created_at DESC, p.id DESC"
        ).fetchall()


def list_pickups_for_resident(resident_id: int) -> list[dict[str, Any]]:
    with db.cursor() as cur:
        return cur.execute(
            _PICKUP_SELECT + """
            WHERE p.resident_id = %s
            ORDER BY
                CASE p.status
                    WHEN 'claimed' THEN 0 WHEN 'open' THEN 1
                    WHEN 'done' THEN 2 ELSE 3
                END,
                p.created_at DESC, p.id DESC
            """,
            (resident_id,),
        ).fetchall()


def list_pickups_for_collector(collector_id: int) -> list[dict[str, Any]]:
    with db.cursor() as cur:
        return cur.execute(
            _PICKUP_SELECT + """
            WHERE p.collector_id = %s
            ORDER BY
                CASE p.status WHEN 'claimed' THEN 0 ELSE 1 END,
                p.claimed_at DESC
            """,
            (collector_id,),
        ).fetchall()


# --- photos -----------------------------------------------------------------

PHOTO_MIMES = ("image/jpeg", "image/png", "image/webp", "image/gif")
PHOTO_MAX_BYTES = 1_000_000  # keep Neon's free 500MB honest


def attach_photo(pickup_id: int, mime: str, data_b64: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO photos (pickup_id, mime, data_b64) VALUES (%s, %s, %s)
            ON CONFLICT(pickup_id) DO UPDATE SET
                mime = excluded.mime, data_b64 = excluded.data_b64
            """,
            (pickup_id, mime, data_b64),
        )


def get_photo(pickup_id: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        return cur.execute(
            "SELECT mime, data_b64 FROM photos WHERE pickup_id = %s", (pickup_id,)
        ).fetchone()


# --- the state transitions --------------------------------------------------


def claim_pickup(pickup_id: int, collector_id: int) -> dict[str, Any]:
    """Claim an open pickup for a collector.

    This is the whole point of the project. Two collectors can run this at the
    same instant; the `AND status = 'open'` in the WHERE clause means the
    database decides the winner, not us. The loser's UPDATE matches zero rows
    because by the time it gets its turn to write, the status is already
    'claimed'.

    There is deliberately no SELECT-then-UPDATE here. Checking availability in
    Python and updating afterwards is the bug this whole design exists to
    avoid: both requests would read 'open', and both would write.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE pickups
               SET status = 'claimed',
                   collector_id = %s,
                   claimed_at = %s
             WHERE id = %s
               AND status = 'open'
            """,
            (collector_id, _now(), pickup_id),
        )
        if cur.rowcount == 1:
            return get_pickup(pickup_id)

    # Zero rows. Work out why, purely for a message the collector can act on.
    # This read is allowed to be stale - it decides nothing.
    current = get_pickup(pickup_id)
    if current is None:
        raise ClaimFailed("That pickup no longer exists.")
    if current["status"] == CLAIMED:
        if current["collector_id"] == collector_id:
            return current  # a double-tap on our own claim is a no-op
        raise ClaimFailed(f"{current['collector_name']} claimed this one first.")
    if current["status"] == DONE:
        raise ClaimFailed("That pickup has already been collected.")
    raise ClaimFailed("The resident cancelled that pickup.")


def complete_pickup(pickup_id: int, collector_id: int) -> dict[str, Any]:
    """Mark a claimed pickup done. Only the collector holding it may."""
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE pickups
               SET status = 'done',
                   completed_at = %s
             WHERE id = %s
               AND status = 'claimed'
               AND collector_id = %s
            """,
            (_now(), pickup_id, collector_id),
        )
        if cur.rowcount == 1:
            return get_pickup(pickup_id)

    current = get_pickup(pickup_id)
    if current is None:
        raise NotAllowed("That pickup no longer exists.")
    if current["status"] == DONE and current["collector_id"] == collector_id:
        return current
    if current["collector_id"] != collector_id:
        raise NotAllowed("That pickup isn't yours to close.")
    raise NotAllowed("Only a claimed pickup can be marked done.")


def release_pickup(pickup_id: int, collector_id: int) -> dict[str, Any]:
    """Give a claimed pickup back to the open list."""
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE pickups
               SET status = 'open',
                   collector_id = NULL,
                   claimed_at = NULL
             WHERE id = %s
               AND status = 'claimed'
               AND collector_id = %s
            """,
            (pickup_id, collector_id),
        )
        if cur.rowcount == 1:
            return get_pickup(pickup_id)
    raise NotAllowed("You can only release a pickup you're currently holding.")


def cancel_pickup(pickup_id: int, resident_id: int) -> dict[str, Any]:
    """A resident withdraws their own request, while it is still unclaimed."""
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE pickups
               SET status = 'cancelled'
             WHERE id = %s
               AND status = 'open'
               AND resident_id = %s
            """,
            (pickup_id, resident_id),
        )
        if cur.rowcount == 1:
            return get_pickup(pickup_id)

    current = get_pickup(pickup_id)
    if current is None:
        raise NotAllowed("That pickup no longer exists.")
    if current["resident_id"] != resident_id:
        raise NotAllowed("That isn't your request.")
    if current["status"] == CLAIMED:
        raise NotAllowed(
            f"{current['collector_name']} is already on the way. "
            "Message them rather than cancelling."
        )
    raise NotAllowed(f"A {current['status']} pickup can't be cancelled.")


# --- distance ---------------------------------------------------------------

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance. Good enough for sorting a suburb's worth of jobs."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def sort_by_distance(
    pickups: list[dict[str, Any]], lat: float | None, lng: float | None
) -> list[dict[str, Any]]:
    """Annotate each pickup with `distance_km` and sort nearest first.

    Pickups we couldn't geocode keep a distance of None and sort to the
    bottom rather than being dropped. An un-geocoded pickup is still a real
    pickup; it has an address a human can read.
    """
    for p in pickups:
        if lat is None or lng is None or p.get("lat") is None or p.get("lng") is None:
            p["distance_km"] = None
        else:
            p["distance_km"] = round(haversine_km(lat, lng, p["lat"], p["lng"]), 2)

    return sorted(
        pickups,
        key=lambda p: (
            p["distance_km"] is None,
            p["distance_km"] if p["distance_km"] is not None else 0.0,
        ),
    )
