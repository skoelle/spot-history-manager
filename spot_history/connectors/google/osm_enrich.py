# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..osm import db as osm_db
from ..osm.db import Poi, haversine_m
from ..osm.overpass import (
    NominatimClient,
    NominatimError,
    OverpassClient,
    OverpassError,
)
from . import db as google_db
from .config import GoogleConfig
from .scoring import (
    NOMINATIM_OSM_MAX_DISTANCE_M,
    PoiCandidate,
    decide,
    is_valid_poi,
    nominatim_candidate,
    poi_city,
    rank_candidates,
)

BUCKET_DEGREES = 0.01


@dataclass
class EnrichSummary:
    total: int = 0
    pending: int = 0
    skipped: int = 0
    cache_hits: int = 0
    overpass_queries: int = 0
    overpass_pois: int = 0
    nominatim_queries: int = 0
    auto_named: int = 0
    osm_from_nominatim: int = 0
    candidates: int = 0
    addresses: int = 0
    unmatched: int = 0
    errors: int = 0
    dry_run: bool = False


class PoiIndex:
    def __init__(self) -> None:
        self.buckets: dict[tuple[int, int], list[Poi]] = {}
        self.seen: set[tuple[str, int]] = set()

    def add(self, poi: Poi) -> None:
        if poi.lat is None or poi.lng is None:
            return
        if poi.key in self.seen:
            return
        self.seen.add(poi.key)
        key = (
            math.floor(poi.lat / BUCKET_DEGREES),
            math.floor(poi.lng / BUCKET_DEGREES),
        )
        self.buckets.setdefault(key, []).append(poi)

    def near(self, lat: float, lng: float, radius_m: float) -> list[tuple[Poi, float]]:
        key_lat = math.floor(lat / BUCKET_DEGREES)
        key_lng = math.floor(lng / BUCKET_DEGREES)
        result: list[tuple[Poi, float]] = []
        for offset_lat in (-1, 0, 1):
            for offset_lng in (-1, 0, 1):
                for poi in self.buckets.get(
                    (key_lat + offset_lat, key_lng + offset_lng), []
                ):
                    if poi.lat is None or poi.lng is None:
                        continue
                    distance = haversine_m(lat, lng, poi.lat, poi.lng)
                    if distance <= radius_m:
                        result.append((poi, distance))
        return result


def _load_shared_cache(path: Path, index: PoiIndex) -> int:
    if not path.exists():
        return 0
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    count = 0
    try:
        for row in conn.execute(
            "SELECT osm_type, osm_id, name, lat, lng, tags_json FROM pois "
            "WHERE lat IS NOT NULL AND lng IS NOT NULL"
        ):
            try:
                tags = json.loads(row["tags_json"]) if row["tags_json"] else {}
            except (TypeError, ValueError):
                tags = {}
            poi = Poi(
                osm_type=row["osm_type"],
                osm_id=int(row["osm_id"]),
                name=row["name"],
                lat=row["lat"],
                lng=row["lng"],
                tags=tags if isinstance(tags, dict) else {},
            )
            if is_valid_poi(poi):
                index.add(poi)
                count += 1
    finally:
        conn.close()
    return count


def _load_cache_pois(conn: sqlite3.Connection, index: PoiIndex) -> int:
    count = 0
    for row in conn.execute(
        "SELECT osm_type, osm_id, name, lat, lng, tags_json FROM pois "
        "WHERE lat IS NOT NULL AND lng IS NOT NULL"
    ):
        try:
            tags = json.loads(row["tags_json"]) if row["tags_json"] else {}
        except (TypeError, ValueError):
            tags = {}
        poi = Poi(
            osm_type=row["osm_type"],
            osm_id=int(row["osm_id"]),
            name=row["name"],
            lat=row["lat"],
            lng=row["lng"],
            tags=tags if isinstance(tags, dict) else {},
        )
        if is_valid_poi(poi):
            index.add(poi)
            count += 1
    return count


def _candidates_for(
    index: PoiIndex, cluster: dict, radius: float
) -> list[PoiCandidate]:
    lat = cluster.get("lat")
    lng = cluster.get("lng")
    if lat is None or lng is None:
        return []
    return rank_candidates(index.near(lat, lng, radius))


def _store_candidates(
    db: google_db.Database, cluster_key: str, candidates: list[PoiCandidate]
) -> None:
    db.clear_candidates(cluster_key)
    for candidate in candidates[:5]:
        db.add_candidate(
            cluster_key,
            osm_type=candidate.poi.osm_type,
            osm_id=candidate.poi.osm_id,
            osm_name=candidate.poi.name,
            category=candidate.category,
            distance_m=candidate.distance_m,
            score=candidate.score,
            reason=candidate.reason,
        )


