# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..connectors.base import BaseConnector
from .config import Paths
from .identity import (
    load_aliases,
    load_manual_addresses,
    load_manual_coords,
    load_manual_names,
    load_osm_assignments,
    load_osm_candidate_keys,
    load_osm_states,
    local_key,
    record_aliases,
)
from .osm_coords import load_osm_cities, load_osm_coords
from .places import (
    collect_source_places,
    load_rules,
    resolve_canonical_places,
)
from .timezones import apply_timezone_offsets


def iso_utc(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat(timespec="seconds")


def iso_local(timestamp: int, offset_minutes: int | None) -> str | None:
    if offset_minutes is None:
        return None
    offset = timedelta(minutes=int(offset_minutes))
    local = datetime.fromtimestamp(int(timestamp), timezone.utc) + offset
    return local.strftime("%Y-%m-%dT%H:%M:%S")


def effective_coords(row) -> tuple[float, float, str]:
    osm_lat = row["osm_lat"]
    osm_lng = row["osm_lng"]
    if osm_lat is not None and osm_lng is not None:
        return osm_lat, osm_lng, "osm"
    manual_lat = row["manual_lat"] if "manual_lat" in row.keys() else None  # noqa: SIM118
    manual_lng = row["manual_lng"] if "manual_lng" in row.keys() else None  # noqa: SIM118
    if manual_lat is not None and manual_lng is not None:
        return manual_lat, manual_lng, "manual"
    return row["source_lat"], row["source_lng"], "source"


def feature_for_row(row, photos: list[str], sources: list[str] | None = None) -> dict:
    lat, lng, coords_source = effective_coords(row)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lng, lat]},
        "properties": {
            "source": row["source"],
            "visit_id": row["source_visit_id"],
            "place": row["place_name"],
            "place_id": row["place_id"],
            "place_key": row["place_key"],
            "osm_type": row["osm_type"],
            "osm_id": row["osm_id"],
            "match_status": row["match_status"],
            "lat": row["source_lat"],
            "lng": row["source_lng"],
            "osm_lat": row["osm_lat"],
            "osm_lng": row["osm_lng"],
            "manual_lat": (
                row["manual_lat"] if "manual_lat" in row.keys() else None  # noqa: SIM118
            ),
            "manual_lng": (
                row["manual_lng"] if "manual_lng" in row.keys() else None  # noqa: SIM118
            ),
            "coords_source": coords_source,
            "category": row["category"],
            "category_group": row["category_group"],
            "category_group_source": row["category_group_source"],
            "visited_at": row["visited_at"],
            "visited_at_utc": iso_utc(row["visited_at"]),
            "local_datetime": row["local_datetime"],
            "city": row["city"],
            "state": row["state"],
            "country_code": row["country_code"],
            "note": row["note"],
            "url": row["source_url"],
            "has_coords": bool(row["has_coords"]),
            "coords_derived": bool(row["coords_derived"]),
            "sources": sources or [row["source"]],
            "photos": photos,
            "photo_count": len(photos),
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

CREATE TABLE source_profiles (
    source TEXT PRIMARY KEY,
    source_profile_id TEXT,
    display_name TEXT,
    home_city TEXT,
    country_code TEXT,
    photo_path TEXT,
    counts_json TEXT,
    updated_at TEXT
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

CREATE TABLE place_details (
    source TEXT NOT NULL,
    source_place_id TEXT NOT NULL,
    name TEXT,
    tel TEXT,
    website TEXT,
    email TEXT,
    social_json TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,
    country_code TEXT,
    formatted_address TEXT,
    lat REAL,
    lng REAL,
    categories_json TEXT,
    date_created TEXT,
    date_refreshed TEXT,
    placemaker_url TEXT,
    has_related INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT,
    updated_at TEXT,
    PRIMARY KEY(source, source_place_id)
);

CREATE TABLE canonical_places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key TEXT NOT NULL UNIQUE,
    name TEXT,
    lat REAL,
    lng REAL,
    category TEXT,
    category_group TEXT,
    category_group_source TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,
    country_code TEXT,
    primary_source TEXT,
    sources_json TEXT NOT NULL DEFAULT '[]',
    source_count INTEGER NOT NULL DEFAULT 0,
    osm_type TEXT,
    osm_id INTEGER,
    osm_lat REAL,
    osm_lng REAL,
    manual_lat REAL,
    manual_lng REAL,
    match_status TEXT NOT NULL DEFAULT 'local',
    place_kind TEXT NOT NULL DEFAULT 'venue',
    has_osm_candidate INTEGER NOT NULL DEFAULT 0,
    place_state TEXT NOT NULL DEFAULT 'open',
    place_state_reason TEXT
);

CREATE TABLE canonical_aliases (
    alias_key TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL
);

CREATE TABLE place_links (
    canonical_id INTEGER NOT NULL REFERENCES canonical_places(id),
    source TEXT NOT NULL,
    source_place_id TEXT NOT NULL,
    name TEXT,
    url TEXT,
    match_method TEXT,
    confidence TEXT,
    PRIMARY KEY(source, source_place_id)
);

CREATE INDEX idx_place_links_canonical ON place_links(canonical_id);
CREATE INDEX idx_place_links_source ON place_links(source);

CREATE TABLE merge_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_a TEXT NOT NULL,
    source_place_id_a TEXT NOT NULL,
    name_a TEXT,
    source_b TEXT NOT NULL,
    source_place_id_b TEXT NOT NULL,
    name_b TEXT,
    reason TEXT,
    confidence TEXT,
    distance_m REAL,
    city TEXT,
    UNIQUE(source_a, source_place_id_a, source_b, source_place_id_b)
);

