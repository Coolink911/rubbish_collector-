# Log

What actually happened, in order. The AI referred to throughout is Claude
(Opus, later Fable) running in Claude Code, working in this repo with me.

> The unflattering parts are left in on purpose. An earlier version of this
> file was the most unflattering part of all — see 8 September.

## 8 September — the false start

I pasted my README draft and said "we start to build". The AI built the
**entire six-day project in one sitting** — app, tests, docs — including a
`LOG.md` written in my first person, narrating a week of debugging I had
never lived. It read well. None of it had happened to me.

That fabricated log was the thing that made the problem obvious: I couldn't
explain the code line by line, and the document claiming I could was fiction.
I deleted every file and restarted on a day-by-day plan, with the AI coaching
instead of building — repo and venv first, a skeleton with deliberate holes
(the INSERT, the persistence test, the data-model questions) for me to fill.

Then I changed course a second time, and this is the decision worth
recording: the brief says *"use AI hard and check its work"* and explicitly
does not care whether I got there without it. So I had the AI build the full
project after all — but in reviewable commits, with the docs telling the
truth about authorship, and with me owning the explanations. This log is
part of that deal. What I can and can't explain is tested in `DEMO.md`'s
question list, not assumed.

## Where AI genuinely sped me up

Nearly everywhere, honestly: the whole FastAPI/psycopg scaffolding, the
Jinja templates, 77 tests, and the docs drafting. Two specific things I
would not have produced alone this week:

- **The barrier.** My race test would have started two threads in sequence
  and called it concurrency. The AI pointed out that version also passes
  against the *broken* read-then-write code — a test that proves nothing —
  and reached for `threading.Barrier` so eight claims genuinely collide.
- **The hosting check.** I was headed for a SQLite file on a free Hugging
  Face Space, which wipes its filesystem on restart — silent data loss,
  a straight fail on rule 4. The AI also went and verified the alternatives
  instead of asserting them: Fly's free allowance is closed to new accounts,
  Render's free Postgres expires after 30 days, Supabase free pauses after
  7 idle days and needs a manual restore. Neon (wakes itself on the next
  query) plus a stateless Space is the design that came out of that.

## Where AI confidently got it wrong, and it was caught

- **The fabricated log** (above). The big one.
- **A deprecated API, stated as current.** The first skeleton used
  `@app.on_event("startup")` — the pattern all over the internet and in the
  model's training. FastAPI 0.141 deprecates it. Caught by running the
  import with warnings-as-errors after noticing the installed versions were
  newer than the AI assumed; rewritten as a `lifespan` handler.
- **An import for a library that wasn't there.** `geocode.py` was written
  with `import httpx`, but this venv only has `httpx2` (the anthropic SDK's
  HTTP layer). Caught at the import smoke test, before it could look like a
  geocoding bug.
- **`git add -A` shipped the database.** `data/binrun.db` (smoke-test rows,
  no secrets) got committed. Caught on a checklist pass; untracked and
  gitignored the next commit. State does not belong in a repo.

## The bug that cost the most time

**localhost:8000 was serving my old project.** During a demo run of the
(since-deleted) first build, the browser showed the UCT Jammie Shuttle —
my previous app — instead of Bin Run. The server logs said Bin Run was up.

Cornering it: check what actually owns the port (`ss -ltnp` — one listener,
the right uvicorn pid), then curl the same URL (`<title>Join · Bin Run</title>`
comes back), so the wrong pixels were being produced by the *browser*, not
the server. A stale **service worker** from when the shuttle app ran on
localhost:8000 was intercepting requests and serving its cached copy.
Serving on a port the browser had never seen fixed it instantly; unregister
worker + clear site data fixed 8000 itself. Lesson: when server and browser
disagree, believe curl.

Honourable mention: `pkill -f "uvicorn app.main:app"` kept exiting the whole
shell with code 144 — the pattern matched the *shell's own* command line,
which contained the same text, so pkill killed its parent. It bit twice
before switching to `fuser -k -n tcp <port>`, which kills by port and can't
self-match.

## What I'd do with another week

1. Point `TEST_DATABASE_URL` at a Neon branch in CI so the race is *proven*
   on Postgres, then delete the SQLite path and the dual-engine wrapper.
2. Real accounts — password hash, unique names, login — so returning
   tomorrow doesn't make a second me.
3. Notifications (even just email) instead of reload-to-find-out.
4. A bounding box in SQL before the haversine in Python, and pagination.
5. An events table so a pickup's claim/release history survives, instead of
   being overwritten in place.

## What I still don't understand about my own project

<!-- COLLINS: this section only counts if it's true. Keep what is, cut what
     isn't, add your own. These are the ones that surfaced during review. -->

- I can say "Postgres re-evaluates the WHERE under READ COMMITTED when the
  row lock releases", but I could not yet draw what happens step by step
  with three writers, or say precisely what changes under other isolation
  levels.
- I know the 5s SQLite busy timeout holds for 8 threads because the test
  says so. I don't know where it stops holding, or what the failure looks
  like when it does.
- Sessions: I understand the signature stops forging *someone else's*
  cookie, but I haven't thought through everything an attacker can do by
  replaying or keeping their *own* old cookie (e.g. after "logout").
