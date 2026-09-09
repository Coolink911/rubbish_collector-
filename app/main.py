"""Routes.

Day 1 scope, and nothing more: prove that a row written through this app is
still there after the host restarts. The `heartbeats` table below is a probe,
not a feature - delete it on Wednesday once real tables exist.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once on boot, and once on shutdown.

    Everything before `yield` happens as the app starts; everything after
    happens as it stops. (There is an older `@app.on_event("startup")` style
    you will see all over the internet - it still works but is deprecated, so
    this is the current way.)

    Creating the table here is safe to run on every boot because of
    IF NOT EXISTS: it does nothing on the second and every later start. That
    property has a name worth knowing - the operation is *idempotent*.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS heartbeats (
                id         SERIAL PRIMARY KEY,
                note       TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    yield


app = FastAPI(title="Bin Run", docs_url=None, redoc_url=None, lifespan=lifespan)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@app.get("/")
def index(request: Request):
    """Read every heartbeat back out.

    This one is done for you as the worked example - the shape of every read in
    this app is the same three lines: open a cursor, execute, fetch.
    """
    with db.cursor() as cur:
        cur.execute("SELECT id, note, created_at FROM heartbeats ORDER BY id DESC")
        beats = cur.fetchall()

    return templates.TemplateResponse(request, "index.html", {"beats": beats})


@app.post("/heartbeat")
async def add_heartbeat(request: Request):
    """Write one row, then send the browser back to "/".

    YOUR TURN. Three things to do:

      1. Read the submitted form:  form = await request.form()
         then form.get("note") for the text the user typed.

      2. Open a cursor like index() does, and execute an INSERT.
         Pass the value as a *parameter*, not by building the string:

             cur.execute("INSERT INTO heartbeats (note) VALUES (%s)", (note,))

         Never f-string a user's input into SQL. Someone types
         '); DROP TABLE heartbeats;-- and you find out why.

      3. Return RedirectResponse("/", status_code=303).
         303 tells the browser "go and GET this instead", so a refresh doesn't
         re-submit the form. Returning the page directly instead of redirecting
         is a real bug with a name - look up Post/Redirect/Get.

    When this works you will have answered learning checkpoint #1: what happens
    between the browser sending a form and a row existing in the database.
    """
    raise NotImplementedError("Write the INSERT - see the docstring above.")


@app.get("/healthz")
def healthz():
    """Cheap liveness check. Also proves the database is reachable, not just
    that the web process is up - those are different failures."""
    with db.cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        cur.fetchone()
    return {"ok": True}
