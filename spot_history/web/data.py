from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PAGE_SIZE = 60
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
        "place_name": row["place_name"],
        "category": row["category"],
        "address": row["address"],
        "city": row["city"],
        "lat": row["lat"],
        "lng": row["lng"],
        "note": row["note"],
        "source_url": row["source_url"],
        "photo_count": row["photo_count"] or 0,
    }


VISIT_SELECT = (
    "SELECT v.id, v.source, v.source_visit_id, v.visited_at, v.local_datetime, "
    "v.note, v.source_url, p.name AS place_name, p.category, p.address, p.city, "
    "p.lat, p.lng, "
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
            "category": row["category"],
            "date": _format_moment(row["local_datetime"], row["visited_at"]).strftime("%d.%m.%Y"),
            "time": _format_moment(row["local_datetime"], row["visited_at"]).strftime("%H:%M"),
            "source": row["source"],
            "url": row["source_url"],
        }
        for row in rows
    ]


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


def build_time(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM index_meta WHERE key = 'built_at'").fetchone()
    if not row:
        return None
    try:
        moment = datetime.fromisoformat(row["value"])
        return moment.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return row["value"]
