from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Paths
from ..connectors.base import BaseConnector, Visit


def iso_utc(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat(timespec="seconds")


def iso_local(timestamp: int, offset_minutes: int | None) -> str | None:
    if offset_minutes is None:
        return None
    offset = timedelta(minutes=int(offset_minutes))
    local = datetime.fromtimestamp(int(timestamp), timezone.utc) + offset
    return local.strftime("%Y-%m-%dT%H:%M:%S")


def feature_for(visit: Visit) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [visit.lng, visit.lat]},
        "properties": {
            "source": visit.source,
            "visit_id": visit.source_visit_id,
            "place": visit.place_name,
            "place_id": visit.source_place_id,
            "category": visit.category,
            "visited_at": visit.visited_at,
            "visited_at_utc": iso_utc(visit.visited_at),
            "local_datetime": iso_local(visit.visited_at, visit.tz_offset),
            "city": visit.city,
            "country_code": visit.country_code,
            "note": visit.note,
            "url": visit.source_url,
            "photos": [photo.local_path for photo in visit.photos if photo.local_path],
            "photo_count": sum(1 for photo in visit.photos if photo.local_path),
        },
    }


def write_geojson(path: Path, features: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"type": "FeatureCollection", "features": features}
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    tmp.replace(path)


SCHEMA = """
CREATE TABLE sources (
    name TEXT PRIMARY KEY,
    kind TEXT,
    indexed_at TEXT,
    visits INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_place_id TEXT NOT NULL,
    name TEXT,
    lat REAL,
    lng REAL,
    category TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,
    country_code TEXT,
    UNIQUE(source, source_place_id)
);

CREATE TABLE visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_visit_id TEXT NOT NULL,
    place_id INTEGER REFERENCES places(id),
    visited_at INTEGER NOT NULL,
    visited_at_utc TEXT NOT NULL,
    local_datetime TEXT,
    tz_offset INTEGER,
    note TEXT,
    source_url TEXT,
    extra_json TEXT,
    UNIQUE(source, source_visit_id)
);

CREATE INDEX idx_visits_visited_at ON visits(visited_at);
CREATE INDEX idx_visits_source ON visits(source);
CREATE INDEX idx_visits_place ON visits(place_id);

CREATE TABLE photos (
    source TEXT NOT NULL,
    source_photo_id TEXT NOT NULL,
    visit_id INTEGER REFERENCES visits(id),
    local_path TEXT,
    url TEXT,
    taken_at INTEGER,
    PRIMARY KEY(source, source_photo_id)
);

CREATE INDEX idx_photos_visit ON photos(visit_id);

CREATE TABLE index_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def build_index(
    connectors: dict[str, BaseConnector],
    paths: Paths,
    log=print,
) -> dict:
    paths.index_db.parent.mkdir(parents=True, exist_ok=True)
    paths.map_geojson.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.index_db.with_name(paths.index_db.name + ".tmp")
    tmp.unlink(missing_ok=True)

    conn = sqlite3_connect(tmp)
    total_visits = total_photos = feature_count = 0
    loaded_sources: list[str] = []
    source_counts: dict[str, int] = {}
    features: list[dict] = []
    try:
        conn.executescript(SCHEMA)
        for name, connector in sorted(connectors.items()):
            visits = list(connector.iter_visits())
            if not visits:
                log(f"Index: {name}: keine Daten, übersprungen")
                continue
            loaded_sources.append(name)
            for visit in visits:
                place_id = None
                if visit.source_place_id:
                    conn.execute(
                        "INSERT INTO places (source, source_place_id, name, lat, lng, "
                        "category, address, city, state, postal_code, country_code) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(source, source_place_id) DO UPDATE SET "
                        "name=excluded.name, lat=excluded.lat, lng=excluded.lng, "
                        "category=excluded.category, address=excluded.address, "
                        "city=excluded.city, state=excluded.state, "
                        "postal_code=excluded.postal_code, "
                        "country_code=excluded.country_code",
                        (
                            visit.source, visit.source_place_id, visit.place_name,
                            visit.lat, visit.lng, visit.category, visit.address,
                            visit.city, visit.state, visit.postal_code,
                            visit.country_code,
                        ),
                    )
                    row = conn.execute(
                        "SELECT id FROM places WHERE source=? AND source_place_id=?",
                        (visit.source, visit.source_place_id),
                    ).fetchone()
                    place_id = row[0]
                extra = json.dumps(visit.extra, ensure_ascii=False) if visit.extra else None
                conn.execute(
                    "INSERT INTO visits (source, source_visit_id, place_id, visited_at, "
                    "visited_at_utc, local_datetime, tz_offset, note, source_url, extra_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source, source_visit_id) DO UPDATE SET "
                    "place_id=excluded.place_id, visited_at=excluded.visited_at, "
                    "visited_at_utc=excluded.visited_at_utc, "
                    "local_datetime=excluded.local_datetime, tz_offset=excluded.tz_offset, "
                    "note=excluded.note, source_url=excluded.source_url, "
                    "extra_json=excluded.extra_json",
                    (
                        visit.source, visit.source_visit_id, place_id, visit.visited_at,
                        iso_utc(visit.visited_at),
                        iso_local(visit.visited_at, visit.tz_offset),
                        visit.tz_offset, visit.note, visit.source_url, extra,
                    ),
                )
                row = conn.execute(
                    "SELECT id FROM visits WHERE source=? AND source_visit_id=?",
                    (visit.source, visit.source_visit_id),
                ).fetchone()
                visit_id = row[0]
                for photo in visit.photos:
                    if not photo.source_photo_id:
                        continue
                    conn.execute(
                        "INSERT INTO photos (source, source_photo_id, visit_id, "
                        "local_path, url, taken_at) VALUES (?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(source, source_photo_id) DO UPDATE SET "
                        "visit_id=excluded.visit_id, local_path=excluded.local_path, "
                        "url=excluded.url, taken_at=excluded.taken_at",
                        (
                            visit.source, photo.source_photo_id, visit_id,
                            photo.local_path, photo.url, photo.taken_at,
                        ),
                    )
                    total_photos += 1
                total_visits += 1
                source_counts[visit.source] = source_counts.get(visit.source, 0) + 1
                if visit.lat is not None and visit.lng is not None:
                    features.append(feature_for(visit))
                    feature_count += 1
        indexed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.executemany(
            "INSERT INTO sources (name, kind, indexed_at, visits) VALUES (?, ?, ?, ?)",
            [
                (name, connectors[name].kind, indexed_at, source_counts.get(name, 0))
                for name in loaded_sources
            ],
        )
        conn.execute(
            "INSERT INTO index_meta (key, value) VALUES ('built_at', ?)",
            (indexed_at,),
        )
        conn.commit()
    finally:
        conn.close()

    tmp.replace(paths.index_db)
    write_geojson(paths.map_geojson, features)
    summary = {
        "sources": loaded_sources,
        "visits": total_visits,
        "photos": total_photos,
        "features": feature_count,
        "index_db": paths.index_db,
        "geojson": paths.map_geojson,
    }
    log(
        f"Index: {total_visits} Visits, {total_photos} Fotos, "
        f"{feature_count} Kartenpunkte -> {paths.index_db}"
    )
    return summary


def sqlite3_connect(path: Path):
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
