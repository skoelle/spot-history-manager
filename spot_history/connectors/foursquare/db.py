from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS checkins (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    created_at_utc TEXT NOT NULL,
    local_datetime TEXT,
    time_zone_offset INTEGER,
    type TEXT,
    visibility TEXT,
    canonical_url TEXT,
    editable_until INTEGER,
    shout TEXT,
    is_mayor INTEGER NOT NULL DEFAULT 0,
    liked INTEGER NOT NULL DEFAULT 0,
    source_name TEXT,
    source_url TEXT,
    posts_count INTEGER NOT NULL DEFAULT 0,
    posts_text_count INTEGER NOT NULL DEFAULT 0,
    likes_count INTEGER NOT NULL DEFAULT 0,
    comments_count INTEGER NOT NULL DEFAULT 0,
    comments_synced INTEGER NOT NULL DEFAULT 0,
    photos_count INTEGER NOT NULL DEFAULT 0,
    venue_id TEXT,
    venue_name TEXT,
    lat REAL,
    lng REAL,
    address TEXT,
    cross_street TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,
    country TEXT,
    country_code TEXT,
    neighborhood TEXT,
    category_id TEXT,
    category_name TEXT,
    category_short_name TEXT,
    category_code INTEGER,
    time_zone TEXT,
    raw_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkins_created_at ON checkins(created_at);
CREATE INDEX IF NOT EXISTS idx_checkins_venue_id ON checkins(venue_id);
CREATE INDEX IF NOT EXISTS idx_checkins_city ON checkins(city);
CREATE INDEX IF NOT EXISTS idx_checkins_category_code ON checkins(category_code);

CREATE TABLE IF NOT EXISTS venues (
    id TEXT PRIMARY KEY,
    name TEXT,
    lat REAL,
    lng REAL,
    address TEXT,
    cross_street TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,
    country TEXT,
    country_code TEXT,
    neighborhood TEXT,
    category_id TEXT,
    category_name TEXT,
    time_zone TEXT,
    created_at INTEGER,
    closed INTEGER,
    raw_json TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS checkin_categories (
    checkin_id TEXT NOT NULL REFERENCES checkins(id) ON DELETE CASCADE,
    category_id TEXT NOT NULL,
    name TEXT,
    short_name TEXT,
    category_code INTEGER,
    is_primary INTEGER,
    PRIMARY KEY (checkin_id, category_id)
);

CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY,
    checkin_id TEXT REFERENCES checkins(id) ON DELETE CASCADE,
    created_at INTEGER,
    width INTEGER,
    height INTEGER,
    prefix TEXT,
    suffix TEXT,
    url TEXT,
    canonical_url TEXT,
    visibility TEXT,
    user_id TEXT,
    user_display_name TEXT,
    raw_json TEXT,
    downloaded_path TEXT,
    downloaded_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_photos_checkin ON photos(checkin_id);

CREATE TABLE IF NOT EXISTS checkin_comments (
    id TEXT PRIMARY KEY,
    checkin_id TEXT REFERENCES checkins(id) ON DELETE CASCADE,
    created_at INTEGER,
    user_id TEXT,
    user_display_name TEXT,
    text TEXT,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_comments_checkin ON checkin_comments(checkin_id);

CREATE TABLE IF NOT EXISTS likes (
    checkin_id TEXT NOT NULL REFERENCES checkins(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    display_name TEXT,
    handle TEXT,
    city TEXT,
    state TEXT,
    country_code TEXT,
    is_service_user INTEGER,
    PRIMARY KEY (checkin_id, user_id)
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT,
    started_at TEXT,
    finished_at TEXT,
    pages INTEGER NOT NULL DEFAULT 0,
    fetched INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    comments_fetched INTEGER NOT NULL DEFAULT 0,
    photos_downloaded INTEGER NOT NULL DEFAULT 0,
    api_total INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS venue_history (
    venue_id TEXT PRIMARY KEY,
    been_here INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lists (
    id TEXT PRIMARY KEY,
    name TEXT,
    type TEXT,
    visibility TEXT,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS list_items (
    list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    venue_id TEXT NOT NULL,
    venue_name TEXT,
    note TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (list_id, venue_id)
);

CREATE INDEX IF NOT EXISTS idx_list_items_list ON list_items(list_id);

CREATE TABLE IF NOT EXISTS tips (
    id TEXT PRIMARY KEY,
    created_at INTEGER,
    text TEXT,
    venue_id TEXT,
    venue_name TEXT,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tips_created_at ON tips(created_at);
"""

CHECKIN_COLUMNS = [
    "id", "created_at", "created_at_utc", "local_datetime", "time_zone_offset",
    "type", "visibility", "canonical_url", "editable_until", "shout", "is_mayor",
    "liked", "source_name", "source_url", "posts_count", "posts_text_count",
    "likes_count", "comments_count", "comments_synced", "photos_count",
    "venue_id", "venue_name",
    "lat", "lng", "address", "cross_street", "city", "state", "postal_code",
    "country", "country_code", "neighborhood", "category_id", "category_name",
    "category_short_name", "category_code", "time_zone", "raw_json",
    "first_seen_at", "updated_at",
]

CHECKIN_SQL = (
    f"INSERT INTO checkins ({', '.join(CHECKIN_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in CHECKIN_COLUMNS)}) "
    "ON CONFLICT(id) DO UPDATE SET "
    + ", ".join(
        f"{column}=excluded.{column}"
        for column in CHECKIN_COLUMNS
        if column not in ("id", "first_seen_at", "comments_synced")
    )
)

VENUE_SQL = """
INSERT INTO venues (
    id, name, lat, lng, address, cross_street, city, state, postal_code,
    country, country_code, neighborhood, category_id, category_name, time_zone,
    created_at, closed, raw_json, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    name=excluded.name, lat=excluded.lat, lng=excluded.lng,
    address=excluded.address, cross_street=excluded.cross_street,
    city=excluded.city, state=excluded.state, postal_code=excluded.postal_code,
    country=excluded.country, country_code=excluded.country_code,
    neighborhood=excluded.neighborhood, category_id=excluded.category_id,
    category_name=excluded.category_name, time_zone=excluded.time_zone,
    created_at=excluded.created_at, closed=excluded.closed,
    raw_json=excluded.raw_json, updated_at=excluded.updated_at
"""

PHOTO_SQL = """
INSERT INTO photos (
    id, checkin_id, created_at, width, height, prefix, suffix, url,
    canonical_url, visibility, user_id, user_display_name, raw_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    checkin_id=excluded.checkin_id, created_at=excluded.created_at,
    width=excluded.width, height=excluded.height, prefix=excluded.prefix,
    suffix=excluded.suffix, url=excluded.url,
    canonical_url=excluded.canonical_url, visibility=excluded.visibility,
    user_id=excluded.user_id, user_display_name=excluded.user_display_name,
    raw_json=excluded.raw_json
"""

COMMENT_SQL = """
INSERT INTO checkin_comments (
    id, checkin_id, created_at, user_id, user_display_name, text, raw_json
) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    checkin_id=excluded.checkin_id, created_at=excluded.created_at,
    user_id=excluded.user_id, user_display_name=excluded.user_display_name,
    text=excluded.text, raw_json=excluded.raw_json
"""

VENUE_HISTORY_SQL = """
INSERT INTO venue_history (venue_id, been_here, raw_json, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(venue_id) DO UPDATE SET
    been_here=excluded.been_here, raw_json=excluded.raw_json,
    updated_at=excluded.updated_at
"""

LIST_SQL = """
INSERT INTO lists (id, name, type, visibility, count, updated_at, raw_json)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    name=excluded.name, type=excluded.type, visibility=excluded.visibility,
    count=excluded.count, updated_at=excluded.updated_at,
    raw_json=excluded.raw_json
"""

LIST_ITEM_SQL = """
INSERT INTO list_items (list_id, venue_id, venue_name, note, raw_json)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(list_id, venue_id) DO UPDATE SET
    venue_name=excluded.venue_name, note=excluded.note,
    raw_json=excluded.raw_json
"""

TIP_SQL = """
INSERT INTO tips (
    id, created_at, text, venue_id, venue_name, raw_json
) VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    created_at=excluded.created_at, text=excluded.text,
    venue_id=excluded.venue_id, venue_name=excluded.venue_name,
    raw_json=excluded.raw_json
"""


def iso_utc(timestamp: int | float) -> str:
    return datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat(timespec="seconds")


def iso_local(timestamp: int | float, offset_minutes: int | None) -> str:
    offset = timedelta(minutes=int(offset_minutes or 0))
    local = datetime.fromtimestamp(int(timestamp), timezone.utc) + offset
    return local.strftime("%Y-%m-%dT%H:%M:%S")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def photo_url(photo: dict[str, Any]) -> str:
    prefix = photo.get("prefix") or ""
    suffix = photo.get("suffix") or ""
    width = photo.get("width")
    height = photo.get("height")
    if width and height:
        return f"{prefix}{width}x{height}{suffix}"
    return f"{prefix}{suffix}"


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
        self.conn.commit()
        return self

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> "Database":
        return self.open()

    def __exit__(self, *_: Any) -> None:
        self.close()

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def get_int_state(self, key: str, default: int = 0) -> int:
        value = self.get_state(key)
        try:
            return int(value) if value is not None else default
        except ValueError:
            return default

    def set_state(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO sync_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        self.conn.commit()

    def max_created_at(self) -> int:
        row = self.conn.execute("SELECT MAX(created_at) AS value FROM checkins").fetchone()
        return int(row["value"] or 0)

    def count_checkins(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS value FROM checkins").fetchone()
        return int(row["value"])

    def existing_ids(self, ids: Iterable[str]) -> set[str]:
        ids = list(ids)
        if not ids:
            return set()
        result: set[str] = set()
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            placeholders = ", ".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT id FROM checkins WHERE id IN ({placeholders})", chunk
            ).fetchall()
            result.update(row["id"] for row in rows)
        return result

    def upsert_checkins(self, items: list[dict[str, Any]]) -> tuple[int, int]:
        if not items:
            return 0, 0
        now = iso_now()
        inserted = 0
        updated = 0
        with self.conn:
            for raw in items:
                checkin_id = raw["id"]
                exists = self.conn.execute(
                    "SELECT 1 FROM checkins WHERE id=?", (checkin_id,)
                ).fetchone()
                if exists:
                    updated += 1
                else:
                    inserted += 1
                self._upsert_checkin(raw, now)
        return inserted, updated

    def _upsert_checkin(self, raw: dict[str, Any], now: str) -> None:
        checkin_id = raw["id"]
        created_at = int(raw["createdAt"])
        offset = raw.get("timeZoneOffset")
        venue = raw.get("venue") or {}
        location = venue.get("location") or {}
        categories = venue.get("categories") or []
        primary = next(
            (category for category in categories if category.get("primary")),
            categories[0] if categories else {},
        )
        likes = raw.get("likes") or {}
        posts = raw.get("posts") or {}
        photos = raw.get("photos") or {}
        source = raw.get("source") or {}

        values = [
            checkin_id,
            created_at,
            iso_utc(created_at),
            iso_local(created_at, offset),
            offset,
            raw.get("type"),
            raw.get("visibility"),
            raw.get("canonicalUrl"),
            raw.get("editableUntil"),
            raw.get("shout"),
            1 if raw.get("isMayor") else 0,
            1 if raw.get("like") else 0,
            source.get("name"),
            source.get("url"),
            int(posts.get("count") or 0),
            int(posts.get("textCount") or 0),
            int(likes.get("count") or 0),
            int((raw.get("comments") or {}).get("count") or 0),
            0,
            int(photos.get("count") or 0),
            venue.get("id"),
            venue.get("name"),
            location.get("lat"),
            location.get("lng"),
            location.get("address"),
            location.get("crossStreet"),
            location.get("city"),
            location.get("state"),
            location.get("postalCode"),
            location.get("country"),
            location.get("cc"),
            location.get("neighborhood"),
            primary.get("id"),
            primary.get("name"),
            primary.get("shortName"),
            primary.get("categoryCode"),
            venue.get("timeZone"),
            json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
            now,
            now,
        ]
        self.conn.execute(CHECKIN_SQL, values)

        if venue.get("id"):
            self.conn.execute(
                VENUE_SQL,
                (
                    venue.get("id"),
                    venue.get("name"),
                    location.get("lat"),
                    location.get("lng"),
                    location.get("address"),
                    location.get("crossStreet"),
                    location.get("city"),
                    location.get("state"),
                    location.get("postalCode"),
                    location.get("country"),
                    location.get("cc"),
                    location.get("neighborhood"),
                    primary.get("id"),
                    primary.get("name"),
                    venue.get("timeZone"),
                    venue.get("createdAt"),
                    1 if venue.get("closed") else 0,
                    json.dumps(venue, ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )

        self.conn.execute("DELETE FROM checkin_categories WHERE checkin_id=?", (checkin_id,))
        for category in categories:
            self.conn.execute(
                "INSERT OR REPLACE INTO checkin_categories "
                "(checkin_id, category_id, name, short_name, category_code, is_primary) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    checkin_id,
                    category.get("id"),
                    category.get("name"),
                    category.get("shortName"),
                    category.get("categoryCode"),
                    1 if category.get("primary") else 0,
                ),
            )

        for photo in photos.get("items") or []:
            user = photo.get("user") or {}
            self.conn.execute(
                PHOTO_SQL,
                (
                    photo.get("id"),
                    checkin_id,
                    photo.get("createdAt"),
                    photo.get("width"),
                    photo.get("height"),
                    photo.get("prefix"),
                    photo.get("suffix"),
                    photo_url(photo),
                    photo.get("canonicalUrl"),
                    photo.get("visibility"),
                    user.get("id"),
                    user.get("displayName"),
                    json.dumps(photo, ensure_ascii=False, separators=(",", ":")),
                ),
            )

        self.conn.execute("DELETE FROM likes WHERE checkin_id=?", (checkin_id,))
        for group in likes.get("groups") or []:
            for user in group.get("items") or []:
                if not user.get("id"):
                    continue
                self.conn.execute(
                    "INSERT OR REPLACE INTO likes "
                    "(checkin_id, user_id, display_name, handle, city, state, "
                    "country_code, is_service_user) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        checkin_id,
                        user.get("id"),
                        user.get("displayName"),
                        user.get("handle"),
                        user.get("city"),
                        user.get("state"),
                        user.get("countryCode"),
                        1 if user.get("isServiceUser") else 0,
                    ),
                )

    def pending_comment_checkins(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM checkins WHERE comments_count > 0 AND comments_synced = 0 "
            "ORDER BY created_at"
        ).fetchall()
        return [row["id"] for row in rows]

    def mark_comments_synced(self, checkin_id: str) -> None:
        self.conn.execute(
            "UPDATE checkins SET comments_synced=1 WHERE id=?", (checkin_id,)
        )
        self.conn.commit()

    def upsert_comments(self, checkin_id: str, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        with self.conn:
            for comment in items:
                user = comment.get("user") or {}
                self.conn.execute(
                    COMMENT_SQL,
                    (
                        comment.get("id"),
                        checkin_id,
                        comment.get("createdAt"),
                        user.get("id"),
                        user.get("displayName"),
                        comment.get("text"),
                        json.dumps(comment, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
        return len(items)

    def upsert_photos(self, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        inserted = 0
        with self.conn:
            for photo in items:
                photo_id = photo.get("id")
                if not photo_id:
                    continue
                checkin = photo.get("checkin") or {}
                checkin_id = checkin.get("id")
                if checkin_id and not self.conn.execute(
                    "SELECT 1 FROM checkins WHERE id=?", (checkin_id,)
                ).fetchone():
                    checkin_id = None
                existing = self.conn.execute(
                    "SELECT checkin_id FROM photos WHERE id=?", (photo_id,)
                ).fetchone()
                if existing is None:
                    inserted += 1
                elif checkin_id is None:
                    checkin_id = existing["checkin_id"]
                user = photo.get("user") or {}
                self.conn.execute(
                    PHOTO_SQL,
                    (
                        photo_id,
                        checkin_id,
                        photo.get("createdAt"),
                        photo.get("width"),
                        photo.get("height"),
                        photo.get("prefix"),
                        photo.get("suffix"),
                        photo_url(photo),
                        photo.get("canonicalUrl"),
                        photo.get("visibility"),
                        user.get("id"),
                        user.get("displayName"),
                        json.dumps(photo, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
        return inserted

    def upsert_venue_history(self, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        now = iso_now()
        count = 0
        with self.conn:
            for item in items:
                venue = item.get("venue") or {}
                venue_id = venue.get("id") or item.get("venueId")
                if not venue_id:
                    continue
                self.conn.execute(
                    VENUE_HISTORY_SQL,
                    (
                        venue_id,
                        int(item.get("beenHere") or 0),
                        json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                        now,
                    ),
                )
                count += 1
        return count

    def upsert_lists(self, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        now = iso_now()
        count = 0
        with self.conn:
            for raw in items:
                item = raw.get("list") if isinstance(raw.get("list"), dict) else raw
                list_id = item.get("id")
                if not list_id:
                    continue
                list_items = item.get("listItems") or {}
                self.conn.execute(
                    LIST_SQL,
                    (
                        list_id,
                        item.get("name"),
                        item.get("type"),
                        item.get("visibility"),
                        int(list_items.get("count") or item.get("count") or 0),
                        now,
                        json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                count += 1
        return count

    def upsert_list_items(self, list_id: str, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        count = 0
        with self.conn:
            for item in items:
                venue = item.get("venue") or {}
                venue_id = venue.get("id") or item.get("venueId") or item.get("id")
                if not venue_id:
                    continue
                self.conn.execute(
                    LIST_ITEM_SQL,
                    (
                        list_id,
                        str(venue_id),
                        venue.get("name") or item.get("venueName"),
                        item.get("note") or item.get("text"),
                        json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                count += 1
        return count

    def upsert_tips(self, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        count = 0
        with self.conn:
            for tip in items:
                tip_id = tip.get("id")
                if not tip_id:
                    continue
                venue = tip.get("venue") or {}
                self.conn.execute(
                    TIP_SQL,
                    (
                        tip_id,
                        tip.get("createdAt"),
                        tip.get("text"),
                        venue.get("id"),
                        venue.get("name"),
                        json.dumps(tip, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                count += 1
        return count

    def pending_photos(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM photos WHERE downloaded_path IS NULL OR downloaded_path='' "
            "ORDER BY checkin_id, created_at"
        ).fetchall()

    def photo_count(self) -> tuple[int, int]:
        total = self.conn.execute("SELECT COUNT(*) AS value FROM photos").fetchone()["value"]
        done = self.conn.execute(
            "SELECT COUNT(*) AS value FROM photos WHERE downloaded_path IS NOT NULL "
            "AND downloaded_path != ''"
        ).fetchone()["value"]
        return int(total), int(done)

    def set_photo_path(self, photo_id: str, relative_path: str) -> None:
        self.conn.execute(
            "UPDATE photos SET downloaded_path=?, downloaded_at=? WHERE id=?",
            (relative_path, iso_now(), photo_id),
        )
        self.conn.commit()

    def start_run(self, mode: str) -> int:
        cursor = self.conn.execute(
            "INSERT INTO sync_runs (mode, started_at) VALUES (?, ?)", (mode, iso_now())
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        pages: int,
        fetched: int,
        inserted: int,
        updated: int,
        comments_fetched: int,
        photos_downloaded: int,
        api_total: int | None,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE sync_runs SET finished_at=?, pages=?, fetched=?, inserted=?, "
            "updated=?, comments_fetched=?, photos_downloaded=?, api_total=?, error=? "
            "WHERE id=?",
            (
                iso_now(), pages, fetched, inserted, updated, comments_fetched,
                photos_downloaded, api_total, error, run_id,
            ),
        )
        self.conn.commit()

    def checkins_raw(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT raw_json FROM checkins ORDER BY created_at"
        ).fetchall()
        return [row["raw_json"] for row in rows]

    def checkin_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM checkins ORDER BY created_at"
        ).fetchall()

    def photo_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM photos ORDER BY checkin_id, created_at"
        ).fetchall()

    def comment_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM checkin_comments ORDER BY created_at"
        ).fetchall()

    def venue_history_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM venue_history ORDER BY venue_id"
        ).fetchall()

    def list_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM lists ORDER BY name"
        ).fetchall()

    def list_item_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM list_items ORDER BY list_id, venue_name"
        ).fetchall()

    def tip_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM tips ORDER BY created_at"
        ).fetchall()

    def stats(self) -> dict[str, Any]:
        conn = self.conn
        total = conn.execute("SELECT COUNT(*) AS v FROM checkins").fetchone()["v"]
        bounds = conn.execute(
            "SELECT MIN(created_at) AS lo, MAX(created_at) AS hi FROM checkins"
        ).fetchone()
        photos_total, photos_done = self.photo_count()
        comments = conn.execute("SELECT COUNT(*) AS v FROM checkin_comments").fetchone()["v"]
        likes = conn.execute("SELECT COUNT(*) AS v FROM likes").fetchone()["v"]
        venues = conn.execute("SELECT COUNT(*) AS v FROM venues").fetchone()["v"]
        venue_history = conn.execute(
            "SELECT COUNT(*) AS v FROM venue_history"
        ).fetchone()["v"]
        lists = conn.execute("SELECT COUNT(*) AS v FROM lists").fetchone()["v"]
        list_items = conn.execute("SELECT COUNT(*) AS v FROM list_items").fetchone()["v"]
        tips = conn.execute("SELECT COUNT(*) AS v FROM tips").fetchone()["v"]
        standalone_photos = conn.execute(
            "SELECT COUNT(*) AS v FROM photos WHERE checkin_id IS NULL"
        ).fetchone()["v"]
        top_categories = conn.execute(
            "SELECT category_name AS name, COUNT(*) AS n FROM checkins "
            "WHERE category_name IS NOT NULL GROUP BY category_name ORDER BY n DESC LIMIT 10"
        ).fetchall()
        top_cities = conn.execute(
            "SELECT city AS name, COUNT(*) AS n FROM checkins "
            "WHERE city IS NOT NULL GROUP BY city ORDER BY n DESC LIMIT 10"
        ).fetchall()
        top_venues = conn.execute(
            "SELECT venue_name AS name, COUNT(*) AS n FROM checkins "
            "WHERE venue_name IS NOT NULL GROUP BY venue_name ORDER BY n DESC LIMIT 10"
        ).fetchall()
        return {
            "total": total,
            "oldest": bounds["lo"],
            "newest": bounds["hi"],
            "venues": venues,
            "photos_total": photos_total,
            "photos_downloaded": photos_done,
            "comments": comments,
            "likes": likes,
            "venue_history": venue_history,
            "lists": lists,
            "list_items": list_items,
            "tips": tips,
            "standalone_photos": standalone_photos,
            "top_categories": top_categories,
            "top_cities": top_cities,
            "top_venues": top_venues,
            "last_max_created_at": self.get_int_state("last_max_created_at"),
            "last_sync_at": self.get_state("last_sync_at"),
            "backfill_done": self.get_state("backfill_done"),
            "api_total": self.get_int_state("api_total"),
        }
