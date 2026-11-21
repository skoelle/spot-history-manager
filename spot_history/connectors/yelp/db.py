from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .ids import iso_utc, normalize_name

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS checkins (
    id TEXT PRIMARY KEY,
    visited_at INTEGER NOT NULL,
    visited_at_iso TEXT NOT NULL,
    tz_offset INTEGER,
    place_key TEXT NOT NULL,
    place_name TEXT NOT NULL,
    lat REAL,
    lng REAL,
    note TEXT,
    status TEXT,
    raw_row TEXT,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_yelp_checkins_visited_at ON checkins(visited_at);
CREATE INDEX IF NOT EXISTS idx_yelp_checkins_place_key ON checkins(place_key);
CREATE INDEX IF NOT EXISTS idx_yelp_checkins_name ON checkins(place_name);

CREATE TABLE IF NOT EXISTS places (
    place_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lat REAL,
    lng REAL,
    checkin_count INTEGER NOT NULL DEFAULT 0,
    first_visit_at INTEGER,
    last_visit_at INTEGER,
    city TEXT,
    state TEXT,
    country_code TEXT,
    place_url TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS place_geocodes (
    place_key TEXT PRIMARY KEY,
    lat REAL,
    lng REAL,
    city TEXT,
    state TEXT,
    country_code TEXT,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkin_comments (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    created_at_iso TEXT NOT NULL,
    tz_offset INTEGER,
    place_name TEXT NOT NULL,
    text TEXT,
    status TEXT,
    checkin_id TEXT REFERENCES checkins(id) ON DELETE SET NULL,
    link_method TEXT,
    raw_row TEXT,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_yelp_comments_checkin ON checkin_comments(checkin_id);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    created_at_iso TEXT NOT NULL,
    tz_offset INTEGER,
    place_name TEXT,
    rating REAL,
    text TEXT,
    ip TEXT,
    status TEXT,
    raw_row TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    created_at_iso TEXT NOT NULL,
    place_name TEXT,
    place_url TEXT,
    note TEXT,
    status TEXT,
    raw_row TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    created_at_iso TEXT NOT NULL,
    name TEXT,
    description TEXT,
    is_public TEXT,
    status TEXT,
    raw_row TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_items (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    created_at_iso TEXT NOT NULL,
    place_name TEXT,
    place_url TEXT,
    collection_name TEXT,
    collection_url TEXT,
    note TEXT,
    status TEXT,
    raw_row TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_locations (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    created_at_iso TEXT NOT NULL,
    name TEXT,
    location TEXT,
    is_primary TEXT,
    is_saved TEXT,
    status TEXT,
    raw_row TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS friends (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    created_at_iso TEXT NOT NULL,
    name TEXT,
    url TEXT,
    relation TEXT,
    status TEXT,
    raw_row TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reactions (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    created_at_iso TEXT NOT NULL,
    reaction_type TEXT,
    content_url TEXT,
    device_type TEXT,
    raw_row TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS searches (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    created_at_iso TEXT NOT NULL,
    search_text TEXT,
    categories TEXT,
    lat REAL,
    lng REAL,
    raw_row TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY,
    created_at INTEGER,
    created_at_iso TEXT,
    place_name TEXT,
    place_key TEXT,
    media_type TEXT NOT NULL,
    filename TEXT NOT NULL,
    local_path TEXT,
    caption TEXT,
    status TEXT,
    checkin_id TEXT REFERENCES checkins(id) ON DELETE SET NULL,
    link_method TEXT,
    raw_row TEXT,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_yelp_photos_checkin ON photos(checkin_id);
CREATE INDEX IF NOT EXISTS idx_yelp_photos_place ON photos(place_key);

CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    export_dir TEXT,
    data_dir TEXT,
    checkins INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    reviews INTEGER NOT NULL DEFAULT 0,
    bookmarks INTEGER NOT NULL DEFAULT 0,
    collections INTEGER NOT NULL DEFAULT 0,
    collection_items INTEGER NOT NULL DEFAULT 0,
    saved_locations INTEGER NOT NULL DEFAULT 0,
    friends INTEGER NOT NULL DEFAULT 0,
    reactions INTEGER NOT NULL DEFAULT 0,
    searches INTEGER NOT NULL DEFAULT 0,
    photos INTEGER NOT NULL DEFAULT 0,
    media_copied INTEGER NOT NULL DEFAULT 0,
    media_skipped INTEGER NOT NULL DEFAULT 0,
    linked_photos INTEGER NOT NULL DEFAULT 0,
    linked_comments INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 1,
    error TEXT
);

CREATE TABLE IF NOT EXISTS import_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

COLUMNS: dict[str, list[str]] = {
    "checkins": [
        "id", "visited_at", "visited_at_iso", "tz_offset", "place_key",
        "place_name", "lat", "lng", "note", "status", "raw_row", "imported_at",
    ],
    "checkin_comments": [
        "id", "created_at", "created_at_iso", "tz_offset", "place_name", "text",
        "status", "checkin_id", "link_method", "raw_row", "imported_at",
    ],
    "reviews": [
        "id", "created_at", "created_at_iso", "tz_offset", "place_name", "rating",
        "text", "ip", "status", "raw_row", "imported_at",
    ],
    "bookmarks": [
        "id", "created_at", "created_at_iso", "place_name", "place_url", "note",
        "status", "raw_row", "imported_at",
    ],
    "collections": [
        "id", "created_at", "created_at_iso", "name", "description", "is_public",
        "status", "raw_row", "imported_at",
    ],
    "collection_items": [
        "id", "created_at", "created_at_iso", "place_name", "place_url",
        "collection_name", "collection_url", "note", "status", "raw_row",
        "imported_at",
    ],
    "saved_locations": [
        "id", "created_at", "created_at_iso", "name", "location", "is_primary",
        "is_saved", "status", "raw_row", "imported_at",
    ],
    "friends": [
        "id", "created_at", "created_at_iso", "name", "url", "relation", "status",
        "raw_row", "imported_at",
    ],
    "reactions": [
        "id", "created_at", "created_at_iso", "reaction_type", "content_url",
        "device_type", "raw_row", "imported_at",
    ],
    "searches": [
        "id", "created_at", "created_at_iso", "search_text", "categories", "lat",
        "lng", "raw_row", "imported_at",
    ],
    "photos": [
        "id", "created_at", "created_at_iso", "place_name", "place_key",
        "media_type", "filename", "local_path", "caption", "status", "checkin_id",
        "link_method", "raw_row", "imported_at",
    ],
}

TABLES = [
    "checkins", "checkin_comments", "reviews", "bookmarks", "collections",
    "collection_items", "saved_locations", "friends", "reactions", "searches",
    "photos",
]


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.conn: sqlite3.Connection | None = None

    def open(self) -> "Database":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
        return self

    def _migrate(self) -> None:
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(places)")
        }
        for column in ("city", "state", "country_code", "place_url"):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE places ADD COLUMN {column} TEXT")

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> "Database":
        return self.open()

    def __exit__(self, *_: Any) -> None:
        self.close()

    def upsert(self, table: str, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        columns = COLUMNS[table]
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{column}=excluded.{column}" for column in columns if column != "id"
        )
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        with self.conn:
            self.conn.executemany(
                sql, [[row.get(column) for column in columns] for row in rows]
            )
        return len(rows)

    def _place_urls(self) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT place_name, place_url FROM bookmarks "
            "WHERE place_url IS NOT NULL AND place_url != '' "
            "UNION ALL "
            "SELECT place_name, place_url FROM collection_items "
            "WHERE place_url IS NOT NULL AND place_url != ''"
        )
        urls: dict[str, str] = {}
        for row in rows:
            key = normalize_name(row["place_name"])
            if key and key not in urls:
                urls[key] = row["place_url"]
        return urls

    def rebuild_places(self) -> None:
        urls = self._place_urls()
        rows = self.conn.execute(
            "SELECT c.place_key, MIN(c.place_name) AS name, AVG(c.lat) AS lat, "
            "AVG(c.lng) AS lng, COUNT(*) AS checkin_count, "
            "MIN(c.visited_at) AS first_visit_at, MAX(c.visited_at) AS last_visit_at, "
            "g.city, g.state, g.country_code "
            "FROM checkins c LEFT JOIN place_geocodes g "
            "ON g.place_key = c.place_key AND g.status = 'ok' "
            "GROUP BY c.place_key, g.city, g.state, g.country_code"
        ).fetchall()
        timestamp = iso_now()
        payload = [
            (
                row["place_key"],
                row["name"],
                row["lat"],
                row["lng"],
                row["checkin_count"],
                row["first_visit_at"],
                row["last_visit_at"],
                row["city"],
                row["state"],
                row["country_code"],
                urls.get(normalize_name(row["name"])),
                timestamp,
            )
            for row in rows
        ]
        with self.conn:
            self.conn.execute("DELETE FROM places")
            self.conn.executemany(
                "INSERT INTO places (place_key, name, lat, lng, checkin_count, "
                "first_visit_at, last_visit_at, city, state, country_code, "
                "place_url, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                payload,
            )

    def places_with_coords(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = (
            "SELECT place_key, name, lat, lng FROM places "
            "WHERE lat IS NOT NULL AND lng IS NOT NULL ORDER BY place_key"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql).fetchall()

    def places_without_coords(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = (
            "SELECT place_key, name, checkin_count FROM places "
            "WHERE lat IS NULL OR lng IS NULL ORDER BY place_key"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql).fetchall()

    def geocode_cache(self, place_key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM place_geocodes WHERE place_key=?", (place_key,)
        ).fetchone()

    def upsert_geocodes(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        columns = [
            "place_key", "lat", "lng", "city", "state", "country_code",
            "source", "status", "error", "fetched_at",
        ]
        sql = (
            f"INSERT INTO place_geocodes ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)}) "
            "ON CONFLICT(place_key) DO UPDATE SET "
            + ", ".join(f"{column}=excluded.{column}" for column in columns if column != "place_key")
        )
        with self.conn:
            self.conn.executemany(
                sql, [[row.get(column) for column in columns] for row in rows]
            )
        return len(rows)

    def set_meta(self, key: str, value: Any) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO import_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM import_meta WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def start_run(self, export_dir: str, data_dir: str) -> int:
        cursor = self.conn.execute(
            "INSERT INTO import_runs (started_at, export_dir, data_dir) VALUES (?, ?, ?)",
            (iso_now(), export_dir, data_dir),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, counts: dict[str, int], ok: bool = True,
                   error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE import_runs SET finished_at=?, checkins=?, comments=?, "
                "reviews=?, bookmarks=?, collections=?, collection_items=?, "
                "saved_locations=?, friends=?, reactions=?, searches=?, photos=?, "
                "media_copied=?, media_skipped=?, linked_photos=?, "
                "linked_comments=?, ok=?, error=? WHERE id=?",
                (
                    iso_now(),
                    counts.get("checkins", 0),
                    counts.get("comments", 0),
                    counts.get("reviews", 0),
                    counts.get("bookmarks", 0),
                    counts.get("collections", 0),
                    counts.get("collection_items", 0),
                    counts.get("saved_locations", 0),
                    counts.get("friends", 0),
                    counts.get("reactions", 0),
                    counts.get("searches", 0),
                    counts.get("photos", 0),
                    counts.get("media_copied", 0),
                    counts.get("media_skipped", 0),
                    counts.get("linked_photos", 0),
                    counts.get("linked_comments", 0),
                    1 if ok else 0,
                    error,
                    run_id,
                ),
            )

    def stats(self) -> dict[str, Any]:
        conn = self.conn
        counts = {
            table: conn.execute(f"SELECT COUNT(*) AS v FROM {table}").fetchone()["v"]
            for table in TABLES
        }
        counts["comments"] = counts["checkin_comments"]
        counts["places"] = conn.execute(
            "SELECT COUNT(*) AS v FROM places"
        ).fetchone()["v"]
        bounds = conn.execute(
            "SELECT MIN(visited_at) AS lo, MAX(visited_at) AS hi FROM checkins"
        ).fetchone()
        with_coords = conn.execute(
            "SELECT COUNT(*) AS v FROM checkins WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchone()["v"]
        linked_photos = conn.execute(
            "SELECT COUNT(*) AS v FROM photos WHERE checkin_id IS NOT NULL"
        ).fetchone()["v"]
        linked_comments = conn.execute(
            "SELECT COUNT(*) AS v FROM checkin_comments WHERE checkin_id IS NOT NULL"
        ).fetchone()["v"]
        last_run = conn.execute(
            "SELECT * FROM import_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        places_with_city = conn.execute(
            "SELECT COUNT(*) AS v FROM places WHERE city IS NOT NULL AND city != ''"
        ).fetchone()["v"]
        visits_with_city = conn.execute(
            "SELECT COUNT(*) AS v FROM checkins c JOIN places p "
            "ON p.place_key = c.place_key WHERE p.city IS NOT NULL AND p.city != ''"
        ).fetchone()["v"]
        geocode_sources = [
            dict(row)
            for row in conn.execute(
                "SELECT source, status, COUNT(*) AS count FROM place_geocodes "
                "GROUP BY source, status ORDER BY source, status"
            )
        ]
        cities = [
            dict(row)
            for row in conn.execute(
                "SELECT p.city AS value, COUNT(*) AS count FROM checkins c "
                "JOIN places p ON p.place_key = c.place_key "
                "WHERE p.city IS NOT NULL AND p.city != '' "
                "GROUP BY p.city ORDER BY count DESC LIMIT 10"
            )
        ]
        return {
            **counts,
            "oldest": bounds["lo"],
            "newest": bounds["hi"],
            "with_coords": with_coords,
            "places_with_city": places_with_city,
            "visits_with_city": visits_with_city,
            "geocode_sources": geocode_sources,
            "cities": cities,
            "linked_photos": linked_photos,
            "linked_comments": linked_comments,
            "last_run": dict(last_run) if last_run else None,
        }


def iso_range(stats: dict[str, Any]) -> str:
    if not stats.get("oldest"):
        return "-"
    return f"{iso_utc(stats['oldest'])} .. {iso_utc(stats['newest'])}"
