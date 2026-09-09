# Bin Run

On-demand rubbish collection. Residents request a pickup; collectors nearby claim it and mark it done.

Municipal trucks come once a week. This is for the gaps: the bin you forgot to put out, the four extra bags after a weekend braai, the broken chair that can't wait until Thursday. A resident posts what they need collected and roughly when; collectors see open jobs sorted by distance, claim one, and close it out.

Built for Build Week, September 2026 — with heavy, documented use of AI (see `LOG.md`, which is honest about exactly how heavy).

---

## What's new to me here

**Concurrent server-side state.** Everything I've shipped before has been read-only and client-side. My previous web app (the [UCT Jammie Shuttle](https://huggingface.co/spaces/collins909/uct-jammie-shuttle)) was vanilla JS with a timetable baked in at build time — no server, no database, no writes, and never two people changing the same thing at once.

This project is the opposite. Two collectors can tap "claim" on the same job at the same moment, and exactly one of them has to win. That meant learning, from scratch:

- writing an HTTP API rather than a static page
- a real database with transactions, not a JSON blob loaded into the browser
- atomic conditional updates, and what a race condition actually looks like when you go looking for one
- sessions and roles
- deploying something with persistent state, where the filesystem surviving a restart is not a given

Things that are *not* new to me and that I'm reusing rather than claiming: Python, Leaflet, OpenStreetMap services, and deploying to a free host.

## Run it

Deployed at: `TODO — Space url` *(HF Space, Docker SDK, with `DATABASE_URL` pointing at a free Neon Postgres set as a Space secret — see `Dockerfile`)*

Or locally, from a clean clone, **no accounts or keys needed**:

```bash
git clone TODO
cd "rubbish app"
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000. With nothing configured it uses a local SQLite file at `./data/binrun.db` and creates the schema on first boot (`python -m app.db init` does the same thing from a shell). Pick a name and a role on the way in — resident or collector. There's no password; see "Decisions I deferred" in `ARCHITECTURE.md`.

To see the interesting part, open the app in two browser windows (one normal, one private, so the session cookies differ) as two different collectors and try to claim the same job.

### Environment

| Variable | Needed? | What happens without it |
| --- | --- | --- |
| `DATABASE_URL` | locally no; **on the deployed host yes** | falls back to `./data/binrun.db` (SQLite). Fine on a laptop; a fail on a host whose filesystem resets, which is why the Space points this at Neon |
| `SECRET_KEY` | no | a committed default signs the session cookie — fine locally, not in public |
| `ANTHROPIC_API_KEY` | no | the free-text parser is skipped; requests still submit with manually entered fields |
| `NOMINATIM_USER_AGENT` | no | a default is used, but OSM asks that you set your own |
| `GEOCODE_ENABLED` | no | set `0` to skip address lookups entirely; addresses stay as text |

Nothing here costs money. Geocoding is OpenStreetMap's Nominatim (free, rate-limited to 1 req/s — results are cached partly for that reason); hosting is a free Space plus Neon's free tier, chosen because Neon wakes automatically on the next query instead of pausing until someone logs into a dashboard.

## Tests

```bash
pytest
```

77 tests, no network and no API key required — both external services are stubbed at their seam. Set `TEST_DATABASE_URL` to a throwaway Postgres to run the same suite against the deployed engine.

The tests worth reading are the ones about things going wrong:

- `test_claim_race.py` — eight collectors claim the same job simultaneously, released through a `threading.Barrier` so they genuinely collide (without the barrier the test also passes against broken code, which means it proves nothing). One wins, seven are told who beat them, and the job is never double-assigned.
- `test_geocode_failure.py` — Nominatim times out, returns 503, returns nothing, or returns a result with no coordinates. The request must still be created; a pickup with no coordinates is a real pickup.
- `test_parser_garbage.py` — the model returns prose instead of JSON, or invents a field. The parser degrades to the raw text rather than crashing the submission.
- `test_flow.py` — the ordinary paths and role boundaries, plus the claim race again over real HTTP with sessions.

## Layout

```
app/
  main.py        # routes, sessions, roles
  models.py      # schema operations + every write that can race
  db.py          # dual-engine (SQLite/Postgres) connections, migrations
  geocode.py     # Nominatim client + fallback (never raises)
  parse.py       # free-text -> structured fields (never raises)
  config.py      # env with defaults that always boot
  templates/  static/
tests/
DATA-MODEL.md    # the design questions, answered - working notes
ARCHITECTURE.md  # how it's put together, and what I'm least sure about
LOG.md           # what actually happened, including what went wrong
DEMO.md          # the 10-15 minute walkthrough plan
SIDE-QUEST.md    # the tool-I-didn't-use write-up
```

## Status

**Works, locally, end to end.** Residents post pickups (typed in, or dictated in a sentence and parsed into fields when a key is present), collectors see them nearest-first on a list and a map, claim exactly one at a time, release them, and mark them collected. Residents cancel while a job is unclaimed and see who took it once it isn't. Addresses Nominatim can't place still post and show as text. Verified by hand against live Nominatim, including killing and restarting the server mid-flow with all state intact.

**The part I set out to learn works and is tested.** The claim is one conditional `UPDATE` whose `rowcount` decides the winner. Eight threads through one barrier produce one winner every time.

**Cut, on purpose:**

- **Passwords.** Every "join" creates a new user; returning tomorrow makes a second you. Authentication is a solved problem I've done before — the week's learning budget went on concurrency.
- **Notifications.** The resident finds out by reloading.
- **Payment, ratings, disputes.** A collector says a job is done and the app believes them.
- **Pagination.** The job board loads every open pickup.

**Still to do before Monday:** the deploy itself (Space + Neon secret + a real restart test on the host), the demo recording, and the side quest.
