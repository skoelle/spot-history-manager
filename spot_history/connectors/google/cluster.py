from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from .parser import ParsedVisit

DEFAULT_CLUSTER_DISTANCE_M = 15.0
SEMANTIC_PRIORITY = {
    "Home": 0,
    "Work": 1,
    "Inferred Work": 2,
    "Aliased Location": 3,
    "Searched Address": 4,
    "Unknown": 5,
}
SEMANTIC_LABELS = {
    "Home": "Zuhause",
    "Work": "Arbeit",
    "Inferred Work": "Arbeit",
}


@dataclass
class PlaceAgg:
    place_id: str
    lat: float | None = None
    lng: float | None = None
    semantic_type: str | None = None
    visit_count: int = 0
    first_at: int | None = None
    last_at: int | None = None
    name: str | None = None
    address: str | None = None
    city: str | None = None
    country_code: str | None = None
    category: str | None = None

    @property
    def has_coords(self) -> bool:
        return self.lat is not None and self.lng is not None


@dataclass
class ClusterResult:
    cluster_key: str
    lat: float | None
    lng: float | None
    visit_count: int
    place_count: int
    representative_place_id: str
    semantic_type: str | None
    name: str | None
    address: str | None
    city: str | None
    country_code: str | None
    category: str | None
    name_source: str | None
    first_at: int | None
    last_at: int | None
    members: list[str] = field(default_factory=list)


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = phi2 - phi1
    delta_lambda = math.radians(lng2 - lng1)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(value))


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: str) -> str:
        self.add(item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            parent = self.parent[item]
            self.parent[item] = root
            item = parent
        return root

    def union(self, first: str, second: str) -> None:
        root_a = self.find(first)
        root_b = self.find(second)
        if root_a == root_b:
            return
        if root_a <= root_b:
            self.parent[root_b] = root_a
        else:
            self.parent[root_a] = root_b

    def groups(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for item in list(self.parent):
            groups.setdefault(self.find(item), []).append(item)
        return groups


def aggregate_places(visits: list[ParsedVisit]) -> dict[str, PlaceAgg]:
    places: dict[str, PlaceAgg] = {}
    for visit in visits:
        place = places.get(visit.place_id)
        if place is None:
            place = PlaceAgg(place_id=visit.place_id)
            places[visit.place_id] = place
        place.visit_count += 1
        if place.lat is None and visit.lat is not None:
            place.lat = visit.lat
            place.lng = visit.lng
        if place.semantic_type is None and visit.semantic_type:
            place.semantic_type = visit.semantic_type
        if place.name is None and visit.place_name:
            place.name = visit.place_name
        if place.address is None and visit.address:
            place.address = visit.address
        if place.city is None and visit.city:
            place.city = visit.city
        if place.country_code is None and visit.country_code:
            place.country_code = visit.country_code
        if place.category is None and visit.category:
            place.category = visit.category
        if place.first_at is None or visit.start_at < place.first_at:
            place.first_at = visit.start_at
        end_at = visit.end_at if visit.end_at is not None else visit.start_at
        if place.last_at is None or end_at > place.last_at:
            place.last_at = end_at
    return places


def _cluster_hash(members: list[str]) -> str:
    payload = "\x1f".join(sorted(members))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"g-{digest}"


def _choose_cluster_key(
    members: list[str],
    old_map: dict[str, str],
    used: set[str],
) -> str:
    counts: dict[str, int] = {}
    for member in members:
        old_key = old_map.get(member)
        if old_key:
            counts[old_key] = counts.get(old_key, 0) + 1
    if counts:
        best = min(counts.items(), key=lambda item: (-item[1], item[0]))[0]
        if best not in used:
            used.add(best)
            return best
    key = _cluster_hash(members)
    while key in used:
        key = _cluster_hash(members + [key])
    used.add(key)
    return key


def _member_order(members: list[PlaceAgg]) -> list[PlaceAgg]:
    return sorted(members, key=lambda place: (-place.visit_count, place.place_id))


def _semantic(members: list[PlaceAgg]) -> str | None:
    values = [member.semantic_type for member in members if member.semantic_type]
    if not values:
        return None
    return min(
        values,
        key=lambda value: SEMANTIC_PRIORITY.get(value, 10),
    )


def consolidate(
    places: dict[str, PlaceAgg],
    old_map: dict[str, str] | None = None,
    max_distance_m: float = DEFAULT_CLUSTER_DISTANCE_M,
) -> tuple[dict[str, str], list[ClusterResult]]:
    old_map = old_map or {}
    union = _UnionFind()
    for place_id in places:
        union.add(place_id)
    items = [place for place in places.values() if place.has_coords]
    for index, first in enumerate(items):
        for second in items[index + 1 :]:
            if first.lat is None or first.lng is None:
                continue
            if second.lat is None or second.lng is None:
                continue
            if haversine_m(first.lat, first.lng, second.lat, second.lng) <= max_distance_m:
                union.union(first.place_id, second.place_id)

    used: set[str] = set()
    place_to_cluster: dict[str, str] = {}
    clusters: list[ClusterResult] = []
    for member_ids in union.groups().values():
        members = [places[member_id] for member_id in member_ids if member_id in places]
        if not members:
            continue
        ordered = _member_order(members)
        representative = ordered[0]
        cluster_key = _choose_cluster_key(member_ids, old_map, used)
        semantic_type = _semantic(ordered)
        name = next((member.name for member in ordered if member.name), None)
        address = next((member.address for member in ordered if member.address), None)
        city = next((member.city for member in ordered if member.city), None)
        country_code = next(
            (member.country_code for member in ordered if member.country_code), None
        )
        category = next(
            (member.category for member in ordered if member.category), None
        )
        name_source = "source" if name else None
        if semantic_type in SEMANTIC_LABELS:
            name = SEMANTIC_LABELS[semantic_type]
            name_source = "semantic"
            category = semantic_type.casefold().replace(" ", "_")
            address = None
            city = None
        first_at = min(
            (member.first_at for member in members if member.first_at is not None),
            default=None,
        )
        last_at = max(
            (member.last_at for member in members if member.last_at is not None),
            default=None,
        )
        clusters.append(
            ClusterResult(
                cluster_key=cluster_key,
                lat=representative.lat,
                lng=representative.lng,
                visit_count=sum(member.visit_count for member in members),
                place_count=len(members),
                representative_place_id=representative.place_id,
                semantic_type=semantic_type,
                name=name,
                address=address,
                city=city,
                country_code=country_code,
                category=category,
                name_source=name_source,
                first_at=first_at,
                last_at=last_at,
                members=sorted(member_ids),
            )
        )
        for member_id in member_ids:
            place_to_cluster[member_id] = cluster_key
    clusters.sort(key=lambda cluster: cluster.cluster_key)
    return place_to_cluster, clusters
