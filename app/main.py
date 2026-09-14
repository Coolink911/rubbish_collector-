"""HTTP routes.

Server-rendered forms, no client framework. The only JavaScript that matters
is the map and the "parse my sentence" fetch, and the app works with both of
them broken.

Sessions are signed cookies (SessionMiddleware + SECRET_KEY): the cookie
stores the user id and the server verifies the signature, so a visitor can
read their own cookie but not mint someone else's without the key.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_303_SEE_OTHER

from . import config, db, geocode, models, parse
from .models import ClaimFailed, NotAllowed, User

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Migrate on boot so a fresh deploy doesn't need a shell."""
    version = db.migrate()
    log.info("database at schema version %s", version)
    yield


app = FastAPI(title="Bin Run", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware, secret_key=config.secret_key(), max_age=14 * 24 * 3600
)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

SEE_OTHER = HTTP_303_SEE_OTHER

# One line per request. Not structured JSON logging - at this scale a line a
# human can read in the Space's log tail beats a line a log platform we don't
# have could query. Static files and the healthcheck log at DEBUG so the tail
# stays mostly signal.
access_log = logging.getLogger("binrun.access")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    quiet = request.url.path.startswith("/static") or request.url.path == "/healthz"
    access_log.log(
        logging.DEBUG if quiet else logging.INFO,
        "%s %s -> %s in %.1fms",
        request.method, request.url.path, response.status_code, ms,
    )
    return response


# --- session helpers --------------------------------------------------------


def current_user(request: Request) -> User | None:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    user = models.get_user(user_id)
    if user is None:  # the database was reset out from under the cookie
        request.session.clear()
    return user


def require_user(request: Request) -> User:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Pick a name and a role first.")
    return user


def require_collector(request: Request) -> User:
    user = require_user(request)
    if not user.is_collector:
        raise HTTPException(status_code=403, detail="Only collectors can do that.")
    return user


def require_resident(request: Request) -> User:
    user = require_user(request)
    if user.is_collector:
        raise HTTPException(status_code=403, detail="Only residents can do that.")
    return user


def flash(request: Request, message: str, kind: str = "info") -> None:
    request.session.setdefault("flashes", []).append({"text": message, "kind": kind})


def take_flashes(request: Request) -> list[dict]:
    return request.session.pop("flashes", [])


def render(request: Request, template: str, **context) -> HTMLResponse:
    user = context.pop("user", None) or current_user(request)
    return templates.TemplateResponse(
        request,
        template,
        {
            "user": user,
            "flashes": take_flashes(request),
            "default_center": config.DEFAULT_CENTER,
            **context,
        },
    )


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=SEE_OTHER)


def home_for(user: User) -> str:
    return "/jobs" if user.is_collector else "/requests"


# --- errors -----------------------------------------------------------------


@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException):
    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 401:
        return redirect("/join")
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "user": current_user(request),
            "flashes": [],
            "status": exc.status_code,
            "detail": exc.detail,
        },
        status_code=exc.status_code,
    )


# --- joining ----------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    user = current_user(request)
    if user is None:
        return redirect("/join")
    return redirect(home_for(user))


@app.get("/join", response_class=HTMLResponse)
def join_form(request: Request):
    user = current_user(request)
    if user is not None:
        return redirect(home_for(user))
    return render(request, "join.html")


@app.post("/join")
def join(request: Request, name: str = Form(...), role: str = Form(...)):
    try:
        user = models.create_user(name, role)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return redirect("/join")
    request.session["user_id"] = user.id
    flash(request, f"You're in as {user.name}, a {user.role}.", "ok")
    return redirect(home_for(user))


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/join")


