---
name: run-bin-run
description: >-
  Build, run, smoke-test, screenshot, and drive the Bin Run web app (FastAPI +
  SQLite/Postgres rubbish-collection marketplace). Use when asked to run the
  app, start the server, verify a change end-to-end, reproduce the claim race,
  take a screenshot, or check the Build Week submission still works from a
  clean clone. The smoke driver executes the whole reasoning of the project -
  post, race, restart, collect - and says PASS or where it broke.
---

# Run Bin Run

Server-rendered FastAPI web app. The driver is
`.claude/skills/run-bin-run/smoke.sh` — a curl harness that executes the
project's core ideas in order, as one scripted user flow. All paths below are
relative to the repo root. Every command here was run and worked.

## The plan the driver executes (and why each step exists)

1. **Boot on an isolated throwaway DB** — never touches dev data; proves a
   clean boot migrates its own schema.
2. **Resident posts a pickup with the geocoder off** — proves the design
   rule that an un-geocoded pickup is still a real pickup (brief rule 3:
   the thing we don't control is allowed to be down).
3. **Two collectors claim it concurrently** — the project's headline: one
   conditional UPDATE, exactly one winner, loser redirected to the board
   (the "new to me" concept: concurrent server-side state).
4. **Kill the process, restart, check the claim is still there** — rule 4:
   state survives a restart.
5. **Winner marks it collected; resident's page shows it** — the full
   lifecycle, both roles (rule 5).

## Run (agent path) — do this first

```bash
./.claude/skills/run-bin-run/smoke.sh              # ~5s, prints PASS or the failing step
./.claude/skills/run-bin-run/smoke.sh screenshot   # + smoke-join.png in repo root
```

Needs nothing configured. On a clean clone it creates `.venv` and installs
deps itself (add ~1 min, first run only). Uses port 8321 (`PORT=nnnn` to
override) and a temp SQLite file it deletes on exit.

## Run (human path)

```bash
./.venv/bin/uvicorn app.main:app --reload    # http://localhost:8000
```

Uses `./data/binrun.db` (or `DATABASE_URL` if set). Two roles need two
session cookies: one normal + one private browser window. Stop with Ctrl-C;
a stray background instance dies with `fuser -k -n tcp 8000`.

## Test

```bash
./.venv/bin/python -m pytest    # 81 tests, ~3s, no network or keys needed
```

`TEST_DATABASE_URL=postgresql://...` reruns the same suite on Postgres
(schema dropped and rebuilt per test — throwaway DB only).

## Gotchas (all hit for real in this repo)

- **`pkill -f "uvicorn app.main:app"` kills your own shell** (exit 144) when
  the pattern text appears in the same compound command. Use
  `fuser -k -n tcp <port>` instead — it matches by port and can't self-match.
- **In a launcher script, bare `wait` hangs forever**: it waits for *all*
  background jobs, including the server you backgrounded. `smoke.sh` waits on
  the two claim pids explicitly.
- **`localhost:8000` may show a completely different app** on a developer's
  machine: a stale service worker from a previous project on that origin
  serves its cached copy. curl the port to see the truth; fix via DevTools →
  Application → Service Workers → Unregister, or use another port.
- **The venv's HTTP lib is `httpx2`, not `httpx`** (pulled in by the
  anthropic SDK). `import httpx` fails; the code does `import httpx2 as httpx`.
- **Never `git add -A` while a dev DB exists** without checking: `data/` is
  gitignored now precisely because `binrun.db` once slipped into a commit.

## Troubleshooting

- `server did not come up on :8321` → the script tails the server log for
  you; a port clash means a previous run leaked — `fuser -k -n tcp 8321`.
- `FAIL: BOTH claims won` → the atomic-claim invariant broke; suspect any
  change to `models.claim_pickup` or db.py's autocommit/WAL settings.
- Screenshot step says `warn: chrome screenshot failed` → this host's
  `google-chrome` is missing; the drive itself still PASSes (screenshot is
  optional evidence, not the test).
