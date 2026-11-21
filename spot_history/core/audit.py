from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .naming import name_similarity
from .places import haversine_m, normalize_name

BUCKET_DEGREES = 0.005
DEFAULT_SOURCE = "google"
DEFAULT_RADIUS_M = 50.0
DEFAULT_MIN_SIMILARITY = 0.78


@dataclass(frozen=True)
class AuditPlace:
    id: int
    canonical_key: str
    name: str | None
    lat: float | None
    lng: float | None
    sources: tuple[str, ...]
    match_status: str
    osm_type: str | None = None
    osm_id: int | None = None

    @property
    def has_coords(self) -> bool:
        return self.lat is not None and self.lng is not None

    @property
    def has_osm(self) -> bool:
        return self.osm_type is not None and self.osm_id is not None

    @property
    def osm_ref(self) -> tuple[str, int] | None:
        if not self.has_osm:
            return None
        return (str(self.osm_type), int(self.osm_id))


@dataclass
class NearDuplicate:
    place: AuditPlace
    other: AuditPlace
    distance_m: float
    similarity: float
    classification: str
    reason: str

    @property
    def within_25m(self) -> bool:
        return self.distance_m <= 25.0

    @property
    def within_50m(self) -> bool:
        return self.distance_m <= 50.0

    def as_dict(self) -> dict:
        return {
            "source_place": self.place.canonical_key,
            "source_name": self.place.name,
            "source_match_status": self.place.match_status,
            "other_place": self.other.canonical_key,
            "other_name": self.other.name,
            "other_sources": list(self.other.sources),
            "other_match_status": self.other.match_status,
            "distance_m": round(self.distance_m, 1),
            "similarity": self.similarity,
            "classification": self.classification,
            "reason": self.reason,
        }


@dataclass
class AuditSummary:
    source: str
    source_only: int = 0
    within_radius: int = 0
    duplicates: int = 0
    within_25m: int = 0
    within_50m: int = 0
    by_classification: dict[str, int] = field(default_factory=dict)
    by_reason: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "source_only": self.source_only,
            "within_radius": self.within_radius,
            "duplicates": self.duplicates,
            "within_25m": self.within_25m,
            "within_50m": self.within_50m,
            "by_classification": dict(self.by_classification),
            "by_reason": dict(self.by_reason),
        }


def load_places(connection: sqlite3.Connection) -> list[AuditPlace]:
    rows = connection.execute(
        "SELECT id, canonical_key, name, lat, lng, sources_json, match_status, "
        "osm_type, osm_id FROM canonical_places"
    ).fetchall()
    places: list[AuditPlace] = []
    for row in rows:
        try:
            sources = tuple(json.loads(row["sources_json"]) or [])
        except (TypeError, ValueError):
            sources = ()
        places.append(
            AuditPlace(
                id=int(row["id"]),
                canonical_key=row["canonical_key"],
                name=row["name"],
                lat=row["lat"],
                lng=row["lng"],
                sources=sources,
                match_status=row["match_status"],
                osm_type=row["osm_type"],
                osm_id=row["osm_id"],
            )
        )
    return places


def _bucket(lat: float, lng: float) -> tuple[int, int]:
    return (
        math.floor(lat / BUCKET_DEGREES),
        math.floor(lng / BUCKET_DEGREES),
    )


def _reason(place: AuditPlace, other: AuditPlace) -> str:
    if place.has_osm and other.has_osm:
        if place.osm_ref != other.osm_ref:
            return "osm_conflict"
        return "same_osm"
    if place.has_osm or other.has_osm:
        return "local_vs_osm"
    return "both_local"


def _classification(place: AuditPlace, other: AuditPlace, similarity: float) -> str:
    source_name = normalize_name(place.name)
    other_name = normalize_name(other.name)
    if source_name and source_name == other_name:
        return "exact"
    if similarity >= 0.9:
        return "variant"
    return "weak"


def find_near_duplicates(
    places: list[AuditPlace],
    *,
    source: str = DEFAULT_SOURCE,
    radius_m: float = DEFAULT_RADIUS_M,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> list[NearDuplicate]:
    targets = [
        place
        for place in places
        if place.has_coords and place.sources == (source,)
    ]
    buckets: dict[tuple[int, int], list[AuditPlace]] = {}
    for place in places:
        if not place.has_coords or source in place.sources:
            continue
        buckets.setdefault(_bucket(place.lat, place.lng), []).append(place)

    matches: list[NearDuplicate] = []
    for place in targets:
        best: NearDuplicate | None = None
        key_lat, key_lng = _bucket(place.lat, place.lng)
        visited: set[int] = set()
        for offset_lat in (-1, 0, 1):
            for offset_lng in (-1, 0, 1):
                for other in buckets.get(
                    (key_lat + offset_lat, key_lng + offset_lng), []
                ):
                    if other.id in visited:
                        continue
                    visited.add(other.id)
                    distance = haversine_m(
                        place.lat, place.lng, other.lat, other.lng
                    )
                    if distance > radius_m:
                        continue
                    similarity = name_similarity(place.name, other.name)
                    if similarity < min_similarity:
                        continue
                    candidate = NearDuplicate(
                        place=place,
                        other=other,
                        distance_m=distance,
                        similarity=similarity,
                        classification=_classification(place, other, similarity),
                        reason=_reason(place, other),
                    )
                    if best is None or (candidate.similarity, -candidate.distance_m) > (
                        best.similarity,
                        -best.distance_m,
                    ):
                        best = candidate
        if best is not None:
            matches.append(best)

    matches.sort(key=lambda item: (-item.similarity, item.distance_m, item.place.id))
    return matches


def summarize(
    places: list[AuditPlace],
    matches: list[NearDuplicate],
    *,
    source: str = DEFAULT_SOURCE,
    radius_m: float = DEFAULT_RADIUS_M,
) -> AuditSummary:
    summary = AuditSummary(source=source)
    summary.source_only = sum(
        1 for place in places if place.sources == (source,)
    )
    summary.within_radius = sum(
        1 for place in matches if place.distance_m <= radius_m
    )
    summary.duplicates = len(matches)
    summary.within_25m = sum(1 for match in matches if match.within_25m)
    summary.within_50m = sum(1 for match in matches if match.within_50m)
    for match in matches:
        summary.by_classification[match.classification] = (
            summary.by_classification.get(match.classification, 0) + 1
        )
        summary.by_reason[match.reason] = summary.by_reason.get(match.reason, 0) + 1
    return summary


def audit_index(
    index_db: Path,
    *,
    source: str = DEFAULT_SOURCE,
    radius_m: float = DEFAULT_RADIUS_M,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> tuple[AuditSummary, list[NearDuplicate]]:
    if not index_db.exists():
        raise FileNotFoundError(f"Kein Index vorhanden: {index_db}")
    connection = sqlite3.connect(f"file:{index_db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        places = load_places(connection)
    finally:
        connection.close()
    matches = find_near_duplicates(
        places,
        source=source,
        radius_m=radius_m,
        min_similarity=min_similarity,
    )
    summary = summarize(
        places, matches, source=source, radius_m=radius_m
    )
    return summary, matches
