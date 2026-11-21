from __future__ import annotations

import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..connectors import discover
from ..core import identity
from ..core.config import build_paths
from ..core.index import build_index
from . import data as web_data

BASE = Path(__file__).resolve().parent
paths = build_paths(os.environ.get("SPOT_HISTORY_DATA_ROOT"))

app = FastAPI(title="Spot History", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov"}
MEDIA_DIRECTORIES = {"profile", "photos", "media", "thumbs"}


def _check_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin:
        host = urlsplit(origin).hostname
        if host and host != request.url.hostname:
            raise HTTPException(status_code=403, detail="Cross-Origin nicht erlaubt")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-Origin nicht erlaubt")


async def _form_value(request: Request, name: str) -> str:
    body = (await request.body()).decode("utf-8", "replace")
    values = parse_qs(body, keep_blank_values=True)
    return (values.get(name) or [""])[0].strip()


def _rebuild_index() -> None:
    build_index(discover(), paths, log=lambda _: None)


def _venue_after_rebuild(index_db, fallback_key: str, members: list[dict]):
    conn = web_data.connect(index_db)
    try:
        if members:
            ref = f"{members[0]['source']}:{members[0]['source_place_id']}"
            venue = web_data.get_venue_by_key(conn, ref)
            if venue:
                return venue
        return web_data.get_venue_by_key(conn, fallback_key)
    finally:
        conn.close()


def _redirect_venue(key: str, **params) -> RedirectResponse:
    url = f"/venues/{quote(key, safe='')}"
    clean = {name: value for name, value in params.items() if value}
    if clean:
        url += "?" + urlencode(clean)
    return RedirectResponse(url, status_code=303)


def _range_label(stats: dict) -> str:
    if not stats.get("first_visit"):
        return "—"
    first = datetime.fromtimestamp(int(stats["first_visit"]), timezone.utc).year
    last = datetime.fromtimestamp(int(stats["last_visit"]), timezone.utc).year
    return str(first) if first == last else f"{first}–{last}"


def _page_url(query: dict, page: int) -> str:
    params = {key: value for key, value in query.items() if value}
    params["page"] = page
    return "/?" + urlencode(params)


def _venues_page_url(query: dict, page: int) -> str:
    params = {key: value for key, value in query.items() if value}
    params["page"] = page
    return "/venues?" + urlencode(params)


def _photos_page_url(query: dict, page: int) -> str:
    params = {key: value for key, value in query.items() if value}
    params["page"] = page
    return "/photos?" + urlencode(params)


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    city: str | None = None,
    year: str | None = None,
    category: str | None = None,
    source: str | None = None,
    q: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    index_db = paths.index_db
    if not index_db.exists():
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "index_missing": True,
                "index_path": str(index_db),
                "visits": [],
                "map_points": [],
                "options": {"cities": [], "years": [], "categories": [], "sources": []},
                "stats": {"total": 0, "with_photos": 0, "places": 0, "first_visit": None, "last_visit": None},
                "built_at": None,
                "total": 0,
                "page": 1,
                "pages": 1,
                "prev_url": None,
                "next_url": None,
                "query": {},
                "city_value": city or "",
                "range_label": "—",
                "has_yelp": False,
                "profile": None,
                "photos_by_visit": {},
            },
        )

    conn = web_data.connect(index_db)
    try:
        profiles = web_data.load_profiles(conn)
        profile = next(
            (item for item in profiles if item["source"] == "foursquare"),
            profiles[0] if profiles else None,
        )
        options = web_data.load_options(conn)
        city_value = web_data.resolve_city(city, options)
        filters = web_data.Filters(
            city=city_value,
            year=(year or "").strip() or None,
            category=(category or "").strip() or None,
            source=(source or "").strip() or None,
            q=(q or "").strip() or None,
        )
        total = web_data.count_visits(conn, filters)
        pages = max(1, (total + web_data.PAGE_SIZE - 1) // web_data.PAGE_SIZE)
        page = min(max(1, page), pages)
        visits = web_data.query_visits(
            conn, filters, web_data.PAGE_SIZE, (page - 1) * web_data.PAGE_SIZE
        )
        photos_by_visit = web_data.photos_for_visits(
            conn, [visit["id"] for visit in visits]
        )
        map_points = web_data.query_map_points(conn, filters)
        stats = web_data.load_stats(conn, filters)
        built_at = web_data.build_time(conn)
    finally:
        conn.close()

    query = {
        "city": city_value or "",
        "year": year or "",
        "category": category or "",
        "source": source or "",
        "q": q or "",
    }
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "index_missing": False,
            "index_path": str(index_db),
            "visits": visits,
            "map_points": map_points,
            "options": options,
            "stats": stats,
            "built_at": built_at,
            "total": total,
            "page": page,
            "pages": pages,
            "prev_url": _page_url(query, page - 1) if page > 1 else None,
            "next_url": _page_url(query, page + 1) if page < pages else None,
            "query": query,
            "city_value": city_value or "",
            "range_label": _range_label(stats),
            "has_yelp": any(option["value"] == "yelp" for option in options["sources"]),
            "profile": profile,
            "photos_by_visit": photos_by_visit,
        },
    )


