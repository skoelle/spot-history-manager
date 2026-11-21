# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import difflib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ...core.places import looks_like_address, normalize_name
from .db import Poi, haversine_m

AUTO_SCORE = 0.80
CANDIDATE_SCORE = 0.55
AMBIGUOUS_MARGIN = 0.05
FUZZY_MIN_SIMILARITY = 0.70


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
    osm_type: str | None = None
    osm_id: int | None = None
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
    matched_name: str | None = None
    name_tag: str = "name"
    alternate: bool = False
    fuzzy: bool = False


def load_index_places(index_db: Path) -> list[IndexPlace]:
    if not index_db.exists():
        raise FileNotFoundError(f"Kein Index vorhanden: {index_db}")
    conn = sqlite3.connect(f"file:{index_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, canonical_key, name, lat, lng, city, postal_code, "
            "address, category, osm_type, osm_id FROM canonical_places "
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
            osm_type=row["osm_type"],
            osm_id=row["osm_id"],
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


def _fuzzy_similarity(left: str | None, right: str | None) -> float:
    a = normalize_name(left)
    b = normalize_name(right)
    if not a or not b or min(len(a), len(b)) < 5:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _name_match(
    place_name: str | None,
    poi: Poi,
    *,
    allow_alt: bool,
    allow_fuzzy: bool,
) -> tuple[float, str, str, bool]:
    best = (0.0, poi.name, "name", False)
    variants = poi.name_variants() if allow_alt else [(poi.name, "name")]
    for value, tag in variants:
        base = name_similarity(place_name, value)
        fuzzy = (
            _fuzzy_similarity(place_name, value)
            if allow_fuzzy and base < FUZZY_MIN_SIMILARITY
            else 0.0
        )
        if fuzzy >= FUZZY_MIN_SIMILARITY and fuzzy > base:
            similarity, is_fuzzy = fuzzy, True
        elif base > 0.0:
            similarity, is_fuzzy = base, False
        else:
            continue
        if similarity > best[0]:
            best = (similarity, value, tag, is_fuzzy)
    return best


def _city_matches(place: IndexPlace, poi: Poi) -> bool:
    city = normalize_name(place.city)
    return bool(city) and city == normalize_name(poi.municipality)


def score_poi(
    place: IndexPlace,
    poi: Poi,
    distance_m: float | None,
    *,
    allow_alt: bool = True,
    allow_fuzzy: bool = True,
    max_distance_m: float = 150.0,
) -> Match | None:
    if looks_like_address(place.name):
        return None

    similarity, matched_name, name_tag, fuzzy = _name_match(
        place.name, poi, allow_alt=allow_alt, allow_fuzzy=allow_fuzzy
    )
    if similarity <= 0.0:
        return None
    alternate = name_tag != "name"

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
        elif distance_m <= max_distance_m:
            location_score = 0.0
        else:
            return None
        if _city_matches(place, poi):
            location_score += 0.05
    else:
        location_score = 0.1
        if _city_matches(place, poi):
            location_score = 0.25
        if place.postcode and place.postcode == poi.postcode:
            location_score += 0.1

    if place.postcode and place.postcode == poi.postcode:
        location_score += 0.05

    score = min(1.0, name_score + location_score)
    suffix = "_distance" if distance_m is not None else "_city"
    if fuzzy:
        reason = "fuzzy" + suffix
    elif alternate:
        reason = "alt_name" + suffix
    else:
        reason = "name" + suffix
    return Match(
        poi=poi,
        score=round(score, 3),
        distance_m=round(distance_m, 1) if distance_m is not None else None,
        name_score=round(name_score, 3),
        reason=reason,
        matched_name=matched_name,
        name_tag=name_tag,
        alternate=alternate,
        fuzzy=fuzzy,
    )


def rank_matches(
    place: IndexPlace,
    candidates: list[tuple[Poi, float | None]],
    *,
    max_distance_m: float = 150.0,
) -> list[Match]:
    matches = []
    for poi, distance in candidates:
        match = score_poi(place, poi, distance, max_distance_m=max_distance_m)
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
    if best.score >= AUTO_SCORE and not best.alternate and not best.fuzzy:
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
