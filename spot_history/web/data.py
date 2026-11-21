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

FROM = "FROM visits v LEFT JOIN canonical_places p ON p.id = v.canonical_place_id"


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
    match: str | None = None


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
        clauses.append("v.canonical_place_id = ?")
        params.append(filters.place_id)
    if filters.q:
        like = f"%{filters.q}%"
        clauses.append(
            "(p.name LIKE ? OR p.category LIKE ? OR v.note LIKE ? "
            "OR p.address LIKE ? OR p.city LIKE ? OR EXISTS ("
            "SELECT 1 FROM place_links pl WHERE pl.canonical_id = p.id "
            "AND pl.name LIKE ?))"
        )
        params.extend([like, like, like, like, like, like])
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
        "place_key": row["place_key"],
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
    "p.id AS place_id, p.canonical_key AS place_key, p.name AS place_name, "
    "p.category, p.address, p.city, p.state, p.country_code, p.lat, p.lng, "
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
            "place_key": row["place_key"],
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
    "v.local_datetime, v.source_url, p.id AS place_id, "
    "p.canonical_key AS place_key, p.name AS place_name, "
    "p.category, p.address, p.city "
    "FROM photos ph JOIN visits v ON v.id = ph.visit_id "
    "LEFT JOIN canonical_places p ON p.id = v.canonical_place_id"
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
        clauses.append("v.canonical_place_id = ?")
        params.append(filters.place_id)
    if filters.q:
        like = f"%{filters.q}%"
        clauses.append(
            "(p.name LIKE ? OR p.category LIKE ? OR p.address LIKE ? OR p.city LIKE ? "
            "OR EXISTS (SELECT 1 FROM place_links pl WHERE pl.canonical_id = p.id "
            "AND pl.name LIKE ?))"
        )
        params.extend([like, like, like, like, like])
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
        "place_key": row["place_key"],
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
        f"LEFT JOIN canonical_places p ON p.id = v.canonical_place_id{where}"
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
        clauses.append(
            "EXISTS (SELECT 1 FROM place_links pl "
            "WHERE pl.canonical_id = p.id AND pl.source = ?)"
        )
        params.append(filters.source)
    if filters.match == "osm":
        clauses.append("p.match_status = 'osm'")
    elif filters.match in ("local", "conflict"):
        clauses.append("p.match_status = ?")
        params.append(filters.match)
    elif filters.match == "unmatched":
        clauses.append("p.match_status != 'osm'")
    if filters.q:
        like = f"%{filters.q}%"
        clauses.append(
            "(p.name LIKE ? OR p.category LIKE ? OR p.address LIKE ? "
            "OR p.city LIKE ? OR EXISTS (SELECT 1 FROM place_links pl "
            "WHERE pl.canonical_id = p.id AND pl.name LIKE ?))"
        )
        params.extend([like, like, like, like, like])
    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def _place_options(conn: sqlite3.Connection, column: str, limit: int | None = None) -> list[dict]:
    sql = (
        f"SELECT {column} AS value, COUNT(*) AS count FROM canonical_places p "
        f"WHERE {column} IS NOT NULL AND {column} != '' "
        f"GROUP BY {column} ORDER BY count DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(row) for row in conn.execute(sql)]


def load_venue_options(conn: sqlite3.Connection) -> dict:
    sources = [
        dict(row)
        for row in conn.execute(
            "SELECT pl.source AS value, COUNT(DISTINCT pl.canonical_id) AS count "
            "FROM place_links pl JOIN canonical_places p ON p.id = pl.canonical_id "
            "GROUP BY pl.source ORDER BY count DESC"
        )
    ]
    return {
        "cities": _place_options(conn, "p.city"),
        "categories": _place_options(conn, "p.category", limit=80),
        "sources": sources,
    }


def load_venue_match_counts(conn: sqlite3.Connection) -> dict:
    counts = {"all": 0, "osm": 0, "local": 0, "conflict": 0}
    try:
        rows = conn.execute(
            "SELECT match_status, COUNT(*) AS n FROM canonical_places "
            "GROUP BY match_status"
        ).fetchall()
    except sqlite3.OperationalError:
        return counts
    for row in rows:
        counts["all"] += int(row["n"])
        counts[row["match_status"]] = int(row["n"])
    return counts


def venue_members(conn: sqlite3.Connection, place_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT source, source_place_id, name, url FROM place_links "
        "WHERE canonical_id = ? ORDER BY source, source_place_id",
        (place_id,),
    ).fetchall()
    return [
        {
            "source": row["source"],
            "source_place_id": row["source_place_id"],
            "name": row["name"],
            "url": row["url"],
        }
        for row in rows
    ]


def count_venues(conn: sqlite3.Connection, filters: VenueFilters) -> int:
    where, params = venue_where_clause(filters)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM canonical_places p{where}", params
    ).fetchone()
    return int(row["n"] or 0)


VENUE_SELECT = (
    "SELECT p.id, p.canonical_key, p.primary_source AS source, p.name, "
    "p.category, p.address, p.city, p.state, p.postal_code, p.country_code, "
    "p.lat, p.lng, p.sources_json, p.source_count, p.osm_type, p.osm_id, "
    "p.match_status, "
    "COUNT(v.id) AS visits_count, MIN(v.visited_at) AS first_visit, "
    "MAX(v.visited_at) AS last_visit, "
    "(SELECT COUNT(*) FROM photos ph JOIN visits pv ON pv.id = ph.visit_id "
    " WHERE pv.canonical_place_id = p.id) AS photo_count "
    "FROM canonical_places p LEFT JOIN visits v ON v.canonical_place_id = p.id"
)