@app.get("/venues", response_class=HTMLResponse)
def venues(
    request: Request,
    city: str | None = None,
    category: str | None = None,
    source: str | None = None,
    q: str | None = None,
    match: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    index_db = paths.index_db
    if not index_db.exists():
        return templates.TemplateResponse(
            request,
            "venues.html",
            {
                "request": request,
                "index_missing": True,
                "index_path": str(index_db),
                "venues": [],
                "options": {"cities": [], "categories": [], "sources": []},
                "match_counts": {"all": 0, "osm": 0, "local": 0, "conflict": 0},
                "total": 0,
                "page": 1,
                "pages": 1,
                "prev_url": None,
                "next_url": None,
                "query": {},
                "profile": None,
            },
        )

    match_value = (match or "").strip() or None
    if match_value not in ("osm", "local", "conflict", "unmatched"):
        match_value = None
    conn = web_data.connect(index_db)
    try:
        profiles = web_data.load_profiles(conn)
        profile = next(
            (item for item in profiles if item["source"] == "foursquare"),
            profiles[0] if profiles else None,
        )
        options = web_data.load_venue_options(conn)
        match_counts = web_data.load_venue_match_counts(conn)
        filters = web_data.VenueFilters(
            city=(city or "").strip() or None,
            category=(category or "").strip() or None,
            source=(source or "").strip() or None,
            q=(q or "").strip() or None,
            match=match_value,
        )
        total = web_data.count_venues(conn, filters)
        pages = max(1, (total + web_data.PAGE_SIZE - 1) // web_data.PAGE_SIZE)
        page = min(max(1, page), pages)
        venues = web_data.query_venues(
            conn, filters, web_data.PAGE_SIZE, (page - 1) * web_data.PAGE_SIZE
        )
    finally:
        conn.close()

    query = {
        "city": city or "",
        "category": category or "",
        "source": source or "",
        "q": q or "",
        "match": match_value or "",
    }
    return templates.TemplateResponse(
        request,
        "venues.html",
        {
            "request": request,
            "index_missing": False,
            "index_path": str(index_db),
            "venues": venues,
            "options": options,
            "match_counts": match_counts,
            "total": total,
            "page": page,
            "pages": pages,
            "prev_url": _venues_page_url(query, page - 1) if page > 1 else None,
            "next_url": _venues_page_url(query, page + 1) if page < pages else None,
            "query": query,
            "profile": profile,
        },
    )


@app.get("/photos", response_class=HTMLResponse)
def photos(
    request: Request,
    city: str | None = None,
    year: str | None = None,
    source: str | None = None,
    q: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    index_db = paths.index_db
    if not index_db.exists():
        return templates.TemplateResponse(
            request,
            "photos.html",
            {
                "request": request,
                "index_missing": True,
                "index_path": str(index_db),
                "photos": [],
                "options": {"cities": [], "years": [], "categories": [], "sources": []},
                "total": 0,
                "page": 1,
                "pages": 1,
                "prev_url": None,
                "next_url": None,
                "query": {},
                "profile": None,
            },
        )

    conn = web_data.connect(index_db)
    try:
        profiles = web_data.load_profiles(conn)
        profile = next(
            (item for item in profiles if item["source"] == "foursquare"),
            profiles[0] if profiles else None,
        )
        options = web_data.load_options(conn)
        filters = web_data.PhotoFilters(
            city=(city or "").strip() or None,
            year=(year or "").strip() or None,
            source=(source or "").strip() or None,
            q=(q or "").strip() or None,
        )
        total = web_data.count_photos(conn, filters)
        pages = max(1, (total + web_data.PHOTO_PAGE_SIZE - 1) // web_data.PHOTO_PAGE_SIZE)
        page = min(max(1, page), pages)
        photos = web_data.query_photos(
            conn, filters, web_data.PHOTO_PAGE_SIZE, (page - 1) * web_data.PHOTO_PAGE_SIZE
        )
        built_at = web_data.build_time(conn)
    finally:
        conn.close()

    query = {
        "city": city or "",
        "year": year or "",
        "source": source or "",
        "q": q or "",
    }
    return templates.TemplateResponse(
        request,
        "photos.html",
        {
            "request": request,
            "index_missing": False,
            "index_path": str(index_db),
            "photos": photos,
            "options": options,
            "total": total,
            "page": page,
            "pages": pages,
            "prev_url": _photos_page_url(query, page - 1) if page > 1 else None,
            "next_url": _photos_page_url(query, page + 1) if page < pages else None,
            "query": query,
            "profile": profile,
            "built_at": built_at,
        },
    )


@app.get("/venues/{key}", response_class=HTMLResponse)
def venue_detail(
    request: Request,
    key: str,
    page: int = 1,
    assign_error: str | None = None,
    assign_ok: str | None = None,
) -> HTMLResponse:
    index_db = paths.index_db
    if not index_db.exists():
        raise HTTPException(status_code=503, detail="Index missing")
    conn = web_data.connect(index_db)
    try:
        profiles = web_data.load_profiles(conn)
        profile = next(
            (item for item in profiles if item["source"] == "foursquare"),
            profiles[0] if profiles else None,
        )
        venue = web_data.get_venue_by_key(conn, key)
        if not venue:
            raise HTTPException(status_code=404, detail="Venue not found")
        if key != venue["key"]:
            target = f"/venues/{quote(venue['key'], safe='')}"
            if page > 1:
                target += f"?page={page}"
            return RedirectResponse(target, status_code=302)
        place_id = venue["id"]
        filters = web_data.Filters(place_id=place_id)
        total = web_data.count_visits(conn, filters)
        pages = max(1, (total + web_data.PAGE_SIZE - 1) // web_data.PAGE_SIZE)
        page = min(max(1, page), pages)
        visits = web_data.query_visits(
            conn, filters, web_data.PAGE_SIZE, (page - 1) * web_data.PAGE_SIZE
        )
        photos = web_data.photos_for_place(conn, place_id, limit=24)
        members = web_data.venue_members(conn, place_id)
    finally:
        conn.close()
    member_keys = {
        (member["source"], member["source_place_id"]) for member in members
    }
    osm_candidates = [
        candidate
        for candidate in identity.load_osm_candidates(paths.place_identity_db)
        if (candidate["source"], candidate["source_place_id"]) in member_keys
    ]
    return templates.TemplateResponse(
        request,
        "venue.html",
        {
            "request": request,
            "venue": venue,
            "visits": visits,
            "photos": photos,
            "total": total,
            "page": page,
            "pages": pages,
            "prev_url": (
                f"/venues/{venue['key']}?page={page - 1}" if page > 1 else None
            ),
            "next_url": (
                f"/venues/{venue['key']}?page={page + 1}" if page < pages else None
            ),
            "profile": profile,
            "members": members,
            "osm_candidates": osm_candidates,
            "assign_error": assign_error,
            "assign_ok": assign_ok,
        },
    )


@app.post("/venues/{key}/osm")
async def assign_venue_osm(request: Request, key: str) -> RedirectResponse:
    _check_same_origin(request)
    raw = await _form_value(request, "osm")
    index_db = paths.index_db
    if not index_db.exists():
        raise HTTPException(status_code=503, detail="Index missing")
    conn = web_data.connect(index_db)
    try:
        venue = web_data.get_venue_by_key(conn, key)
        if not venue:
            raise HTTPException(status_code=404, detail="Venue not found")
        members = web_data.venue_members(conn, venue["id"])
    finally:
        conn.close()
    if not raw:
        return _redirect_venue(
            venue["key"], assign_error="Bitte eine OSM-ID angeben."
        )
    if not members:
        return _redirect_venue(
            venue["key"], assign_error="Kein Quell-Ort für diese Zuordnung."
        )
    try:
        osm_type, osm_id = identity.parse_osm_ref(raw)
    except ValueError as exc:
        return _redirect_venue(venue["key"], assign_error=str(exc))
    for member in members:
        identity.upsert_osm_assignment(
            paths.place_identity_db,
            member["source"],
            member["source_place_id"],
            osm_type,
            osm_id,
            method="manual",
            confidence="manual",
            note="web",
        )
        identity.clear_osm_candidates(
            paths.place_identity_db,
            member["source"],
            member["source_place_id"],
        )
    try:
        _rebuild_index()
    except Exception as exc:  # noqa: BLE001 - Fehler im UI anzeigen
        return _redirect_venue(
            venue["key"], assign_error=f"Index-Rebuild fehlgeschlagen: {exc}"
        )
    updated = _venue_after_rebuild(index_db, venue["key"], members)
    if not updated:
        return _redirect_venue(venue["key"], assign_error="Ort nach Rebuild nicht gefunden.")
    return _redirect_venue(updated["key"], assign_ok=f"{osm_type}/{osm_id}")


@app.post("/venues/{key}/osm/clear")
async def clear_venue_osm(request: Request, key: str) -> RedirectResponse:
    _check_same_origin(request)
    index_db = paths.index_db
    if not index_db.exists():
        raise HTTPException(status_code=503, detail="Index missing")
    conn = web_data.connect(index_db)
    try:
        venue = web_data.get_venue_by_key(conn, key)
        if not venue:
            raise HTTPException(status_code=404, detail="Venue not found")
        members = web_data.venue_members(conn, venue["id"])
    finally:
        conn.close()
    removed = 0
    for member in members:
        if identity.delete_osm_assignment(
            paths.place_identity_db,
            member["source"],
            member["source_place_id"],
        ):
            removed += 1
    try:
        _rebuild_index()
    except Exception as exc:  # noqa: BLE001 - Fehler im UI anzeigen
        return _redirect_venue(
            venue["key"], assign_error=f"Index-Rebuild fehlgeschlagen: {exc}"
        )
    updated = _venue_after_rebuild(index_db, venue["key"], members)
    if not updated:
        return _redirect_venue(venue["key"], assign_error="Ort nach Rebuild nicht gefunden.")
    message = "Zuordnung entfernt." if removed else "Keine Zuordnung vorhanden."
    return _redirect_venue(updated["key"], assign_ok=message)


@app.get("/media/{path:path}")
def media(path: str) -> FileResponse:
    data_root = paths.data_root.resolve()
    candidate = (paths.root / path).resolve()
    if not candidate.is_relative_to(data_root):
        raise HTTPException(status_code=404)
    relative = candidate.relative_to(data_root)
    if (
        len(relative.parts) < 3
        or relative.parts[1] not in MEDIA_DIRECTORIES
        or candidate.suffix.lower() not in MEDIA_EXTENSIONS
        or not candidate.is_file()
    ):
        raise HTTPException(status_code=404)
    media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    return FileResponse(candidate, media_type=media_type)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "index": str(paths.index_db), "exists": paths.index_db.exists()}
