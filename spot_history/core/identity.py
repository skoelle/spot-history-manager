# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

OSM_TYPES = ("node", "way", "relation")

IDENTITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS place_osm (
    source TEXT NOT NULL,
    source_place_id TEXT NOT NULL,
    osm_type TEXT NOT NULL CHECK (osm_type IN ('node', 'way', 'relation')),
    osm_id INTEGER NOT NULL,
    method TEXT NOT NULL DEFAULT 'auto',
    confidence TEXT,
    distance_m REAL,
    status TEXT NOT NULL DEFAULT 'active',
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, source_place_id)
);

CREATE INDEX IF NOT EXISTS idx_place_osm_object ON place_osm(osm_type, osm_id);

CREATE TABLE IF NOT EXISTS osm_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_place_id TEXT NOT NULL,
    name TEXT,
    city TEXT,
    osm_type TEXT NOT NULL,
    osm_id INTEGER NOT NULL,
    osm_name TEXT,
    distance_m REAL,
    score REAL,
    reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(source, source_place_id, osm_type, osm_id)
);

CREATE TABLE IF NOT EXISTS canonical_alias (
    alias_key TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def osm_key(osm_type: str, osm_id: int | str) -> str:
    return f"osm-{osm_type}-{int(osm_id)}"


_SAFE_KEY = re.compile(r"^[A-Za-z0-9._~-]+$")
_UNSAFE_KEY = re.compile(r"[^A-Za-z0-9._~-]+")


def safe_key_component(value: str) -> str:
    text = str(value or "")
    if _SAFE_KEY.match(text):
        return text
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    cleaned = _UNSAFE_KEY.sub("-", text).strip("-")
    return f"{cleaned or 'x'}-{digest}"


def local_key(source: str, source_place_id: str) -> str:
    return f"local-{safe_key_component(source)}-{safe_key_component(source_place_id)}"


@dataclass(frozen=True)
class OsmAssignment:
    osm_type: str
    osm_id: int
    method: str = "auto"
    confidence: str | None = None
    distance_m: float | None = None
    status: str = "active"

    @property
    def key(self) -> str:
        return osm_key(self.osm_type, self.osm_id)


def validate_osm(osm_type: str, osm_id: int | str) -> tuple[str, int]:
    clean_type = (osm_type or "").strip().lower()
    if clean_type not in OSM_TYPES:
        raise ValueError(
            f"Ungültiger OSM-Typ: {osm_type!r} (erwartet node, way oder relation)"
        )
    try:
        clean_id = int(str(osm_id).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Ungültige OSM-ID: {osm_id!r}") from exc
    if clean_id <= 0:
        raise ValueError(f"Ungültige OSM-ID: {osm_id!r}")
    return clean_type, clean_id


def parse_osm_ref(value: str) -> tuple[str, int]:
    """Parse 'node/123', 'way-123', 'osm-way-123' or an openstreetmap.org URL."""
    text = (value or "").strip()
    if not text:
        raise ValueError("Leere OSM-Referenz")
    if "openstreetmap.org/" in text:
        text = text.split("openstreetmap.org/", 1)[1]
        text = text.split("?", 1)[0].split("#", 1)[0]
    text = text.removeprefix("osm-")
    for separator in ("/", "-", ":"):
        if separator in text:
            osm_type, _, raw_id = text.partition(separator)
            return validate_osm(osm_type, raw_id)
    raise ValueError(
        f"Ungültige OSM-Referenz: {value!r} (erwartet node/123, way-123 oder URL)"
    )


def open_identity_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(IDENTITY_SCHEMA)
    conn.commit()
    return conn


def _read_connection(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_osm_assignments(
    path: Path, include_rejected: bool = False
) -> dict[tuple[str, str], OsmAssignment]:
    conn = _read_connection(path)
    if conn is None:
        return {}
    try:
        try:
            rows = conn.execute(
                "SELECT source, source_place_id, osm_type, osm_id, method, "
                "confidence, distance_m, status FROM place_osm"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
    finally:
        conn.close()
    assignments: dict[tuple[str, str], OsmAssignment] = {}
    for row in rows:
        if not include_rejected and row["status"] == "rejected":
            continue
        assignments[(row["source"], row["source_place_id"])] = OsmAssignment(
            osm_type=row["osm_type"],
            osm_id=int(row["osm_id"]),
            method=row["method"],
            confidence=row["confidence"],
            distance_m=row["distance_m"],
            status=row["status"],
        )
    return assignments


def load_aliases(path: Path) -> dict[str, str]:
    conn = _read_connection(path)
    if conn is None:
        return {}
    try:
        try:
            rows = conn.execute(
                "SELECT alias_key, canonical_key FROM canonical_alias"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
    finally:
        conn.close()
    return {row["alias_key"]: row["canonical_key"] for row in rows}


def upsert_osm_assignment(
    path: Path,
    source: str,
    source_place_id: str,
    osm_type: str,
    osm_id: int | str,
    method: str = "manual",
    confidence: str | None = "manual",
    distance_m: float | None = None,
    note: str | None = None,
) -> OsmAssignment:
    clean_type, clean_id = validate_osm(osm_type, osm_id)
    stamp = now_iso()
    conn = open_identity_db(path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO place_osm (source, source_place_id, osm_type, osm_id, "
                "method, confidence, distance_m, status, note, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?) "
                "ON CONFLICT(source, source_place_id) DO UPDATE SET "
                "osm_type=excluded.osm_type, osm_id=excluded.osm_id, "
                "method=excluded.method, confidence=excluded.confidence, "
                "distance_m=excluded.distance_m, status='active', "
                "note=excluded.note, updated_at=excluded.updated_at",
                (
                    source,
                    source_place_id,
                    clean_type,
                    clean_id,
                    method,
                    confidence,
                    distance_m,
                    note,
                    stamp,
                    stamp,
                ),
            )
    finally:
        conn.close()
    return OsmAssignment(
        osm_type=clean_type,
        osm_id=clean_id,
        method=method,
        confidence=confidence,
        distance_m=distance_m,
    )


def delete_osm_assignment(path: Path, source: str, source_place_id: str) -> bool:
    if not path.exists():
        return False
    conn = open_identity_db(path)
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM place_osm WHERE source=? AND source_place_id=?",
                (source, source_place_id),
            )
        return cursor.rowcount > 0
    finally:
        conn.close()


def upsert_osm_candidate(
    path: Path,
    source: str,
    source_place_id: str,
    osm_type: str,
    osm_id: int | str,
    *,
    name: str | None = None,
    city: str | None = None,
    osm_name: str | None = None,
    distance_m: float | None = None,
    score: float | None = None,
    reason: str | None = None,
) -> None:
    clean_type, clean_id = validate_osm(osm_type, osm_id)
    stamp = now_iso()
    conn = open_identity_db(path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO osm_candidates (source, source_place_id, name, city, "
                "osm_type, osm_id, osm_name, distance_m, score, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, source_place_id, osm_type, osm_id) DO UPDATE SET "
                "name=excluded.name, city=excluded.city, osm_name=excluded.osm_name, "
                "distance_m=excluded.distance_m, score=excluded.score, "
                "reason=excluded.reason, created_at=excluded.created_at",
                (
                    source,
                    source_place_id,
                    name,
                    city,
                    clean_type,
                    clean_id,
                    osm_name,
                    distance_m,
                    score,
                    reason,
                    stamp,
                ),
            )
    finally:
        conn.close()


def clear_osm_candidates(path: Path, source: str, source_place_id: str) -> int:
    if not path.exists():
        return 0
    conn = open_identity_db(path)
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM osm_candidates WHERE source=? AND source_place_id=?",
                (source, source_place_id),
            )
        return cursor.rowcount
    finally:
        conn.close()


def load_osm_candidates(path: Path, limit: int | None = None) -> list[dict]:
    conn = _read_connection(path)
    if conn is None:
        return []
    try:
        try:
            sql = (
                "SELECT source, source_place_id, name, city, osm_type, osm_id, "
                "osm_name, distance_m, score, reason, created_at "
                "FROM osm_candidates ORDER BY score DESC, name"
            )
            if limit:
                sql += f" LIMIT {int(limit)}"
            rows = conn.execute(sql).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    return [dict(row) for row in rows]


def record_aliases(path: Path, aliases: dict[str, str]) -> None:
    pairs = [
        (alias, target)
        for alias, target in aliases.items()
        if alias and target and alias != target
    ]
    if not pairs:
        return
    stamp = now_iso()
    conn = open_identity_db(path)
    try:
        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO canonical_alias "
                "(alias_key, canonical_key, created_at) VALUES (?, ?, ?)",
                [(alias, target, stamp) for alias, target in pairs],
            )
    finally:
        conn.close()