@app.post("/me/location")
async def set_location(request: Request):
    """The browser's geolocation, if the collector shares it."""
    user = require_user(request)
    try:
        payload = await request.json()
        lat, lng = float(payload["lat"]), float(payload["lng"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="lat and lng must be numbers.")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(status_code=400, detail="Those aren't real coordinates.")
    models.set_user_location(user.id, lat, lng)
    return {"ok": True, "lat": lat, "lng": lng}


def _read_photo(photo: UploadFile | None) -> tuple[str, str] | tuple[None, str | None]:
    """Validate an uploaded photo. Returns (b64, mime) or (None, reason).

    A bad photo is never a reason to lose the pickup - callers post without
    it and tell the resident why.
    """
    if photo is None or not (photo.filename or "").strip():
        return None, None
    mime = (photo.content_type or "").lower()
    if mime not in models.PHOTO_MIMES:
        return None, "That file isn't a photo I can read (JPEG/PNG/WebP/GIF)."
    data = photo.file.read(models.PHOTO_MAX_BYTES + 1)
    if len(data) > models.PHOTO_MAX_BYTES:
        return None, "That photo is over 1 MB - the pickup posted without it."
    if not data:
        return None, None
    return base64.standard_b64encode(data).decode("ascii"), mime


# --- residents --------------------------------------------------------------


@app.get("/requests", response_class=HTMLResponse)
def my_requests(request: Request):
    user = require_resident(request)
    return render(
        request, "requests.html", pickups=models.list_pickups_for_resident(user.id)
    )


@app.get("/api/requests.json")
def requests_json(request: Request):
    """Status snapshot for the resident's own requests. The requests page
    polls this and reloads itself only when something actually changed -
    the free-tier answer to push notifications."""
    user = require_resident(request)
    return {
        "requests": [
            {"id": p["id"], "status": p["status"]}
            for p in models.list_pickups_for_resident(user.id)
        ]
    }


@app.get("/new", response_class=HTMLResponse)
def new_request_form(request: Request):
    require_resident(request)
    return render(request, "new.html", draft={})


@app.post("/parse")
async def parse_endpoint(request: Request):
    """Parse free text into form fields. Always 200 - the form reads `parsed`."""
    require_resident(request)
    try:
        payload = await request.json()
        text = payload.get("text", "")
    except Exception:
        text = ""
    return dict(parse.parse_free_text(text))


@app.post("/parse-photo")
def parse_photo_endpoint(request: Request, photo: UploadFile | None = File(None)):
    """Photo -> form fields. Always 200; the form reads `parsed`, exactly
    like the text parser."""
    require_resident(request)
    photo_b64, note = _read_photo(photo)
    if not photo_b64:
        return dict(parse.fallback("", note or "No photo received."))
    return dict(parse.analyze_photo(photo_b64, photo.content_type.lower()))


@app.post("/pickups")
def create_pickup(
    request: Request,
    description: str = Form(""),
    bag_count: str = Form(""),
    size: str = Form(""),
    address: str = Form(""),
    when_text: str = Form(""),
    window_start: str = Form(""),
    window_end: str = Form(""),
    notes: str = Form(""),
    raw_text: str = Form(""),
    parsed: str = Form(""),
    photo: UploadFile | None = File(None),
):
    user = require_resident(request)

    description = description.strip()
    if not description:
        flash(request, "Say what needs collecting.", "error")
        return render(
            request,
            "new.html",
            draft={
                "description": description,
                "bag_count": bag_count,
                "size": size,
                "address": address,
                "when_text": when_text,
                "notes": notes,
                "raw_text": raw_text,
            },
        )

    try:
        bags = int(bag_count) if bag_count.strip() else None
    except ValueError:
        bags = None

    located = geocode.geocode(address)

    pickup_id = models.create_pickup(
        user.id,
        description,
        bag_count=bags,
        size=size.strip() or None,
        address=address.strip() or None,
        when_text=when_text.strip() or None,
        # Hidden fields the parser filled - but forms are editable by anyone
        # with devtools, so they go through the same cleaner as model output.
        window_start=parse._clean_datetime(window_start),
        window_end=parse._clean_datetime(window_end),
        notes=notes.strip() or None,
        raw_text=raw_text.strip() or None,
        parsed=parsed == "1",
        lat=located.lat,
        lng=located.lng,
        geocode_note=located.note,
    )

    photo_b64, photo_note = _read_photo(photo)
    if photo_b64:
        models.attach_photo(pickup_id, photo.content_type.lower(), photo_b64)
    elif photo_note:
        flash(request, photo_note, "warn")

    if located.ok:
        flash(request, "Posted. Collectors nearby can see it now.", "ok")
    else:
        flash(
            request,
            f"Posted. {located.note or 'We could not place it on the map.'} "
            "Collectors will still see the address.",
            "warn",
        )
    return redirect(f"/pickups/{pickup_id}")


@app.post("/pickups/{pickup_id}/cancel")
def cancel(request: Request, pickup_id: int):
    user = require_resident(request)
    try:
        models.cancel_pickup(pickup_id, user.id)
        flash(request, "Request cancelled.", "ok")
    except NotAllowed as exc:
        flash(request, str(exc), "error")
    return redirect("/requests")


# --- collectors -------------------------------------------------------------


@app.get("/jobs", response_class=HTMLResponse)
def jobs(request: Request):
    user = require_collector(request)
    open_jobs = models.sort_by_distance(models.list_open_pickups(), user.lat, user.lng)
    return render(
        request,
        "jobs.html",
        jobs=open_jobs,
        mine=models.list_pickups_for_collector(user.id),
    )


@app.get("/api/jobs.json")
def jobs_json(request: Request):
    user = require_collector(request)
    open_jobs = models.sort_by_distance(models.list_open_pickups(), user.lat, user.lng)
    return {
        "me": {"lat": user.lat, "lng": user.lng},
        "jobs": [
            {
                "id": j["id"],
                "description": j["description"],
                "address": j["address"],
                "lat": j["lat"],
                "lng": j["lng"],
                "distance_km": j["distance_km"],
                "size": j["size"],
                "bag_count": j["bag_count"],
            }
            for j in open_jobs
        ],
    }


@app.post("/pickups/{pickup_id}/claim")
def claim(request: Request, pickup_id: int):
    user = require_collector(request)
    try:
        models.claim_pickup(pickup_id, user.id)
        flash(request, "Claimed. It's yours - go get it.", "ok")
        return redirect(f"/pickups/{pickup_id}")
    except ClaimFailed as exc:
        # The losing side of the race lands here. Not an error page: the job
        # board is exactly where they want to be next.
        flash(request, str(exc), "warn")
        return redirect("/jobs")


@app.post("/pickups/{pickup_id}/done")
def done(request: Request, pickup_id: int):
    user = require_collector(request)
    try:
        models.complete_pickup(pickup_id, user.id)
        flash(request, "Marked collected.", "ok")
    except NotAllowed as exc:
        flash(request, str(exc), "error")
    return redirect(f"/pickups/{pickup_id}")


@app.post("/pickups/{pickup_id}/release")
def release(request: Request, pickup_id: int):
    user = require_collector(request)
    try:
        models.release_pickup(pickup_id, user.id)
        flash(request, "Released. It's back on the open list.", "ok")
        return redirect("/jobs")
    except NotAllowed as exc:
        flash(request, str(exc), "error")
        return redirect(f"/pickups/{pickup_id}")


# --- shared -----------------------------------------------------------------


@app.get("/pickups/{pickup_id}", response_class=HTMLResponse)
def pickup_detail(request: Request, pickup_id: int):
    user = require_user(request)
    pickup = models.get_pickup(pickup_id)
    if pickup is None:
        raise HTTPException(status_code=404, detail="No pickup with that number.")
    if user.is_collector:
        pickup["distance_km"] = (
            round(models.haversine_km(user.lat, user.lng, pickup["lat"], pickup["lng"]), 2)
            if None not in (user.lat, user.lng, pickup["lat"], pickup["lng"])
            else None
        )
    return render(request, "pickup.html", pickup=pickup)


@app.get("/pickups/{pickup_id}/photo")
def pickup_photo(request: Request, pickup_id: int):
    require_user(request)  # photos are for participants, not the open web
    photo = models.get_photo(pickup_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="No photo on that pickup.")
    return Response(
        content=base64.standard_b64decode(photo["data_b64"]),
        media_type=photo["mime"],
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/healthz")
def healthz():
    """Proves the database is reachable, not just that the process is up -
    those are different failures."""
    with db.cursor() as cur:
        cur.execute("SELECT 1 AS ok").fetchone()
    return {"ok": True}
