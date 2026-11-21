# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .cluster import ClusterResult, aggregate_places, consolidate, haversine_m
from .config import GoogleConfig
from .db import Database
from .parser import ParsedRecord, ParsedVisit, iter_files, load_file


@dataclass
class ImportSummary:
    files: int = 0
    visits: int = 0
    places: int = 0
    clusters: int = 0
    records: int = 0
    named: int = 0
    home_work: int = 0
    first_at: int | None = None
    last_at: int | None = None
    warnings: list[str] = field(default_factory=list)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _merge_visit(existing: ParsedVisit, new: ParsedVisit) -> None:
    if existing.lat is None and new.lat is not None:
        existing.lat = new.lat
        existing.lng = new.lng
    if existing.semantic_type is None and new.semantic_type:
        existing.semantic_type = new.semantic_type
    if existing.place_name is None and new.place_name:
        existing.place_name = new.place_name
    if existing.address is None and new.address:
        existing.address = new.address
    if existing.city is None and new.city:
        existing.city = new.city
    if existing.country_code is None and new.country_code:
        existing.country_code = new.country_code
    if existing.category is None and new.category:
        existing.category = new.category
    if existing.probability is None and new.probability is not None:
        existing.probability = new.probability
    if existing.hierarchy_level is None and new.hierarchy_level is not None:
        existing.hierarchy_level = new.hierarchy_level


def _dedupe_visits(visits: list[ParsedVisit]) -> list[ParsedVisit]:
    unique: dict[str, ParsedVisit] = {}
    for visit in visits:
        existing = unique.get(visit.source_visit_id)
        if existing is None:
            unique[visit.source_visit_id] = visit
        else:
            _merge_visit(existing, visit)
    return list(unique.values())


def _nearest_cluster(
    lat: float, lng: float, clusters: list[ClusterResult]
) -> tuple[ClusterResult | None, float]:
    best: ClusterResult | None = None
    best_distance = float("inf")
    for cluster in clusters:
        if cluster.lat is None or cluster.lng is None:
            continue
        distance = haversine_m(lat, lng, cluster.lat, cluster.lng)
        if distance < best_distance:
            best = cluster
            best_distance = distance
    return best, best_distance


def _match_records(
    records: list[ParsedRecord], clusters: list[ClusterResult]
) -> None:
    for record in records:
        if record.lat is None or record.lng is None:
            continue
        best, distance = _nearest_cluster(record.lat, record.lng, clusters)
        if best is not None and distance <= 50:
            record.source_place_id = best.cluster_key


def _apply_record_hints(
    records: list[ParsedRecord], clusters: list[ClusterResult]
) -> None:
    ordered = sorted(
        records,
        key=lambda record: (0 if record.kind == "review" else 1, record.record_id),
    )
    for record in ordered:
        if (
            not record.place_name
            or record.lat is None
            or record.lng is None
        ):
            continue
        best, distance = _nearest_cluster(record.lat, record.lng, clusters)
        if best is None or distance > 15:
            continue
        if best.semantic_type in ("Home", "Work", "Inferred Work"):
            continue
        if best.name_source in ("manual", "semantic", "source", "review"):
            continue
        best.name = record.place_name
        best.name_source = record.kind
        best.category = None
        address = record.payload.get("address")
        if address:
            best.address = address
        country_code = record.payload.get("country_code")
        if country_code:
            best.country_code = country_code


def run_import(
    config: GoogleConfig,
    input_path: str | Path | None = None,
    log=print,
) -> ImportSummary:
    config.ensure_dirs()
    source = Path(input_path) if input_path else config.inbox
    files = iter_files(source)
    summary = ImportSummary()
    if not files:
        summary.warnings.append(f"Keine JSON-Dateien gefunden: {source}")

    visits: list[ParsedVisit] = []
    records: list[ParsedRecord] = []
    file_rows: list[dict] = []
    for path in files:
        try:
            parsed = load_file(path)
        except (OSError, ValueError) as exc:
            summary.warnings.append(f"{path.name}: nicht lesbar ({exc})")
            continue
        summary.files += 1
        if parsed.format == "records":
            summary.warnings.append(
                f"{path.name}: rohe GPS-Pings (Records-Format), übersprungen"
            )
        elif parsed.format == "unknown":
            summary.warnings.append(f"{path.name}: unbekanntes Format, übersprungen")
        visits.extend(parsed.visits)
        records.extend(parsed.records)
        file_rows.append(
            {
                "sha256": _file_hash(path),
                "filename": str(path),
                "size": path.stat().st_size,
                "format": parsed.format,
                "visits": len(parsed.visits),
                "records": len(parsed.records),
            }
        )

    visits = _dedupe_visits(visits)
    record_map: dict[str, ParsedRecord] = {}
    for record in records:
        record_map.setdefault(record.record_id, record)
    records = list(record_map.values())

    places = aggregate_places(visits)
    summary.visits = len(visits)
    summary.places = len(places)
    summary.records = len(records)
    if visits:
        summary.first_at = min(visit.start_at for visit in visits)
        summary.last_at = max(
            (visit.end_at or visit.start_at) for visit in visits
        )

    db = Database(config.db_path).open()
    run_id = db.start_run()
    try:
        old_map = db.old_cluster_map()
        _, clusters = consolidate(places, old_map)
        _match_records(records, clusters)
        _apply_record_hints(records, clusters)
        summary.clusters = len(clusters)
        summary.named = sum(1 for cluster in clusters if cluster.name)
        summary.home_work = sum(
            1
            for cluster in clusters
            if cluster.semantic_type in ("Home", "Work", "Inferred Work")
        )
        db.replace_data(visits, places, clusters, records, file_rows)
        summary.named = db.connection.execute(
            "SELECT COUNT(*) FROM clusters WHERE name IS NOT NULL AND name != ''"
        ).fetchone()[0]
        summary.home_work = db.connection.execute(
            "SELECT COUNT(*) FROM clusters WHERE semantic_type IN "
            "('Home', 'Work', 'Inferred Work')"
        ).fetchone()[0]
        db.finish_run(
            run_id,
            files=summary.files,
            visits=summary.visits,
            places=summary.places,
            clusters=summary.clusters,
            records=summary.records,
            ok=True,
        )
    except Exception as exc:
        db.finish_run(
            run_id,
            files=summary.files,
            visits=summary.visits,
            places=summary.places,
            clusters=summary.clusters,
            records=summary.records,
            ok=False,
            error=str(exc),
        )
        raise
    finally:
        db.close()

    for warning in summary.warnings:
        log(f"Warnung: {warning}")
    return summary


def scan(input_path: str | Path, log=print) -> list[dict]:
    files = iter_files(input_path)
    rows: list[dict] = []
    for path in files:
        try:
            parsed = load_file(path)
        except (OSError, ValueError) as exc:
            rows.append(
                {
                    "filename": path.name,
                    "format": "error",
                    "visits": 0,
                    "records": 0,
                    "error": str(exc),
                }
            )
            continue
        rows.append(
            {
                "filename": str(path),
                "format": parsed.format,
                "visits": len(parsed.visits),
                "records": len(parsed.records),
            }
        )
    for row in rows:
        print(
            f"{row['filename']}\n"
            f"    Format: {row['format']}  "
            f"Visits: {row['visits']}  Records: {row['records']}"
            + (f"  Fehler: {row['error']}" if row.get("error") else "")
        )
    return rows
