"""Database connections.

Plumbing, not thinking - this file is here so the rest of the app doesn't have
to know where the database is. Everything that must survive a restart lives on
the other end of DATABASE_URL, which is a Neon Postgres somewhere in a data
centre, NOT a file on whatever host is running this process.

That is the whole reason a Hugging Face Space is a safe place to deploy this:
the Space's filesystem gets wiped when it restarts, and we never write to it.
"""

import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and paste your "
            "Neon connection string into it."
        )
    return url


@contextmanager
def cursor():
    """One connection, one unit of work, closed afterwards.

    Usage:
        with db.cursor() as cur:
            cur.execute("SELECT ...")
            rows = cur.fetchall()

    `row_factory=dict_row` means rows come back as dicts (row["id"]) instead of
    tuples (row[0]), which keeps the templates readable.

    `autocommit=True` means each statement commits on its own. That is the right
    default for now. On Thursday, when you get to the claim, you will need to
    think about whether it still is - hold that thought.
    """
    with psycopg.connect(database_url(), row_factory=dict_row, autocommit=True) as conn:
        with conn.cursor() as cur:
            yield cur
