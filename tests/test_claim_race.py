"""Two collectors, one job, same instant.

This is the test the project exists for. Everything else in the app is a
form. Note what makes the concurrent version honest: a threading.Barrier.
Threads started in sequence don't overlap, they just run - the barrier makes
all eight arrive at the UPDATE together. Without it this test also passes
against a broken read-then-write implementation, which means it proves
nothing.
"""

import threading

import pytest

from app import models
from app.models import ClaimFailed


@pytest.fixture
def open_pickup(db_file):
    resident = models.create_user("Thandi", "resident")
    return models.create_pickup(
        resident.id, "Four bags after a braai", address="14 Rosmead Ave"
    )


def test_second_claim_loses(db_file, open_pickup):
    """The plain, non-concurrent version of the rule."""
    a = models.create_user("Sipho", "collector")
    b = models.create_user("Nadia", "collector")

    claimed = models.claim_pickup(open_pickup, a.id)
    assert claimed["status"] == "claimed"
    assert claimed["collector_id"] == a.id

    with pytest.raises(ClaimFailed) as exc:
        models.claim_pickup(open_pickup, b.id)
    assert "Sipho" in str(exc.value)

    # And the job did not move.
    assert models.get_pickup(open_pickup)["collector_id"] == a.id


def test_double_tap_by_the_same_collector_is_a_no_op(db_file, open_pickup):
    a = models.create_user("Sipho", "collector")
    first = models.claim_pickup(open_pickup, a.id)
    second = models.claim_pickup(open_pickup, a.id)
    assert first["claimed_at"] == second["claimed_at"]


def test_simultaneous_claims_produce_exactly_one_winner(db_file, open_pickup):
    """Eight collectors, one barrier, one job."""
    collectors = [models.create_user(f"Collector {i}", "collector") for i in range(8)]
    barrier = threading.Barrier(len(collectors))
    results: list[tuple[int, bool, str]] = []
    lock = threading.Lock()

    def attempt(collector):
        barrier.wait()
        try:
            models.claim_pickup(open_pickup, collector.id)
            outcome = (collector.id, True, "")
        except ClaimFailed as exc:
            outcome = (collector.id, False, str(exc))
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt, args=(c,)) for c in collectors]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "a claim thread deadlocked"

    winners = [r for r in results if r[1]]
    losers = [r for r in results if not r[1]]

    assert len(results) == len(collectors)
    assert len(winners) == 1, f"expected one winner, got {len(winners)}"
    assert len(losers) == len(collectors) - 1
    assert all("claimed this one first" in msg for _, _, msg in losers)

    # The row itself agrees with the winner.
    pickup = models.get_pickup(open_pickup)
    assert pickup["status"] == "claimed"
    assert pickup["collector_id"] == winners[0][0]
    assert pickup["claimed_at"] is not None


def test_a_released_job_can_be_claimed_again(db_file, open_pickup):
    a = models.create_user("Sipho", "collector")
    b = models.create_user("Nadia", "collector")

    models.claim_pickup(open_pickup, a.id)
    models.release_pickup(open_pickup, a.id)

    released = models.get_pickup(open_pickup)
    assert released["status"] == "open"
    assert released["collector_id"] is None

    reclaimed = models.claim_pickup(open_pickup, b.id)
    assert reclaimed["collector_id"] == b.id


def test_only_the_holding_collector_can_close_it(db_file, open_pickup):
    a = models.create_user("Sipho", "collector")
    b = models.create_user("Nadia", "collector")
    models.claim_pickup(open_pickup, a.id)

    with pytest.raises(models.NotAllowed):
        models.complete_pickup(open_pickup, b.id)

    done = models.complete_pickup(open_pickup, a.id)
    assert done["status"] == "done"
    assert done["completed_at"] is not None


def test_a_claimed_job_cannot_be_cancelled_by_the_resident(db_file, open_pickup):
    resident_id = models.get_pickup(open_pickup)["resident_id"]
    collector = models.create_user("Sipho", "collector")
    models.claim_pickup(open_pickup, collector.id)

    with pytest.raises(models.NotAllowed):
        models.cancel_pickup(open_pickup, resident_id)
    assert models.get_pickup(open_pickup)["status"] == "claimed"