def _apply_osm_name(
    db: google_db.Database,
    cluster: dict,
    candidate: PoiCandidate,
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    db.update_cluster_enrichment(
        cluster["cluster_key"],
        name=candidate.poi.name,
        address=candidate.poi.address,
        city=poi_city(candidate.poi) or cluster.get("city"),
        country_code=None,
        category=candidate.category,
        name_source="osm",
        osm_type=candidate.poi.osm_type,
        osm_id=candidate.poi.osm_id,
        distance_m=candidate.distance_m,
        score=candidate.score,
    )
    db.clear_candidates(cluster["cluster_key"])


def _nominatim_distance(cluster: dict, info: dict) -> float | None:
    lat = info.get("lat")
    lng = info.get("lng")
    cluster_lat = cluster.get("lat")
    cluster_lng = cluster.get("lng")
    if None in (lat, lng, cluster_lat, cluster_lng):
        return None
    try:
        return haversine_m(
            float(cluster_lat), float(cluster_lng), float(lat), float(lng)
        )
    except (TypeError, ValueError):
        return None


def _nominatim_osm_ref(
    cluster: dict, info: dict
) -> tuple[str, int, float] | None:
    osm_type = info.get("osm_type")
    osm_id = info.get("osm_id")
    if osm_type not in ("node", "way", "relation") or osm_id is None:
        return None
    try:
        clean_id = int(osm_id)
    except (TypeError, ValueError):
        return None
    if clean_id <= 0:
        return None
    distance = _nominatim_distance(cluster, info)
    if distance is None or distance > NOMINATIM_OSM_MAX_DISTANCE_M:
        return None
    return osm_type, clean_id, distance


def _apply_nominatim(
    db: google_db.Database,
    cluster: dict,
    payload: dict,
    *,
    dry_run: bool,
) -> tuple[str, dict]:
    info = nominatim_candidate(payload)
    if info["name"]:
        osm_ref = _nominatim_osm_ref(cluster, info)
        osm_type = osm_id = distance = None
        if osm_ref is not None:
            osm_type, osm_id, distance = osm_ref
            info["distance_m"] = round(distance, 1)
        info["osm_type"] = osm_type
        info["osm_id"] = osm_id
        if not dry_run:
            db.update_cluster_enrichment(
                cluster["cluster_key"],
                name=info["name"],
                address=info["address"],
                city=info["city"],
                country_code=info["country_code"],
                category=info["category"],
                name_source="nominatim",
                osm_type=osm_type,
                osm_id=osm_id,
                distance_m=distance,
            )
        return "named", info
    if info["address"]:
        if not dry_run:
            db.update_cluster_enrichment(
                cluster["cluster_key"],
                name=info["address"],
                address=info["address"],
                city=info["city"],
                country_code=info["country_code"],
                category="address",
                name_source="address",
            )
        return "address", info
    return "none", info


def _cache_key(cluster: dict) -> str:
    return f"google-around:{cluster['lat']:.4f}:{cluster['lng']:.4f}:r55"


def _reverse_key(cluster: dict) -> str:
    return f"google-reverse:{cluster['lat']:.5f}:{cluster['lng']:.5f}"


def enrich(
    config: GoogleConfig,
    *,
    provider: str = "auto",
    radius: int = 55,
    chunk: int = 8,
    limit: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    sleep: float = 2.0,
    verbose: bool = False,
    log=print,
) -> EnrichSummary:
    summary = EnrichSummary(dry_run=dry_run)
    db = google_db.Database(config.db_path).open()
    osm_conn = osm_db.open_db(config.osm_cache_path)
    try:
        clusters = db.clusters_for_enrichment()
        summary.total = len(clusters)
        pending: list[dict] = []
        for cluster in clusters:
            name_source = cluster.get("name_source")
            if name_source in ("manual", "semantic", "source"):
                summary.skipped += 1
                continue
            if (
                name_source == "address"
                and cluster.get("osm_id") is None
                and cluster.get("enriched_at")
                and not force
            ):
                summary.skipped += 1
                continue
            if (
                cluster.get("name")
                and cluster.get("osm_id") is not None
                and not force
            ):
                summary.skipped += 1
                continue
            pending.append(cluster)
        if limit is not None:
            pending = pending[: max(0, limit)]
        summary.pending = len(pending)
        if not pending:
            log("Google-OSM: keine offenen Orte.")
            return summary

        index = PoiIndex()
        shared_path = config.data_dir.parent / "osm" / "osm.db"
        shared = _load_shared_cache(shared_path, index)
        cached = _load_cache_pois(osm_conn, index)
        if verbose:
            log(
                f"Google-OSM: {len(pending)} offene Orte, "
                f"{shared} POIs aus data/osm, {cached} aus google/osm_cache"
            )

        unresolved: list[dict] = []
        for position, cluster in enumerate(pending, start=1):
            if position % 100 == 0:
                log(f"Google-OSM: Cache {position}/{len(pending)} geprüft")
            if not dry_run:
                db.clear_candidates(cluster["cluster_key"])
            candidates = _candidates_for(index, cluster, radius)
            decision, best = decide(candidates)
            semantic = cluster.get("semantic_type")
            if decision == "auto" and best is not None:
                if (
                    (cluster.get("name") and cluster.get("osm_id") is None)
                    or (semantic == "Searched Address" and best.distance_m > 15)
                ):
                    decision = "candidate"
                else:
                    _apply_osm_name(db, cluster, best, dry_run=dry_run)
                    summary.auto_named += 1
                    summary.cache_hits += 1
                    if verbose:
                        log(
                            f"Google-OSM: {cluster['cluster_key']} -> "
                            f"{best.poi.name!r} ({best.distance_m:.0f} m, Cache)"
                        )
                    continue
            if best is not None:
                if not dry_run:
                    _store_candidates(db, cluster["cluster_key"], candidates)
                summary.candidates += 1
            unresolved.append(cluster)

        if provider in ("auto", "overpass") and unresolved:
            point_map: dict[tuple[float, float], list[dict]] = {}
            for cluster in unresolved:
                if force or not osm_db.has_query(osm_conn, _cache_key(cluster)):
                    point = (round(cluster["lat"], 4), round(cluster["lng"], 4))
                    point_map.setdefault(point, []).append(cluster)
            points = sorted(point_map)
            overpass = OverpassClient(sleep=sleep, verbose=verbose)
            fetched_keys: set[tuple[float, float]] = set()
            total_batches = (len(points) + max(1, chunk) - 1) // max(1, chunk)
            for start in range(0, len(points), max(1, chunk)):
                batch_index = start // max(1, chunk) + 1
                batch = points[start : start + max(1, chunk)]
                try:
                    pois = overpass.fetch_around(batch, radius)
                except OverpassError as exc:
                    summary.errors += 1
                    log(f"Google-OSM: Overpass-Batch fehlgeschlagen ({exc})")
                    continue
                summary.overpass_queries += 1
                summary.overpass_pois += len(pois)
                if not dry_run:
                    osm_db.save_pois(osm_conn, pois)
                for poi in pois:
                    if is_valid_poi(poi):
                        index.add(poi)
                for point in batch:
                    fetched_keys.add(point)
                    if not dry_run:
                        cluster = point_map[point][0]
                        osm_db.mark_query(
                            osm_conn,
                            _cache_key(cluster),
                            "google_around",
                            radius,
                            len(pois),
                        )
                log(
                    f"Google-OSM: Overpass {batch_index}/{total_batches}, "
                    f"{len(batch)} Punkte -> {len(pois)} POIs"
                )

            remaining: list[dict] = []
            for cluster in unresolved:
                point = (round(cluster["lat"], 4), round(cluster["lng"], 4))
                candidates = _candidates_for(index, cluster, radius)
                decision, best = decide(candidates)
                semantic = cluster.get("semantic_type")
                if decision == "auto" and best is not None:
                    if (
                        (cluster.get("name") and cluster.get("osm_id") is None)
                        or (
                            semantic == "Searched Address"
                            and best.distance_m > 15
                        )
                    ):
                        decision = "candidate"
                    else:
                        _apply_osm_name(db, cluster, best, dry_run=dry_run)
                        summary.auto_named += 1
                        if verbose:
                            log(
                                f"Google-OSM: {cluster['cluster_key']} -> "
                                f"{best.poi.name!r} ({best.distance_m:.0f} m)"
                            )
                        continue
                if best is not None:
                    if not dry_run:
                        _store_candidates(db, cluster["cluster_key"], candidates)
                    summary.candidates += 1
                if point not in fetched_keys and not force and osm_db.has_query(
                    osm_conn, _cache_key(cluster)
                ):
                    summary.cache_hits += 1
                remaining.append(cluster)
            unresolved = remaining

        if provider in ("auto", "nominatim") and unresolved:
            nominatim = NominatimClient(verbose=verbose)
            for position, cluster in enumerate(unresolved, start=1):
                if position % 25 == 0:
                    log(
                        f"Google-OSM: Nominatim {position}/{len(unresolved)}"
                    )
                key = _reverse_key(cluster)
                payload = None if force else osm_db.get_nominatim_cache(osm_conn, key)
                if payload:
                    summary.cache_hits += 1
                    data = payload[0] if isinstance(payload, list) and payload else {}
                else:
                    try:
                        data = nominatim.reverse_raw(cluster["lat"], cluster["lng"])
                    except NominatimError as exc:
                        summary.errors += 1
                        log(f"Google-OSM: Nominatim übersprungen ({exc})")
                        continue
                    summary.nominatim_queries += 1
                    if not dry_run:
                        osm_db.set_nominatim_cache(osm_conn, key, [data])
                outcome, info = _apply_nominatim(
                    db, cluster, data, dry_run=dry_run
                )
                if outcome == "named":
                    summary.auto_named += 1
                    if info.get("osm_type") and info.get("osm_id") is not None:
                        summary.osm_from_nominatim += 1
                elif outcome == "address":
                    summary.addresses += 1
                else:
                    summary.unmatched += 1
                if verbose and (info.get("name") or info.get("address")):
                    log(
                        f"Google-OSM: Reverse {cluster['cluster_key']} -> "
                        f"{info.get('name') or info.get('address')!r}"
                    )
        elif unresolved:
            summary.unmatched += len(unresolved)
    finally:
        osm_conn.close()
        db.close()
    return summary
