# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...core import identity
from ...core.config import Paths
from . import db as osm_db
from .matcher import (
    IndexPlace,
    decide,
    direct_candidates,
    load_index_places,
    nearby_candidates,
    rank_matches,
)
from .overpass import (
    NominatimClient,
    NominatimError,
    OverpassClient,
    OverpassError,
    parse_nominatim_results,
)

DEFAULT_RADIUS = 120
DEFAULT_CHUNK = 40


@dataclass
class SyncSummary:
    places: int = 0
    pending: int = 0
    queries: int = 0
    cached_queries: int = 0
    auto_matched: int = 0
    candidates: int = 0
    errors: int = 0
    pois: int = 0


def _nominatim_query(place: IndexPlace) -> str:
    parts = [place.name]
    if place.city:
        parts.append(place.city)
    if place.address:
        parts.append(place.address)
    return ", ".join(parts)


def _nominatim_key(place: IndexPlace) -> str:
    return "nominatim:" + _nominatim_query(place).casefold()


def _query_key(place: IndexPlace, radius: int) -> str:
    if place.has_coords:
        return f"around:{place.lat:.4f}:{place.lng:.4f}:r{radius}"
    return _nominatim_key(place)


def sync(
    paths: Paths,
    *,
    radius: int = DEFAULT_RADIUS,
    chunk: int = DEFAULT_CHUNK,
    force: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    sleep: float = 2.0,
    verbose: bool = False,
    provider: str = "auto",
    osm_db_path: Path | None = None,
    log=print,
) -> SyncSummary:
    summary = SyncSummary()
    places = load_index_places(paths.index_db)
    summary.places = len(places)
    assignments = identity.load_osm_assignments(paths.place_identity_db)

    pending: list[IndexPlace] = []
    for place in places:
        if not place.members:
            continue
        if any(member in assignments for member in place.members):
            continue
        pending.append(place)
    if limit is not None:
        pending = pending[: max(0, limit)]
    summary.pending = len(pending)
    if not pending:
        log("OSM: keine offenen Orte zu matchen.")
        return summary

    db_path = osm_db_path or (paths.connector_dir("osm") / "osm.db")
    conn = osm_db.open_db(db_path)
    overpass = OverpassClient(sleep=sleep, verbose=verbose)
    nominatim = NominatimClient(verbose=verbose)
    run_id = None if dry_run else osm_db.start_run(conn, len(places))

    try:
        point_places: dict[tuple[float, float], list[IndexPlace]] = {}
        nominatim_places: dict[str, IndexPlace] = {}
        for place in pending:
            key = _query_key(place, radius)
            if not force and osm_db.has_query(conn, key):
                summary.cached_queries += 1
                continue
            if place.has_coords:
                point = (round(place.lat, 4), round(place.lng, 4))
                point_places.setdefault(point, []).append(place)
            else:
                nominatim_places.setdefault(_nominatim_key(place), place)

        failed_points: set[tuple[float, float]] = set()
        if provider != "nominatim":
            points = sorted(point_places)
            for start in range(0, len(points), max(1, chunk)):
                batch = points[start : start + max(1, chunk)]
                try:
                    pois = overpass.fetch_around(batch, radius)
                except OverpassError as exc:
                    failed_points.update(batch)
                    log(
                        f"OSM: Overpass-Batch fehlgeschlagen, "
                        f"{len(batch)} Orte über Nominatim ({exc})"
                    )
                    continue
                summary.queries += 1
                summary.pois += osm_db.save_pois(conn, pois)
                for point in batch:
                    for place in point_places[point]:
                        osm_db.mark_query(
                            conn,
                            _query_key(place, radius),
                            "around",
                            radius,
                            len(pois),
                        )
                log(
                    f"OSM: {len(batch)} Punkte abgefragt, {len(pois)} POIs "
                    f"(Batch {start // max(1, chunk) + 1})"
                )
        else:
            failed_points.update(point_places)

        for point in failed_points:
            for place in point_places.get(point, []):
                nominatim_places.setdefault(_nominatim_key(place), place)

        for key, place in nominatim_places.items():
            payload = None if force else osm_db.get_nominatim_cache(conn, key)
            if payload is None:
                try:
                    payload = nominatim.search_raw(_nominatim_query(place), limit=5)
                except NominatimError as exc:
                    summary.errors += 1
                    log(f"OSM: Nominatim übersprungen für {place.name!r} ({exc})")
                    continue
                summary.queries += 1
                osm_db.set_nominatim_cache(conn, key, payload)
                pois = parse_nominatim_results(payload)
                summary.pois += osm_db.save_pois(conn, pois)
                osm_db.mark_query(conn, key, "nominatim", None, len(pois))
            else:
                summary.cached_queries += 1
            if place.has_coords:
                osm_db.mark_query(
                    conn,
                    _query_key(place, radius),
                    "nominatim_fallback",
                    radius,
                    len(payload),
                )
            log(f"OSM: Nominatim {place.name!r} -> {len(payload)} Treffer")

        for place in pending:
            if place.has_coords:
                poi_rows = osm_db.pois_near(
                    conn, place.lat, place.lng, max(radius, 150)
                )
                matches = rank_matches(place, nearby_candidates(place, poi_rows))
            else:
                payload = osm_db.get_nominatim_cache(conn, _nominatim_key(place))
                if not payload:
                    continue
                matches = rank_matches(
                    place, direct_candidates(parse_nominatim_results(payload))
                )
            decision, match = decide(matches)
            if decision == "none" or match is None:
                continue
            if decision == "auto":
                summary.auto_matched += 1
                log(
                    f"OSM: {place.name!r} -> {match.poi.osm_type}/{match.poi.osm_id} "
                    f"({match.poi.name!r}, {match.score:.2f}"
                    + (f", {match.distance_m:.0f} m" if match.distance_m is not None else "")
                    + ")"
                )
                if dry_run:
                    continue
                for index, (source, source_place_id) in enumerate(place.members):
                    identity.upsert_osm_assignment(
                        paths.place_identity_db,
                        source,
                        source_place_id,
                        match.poi.osm_type,
                        match.poi.osm_id,
                        method="auto" if index == 0 else "inherited",
                        confidence="high",
                        distance_m=match.distance_m,
                        note=match.reason,
                    )
                    identity.clear_osm_candidates(
                        paths.place_identity_db, source, source_place_id
                    )
            else:
                summary.candidates += 1
                log(
                    f"OSM: Kandidat {place.name!r} ~ {match.poi.name!r} "
                    f"({match.poi.osm_type}/{match.poi.osm_id}, {match.score:.2f})"
                )
                if dry_run or not place.members:
                    continue
                source, source_place_id = place.members[0]
                identity.upsert_osm_candidate(
                    paths.place_identity_db,
                    source,
                    source_place_id,
                    match.poi.osm_type,
                    match.poi.osm_id,
                    name=place.name,
                    city=place.city,
                    osm_name=match.poi.name,
                    distance_m=match.distance_m,
                    score=match.score,
                    reason=match.reason,
                )
    finally:
        if run_id is not None:
            osm_db.finish_run(
                conn,
                run_id,
                queries=summary.queries,
                auto_matched=summary.auto_matched,
                candidates=summary.candidates,
                errors=summary.errors,
            )
        conn.close()

    return summary
