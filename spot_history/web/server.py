from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..core.config import build_paths
from . import data as web_data

BASE = Path(__file__).resolve().parent
paths = build_paths(os.environ.get("SPOT_HISTORY_DATA_ROOT"))

app = FastAPI(title="Spot History", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


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
            },
        )

    conn = web_data.connect(index_db)
    try:
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
        },
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "index": str(paths.index_db), "exists": paths.index_db.exists()}
