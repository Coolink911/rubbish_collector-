# Bin Run

On-demand rubbish collection. Residents request a pickup; collectors nearby
claim it and mark it done.

Built for Build Week, September 2026. **Work in progress - Day 1 of 6.**

---

## Status

Skeleton only. A persistence probe that writes rows to Postgres, to prove data
survives a host restart before anything is built on top of it.

## Run it

```bash
git clone <TODO>
cd "rubbish app"
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # paste your Neon connection string into it
uvicorn app.main:app --reload
```

Open http://localhost:8000.

## Environment

| Variable | Needed? | What happens without it |
| --- | --- | --- |
| `DATABASE_URL` | **yes** | the app refuses to start, with a message telling you why |
| `SECRET_KEY` | not yet | needed from Friday, when sessions arrive |

## Deployment

Hugging Face Space, Docker SDK, serving on port 7860 (see `Dockerfile`).
The Space's filesystem is wiped on every restart, so nothing is written to it -
all state lives in a Neon Postgres reached over `DATABASE_URL`, set as a Space
secret.

## Tests

```bash
pytest -q
```

<!-- TODO before submission:
     - what this is and how to run it (above, keep it current)
     - the "new to me" thing, named as a concept
     - keys and quirks
     - what works, what was cut, and why
-->
