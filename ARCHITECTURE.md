# Architecture

## Shape

```
browser ──HTML forms──▶ FastAPI (app/main.py)
                            │
                            ├── app/models.py   every write that can race
                            ├── app/db.py       two engines, one SQL subset
                            ├── app/geocode.py  Nominatim, never raises
                            └── app/parse.py    free text → fields, never raises
                                     │
                        ┌────────────┴────────────┐
                   SQLite file                Neon Postgres
                (laptop / clean clone)      (deployed - the Space's own
                                             disk is wiped on restart)
```

Server-rendered Jinja templates, no build step, no client framework. Exactly
two pieces of JavaScript — the Leaflet map and a `fetch` that fills the
request form from a sentence — and the app works with both of them broken.

## The one hard problem

Two collectors tap "claim" on the same job at the same moment. Exactly one
must win, and the job must never end up assigned twice.

The version written first, on paper, in `DATA-MODEL.md`:

```python
pickup = get_pickup(id)
if pickup["status"] == "open":     # both requests read 'open'
    set_status(id, "claimed", me)  # both requests write
```

That is a check-then-act across two statements. Between the read and the
write the world changes, and nothing in the second statement notices.

What is in `app/models.py` instead:

```sql
UPDATE pickups
   SET status = 'claimed', collector_id = %s, claimed_at = %s
 WHERE id = %s AND status = 'open'
```

The condition and the write are one statement, so the database serialises
them. `rowcount` is the whole answer: 1 you won, 0 you didn't. The loser then
does a plain read to work out *why* — someone else took it, the resident
cancelled, it's done — purely to write a sentence a human can act on. That
read is allowed to be stale; it decides nothing.

`complete`, `release` and `cancel` follow the same shape: the WHERE clause
names both the state the caller expects *and* who the caller is
(`AND collector_id = %s`), so authorisation and concurrency are enforced by
the same statement. A table CHECK constraint (claimed/done must have a
collector; open/cancelled must not) backstops any future route that forgets.

Why it holds on both engines: in SQLite, writers queue on the database write
lock (WAL + a 5s busy timeout make the loser wait instead of raising
`database is locked`); in Postgres, the second writer blocks on the row lock
and re-evaluates the WHERE against the committed row under READ COMMITTED.
Different machinery, same outcome — *provided* the whole transition is one
statement, which is exactly the design rule.

Connections are opened per request in autocommit mode: the claim's UPDATE is
its own transaction with nothing wrapped around it, and nothing in the app
currently needs a multi-statement transaction.

## Failure is the normal case

Both external services are optional and share a contract: **the module never
raises at its caller.**

- **Geocoding** (`geocode.geocode`) returns coordinates or a note saying why
  not. A pickup with no coordinates still posts — it sorts to the bottom of
  the job list and shows its address as text. Confirmed misses are cached
  (also keeps us under Nominatim's 1 req/s policy); timeouts are not cached,
  because a timeout says nothing about the address.
- **Parsing** (`parse.parse_free_text`) is total. No key, no network, prose
  instead of JSON, an invented field, `"three"` as a bag count — every path
  ends at the same fallback: the resident's own words in the description and
  `parsed: False`. The model call (`_request`) and the sanitising (`coerce`)
  are separate functions so `coerce` is testable with no key and no network.
  Structured outputs constrain the response's *shape*; `coerce` exists
  because shape is not sense, and it never reads keys it doesn't know, so an
  invented field cannot reach the database.

## The decision I'm least confident about

**Supporting two database engines.** `DATABASE_URL` unset means SQLite (so an
assessor's clean clone runs with zero configuration); set means Postgres (so
the deployed instance keeps state off a filesystem that gets wiped). The cost
is that every query must be legal in both engines, held to a shared subset by
a thin wrapper in `db.py` — and, more seriously, that the property this whole
project is about is *proven* by tests on SQLite but only *argued* on Postgres,
because the default test run uses temp SQLite files.

**What would change my mind:** either direction of evidence. If running the
suite with `TEST_DATABASE_URL` pointed at a real Neon branch (the hook is
already in `tests/conftest.py`) ever showed the race behaving differently on
Postgres, I'd drop SQLite and eat the setup cost in the README. And if the
shared-subset rule ever blocked a query I actually needed — or a reader can
show me a divergence between the two engines' UPDATE semantics that matters
here — same conclusion. Conversely, a green Postgres run would upgrade
"argued" to "proven" and I'd stop worrying. That run is on the pre-submission
checklist.

## Other things I'm unsure about, in one line each

- SQLite is correct for one process on one machine; the moment this is two
  machines, SQLite is wrong and only the Postgres path survives.
- The 5s busy timeout is tested at eight concurrent threads. I don't know
  where it stops being enough; I only know it holds at the scale tested.
- The app resolves *claim* contention, not *collection* contention — nothing
  stops a collector claiming six jobs and doing none of them.
- The distance sort loads every open job into Python. Fine for a suburb,
  wrong for a city (it wants a bounding box in SQL first).
- The geocode cache never expires.

## Decisions I deferred

- **No passwords.** A name and a role and you're in; every join is a new user
  row. Wrong in an obvious way, left wrong on purpose: authentication is a
  problem I've solved before, and the learning budget went on concurrency.
  Adding it is a password hash, a unique index on name, and a login route —
  nothing else in this document changes.
- **No push/SMS/email** — they cost money or accounts. The requests page
  polls its own status snapshot (20s) and reloads only on change, which is
  the honest free version of a notification.
- **No payment, rating, or dispute flow.** The app believes the collector.
- **No pagination.**
- **Sessions are signed cookies.** The cookie holds the user id; the
  signature (SECRET_KEY) is what stops you minting someone else's. The
  committed dev default key is therefore fine locally and a real secret on
  the deployed host. Rotating it logs everyone out — which is also the
  entire session-invalidation story.