def _links_for_places(
    conn: sqlite3.Connection, place_ids: list[int]
) -> dict[int, list[dict]]:
    if not place_ids:
        return {}
    placeholders = ", ".join("?" for _ in place_ids)
    rows = conn.execute(
        "SELECT canonical_id, source, source_place_id, name, url, "
        "match_method, confidence FROM place_links "
        f"WHERE canonical_id IN ({placeholders}) ORDER BY canonical_id, source",
        place_ids,
    ).fetchall()
    result: dict[int, list[dict]] = {}
    for row in rows:
        result.setdefault(row["canonical_id"], []).append(
            {
                "source": row["source"],
                "source_place_id": row["source_place_id"],
                "name": row["name"],
                "url": row["url"],
                "match_method": row["match_method"],
                "confidence": row["confidence"],
            }
        )
    return result


def _parse_json(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def _venue_details(
    conn: sqlite3.Connection, source: str, source_place_id: str | None
) -> dict:
    if not source_place_id:
        return {}
    try:
        row = conn.execute(
            "SELECT * FROM place_details WHERE source = ? AND source_place_id = ?",
            (source, source_place_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return {}
    if not row:
        return {}
    social = _parse_json(row["social_json"]) or {}
    if not isinstance(social, dict):
        social = {}
    social = {key: value for key, value in social.items() if value}
    categories = _parse_json(row["categories_json"]) or []
    if not isinstance(categories, list):
        categories = []
    website = row["website"]
    if website and not website.lower().startswith(("http://", "https://")):
        website = f"https://{website}"
    tel = row["tel"]
    return {
        "tel": tel,
        "tel_link": "tel:" + tel.replace(" ", "").replace("/", "") if tel else None,
        "website": website,
        "email": row["email"],
        "social": social,
        "categories": [name for name in categories if name],
        "formatted_address": row["formatted_address"],
        "date_refreshed": row["date_refreshed"],
        "placemaker_url": row["placemaker_url"],
        "has_related": bool(row["has_related"]),
    }


def _is_empty_detail(value) -> bool:
    return value is None or value == "" or value == {} or value == []


def _details_for_links(conn: sqlite3.Connection, links: list[dict]) -> dict:
    details: dict = {}
    for link in links:
        current = _venue_details(conn, link["source"], link["source_place_id"])
        for key, value in current.items():
            replace = key not in details or (
                _is_empty_detail(details[key]) and not _is_empty_detail(value)
            )
            if replace:
                details[key] = value
    return details


def _format_venue(
    row: sqlite3.Row,
    links: list[dict] | None = None,
    details: dict | None = None,
) -> dict:
    first_visit = row["first_visit"]
    last_visit = row["last_visit"]
    source = row["source"]
    links = links or []
    sources = sorted({link["source"] for link in links})
    if not sources and source:
        sources = [source]
    source_place_id = next(
        (
            link["source_place_id"]
            for link in links
            if link["source"] == source and link["source_place_id"]
        ),
        links[0]["source_place_id"] if links else None,
    )
    foursquare_url = None
    for link in links:
        if link["source"] != "foursquare":
            continue
        if link["url"]:
            foursquare_url = link["url"]
        elif link["source_place_id"]:
            foursquare_url = f"https://foursquare.com/v/{link['source_place_id']}"
        break
    return {
        "id": row["id"],
        "key": row["canonical_key"],
        "canonical_key": row["canonical_key"],
        "osm_type": row["osm_type"],
        "osm_id": row["osm_id"],
        "match_status": row["match_status"],
        "osm_url": (
            f"https://www.openstreetmap.org/{row['osm_type']}/{row['osm_id']}"
            if row["osm_type"] and row["osm_id"] is not None
            else None
        ),
        "source": source,
        "sources": sources,
        "links": links,
        "source_place_id": source_place_id,
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
        "source_count": int(row["source_count"] or len(links)),
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
        "foursquare_url": foursquare_url,
        "details": details or {},
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
    links = _links_for_places(conn, [row["id"] for row in rows])
    return [_format_venue(row, links.get(row["id"], [])) for row in rows]


def get_venue(conn: sqlite3.Connection, place_id: int) -> dict | None:
    row = conn.execute(
        f"{VENUE_SELECT} WHERE p.id = ? GROUP BY p.id", (place_id,)
    ).fetchone()
    if not row:
        return None
    venue_links = _links_for_places(conn, [place_id]).get(place_id, [])
    details = _details_for_links(conn, venue_links)
    return _format_venue(row, venue_links, details)


def _venue_id_for_key(conn: sqlite3.Connection, key: str) -> int | None:
    current = key
    seen: set[str] = set()
    for _ in range(8):
        if current in seen:
            break
        seen.add(current)
        row = conn.execute(
            "SELECT id FROM canonical_places WHERE canonical_key = ?", (current,)
        ).fetchone()
        if row:
            return int(row["id"])
        legacy = conn.execute(
            "SELECT canonical_id FROM place_links "
            "WHERE source || ':' || source_place_id = ?",
            (current,),
        ).fetchone()
        if legacy:
            return int(legacy["canonical_id"])
        alias = conn.execute(
            "SELECT canonical_key FROM canonical_aliases WHERE alias_key = ?",
            (current,),
        ).fetchone()
        if not alias:
            break
        current = alias["canonical_key"]
    if key.isdigit():
        row = conn.execute(
            "SELECT id FROM canonical_places WHERE id = ?", (int(key),)
        ).fetchone()
        if row:
            return int(row["id"])
    return None


def get_venue_by_key(conn: sqlite3.Connection, key: str) -> dict | None:
    place_id = _venue_id_for_key(conn, key)
    if place_id is None:
        return None
    return get_venue(conn, place_id)


def build_time(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM index_meta WHERE key = 'built_at'").fetchone()
    if not row:
        return None
    try:
        moment = datetime.fromisoformat(row["value"])
        return moment.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return row["value"]
