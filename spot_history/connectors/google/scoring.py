# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

from dataclasses import dataclass

from ..osm.db import Poi

VALID_TAGS = (
    "amenity",
    "shop",
    "tourism",
    "leisure",
    "office",
    "craft",
    "club",
    "healthcare",
    "historic",
)
HARD_REJECT_TAGS = ("highway", "place", "boundary", "waterway", "railway")
LOW_VALUE_AMENITIES = {
    "parking",
    "parking_space",
    "parking_entrance",
    "bench",
    "waste_basket",
    "bicycle_parking",
    "bicycle_repair_station",
    "drinking_water",
    "post_box",
    "telephone",
    "recycling",
    "toilets",
    "shelter",
    "clock",
    "vending_machine",
    "charging_station",
    "bus_station",
    "taxi",
    "atm",
    "bureau_de_change",
}
TYPE_WEIGHTS = {
    "amenity": 1.0,
    "shop": 0.95,
    "tourism": 0.9,
    "leisure": 0.9,
    "healthcare": 0.8,
    "craft": 0.8,
    "club": 0.8,
    "office": 0.7,
    "historic": 0.7,
}
AUTO_DISTANCE_M = 25.0
CANDIDATE_DISTANCE_M = 50.0
AUTO_MARGIN_M = 8.0
NOMINATIM_OSM_MAX_DISTANCE_M = 50.0


@dataclass
class PoiCandidate:
    poi: Poi
    distance_m: float
    score: float
    category: str | None
    reason: str


def poi_category(poi: Poi) -> str | None:
    for key in VALID_TAGS:
        value = poi.tags.get(key)
        if value:
            return str(value)
    return None


def poi_city(poi: Poi) -> str | None:
    """Return the municipality from OSM tags, never a mere suburb/district."""
    city = poi.tags.get("addr:city")
    return str(city) if city else None


def is_valid_poi(poi: Poi) -> bool:
    if not poi.name or not poi.name.strip():
        return False
    if any(poi.tags.get(key) for key in HARD_REJECT_TAGS):
        return False
    if not any(poi.tags.get(key) for key in VALID_TAGS):
        return False
    amenity = poi.tags.get("amenity")
    if amenity in LOW_VALUE_AMENITIES:
        return False
    if poi.tags.get("shop") == "vacant":
        return False
    return not poi.name.strip().isdigit()


def score_poi(poi: Poi, distance_m: float) -> PoiCandidate:
    category = poi_category(poi)
    if distance_m <= 10:
        distance_score = 1.0
    elif distance_m <= 15:
        distance_score = 0.9
    elif distance_m <= 20:
        distance_score = 0.8
    elif distance_m <= 25:
        distance_score = 0.65
    elif distance_m <= 35:
        distance_score = 0.5
    elif distance_m <= CANDIDATE_DISTANCE_M:
        distance_score = 0.3
    else:
        distance_score = 0.0
    type_score = 0.0
    for key in VALID_TAGS:
        if poi.tags.get(key):
            type_score = TYPE_WEIGHTS.get(key, 0.6)
            break
    score = round(distance_score * 0.7 + type_score * 0.3, 3)
    return PoiCandidate(
        poi=poi,
        distance_m=round(distance_m, 1),
        score=score,
        category=category,
        reason="distance_type",
    )


def rank_candidates(
    pois: list[tuple[Poi, float]],
    *,
    max_distance_m: float = CANDIDATE_DISTANCE_M,
) -> list[PoiCandidate]:
    candidates = [
        score_poi(poi, distance)
        for poi, distance in pois
        if distance <= max_distance_m and is_valid_poi(poi)
    ]
    candidates.sort(
        key=lambda item: (
            item.distance_m,
            -item.score,
            item.poi.name,
        )
    )
    return candidates


def decide(
    candidates: list[PoiCandidate],
    *,
    auto_distance_m: float = AUTO_DISTANCE_M,
    margin_m: float = AUTO_MARGIN_M,
) -> tuple[str, PoiCandidate | None]:
    if not candidates:
        return "none", None
    best = candidates[0]
    if best.distance_m > auto_distance_m:
        return "candidate", best
    for other in candidates[1:]:
        if other.distance_m > auto_distance_m:
            break
        if other.poi.name == best.poi.name:
            continue
        if other.distance_m - best.distance_m < margin_m:
            return "candidate", best
    return "auto", best


def format_address(payload: dict) -> tuple[str | None, str | None, str | None]:
    address = payload.get("address") or {}
    if not isinstance(address, dict):
        address = {}
    road = address.get("road") or address.get("pedestrian") or address.get("footway")
    number = address.get("house_number")
    line = " ".join(part for part in (road, number) if part) or None
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
        or address.get("suburb")
    )
    country = address.get("country_code")
    return line, city, country


def nominatim_candidate(payload: dict) -> dict:
    category = payload.get("category")
    name = payload.get("name")
    if not name:
        namedetails = payload.get("namedetails") or {}
        if isinstance(namedetails, dict):
            name = namedetails.get("name")
    address, city, country = format_address(payload)
    valid_name = bool(name) and category not in (
        "place",
        "highway",
        "boundary",
        "waterway",
        "building",
    )
    return {
        "name": name if valid_name else None,
        "category": f"{category}={payload.get('type')}" if category else None,
        "address": address,
        "city": city,
        "country_code": country,
        "display_name": payload.get("display_name"),
        "lat": payload.get("lat"),
        "lng": payload.get("lon"),
        "osm_type": payload.get("osm_type"),
        "osm_id": payload.get("osm_id"),
    }
