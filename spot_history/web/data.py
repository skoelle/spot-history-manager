from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import ROOT
from ..core.media import is_video, thumb_relpath

PAGE_SIZE = 60
PHOTO_PAGE_SIZE = 48
MAP_LIMIT = 5000
DEFAULT_CITY = "München"
MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

FROM = "FROM visits v LEFT JOIN places p ON p.id = v.place_id"


@dataclass(frozen=True)
class Filters:
    city: str | None = None
    year: str | None = None
    category: str | None = None
    source: str | None = None
    q: str | None = None
    place_id: int | None = None


@dataclass(frozen=True)
class VenueFilters:
    city: str | None = None
    category: str | None = None
    source: str | None = None
    q: str | None = None


@dataclass(frozen=True)
class PhotoFilters:
    city: str | None = None
    year: str | None = None
    source: str | None = None
    q: str | None = None
    place_id: int | None = None


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def where_clause(filters: Filters) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    if filters.city:
        clauses.append("p.city = ?")
        params.append(filters.city)
    if filters.year:
        clauses.append("strftime('%Y', v.visited_at, 'unixepoch') = ?")
        params.append(filters.year)
    if filters.category:
        clauses.append("p.category = ?")
        params.append(filters.category)
    if filters.source:
        clauses.append("v.source = ?")
        params.append(filters.source)
    if filters.place_id is not None:
        clauses.append("v.place_id = ?")
        params.append(filters.place_id)
    if filters.q:
        like = f"%{filters.q}%"
        clauses.append(
            "(p.name LIKE ? OR p.category LIKE ? OR v.note LIKE ? "
            "OR p.address LIKE ? OR p.city LIKE ?)"
        )
        params.extend([like, like, like, like, like])
    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def _options(conn: sqlite3.Connection, column: str, limit: int | None = None) -> list[dict]:
    sql = (
        f"SELECT {column} AS value, COUNT(*) AS count {FROM} "
        f"WHERE {column} IS NOT NULL AND {column} != '' "
        f"GROUP BY {column} ORDER BY count DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(row) for row in conn.execute(sql)]


def load_options(conn: sqlite3.Connection) -> dict:
    years = [
        dict(row)
        for row in conn.execute(
            "SELECT strftime('%Y', v.visited_at, 'unixepoch') AS value, COUNT(*) AS count "
            f"{FROM} GROUP BY value ORDER BY value DESC"
        )
    ]
    return {
        "cities": _options(conn, "p.city"),
        "years": years,
        "categories": _options(conn, "p.category", limit=80),
        "sources": _options(conn, "v.source"),
    }


def resolve_city(city: str | None, options: dict) -> str | None:
    if city is not None:
        return city or None
    available = [row["value"] for row in options["cities"]]
    if DEFAULT_CITY in available:
        return DEFAULT_CITY
    return available[0] if available else None


def _format_moment(local_datetime: str | None, visited_at: int) -> datetime:
    if local_datetime:
        try:
            return datetime.fromisoformat(local_datetime)
        except ValueError:
            pass
    return datetime.fromtimestamp(int(visited_at), timezone.utc)


def _format_row(row: sqlite3.Row) -> dict:
    moment = _format_moment(row["local_datetime"], row["visited_at"])
    return {
        "id": row["id"],
        "source": row["source"],
        "source_visit_id": row["source_visit_id"],
        "visited_at": row["visited_at"],
        "date": moment.strftime("%d.%m.%Y"),
        "time": moment.strftime("%H:%M"),
        "day": moment.strftime("%d"),
        "month": MONTHS[moment.month - 1],
        "year": moment.strftime("%Y"),
        "place_id": row["place_id"],
        "place_name": row["place_name"],
        "category": row["category"],
        "address": row["address"],
        "city": row["city"],
        "state": row["state"],
        "country_code": row["country_code"],
        "lat": row["lat"],
        "lng": row["lng"],
        "note": row["note"],
        "source_url": row["source_url"],
        "photo_count": row["photo_count"] or 0,
        "has_coords": bool(row["has_coords"]),
        "coords_derived": bool(row["coords_derived"]),
    }


VISIT_SELECT = (
    "SELECT v.id, v.source, v.source_visit_id, v.visited_at, v.local_datetime, "
    "v.note, v.source_url, v.has_coords, v.coords_derived, "
    "p.id AS place_id, p.name AS place_name, p.category, p.address, p.city, "
    "p.state, p.country_code, p.lat, p.lng, "
    "(SELECT COUNT(*) FROM photos ph WHERE ph.visit_id = v.id) AS photo_count "
    f"{FROM}"
)


def count_visits(conn: sqlite3.Connection, filters: Filters) -> int:
    where, params = where_clause(filters)
    row = conn.execute(f"SELECT COUNT(*) AS n {FROM}{where}", params).fetchone()
    return int(row["n"])


def query_visits(
    conn: sqlite3.Connection, filters: Filters, limit: int, offset: int
) -> list[dict]:
    where, params = where_clause(filters)
    sql = f"{VISIT_SELECT}{where} ORDER BY v.visited_at DESC LIMIT ? OFFSET ?"
    rows = conn.execute(sql, [*params, limit, offset]).fetchall()
    return [_format_row(row) for row in rows]


def query_map_points(conn: sqlite3.Connection, filters: Filters) -> list[dict]:
    where, params = where_clause(filters)
    condition = "p.lat IS NOT NULL AND p.lng IS NOT NULL"
    if where:
        where = where + " AND " + condition
    else:
        where = " WHERE " + condition
    sql = f"{VISIT_SELECT}{where} ORDER BY v.visited_at DESC LIMIT ?"
    rows = conn.execute(sql, [*params, MAP_LIMIT]).fetchall()
    return [
        {
            "id": row["id"],
            "lat": row["lat"],
            "lng": row["lng"],
            "place": row["place_name"],
            "place_id": row["place_id"],
            "category": row["category"],
            "address": row["address"],
            "city": row["city"],
            "state": row["state"],
            "country_code": row["country_code"],
            "date": _format_moment(row["local_datetime"], row["visited_at"]).strftime("%d.%m.%Y"),
            "time": _format_moment(row["local_datetime"], row["visited_at"]).strftime("%H:%M"),
            "source": row["source"],
            "url": row["source_url"],
            "photo_count": row["photo_count"] or 0,
            "has_coords": bool(row["has_coords"]),
            "coords_derived": bool(row["coords_derived"]),
        }
        for row in rows
    ]


PHOTO_SELECT = (
    "SELECT ph.source, ph.source_photo_id, ph.local_path, "
    "ph.url AS photo_url, ph.taken_at, v.id AS visit_id, v.visited_at, "
    "v.local_datetime, v.source_url, p.id AS place_id, p.name AS place_name, "
    "p.category, p.address, p.city "
    "FROM photos ph JOIN visits v ON v.id = ph.visit_id "
    "LEFT JOIN places p ON p.id = v.place_id"
)


def photo_where_clause(filters: PhotoFilters) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    if filters.city:
        clauses.append("p.city = ?")
        params.append(filters.city)
    if filters.year:
        clauses.append("strftime('%Y', v.visited_at, 'unixepoch') = ?")
        params.append(filters.year)
    if filters.source:
        clauses.append("ph.source = ?")
        params.append(filters.source)
    if filters.place_id is not None:
        clauses.append("v.place_id = ?")
        params.append(filters.place_id)
    if filters.q:
        like = f"%{filters.q}%"
        clauses.append(
            "(p.name LIKE ? OR p.category LIKE ? OR p.address LIKE ? OR p.city LIKE ?)"
        )
        params.extend([like, like, like, like])
    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def _format_photo(row: sqlite3.Row) -> dict:
    source = row["source"]
    photo_id = row["source_photo_id"]
    local_path = row["local_path"] or ""
    video = bool(local_path) and is_video(local_path)
    source_url = f"/media/{local_path}" if local_path else (row["photo_url"] or "")
    thumb_url = source_url
    if local_path and not video:
        relative = thumb_relpath(source, photo_id)
        if (ROOT / relative).is_file():
            thumb_url = f"/media/{relative}"
    moment = _format_moment(row["local_datetime"], row["visited_at"])
    return {
        "id": f"{source}:{photo_id}",
        "source": source,
        "visit_id": row["visit_id"],
        "place_id": row["place_id"],
        "place_name": row["place_name"],
        "category": row["category"],
        "address": row["address"],
        "city": row["city"],
        "date": moment.strftime("%d.%m.%Y"),
        "time": moment.strftime("%H:%M"),
        "source_url": row["source_url"],
        "url": source_url,
        "thumb_url": thumb_url,
        "is_video": video,
        "is_image": not video,
    }


def count_photos(conn: sqlite3.Connection, filters: PhotoFilters) -> int:
    where, params = photo_where_clause(filters)
    sql = (
        "SELECT COUNT(*) AS n FROM photos ph JOIN visits v ON v.id = ph.visit_id "
        f"LEFT JOIN places p ON p.id = v.place_id{where}"
    )
    return int(conn.execute(sql, params).fetchone()["n"] or 0)


def query_photos(
    conn: sqlite3.Connection, filters: PhotoFilters, limit: int, offset: int
) -> list[dict]:
    where, params = photo_where_clause(filters)
    sql = (
        f"{PHOTO_SELECT}{where} "
        "ORDER BY COALESCE(ph.taken_at, v.visited_at) DESC LIMIT ? OFFSET ?"
    )
    rows = conn.execute(sql, [*params, limit, offset]).fetchall()
    return [_format_photo(row) for row in rows]


def photos_for_visits(
    conn: sqlite3.Connection, visit_ids: list[int], limit_per_visit: int = 3
) -> dict[int, list[dict]]:
    if not visit_ids:
        return {}
    placeholders = ", ".join("?" for _ in visit_ids)
    rows = conn.execute(
        f"{PHOTO_SELECT} WHERE v.id IN ({placeholders}) "
        "ORDER BY COALESCE(ph.taken_at, v.visited_at)",
        visit_ids,
    ).fetchall()
    result: dict[int, list[dict]] = {}
    for row in rows:
        bucket = result.setdefault(row["visit_id"], [])
        if len(bucket) < limit_per_visit:
            bucket.append(_format_photo(row))
    return result


def photos_for_place(
    conn: sqlite3.Connection, place_id: int, limit: int = 24
) -> list[dict]:
    return query_photos(conn, PhotoFilters(place_id=place_id), limit, 0)


def load_stats(conn: sqlite3.Connection, filters: Filters) -> dict:
    where, params = where_clause(filters)
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN EXISTS (SELECT 1 FROM photos ph WHERE ph.visit_id = v.id) "
        "THEN 1 ELSE 0 END) AS with_photos, "
        "COUNT(DISTINCT p.id) AS places, "
        "MIN(v.visited_at) AS first_visit, MAX(v.visited_at) AS last_visit "
        f"{FROM}{where}",
        params,
    ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "with_photos": int(row["with_photos"] or 0),
        "places": int(row["places"] or 0),
        "first_visit": row["first_visit"],
        "last_visit": row["last_visit"],
    }