CREATE TABLE visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_visit_id TEXT NOT NULL,
    place_id INTEGER REFERENCES places(id),
    canonical_place_id INTEGER REFERENCES canonical_places(id),
    visited_at INTEGER NOT NULL,
    visited_at_utc TEXT NOT NULL,
    local_datetime TEXT,
    tz_offset INTEGER,
    note TEXT,
    source_url TEXT,
    extra_json TEXT,
    has_coords INTEGER NOT NULL DEFAULT 0,
    coords_derived INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, source_visit_id)
);

CREATE INDEX idx_visits_visited_at ON visits(visited_at);
CREATE INDEX idx_visits_source ON visits(source);
CREATE INDEX idx_visits_place ON visits(place_id);
CREATE INDEX idx_visits_canonical_place ON visits(canonical_place_id);

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

CREATE TABLE source_records (
    source TEXT NOT NULL,
    record_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_place_id TEXT,
    created_at INTEGER,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(source, record_id)
);

CREATE INDEX idx_source_records_place
    ON source_records(source, source_place_id, kind);
CREATE INDEX idx_source_records_kind ON source_records(kind);

CREATE TABLE index_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


INDEX_REQUIRED_COLUMNS = {"place_state", "manual_lat", "manual_lng"}


def index_schema_ok(path: Path) -> bool:
    if not path.exists():
        return False
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute("PRAGMA table_info(canonical_places)").fetchall()
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
    columns = {row[1] for row in rows}
    return INDEX_REQUIRED_COLUMNS.issubset(columns)


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
    place_details_total = 0
    records_total = 0
    loaded_sources: list[str] = []
    source_visits: dict[str, list] = {}
    source_counts: dict[str, int] = {}
    features: list[dict] = []
    try:
        conn.executescript(SCHEMA)
        for name, connector in sorted(connectors.items()):
            iter_profile = getattr(connector, "iter_profile", None)
            if callable(iter_profile):
                try:
                    profile = iter_profile()
                except Exception as exc:
                    log(f"Index: {name}: Profil übersprungen ({exc})")
                    profile = None
                if profile:
                    conn.execute(
                        "INSERT INTO source_profiles (source, source_profile_id, "
                        "display_name, home_city, country_code, photo_path, "
                        "counts_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(source) DO UPDATE SET "
                        "source_profile_id=excluded.source_profile_id, "
                        "display_name=excluded.display_name, "
                        "home_city=excluded.home_city, "
                        "country_code=excluded.country_code, "
                        "photo_path=excluded.photo_path, "
                        "counts_json=excluded.counts_json, "
                        "updated_at=excluded.updated_at",
                        (
                            name,
                            profile.get("source_profile_id"),
                            profile.get("display_name"),
                            profile.get("home_city"),
                            profile.get("country_code"),
                            profile.get("photo_path"),
                            profile.get("counts_json")
                            or json.dumps(profile.get("counts") or {}, ensure_ascii=False),
                            profile.get("updated_at"),
                        ),
                    )
            iter_place_details = getattr(connector, "iter_place_details", None)
            if callable(iter_place_details):
                try:
                    details = list(iter_place_details())
                except Exception as exc:
                    log(f"Index: {name}: Ortsdetails übersprungen ({exc})")
                    details = []
                for detail in details:
                    source_place_id = detail.get("source_place_id")
                    if not source_place_id:
                        continue
                    conn.execute(
                        "INSERT INTO place_details (source, source_place_id, name, "
                        "tel, website, email, social_json, address, city, state, "
                        "postal_code, country_code, formatted_address, lat, lng, "
                        "categories_json, date_created, date_refreshed, "
                        "placemaker_url, has_related, fetched_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                        "?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(source, source_place_id) DO UPDATE SET "
                        "name=excluded.name, tel=excluded.tel, "
                        "website=excluded.website, email=excluded.email, "
                        "social_json=excluded.social_json, "
                        "address=excluded.address, city=excluded.city, "
                        "state=excluded.state, postal_code=excluded.postal_code, "
                        "country_code=excluded.country_code, "
                        "formatted_address=excluded.formatted_address, "
                        "lat=excluded.lat, lng=excluded.lng, "
                        "categories_json=excluded.categories_json, "
                        "date_created=excluded.date_created, "
                        "date_refreshed=excluded.date_refreshed, "
                        "placemaker_url=excluded.placemaker_url, "
                        "has_related=excluded.has_related, "
                        "fetched_at=excluded.fetched_at, "
                        "updated_at=excluded.updated_at",
                        (
                            name,
                            source_place_id,
                            detail.get("name"),
                            detail.get("tel"),
                            detail.get("website"),
                            detail.get("email"),
                            detail.get("social_json"),
                            detail.get("address"),
                            detail.get("city"),
                            detail.get("state"),
                            detail.get("postal_code"),
                            detail.get("country_code"),
                            detail.get("formatted_address"),
                            detail.get("lat"),
                            detail.get("lng"),
                            detail.get("categories_json"),
                            detail.get("date_created"),
                            detail.get("date_refreshed"),
                            detail.get("placemaker_url"),
                            detail.get("has_related") or 0,
                            detail.get("fetched_at"),
                            detail.get("updated_at"),
                        ),
                    )
                    place_details_total += 1
            iter_records = getattr(connector, "iter_records", None)
            if callable(iter_records):
                try:
                    records = list(iter_records())
                except Exception as exc:
                    log(f"Index: {name}: Detaildaten übersprungen ({exc})")
                    records = []
                for record in records:
                    record_id = record.get("record_id")
                    kind = record.get("kind")
                    if not record_id or not kind:
                        continue
                    payload = record.get("payload") or {}
                    conn.execute(
                        "INSERT INTO source_records (source, record_id, kind, "
                        "source_place_id, created_at, payload_json) "
                        "VALUES (?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(source, record_id) DO UPDATE SET "
                        "kind=excluded.kind, "
                        "source_place_id=excluded.source_place_id, "
                        "created_at=excluded.created_at, "
                        "payload_json=excluded.payload_json",
                        (
                            record.get("source") or name,
                            record_id,
                            kind,
                            record.get("source_place_id"),
                            record.get("created_at"),
                            json.dumps(payload, ensure_ascii=False),
                        ),
                    )
                    records_total += 1
            visits = list(connector.iter_visits())
            if not visits:
                log(f"Index: {name}: keine Daten, übersprungen")
                continue
            loaded_sources.append(name)
            source_visits[name] = visits

        all_visits = []
        for name in loaded_sources:
            all_visits.extend(source_visits[name])

        source_places = collect_source_places(all_visits)
        rules = load_rules(paths.place_rules_db)
        osm_assignments = load_osm_assignments(paths.place_identity_db)
        osm_coords = load_osm_coords(paths.osm_db)
        osm_cities = load_osm_cities(paths.osm_db)
        osm_candidate_keys = load_osm_candidate_keys(paths.place_identity_db)
        osm_states = load_osm_states(paths.place_identity_db)
        manual_coords = load_manual_coords(paths.place_identity_db)
        manual_addresses = load_manual_addresses(paths.place_identity_db)
        manual_names = load_manual_names(paths.place_identity_db)
        persistent_aliases = load_aliases(paths.place_identity_db)
        canonical_places, candidates = resolve_canonical_places(
            source_places.values(),
            rules,
            log=log,
            osm_assignments=osm_assignments,
            osm_cities=osm_cities,
        )

        source_place_ids: dict[tuple[str, str], int] = {}
        for place in sorted(
            source_places.values(), key=lambda item: (item.source, item.source_place_id)
        ):
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
                    place.source, place.source_place_id, place.name,
                    place.lat, place.lng, place.category, place.address,
                    place.city, place.state, place.postal_code,
                    place.country_code,
                ),
            )
            row = conn.execute(
                "SELECT id FROM places WHERE source=? AND source_place_id=?",
                (place.source, place.source_place_id),
            ).fetchone()
            source_place_ids[place.key] = row[0]

        confidence_for_method = {
            "manual": "manual",
            "osm": "high",
            "name_coords": "high",
            "name_fuzzy_coords": "high",
            "name_city": "medium",
            "name_unique": "medium",
            "single": "high",
        }
        canonical_ids: dict[tuple[str, str], int] = {}
        canonical_by_id: dict[int, object] = {}
        alias_map: dict[str, str] = {}
        link_total = 0
        state_counts = {
            state: 0
            for state in ("open", "no_osm", "closed", "no_coords", "ignored")
        }
        for canonical in canonical_places:
            lat, lng = canonical.coords
            osm_lat = osm_lng = None
            if canonical.osm_type and canonical.osm_id is not None:
                osm_coords_for_place = osm_coords.get(
                    (canonical.osm_type, int(canonical.osm_id))
                )
                if osm_coords_for_place:
                    osm_lat, osm_lng = osm_coords_for_place
            has_osm_candidate = int(
                any(member.key in osm_candidate_keys for member in canonical.members)
            )
            manual_lat = manual_lng = None
            for member in canonical.sorted_members:
                manual = manual_coords.get(member.key)
                if manual:
                    manual_lat = manual["lat"]
                    manual_lng = manual["lng"]
                    break
            name_override = canonical.name
            for member in canonical.sorted_members:
                manual_name = manual_names.get(member.key)
                if manual_name:
                    name_override = manual_name["name"]
                    break
            address_override = canonical.address
            city_override = canonical.city
            postal_override = canonical.postal_code
            for member in canonical.sorted_members:
                manual_address = manual_addresses.get(member.key)
                if manual_address:
                    address_override = manual_address["address"]
                    city_override = manual_address["city"] or city_override
                    postal_override = (
                        manual_address["postal_code"] or postal_override
                    )
                    break
            place_state = "open"
            place_state_reason = None
            for member in canonical.sorted_members:
                state_info = osm_states.get(member.key)
                if state_info:
                    place_state = state_info["state"]
                    place_state_reason = (
                        state_info.get("reason") or state_info.get("note")
                    )
                    break
            state_counts[place_state] = state_counts.get(place_state, 0) + 1
            cursor = conn.execute(
                "INSERT INTO canonical_places (canonical_key, name, lat, lng, "
                "category, category_group, category_group_source, address, "
                "city, state, postal_code, country_code, primary_source, "
                "sources_json, source_count, osm_type, osm_id, osm_lat, osm_lng, "
                "manual_lat, manual_lng, "
                "match_status, place_kind, has_osm_candidate, place_state, "
                "place_state_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical.canonical_key,
                    name_override,
                    lat,
                    lng,
                    canonical.category,
                    canonical.category_group,
                    canonical.category_group_source,
                    address_override,
                    city_override,
                    canonical.state,
                    postal_override,
                    canonical.country_code,
                    canonical.primary_source,
                    json.dumps(canonical.sources, ensure_ascii=False),
                    len(canonical.members),
                    canonical.osm_type,
                    canonical.osm_id,
                    osm_lat,
                    osm_lng,
                    manual_lat,
                    manual_lng,
                    canonical.match_status,
                    canonical.place_kind,
                    has_osm_candidate,
                    place_state,
                    place_state_reason,
                ),
            )
            for member in canonical.members:
                for alias in (
                    local_key(member.source, member.source_place_id),
                    member.ref,
                ):
                    if alias and alias != canonical.canonical_key:
                        alias_map.setdefault(alias, canonical.canonical_key)
            canonical_id = int(cursor.lastrowid)
            canonical_by_id[canonical_id] = canonical
            for member in canonical.members:
                method = canonical.methods.get(member.key, "single")
                conn.execute(
                    "INSERT INTO place_links (canonical_id, source, source_place_id, "
                    "name, url, match_method, confidence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        canonical_id,
                        member.source,
                        member.source_place_id,
                        member.name,
                        member.place_url,
                        method,
                        confidence_for_method.get(method, "medium"),
                    ),
                )
                canonical_ids[member.key] = canonical_id
                link_total += 1

        known_keys = {canonical.canonical_key for canonical in canonical_places}
        for alias, target in persistent_aliases.items():
            if alias in known_keys or target not in known_keys:
                continue
            alias_map.setdefault(alias, target)
        if alias_map:
            conn.executemany(
                "INSERT OR IGNORE INTO canonical_aliases (alias_key, canonical_key) "
                "VALUES (?, ?)",
                sorted(alias_map.items()),
            )
            record_aliases(paths.place_identity_db, alias_map)

        for candidate in candidates:
            first, second = candidate.ordered_pair
            conn.execute(
                "INSERT OR IGNORE INTO merge_candidates (source_a, source_place_id_a, "
                "name_a, source_b, source_place_id_b, name_b, reason, confidence, "
                "distance_m, city) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    first.source,
                    first.source_place_id,
                    first.name,
                    second.source,
                    second.source_place_id,
                    second.name,
                    candidate.reason,
                    candidate.confidence,
                    candidate.distance_m,
                    candidate.city,
                ),
            )

        timezone_summary = apply_timezone_offsets(
            all_visits, canonical_ids, canonical_by_id, log=log
        )

        for visit in all_visits:
            key = (visit.source, visit.source_place_id) if visit.source_place_id else None
            place_id = source_place_ids.get(key) if key else None
            canonical_id = canonical_ids.get(key) if key else None
            extra = json.dumps(visit.extra, ensure_ascii=False) if visit.extra else None
            has_coords = visit.lat is not None and visit.lng is not None
            conn.execute(
                "INSERT INTO visits (source, source_visit_id, place_id, "
                "canonical_place_id, visited_at, visited_at_utc, local_datetime, "
                "tz_offset, note, source_url, extra_json, has_coords, coords_derived) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0) "
                "ON CONFLICT(source, source_visit_id) DO UPDATE SET "
                "place_id=excluded.place_id, "
                "canonical_place_id=excluded.canonical_place_id, "
                "visited_at=excluded.visited_at, "
                "visited_at_utc=excluded.visited_at_utc, "
                "local_datetime=excluded.local_datetime, tz_offset=excluded.tz_offset, "
                "note=excluded.note, source_url=excluded.source_url, "
                "extra_json=excluded.extra_json, has_coords=excluded.has_coords, "
                "coords_derived=excluded.coords_derived",
                (
                    visit.source, visit.source_visit_id, place_id, canonical_id,
                    visit.visited_at,
                    iso_utc(visit.visited_at),
                    iso_local(visit.visited_at, visit.tz_offset),
                    visit.tz_offset, visit.note, visit.source_url, extra,
                    1 if has_coords else 0,
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

        conn.execute(
            "UPDATE visits SET coords_derived = CASE "
            "WHEN has_coords = 0 AND canonical_place_id IS NOT NULL AND EXISTS ("
            "SELECT 1 FROM canonical_places p WHERE p.id = visits.canonical_place_id "
            "AND COALESCE(p.osm_lat, p.manual_lat, p.lat) IS NOT NULL "
            "AND COALESCE(p.osm_lng, p.manual_lng, p.lng) IS NOT NULL) "
            "THEN 1 ELSE 0 END"
        )
        photo_paths: dict[int, list[str]] = {}
        for photo_row in conn.execute(
            "SELECT visit_id, local_path FROM photos "
            "WHERE local_path IS NOT NULL AND local_path != '' "
            "ORDER BY taken_at"
        ):
            photo_paths.setdefault(photo_row["visit_id"], []).append(photo_row["local_path"])
        for visit_row in conn.execute(
            "SELECT v.id, v.source, v.source_visit_id, v.visited_at, v.local_datetime, "
            "v.note, v.source_url, v.has_coords, v.coords_derived, "
            "p.id AS place_id, p.canonical_key AS place_key, p.name AS place_name, "
            "p.lat AS source_lat, p.lng AS source_lng, p.osm_lat, p.osm_lng, "
            "p.manual_lat, p.manual_lng, "
            "p.osm_type, p.osm_id, p.match_status, "
            "p.category, p.category_group, p.category_group_source, "
            "p.address, p.city, p.state, p.country_code, p.sources_json "
            "FROM visits v JOIN canonical_places p ON p.id = v.canonical_place_id "
            "WHERE COALESCE(p.osm_lat, p.manual_lat, p.lat) IS NOT NULL "
            "AND COALESCE(p.osm_lng, p.manual_lng, p.lng) IS NOT NULL "
            "ORDER BY v.visited_at"
        ):
            sources = json.loads(visit_row["sources_json"]) if visit_row["sources_json"] else None
            features.append(
                feature_for_row(
                    visit_row, photo_paths.get(visit_row["id"], []), sources
                )
            )
        feature_count = len(features)
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
        "place_details": place_details_total,
        "records": records_total,
        "places": len(source_places),
        "canonical_places": len(canonical_places),
        "place_links": link_total,
        "merge_candidates": len(candidates),
        "timezones": timezone_summary,
        "matched_osm": sum(1 for place in canonical_places if place.match_status == "osm"),
        "unmatched_local": sum(
            1 for place in canonical_places if place.match_status == "local"
        ),
        "osm_conflicts": sum(
            1 for place in canonical_places if place.match_status == "conflict"
        ),
        "address_places": sum(
            1 for place in canonical_places if place.place_kind == "address"
        ),
        "manual_addresses": len(manual_addresses),
        "manual_names": len(manual_names),
        "place_states": state_counts,
        "without_osm_suggestion": sum(
            1
            for place in canonical_places
            if place.match_status != "osm"
            and not any(
                member.key in osm_candidate_keys for member in place.members
            )
        ),
        "aliases": len(alias_map),
        "index_db": paths.index_db,
        "geojson": paths.map_geojson,
    }
    log(
        f"Index: {total_visits} Visits, {total_photos} Fotos, "
        f"{feature_count} Kartenpunkte, {place_details_total} Ortsdetails, "
        f"{records_total} Detailrecords, "
        f"{len(source_places)} Quell-Orte -> {len(canonical_places)} kanonische Orte "
        f"({link_total} Links, {len(candidates)} Merge-Vorschläge, "
        f"{summary['matched_osm']} OSM / {summary['unmatched_local']} lokal) "
        f"-> {paths.index_db}"
    )
    return summary


def sqlite3_connect(path: Path):
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
