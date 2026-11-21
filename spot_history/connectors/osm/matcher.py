from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ...core.places import normalize_name
from .db import Poi, haversine_m

AUTO_SCORE = 0.80
CANDIDATE_SCORE = 0.55
AMBIGUOUS_MARGIN = 0.05


@dataclass
class IndexPlace:
    canonical_id: int
    key: str
    name: str
    lat: float | None = None
    lng: float | None = None
    city: str | None = None
    postcode: str | None = None
    address: str | None = None
    category: str | None = None
    members: list[tuple[str, str]] = field(default_factory=list)

    @property
    def has_coords(self) -> bool:
        return self.lat is not None and self.lng is not None


@dataclass
class Match:
    poi: Poi
    score: float
    distance_m: float | None
    name_score: float
    reason: str


def load_index_places(index_db: Path) -> list[IndexPlace]:
    if not index_db.exists():
        raise FileNotFoundError(f"Kein Index vorhanden: {index_db}")
    conn = sqlite3.connect(f"file:{index_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, canonical_key, name, lat, lng, city, postal_code, "
            "address, category FROM canonical_places "
            "WHERE name IS NOT NULL AND name != ''"
        ).fetchall()
        links = conn.execute(
            "SELECT canonical_id, source, source_place_id FROM place_links "
            "ORDER BY canonical_id, source, source_place_id"
        ).fetchall()
    finally:
        conn.close()
    members: dict[int, list[tuple[str, str]]] = {}
    for link in links:
        members.setdefault(int(link["canonical_id"]), []).append(
            (link["source"], link["source_place_id"])
        )
    return [
        IndexPlace(
            canonical_id=int(row["id"]),
            key=row["canonical_key"],
            name=row["name"],
            lat=row["lat"],
            lng=row["lng"],
            city=row["city"],
            postcode=row["postal_code"],
            address=row["address"],
            category=row["category"],
            members=members.get(int(row["id"]), []),
        )
        for row in rows
    ]


def _tokens(value: str | None) -> set[str]:
    normalized = normalize_name(value)
    return {token for token in normalized.split() if len(token) > 1}


def name_similarity(left: str | None, right: str | None) -> float:
    a = normalize_name(left)
    b = normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(min(a, b, key=len)) >= 6 and (a in b or b in a):
        return 0.62
    tokens_a, tokens_b = _tokens(left), _tokens(right)
    if not tokens_a or not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return len(tokens_a & tokens_b) / len(union)


def score_poi(
    place: IndexPlace,
    poi: Poi,
    distance_m: float | None,
) -> Match | None:
    similarity = name_similarity(place.name, poi.name)
    if similarity <= 0.0:
        return None

    name_score = 0.6 if similarity >= 0.99 else 0.5 * similarity

    if distance_m is not None:
        if distance_m <= 25:
            location_score = 0.3
        elif distance_m <= 60:
            location_score = 0.22
        elif distance_m <= 120:
            location_score = 0.1
        elif distance_m <= 150:
            location_score = 0.03
        else:
            return None
        if normalize_name(place.city) and normalize_name(place.city) == normalize_name(poi.city):
            location_score += 0.05
    else:
        location_score = 0.1
        if normalize_name(place.city) and normalize_name(place.city) == normalize_name(poi.city):
            location_score = 0.25
        if place.postcode and place.postcode == poi.postcode:
            location_score += 0.1

    if place.postcode and place.postcode == poi.postcode:
        location_score += 0.05

    score = min(1.0, name_score + location_score)
    reason = (
        "name_distance"
        if distance_m is not None
        else "name_city"
    )
    return Match(
        poi=poi,
        score=round(score, 3),
        distance_m=round(distance_m, 1) if distance_m is not None else None,
        name_score=round(name_score, 3),
        reason=reason,
    )


def rank_matches(
    place: IndexPlace,
    candidates: list[tuple[Poi, float | None]],
) -> list[Match]:
    matches = []
    for poi, distance in candidates:
        match = score_poi(place, poi, distance)
        if match is not None:
            matches.append(match)
    matches.sort(
        key=lambda item: (
            -item.score,
            item.distance_m if item.distance_m is not None else 99999,
        )
    )
    return matches


def decide(matches: list[Match]) -> tuple[str, Match | None]:
    if not matches:
        return "none", None
    best = matches[0]
    if best.score >= AUTO_SCORE:
        if (
            len(matches) > 1
            and matches[1].score >= best.score - AMBIGUOUS_MARGIN
            and normalize_name(matches[1].poi.name) == normalize_name(best.poi.name)
        ):
            return "candidate", best
        return "auto", best
    if best.score >= CANDIDATE_SCORE:
        return "candidate", best
    return "none", None


def nearby_candidates(
    place: IndexPlace, pois: list[tuple[Poi, float]]
) -> list[tuple[Poi, float | None]]:
    return [(poi, distance) for poi, distance in pois]


def direct_candidates(pois: list[Poi]) -> list[tuple[Poi, float | None]]:
    return [(poi, None) for poi in pois]


def distance_between(place: IndexPlace, poi: Poi) -> float | None:
    if not place.has_coords or poi.lat is None or poi.lng is None:
        return None
    return haversine_m(place.lat, place.lng, poi.lat, poi.lng)