def load_profiles(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute("SELECT * FROM source_profiles ORDER BY source").fetchall()
    except sqlite3.OperationalError:
        return []
    profiles = []
    for row in rows:
        counts = {}
        if row["counts_json"]:
            try:
                counts = json.loads(row["counts_json"])
            except (TypeError, ValueError):
                counts = {}
        display_name = row["display_name"] or row["source_profile_id"] or row["source"]
        initials = "".join(
            part[0].upper() for part in display_name.split() if part
        )[:2]
        profiles.append(
            {
                "source": row["source"],
                "source_profile_id": row["source_profile_id"],
                "display_name": display_name,
                "home_city": row["home_city"],
                "country_code": row["country_code"],
                "photo_path": row["photo_path"],
                "counts": counts,
                "initials": initials or "?",
                "updated_at": row["updated_at"],
            }
        )
    return profiles


def venue_where_clause(filters: VenueFilters) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    if filters.city:
        clauses.append("p.city = ?")
        params.append(filters.city)
    if filters.category:
        clauses.append("p.category = ?")
        params.append(filters.category)
    if filters.source:
        clauses.append("p.source = ?")
        params.append(filters.source)
    if filters.q:
        like = f"%{filters.q}%"
        clauses.append(
            "(p.name LIKE ? OR p.category LIKE ? OR p.address LIKE ? OR p.city LIKE ?)"
        )
        params.extend([like, like, like, like])
    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def _place_options(conn: sqlite3.Connection, column: str, limit: int | None = None) -> list[dict]:
    sql = (
        f"SELECT {column} AS value, COUNT(*) AS count FROM places p "
        f"WHERE {column} IS NOT NULL AND {column} != '' "
        f"GROUP BY {column} ORDER BY count DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(row) for row in conn.execute(sql)]


def load_venue_options(conn: sqlite3.Connection) -> dict:
    return {
        "cities": _place_options(conn, "p.city"),
        "categories": _place_options(conn, "p.category", limit=80),
        "sources": _place_options(conn, "p.source"),
    }


def count_venues(conn: sqlite3.Connection, filters: VenueFilters) -> int:
    where, params = venue_where_clause(filters)
    row = conn.execute(f"SELECT COUNT(*) AS n FROM places p{where}", params).fetchone()
    return int(row["n"] or 0)


VENUE_SELECT = (
    "SELECT p.id, p.source, p.source_place_id, p.name, p.category, p.address, "
    "p.city, p.state, p.postal_code, p.country_code, p.lat, p.lng, "
    "COUNT(v.id) AS visits_count, MIN(v.visited_at) AS first_visit, "
    "MAX(v.visited_at) AS last_visit, "
    "(SELECT COUNT(*) FROM photos ph JOIN visits pv ON pv.id = ph.visit_id "
    " WHERE pv.place_id = p.id) AS photo_count "
    "FROM places p LEFT JOIN visits v ON v.place_id = p.id"
)


def _format_venue(row: sqlite3.Row) -> dict:
    first_visit = row["first_visit"]
    last_visit = row["last_visit"]
    source = row["source"]
    return {
        "id": row["id"],
        "source": source,
        "source_place_id": row["source_place_id"],
        "name": row["name"],
        "category": row["category"],
        "address": row["address"],
        "city": row["city"],
        "state": row["state"],
        "postal_code": row["postal_code"],
        "country_code": row["country_code"],
        "lat": row["lat"],
        "lng": row["lng"],
        "visits_count": int(row["visits_count"] or 0),
        "photo_count": int(row["photo_count"] or 0),
        "first_visit": first_visit,
        "last_visit": last_visit,
        "first_visit_label": (
            datetime.fromtimestamp(int(first_visit), timezone.utc).strftime("%d.%m.%Y")
            if first_visit else None
        ),
        "last_visit_label": (
            datetime.fromtimestamp(int(last_visit), timezone.utc).strftime("%d.%m.%Y")
            if last_visit else None
        ),
        "foursquare_url": (
            f"https://foursquare.com/v/{row['source_place_id']}"
            if source == "foursquare" and row["source_place_id"] else None
        ),
    }


def query_venues(
    conn: sqlite3.Connection, filters: VenueFilters, limit: int, offset: int
) -> list[dict]:
    where, params = venue_where_clause(filters)
    sql = (
        f"{VENUE_SELECT}{where} GROUP BY p.id "
        "ORDER BY last_visit DESC, p.name LIMIT ? OFFSET ?"
    )
    rows = conn.execute(sql, [*params, limit, offset]).fetchall()
    return [_format_venue(row) for row in rows]


def get_venue(conn: sqlite3.Connection, place_id: int) -> dict | None:
    row = conn.execute(
        f"{VENUE_SELECT} WHERE p.id = ? GROUP BY p.id", (place_id,)
    ).fetchone()
    return _format_venue(row) if row else None


def build_time(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM index_meta WHERE key = 'built_at'").fetchone()
    if not row:
        return None
    try:
        moment = datetime.fromisoformat(row["value"])
        return moment.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return row["value"]
