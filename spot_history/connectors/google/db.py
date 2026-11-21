# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cluster import ClusterResult, PlaceAgg
from .parser import ParsedRecord, ParsedVisit

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS clusters (
    cluster_key TEXT PRIMARY KEY,
    lat REAL,
    lng REAL,
    visit_count INTEGER NOT NULL DEFAULT 0,
    place_count INTEGER NOT NULL DEFAULT 0,
    representative_place_id TEXT,
    semantic_type TEXT,
    name TEXT,
    address TEXT,
    city TEXT,
    country_code TEXT,
    category TEXT,
    name_source TEXT,
    osm_type TEXT,
    osm_id INTEGER,
    osm_distance_m REAL,
    osm_score REAL,
    enriched_at TEXT,
    first_at INTEGER,
    last_at INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS places (
    place_id TEXT PRIMARY KEY,
    cluster_key TEXT NOT NULL,
    lat REAL,
    lng REAL,
    semantic_type TEXT,
    visit_count INTEGER NOT NULL DEFAULT 0,
    first_at INTEGER,
    last_at INTEGER,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_google_places_cluster ON places(cluster_key);

CREATE TABLE IF NOT EXISTS visits (
    id TEXT PRIMARY KEY,
    place_id TEXT NOT NULL,
    cluster_key TEXT NOT NULL,
    visited_at INTEGER NOT NULL,
    ended_at INTEGER,
    tz_offset INTEGER,
    lat REAL,
    lng REAL,
    semantic_type TEXT,
    probability REAL,
    start_iso TEXT NOT NULL,
    end_iso TEXT,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_google_visits_time ON visits(visited_at);
CREATE INDEX IF NOT EXISTS idx_google_visits_cluster ON visits(cluster_key);

CREATE TABLE IF NOT EXISTS source_records (
    record_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source_place_id TEXT,
    created_at INTEGER,
    payload_json TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_google_records_kind ON source_records(kind);

CREATE TABLE IF NOT EXISTS imported_files (
    sha256 TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL,
    format TEXT NOT NULL,
    visits INTEGER NOT NULL DEFAULT 0,
    records INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    files INTEGER NOT NULL DEFAULT 0,
    visits INTEGER NOT NULL DEFAULT 0,
    places INTEGER NOT NULL DEFAULT 0,
    clusters INTEGER NOT NULL DEFAULT 0,
    records INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS place_overrides (
    place_id TEXT PRIMARY KEY,
    name TEXT,
    address TEXT,
    city TEXT,
    country_code TEXT,
    category TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS osm_candidates (
    cluster_key TEXT NOT NULL,
    osm_type TEXT NOT NULL,
    osm_id INTEGER NOT NULL,
    osm_name TEXT,
    category TEXT,
    distance_m REAL,
    score REAL,
    reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(cluster_key, osm_type, osm_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.conn: sqlite3.Connection | None = None

    def open(self) -> Database:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        return self

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self.conn is None:
            raise RuntimeError("Datenbank ist nicht geöffnet")
        return self.conn

    def start_run(self) -> int:
        cursor = self.connection.execute(
            "INSERT INTO import_runs (started_at) VALUES (?)", (now_iso(),)
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        files: int,
        visits: int,
        places: int,
        clusters: int,
        records: int,
        ok: bool,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            "UPDATE import_runs SET finished_at=?, files=?, visits=?, places=?, "
            "clusters=?, records=?, ok=?, error=? WHERE id=?",
            (
                now_iso(),
                files,
                visits,
                places,
                clusters,
                records,
                1 if ok else 0,
                error,
                run_id,
            ),
        )
        self.connection.commit()

    def old_cluster_map(self) -> dict[str, str]:
        rows = self.connection.execute(
            "SELECT place_id, cluster_key FROM places"
        ).fetchall()
        return {row["place_id"]: row["cluster_key"] for row in rows}

    def old_clusters(self) -> dict[str, dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM clusters").fetchall()
        return {row["cluster_key"]: dict(row) for row in rows}

    def overrides(self) -> dict[str, dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM place_overrides").fetchall()
        return {row["place_id"]: dict(row) for row in rows}

    def replace_data(
        self,
        visits: list[ParsedVisit],
        places: dict[str, PlaceAgg],
        clusters: list[ClusterResult],
        records: list[ParsedRecord],
        files: list[dict[str, Any]],
    ) -> None:
        old_clusters = self.old_clusters()
        overrides = self.overrides()
        stamp = now_iso()
        place_to_cluster: dict[str, str] = {}
        cluster_rows: list[tuple[Any, ...]] = []
        for cluster in clusters:
            old = old_clusters.get(cluster.cluster_key) or {}
            override = next(
                (overrides[member] for member in cluster.members if member in overrides),
                None,
            )
            merged = self._merge_cluster(cluster, old, override, stamp)
            cluster_rows.append(merged)
            for member in cluster.members:
                place_to_cluster[member] = cluster.cluster_key

        with self.connection:
            self.connection.execute("DELETE FROM visits")
            self.connection.execute("DELETE FROM places")
            self.connection.execute("DELETE FROM clusters")
            self.connection.execute("DELETE FROM source_records")
            self.connection.executemany(
                "INSERT INTO clusters (cluster_key, lat, lng, visit_count, "
                "place_count, representative_place_id, semantic_type, name, "
                "address, city, country_code, category, name_source, osm_type, "
                "osm_id, osm_distance_m, osm_score, enriched_at, first_at, "
                "last_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                cluster_rows,
            )
            self.connection.execute(
                "DELETE FROM osm_candidates WHERE cluster_key NOT IN "
                "(SELECT cluster_key FROM clusters)"
            )
            self.connection.executemany(
                "INSERT INTO places (place_id, cluster_key, lat, lng, "
                "semantic_type, visit_count, first_at, last_at, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        place.place_id,
                        place_to_cluster.get(place.place_id),
                        place.lat,
                        place.lng,
                        place.semantic_type,
                        place.visit_count,
                        place.first_at,
                        place.last_at,
                        stamp,
                    )
                    for place in places.values()
                    if place.place_id in place_to_cluster
                ],
            )
            self.connection.executemany(
                "INSERT INTO visits (id, place_id, cluster_key, visited_at, "
                "ended_at, tz_offset, lat, lng, semantic_type, probability, "
                "start_iso, end_iso, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?)",
                [
                    (
                        visit.source_visit_id,
                        visit.place_id,
                        place_to_cluster.get(visit.place_id, ""),
                        visit.start_at,
                        visit.end_at,
                        visit.tz_offset,
                        visit.lat,
                        visit.lng,
                        visit.semantic_type,
                        visit.probability,
                        visit.start_iso,
                        visit.end_iso,
                        stamp,
                    )
                    for visit in visits
                ],
            )
            if records:
                self.connection.executemany(
                    "INSERT OR REPLACE INTO source_records (record_id, kind, "
                    "source_place_id, created_at, payload_json, imported_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            record.record_id,
                            record.kind,
                            record.source_place_id,
                            record.created_at,
                            json.dumps(record.payload, ensure_ascii=False),
                            stamp,
                        )
                        for record in records
                    ],
                )
            if files:
                self.connection.executemany(
                    "INSERT OR REPLACE INTO imported_files (sha256, filename, "
                    "size, format, visits, records, imported_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            item["sha256"],
                            item["filename"],
                            item["size"],
                            item["format"],
                            item["visits"],
                            item["records"],
                            stamp,
                        )
                        for item in files
                    ],
                )
            self.connection.execute(
                "INSERT INTO meta (key, value) VALUES ('last_import_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (stamp,),
            )

    @staticmethod
    def _merge_cluster(
        cluster: ClusterResult,
        old: dict[str, Any],
        override: dict[str, Any] | None,
        stamp: str,
    ) -> tuple[Any, ...]:
        name = cluster.name
        address = cluster.address
        city = cluster.city
        country_code = cluster.country_code
        category = cluster.category
        name_source = cluster.name_source
        osm_type = old.get("osm_type")
        osm_id = old.get("osm_id")
        osm_distance_m = old.get("osm_distance_m")
        osm_score = old.get("osm_score")
        enriched_at = old.get("enriched_at")

        if cluster.semantic_type in ("Home", "Work", "Inferred Work"):
            name = cluster.name
            name_source = "semantic"
            address = None
            city = None
        elif override is not None:
            name = override.get("name") or name
            address = override.get("address") or address
            city = override.get("city") or city
            country_code = override.get("country_code") or country_code
            category = override.get("category") or category
            name_source = "manual"
        elif cluster.name is None and old.get("name") and old.get("name_source") not in (
            None,
            "source",
        ):
            name = old.get("name")
            address = old.get("address")
            city = old.get("city")
            country_code = old.get("country_code")
            category = old.get("category")
            name_source = old.get("name_source")

        return (
            cluster.cluster_key,
            cluster.lat,
            cluster.lng,
            cluster.visit_count,
            cluster.place_count,
            cluster.representative_place_id,
            cluster.semantic_type,
            name,
            address,
            city,
            country_code,
            category,
            name_source,
            osm_type,
            osm_id,
            osm_distance_m,
            osm_score,
            enriched_at,
            cluster.first_at,
            cluster.last_at,
            stamp,
        )

    def set_override(
        self,
        place_id: str,
        *,
        name: str | None,
        address: str | None = None,
        city: str | None = None,
        country_code: str | None = None,
        category: str | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO place_overrides (place_id, name, address, city, "
                "country_code, category, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(place_id) DO UPDATE SET name=excluded.name, "
                "address=excluded.address, city=excluded.city, "
                "country_code=excluded.country_code, "
                "category=excluded.category, updated_at=excluded.updated_at",
                (place_id, name, address, city, country_code, category, now_iso()),
            )

    def delete_override(self, place_id: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM place_overrides WHERE place_id=?", (place_id,)
            )
        return cursor.rowcount > 0

    def representative_for(self, value: str) -> tuple[str, str] | None:
        row = self.connection.execute(
            "SELECT cluster_key, representative_place_id FROM clusters "
            "WHERE cluster_key=?",
            (value,),
        ).fetchone()
        if row:
            return row["cluster_key"], row["representative_place_id"]
        row = self.connection.execute(
            "SELECT cluster_key, place_id FROM places WHERE place_id=?",
            (value,),
        ).fetchone()
        if row:
            representative = self.connection.execute(
                "SELECT representative_place_id FROM clusters WHERE cluster_key=?",
                (row["cluster_key"],),
            ).fetchone()
            return row["cluster_key"], (
                representative["representative_place_id"] if representative else row["place_id"]
            )
        return None

    def clusters_for_enrichment(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT cluster_key, lat, lng, semantic_type, name, name_source, "
            "address, representative_place_id, osm_id, enriched_at FROM clusters "
            "WHERE lat IS NOT NULL AND lng IS NOT NULL "
            "AND COALESCE(semantic_type, '') NOT IN ('Home', 'Work', 'Inferred Work') "
            "ORDER BY visit_count DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def update_cluster_enrichment(
        self,
        cluster_key: str,
        *,
        name: str | None,
        address: str | None = None,
        city: str | None = None,
        country_code: str | None = None,
        category: str | None = None,
        name_source: str | None = None,
        osm_type: str | None = None,
        osm_id: int | None = None,
        distance_m: float | None = None,
        score: float | None = None,
    ) -> None:
        stamp = now_iso()
        with self.connection:
            self.connection.execute(
                "UPDATE clusters SET name=?, address=?, city=?, "
                "country_code=?, category=?, name_source=?, osm_type=?, "
                "osm_id=?, osm_distance_m=?, osm_score=?, enriched_at=?, "
                "updated_at=? WHERE cluster_key=?",
                (
                    name,
                    address,
                    city,
                    country_code,
                    category,
                    name_source,
                    osm_type,
                    osm_id,
                    distance_m,
                    score,
                    stamp,
                    stamp,
                    cluster_key,
                ),
            )

    def clear_candidates(self, cluster_key: str) -> None:
        with self.connection:
            self.connection.execute(
                "DELETE FROM osm_candidates WHERE cluster_key=?", (cluster_key,)
            )

    def add_candidate(
        self,
        cluster_key: str,
        *,
        osm_type: str,
        osm_id: int,
        osm_name: str | None,
        category: str | None,
        distance_m: float | None,
        score: float | None,
        reason: str | None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO osm_candidates (cluster_key, osm_type, osm_id, "
                "osm_name, category, distance_m, score, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(cluster_key, osm_type, osm_id) DO UPDATE SET "
                "osm_name=excluded.osm_name, category=excluded.category, "
                "distance_m=excluded.distance_m, score=excluded.score, "
                "reason=excluded.reason, created_at=excluded.created_at",
                (
                    cluster_key,
                    osm_type,
                    int(osm_id),
                    osm_name,
                    category,
                    distance_m,
                    score,
                    reason,
                    now_iso(),
                ),
            )

    def candidates(self, limit: int | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT c.cluster_key, c.osm_type, c.osm_id, c.osm_name, "
            "c.category, c.distance_m, c.score, c.reason, "
            "p.name AS cluster_name, p.address, p.semantic_type "
            "FROM osm_candidates c LEFT JOIN clusters p "
            "ON p.cluster_key = c.cluster_key "
            "WHERE p.osm_id IS NULL "
            "AND COALESCE(p.name_source, '') NOT IN "
            "('manual', 'semantic', 'source') "
            "ORDER BY c.distance_m, c.osm_name"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(row) for row in self.connection.execute(sql)]

    def stats(self) -> dict[str, Any]:
        counts = {
            table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("clusters", "places", "visits", "source_records", "imported_files")
        }
        named = self.connection.execute(
            "SELECT COUNT(*) FROM clusters WHERE name IS NOT NULL AND name != ''"
        ).fetchone()[0]
        sources = {
            row["name_source"]: row["n"]
            for row in self.connection.execute(
                "SELECT name_source, COUNT(*) AS n FROM clusters "
                "GROUP BY name_source ORDER BY n DESC"
            )
        }
        range_row = self.connection.execute(
            "SELECT MIN(visited_at) AS first, MAX(visited_at) AS last FROM visits"
        ).fetchone()
        candidates = self.connection.execute(
            "SELECT COUNT(*) FROM osm_candidates"
        ).fetchone()[0] if self._has_table("osm_candidates") else 0
        return {
            "counts": counts,
            "named": named,
            "name_sources": sources,
            "first": range_row["first"],
            "last": range_row["last"],
            "candidates": candidates,
        }

    def _has_table(self, name: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None


def open_read_only(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
