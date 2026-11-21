from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pois (
    osm_type TEXT NOT NULL,
    osm_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    lat REAL,
    lng REAL,
    tags_json TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (osm_type, osm_id)
);

CREATE INDEX IF NOT EXISTS idx_pois_bbox ON pois(lat, lng);

CREATE TABLE IF NOT EXISTS query_cache (
    query_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    radius INTEGER,
    result_count INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nominatim_cache (
    query_key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    places INTEGER NOT NULL DEFAULT 0,
    queries INTEGER NOT NULL DEFAULT 0,
    auto_matched INTEGER NOT NULL DEFAULT 0,
    candidates INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Poi:
    osm_type: str
    osm_id: int
    name: str
    lat: float | None = None
    lng: float | None = None
    tags: dict = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, int]:
        return (self.osm_type, int(self.osm_id))

    @property
    def city(self) -> str | None:
        return self.tags.get("addr:city") or self.tags.get("addr:suburb")

    @property
    def postcode(self) -> str | None:
        return self.tags.get("addr:postcode")

    @property
    def category(self) -> str | None:
        for key in (
            "amenity",
            "shop",
            "tourism",
            "leisure",
            "craft",
            "office",
            "club",
        ):
            value = self.tags.get(key)
            if value:
                return value
        return None

    @property
    def address(self) -> str | None:
        street = self.tags.get("addr:street")
        number = self.tags.get("addr:housenumber")
        if street and number:
            return f"{street} {number}"
        return street


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = phi2 - phi1
    delta_lambda = math.radians(lng2 - lng1)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(value))


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def save_pois(conn: sqlite3.Connection, pois: list[Poi]) -> int:
    stamp = now_iso()
    rows = [
        (
            poi.osm_type,
            int(poi.osm_id),
            poi.name,
            poi.lat,
            poi.lng,
            json.dumps(poi.tags, ensure_ascii=False),
            stamp,
        )
        for poi in pois
        if poi.name
    ]
    if not rows:
        return 0
    with conn:
        conn.executemany(
            "INSERT INTO pois (osm_type, osm_id, name, lat, lng, tags_json, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(osm_type, osm_id) DO UPDATE SET "
            "name=excluded.name, lat=excluded.lat, lng=excluded.lng, "
            "tags_json=excluded.tags_json, fetched_at=excluded.fetched_at",
            rows,
        )
    return len(rows)


def _row_to_poi(row: sqlite3.Row) -> Poi:
    try:
        tags = json.loads(row["tags_json"]) if row["tags_json"] else {}
    except (TypeError, ValueError):
        tags = {}
    if not isinstance(tags, dict):
        tags = {}
    return Poi(
        osm_type=row["osm_type"],
        osm_id=int(row["osm_id"]),
        name=row["name"],
        lat=row["lat"],
        lng=row["lng"],
        tags=tags,
    )


def pois_near(
    conn: sqlite3.Connection,
    lat: float,
    lng: float,
    radius_m: float,
) -> list[tuple[Poi, float]]:
    delta_lat = radius_m / 111_320.0
    cos_lat = math.cos(math.radians(lat))
    delta_lng = radius_m / (111_320.0 * max(abs(cos_lat), 0.01))
    rows = conn.execute(
        "SELECT * FROM pois WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
        (lat - delta_lat, lat + delta_lat, lng - delta_lng, lng + delta_lng),
    ).fetchall()
    result: list[tuple[Poi, float]] = []
    for row in rows:
        poi = _row_to_poi(row)
        if poi.lat is None or poi.lng is None:
            continue
        distance = haversine_m(lat, lng, poi.lat, poi.lng)
        if distance <= radius_m:
            result.append((poi, distance))
    return result


def has_query(conn: sqlite3.Connection, query_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM query_cache WHERE query_key = ?", (query_key,)
    ).fetchone()
    return row is not None


def mark_query(
    conn: sqlite3.Connection,
    query_key: str,
    kind: str,
    radius: int | None,
    result_count: int,
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO query_cache (query_key, kind, radius, result_count, fetched_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(query_key) DO UPDATE SET kind=excluded.kind, "
            "radius=excluded.radius, result_count=excluded.result_count, "
            "fetched_at=excluded.fetched_at",
            (query_key, kind, radius, result_count, now_iso()),
        )


def get_nominatim_cache(
    conn: sqlite3.Connection, query_key: str
) -> list[dict] | None:
    row = conn.execute(
        "SELECT payload_json FROM nominatim_cache WHERE query_key = ?", (query_key,)
    ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, list) else None


def set_nominatim_cache(
    conn: sqlite3.Connection, query_key: str, payload: list[dict]
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO nominatim_cache (query_key, payload_json, fetched_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(query_key) DO UPDATE SET "
            "payload_json=excluded.payload_json, fetched_at=excluded.fetched_at",
            (query_key, json.dumps(payload, ensure_ascii=False), now_iso()),
        )


def start_run(conn: sqlite3.Connection, places: int) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO sync_runs (started_at, places) VALUES (?, ?)",
            (now_iso(), places),
        )
    return int(cursor.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    queries: int,
    auto_matched: int,
    candidates: int,
    errors: int,
) -> None:
    with conn:
        conn.execute(
            "UPDATE sync_runs SET finished_at=?, queries=?, auto_matched=?, "
            "candidates=?, errors=? WHERE id=?",
            (now_iso(), queries, auto_matched, candidates, errors, run_id),
        )


def stats(conn: sqlite3.Connection) -> dict:
    counts = {}
    for table in ("pois", "query_cache", "nominatim_cache", "sync_runs"):
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    last = conn.execute(
        "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {"counts": counts, "last_run": dict(last) if last else None}
