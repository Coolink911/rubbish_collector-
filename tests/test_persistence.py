"""Day 1 test.

Rule 7 wants at least one automated test, preferably on the ugly path. This
is not that test yet - the ugly-path ones arrive Thursday (the claim race) and
Saturday (garbage input). This one just proves the database is reachable and a
write survives being read back by a *separate* connection, which is the
smallest honest version of "it persists".

Run with:  pytest -q
Needs DATABASE_URL set, same as the app.
"""

import os

import pytest

from app import db


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"
)
def test_a_row_written_on_one_connection_is_visible_on_another():
    """YOUR TURN.

    Sketch:
      1. Open a cursor, INSERT a heartbeat with a note you can recognise
         (use something unique - a uuid4 hex string is easiest).
      2. Let that `with` block close. The connection is now gone.
      3. Open a *second*, fresh cursor and SELECT the row back by that note.
      4. assert you found exactly one.

    Why bother, when the app obviously works when you click it? Because
    clicking proves one connection can read its own write. This proves the row
    left the process. That is the property you actually need.
    """
    raise NotImplementedError("Write the test - see the docstring above.")
