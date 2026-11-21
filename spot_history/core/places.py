from __future__ import annotations

import math
import sqlite3
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .categories import UNKNOWN_GROUP, resolve_category_group
from .identity import OsmAssignment, osm_key, validate_osm
from .identity import local_key as build_local_key
from .naming import name_similarity

SOURCE_PRIORITY = ("foursquare", "yelp")
MATCH_DISTANCE_M = 50.0
FUZZY_DISTANCE_M = 25.0
FUZZY_AUTO_SIMILARITY = 0.9
FUZZY_CANDIDATE_SIMILARITY = 0.78
OSM_CONFLICT_CANDIDATE_DISTANCE_M = 250.0

RULES_SCHEMA = """
CREATE TABLE IF NOT EXISTS place_rules (
    source TEXT NOT NULL,
    source_place_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('merge', 'standalone')),
    target_source TEXT,
    target_source_place_id TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source, source_place_id)
);

CREATE INDEX IF NOT EXISTS idx_place_rules_target
    ON place_rules(target_source, target_source_place_id);
"""


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    text = (
        text.replace("ß", "ss")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
    )
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.split())


def haversine_m(
    lat1: float, lng1: float, lat2: float, lng2: float
) -> float:
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = phi2 - phi1
    delta_lambda = math.radians(lng2 - lng1)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(value))


def source_rank(source: str) -> int:
    try:
        return SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(SOURCE_PRIORITY)


def _category_compatible(first: SourcePlace, second: SourcePlace) -> bool:
    first_group, _ = resolve_category_group(first.category, first.name)
    second_group, _ = resolve_category_group(second.category, second.name)
    if first_group == UNKNOWN_GROUP or second_group == UNKNOWN_GROUP:
        return True
    return first_group == second_group


def _near_pairs(
    places: Iterable[SourcePlace], max_distance_m: float
) -> Iterable[tuple[SourcePlace, SourcePlace, float]]:
    step = max(0.001, max_distance_m / 80000)
    buckets: dict[tuple[int, int], list[SourcePlace]] = {}
    for place in places:
        if not place.has_coords:
            continue
        key = (
            math.floor(place.lat / step),
            math.floor(place.lng / step),
        )
        buckets.setdefault(key, []).append(place)
    seen: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    for place in places:
        if not place.has_coords:
            continue
        key_lat = math.floor(place.lat / step)
        key_lng = math.floor(place.lng / step)
        for offset_lat in (-1, 0, 1):
            for offset_lng in (-1, 0, 1):
                for other in buckets.get(
                    (key_lat + offset_lat, key_lng + offset_lng), []
                ):
                    if other.key <= place.key:
                        continue
                    pair = (place.key, other.key)
                    if pair in seen:
                        continue
                    seen.add(pair)
                    distance = haversine_m(
                        place.lat, place.lng, other.lat, other.lng
                    )
                    if distance <= max_distance_m:
                        yield place, other, distance
def place_ref(source: str, source_place_id: str) -> str:
    return f"{source}:{source_place_id}"


def parse_place_ref(value: str) -> tuple[str, str]:
    source, separator, source_place_id = value.partition(":")
    if not separator or not source or not source_place_id:
        raise ValueError(f"Ungültige Ortsreferenz: {value!r} (erwartet source:id)")
    return source, source_place_id


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SourcePlace:
    source: str
    source_place_id: str
    name: str | None = None
    lat: float | None = None
    lng: float | None = None
    category: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    place_url: str | None = None
    visit_count: int = 0
    comment_only: bool = False
    osm_type: str | None = None
    osm_id: int | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.source_place_id)

    @property
    def ref(self) -> str:
        return place_ref(self.source, self.source_place_id)

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
        return (self.osm_type, int(self.osm_id))


@dataclass
class PlaceRule:
    source: str
    source_place_id: str
    action: str
    target_source: str | None = None
    target_source_place_id: str | None = None
    note: str | None = None
    created_at: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.source_place_id)

    @property
    def target_key(self) -> tuple[str, str] | None:
        if not self.target_source or not self.target_source_place_id:
            return None
        return (self.target_source, self.target_source_place_id)


@dataclass
class MergeCandidate:
    a: SourcePlace
    b: SourcePlace
    reason: str
    confidence: str
    distance_m: float | None = None
    city: str | None = None

    @property
    def ordered_pair(self) -> tuple[SourcePlace, SourcePlace]:
        return (self.a, self.b) if self.a.ref <= self.b.ref else (self.b, self.a)


@dataclass
class CanonicalPlace:
    canonical_key: str
    primary: SourcePlace
    members: list[SourcePlace]
    methods: dict[tuple[str, str], str] = field(default_factory=dict)
    osm_type: str | None = None
    osm_id: int | None = None
    match_status: str = "local"

    @property
    def local_fallback_key(self) -> str:
        return build_local_key(self.primary.source, self.primary.source_place_id)

    @property
    def legacy_key(self) -> str:
        return self.primary.ref

    @property
    def sorted_members(self) -> list[SourcePlace]:
        others = sorted(
            (
                place
                for place in self.members
                if place.key != self.primary.key
            ),
            key=lambda place: (source_rank(place.source), place.source, place.source_place_id),
        )
        return [self.primary, *others]

    def best(self, attribute: str) -> Any:
        for member in self.sorted_members:
            value = getattr(member, attribute)
            if value is not None and value != "":
                return value
        return None

    @property
    def name(self) -> str | None:
        return self.best("name")

    @property
    def category(self) -> str | None:
        return self.best("category")

    @property
    def category_group(self) -> str:
        return resolve_category_group(self.category, self.name)[0]

    @property
    def category_group_source(self) -> str | None:
        return resolve_category_group(self.category, self.name)[1]

    @property
    def address(self) -> str | None:
        return self.best("address")

    @property
    def city(self) -> str | None:
        return self.best("city")

    @property
    def state(self) -> str | None:
        return self.best("state")

    @property
    def postal_code(self) -> str | None:
        return self.best("postal_code")

    @property
    def country_code(self) -> str | None:
        return self.best("country_code")

    @property
    def coords(self) -> tuple[float | None, float | None]:
        for member in self.sorted_members:
            if member.has_coords:
                return member.lat, member.lng
        return None, None

    @property
    def primary_source(self) -> str:
        return self.primary.source

    @property
    def sources(self) -> list[str]:
        return [member.source for member in self.sorted_members]


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[tuple[str, str], tuple[str, str]] = {}

    def add(self, item: tuple[str, str]) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: tuple[str, str]) -> tuple[str, str]:
        self.add(item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            parent = self.parent[item]
            self.parent[item] = root
            item = parent
        return root

    def union(self, first: tuple[str, str], second: tuple[str, str]) -> None:
        root_a = self.find(first)
        root_b = self.find(second)
        if root_a == root_b:
            return
        if root_a <= root_b:
            self.parent[root_b] = root_a
        else:
            self.parent[root_a] = root_b

    def groups(self) -> dict[tuple[str, str], list[tuple[str, str]]]:
        groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for item in list(self.parent):
            groups.setdefault(self.find(item), []).append(item)
        return groups


def collect_source_places(visits: Iterable[Any]) -> dict[tuple[str, str], SourcePlace]:
    places: dict[tuple[str, str], SourcePlace] = {}
    for visit in visits:
        source_place_id = getattr(visit, "source_place_id", None)
        if not source_place_id:
            continue
        key = (visit.source, source_place_id)
        place = places.get(key)
        if place is None:
            place = SourcePlace(source=visit.source, source_place_id=source_place_id)
            places[key] = place
        for attribute in (
            "name",
            "lat",
            "lng",
            "category",
            "address",
            "city",
            "state",
            "postal_code",
            "country_code",
            "place_url",
        ):
            value = getattr(visit, attribute, None)
            if value is not None and value != "":
                setattr(place, attribute, value)
            elif attribute == "name" and getattr(visit, "place_name", None):
                place.name = visit.place_name
        extra = getattr(visit, "extra", None) or {}
        if extra.get("comment_only"):
            place.comment_only = True
        osm_type = extra.get("osm_type")
        osm_id = extra.get("osm_id")
        if osm_type and osm_id is not None:
            try:
                clean_type, clean_id = validate_osm(osm_type, osm_id)
            except ValueError:
                pass
            else:
                place.osm_type = clean_type
                place.osm_id = clean_id
        place.visit_count += 1
    return places


def _select_primary(members: list[SourcePlace]) -> SourcePlace:
    return min(
        members,
        key=lambda place: (
            source_rank(place.source),
            -place.visit_count,
            place.source,
            place.source_place_id,
        ),
    )


def resolve_canonical_places(
    source_places: Iterable[SourcePlace],
    rules: Iterable[PlaceRule] = (),
    log=None,
    osm_assignments: dict[tuple[str, str], OsmAssignment] | None = None,
) -> tuple[list[CanonicalPlace], list[MergeCandidate]]:
    places = {place.key: place for place in source_places}
    if osm_assignments:
        for key, place in places.items():
            assignment = osm_assignments.get(key)
            if assignment is None or assignment.status == "rejected":
                continue
            place.osm_type = assignment.osm_type
            place.osm_id = int(assignment.osm_id)
    union = _UnionFind()
    for key in places:
        union.add(key)

    standalone: set[tuple[str, str]] = set()
    manual_edges: list[tuple[tuple[str, str], tuple[str, str]]] = []
    for rule in rules:
        if rule.key not in places:
            if log:
                log(f"Orte: Regel für unbekannten Ort {place_ref(*rule.key)} ignoriert")
            continue
        if rule.action == "standalone":
            standalone.add(rule.key)
            continue
        if rule.action == "merge":
            target = rule.target_key
            if target is None or target not in places:
                if log:
                    log(
                        f"Orte: Merge-Ziel für {place_ref(*rule.key)} nicht gefunden "
                        f"({rule.target_source}:{rule.target_source_place_id})"
                    )
                continue
            if target == rule.key:
                continue
            manual_edges.append((rule.key, target))

    methods: dict[tuple[str, str], str] = {}
    for key, target in manual_edges:
        methods[key] = "manual"
        methods.setdefault(target, "manual")
        union.union(key, target)

    osm_groups: dict[tuple[str, int], list[SourcePlace]] = {}
    for place in places.values():
        if place.has_osm:
            osm_groups.setdefault(place.osm_ref, []).append(place)
    for members in osm_groups.values():
        first = members[0]
        for other in members[1:]:
            union.union(first.key, other.key)
        for member in members:
            methods.setdefault(member.key, "osm")

    def osm_conflict(a: SourcePlace, b: SourcePlace) -> bool:
        return a.has_osm and b.has_osm and a.osm_ref != b.osm_ref

    name_groups: dict[str, list[SourcePlace]] = {}
    for place in places.values():
        normalized = normalize_name(place.name)
        if normalized:
            name_groups.setdefault(normalized, []).append(place)

    candidates: dict[tuple[str, str, str, str], MergeCandidate] = {}

    def add_candidate(
        a: SourcePlace,
        b: SourcePlace,
        reason: str,
        confidence: str,
        distance_m: float | None,
        city: str | None,
    ) -> None:
        ordered_a, ordered_b = (
            (a, b) if a.ref <= b.ref else (b, a)
        )
        key = (
            ordered_a.source,
            ordered_a.source_place_id,
            ordered_b.source,
            ordered_b.source_place_id,
        )
        candidates.setdefault(
            key,
            MergeCandidate(
                a=ordered_a,
                b=ordered_b,
                reason=reason,
                confidence=confidence,
                distance_m=distance_m,
                city=city,
            ),
        )

    def dedupe_near_edges(
        edges: list[tuple[SourcePlace, SourcePlace, float]],
    ) -> list[tuple[SourcePlace, SourcePlace, float]]:
        grouped: dict[
            tuple[tuple[str, str], tuple[str, str]],
            tuple[SourcePlace, SourcePlace, float],
        ] = {}
        for first, second, distance in edges:
            roots = tuple(sorted((union.find(first.key), union.find(second.key))))
            if roots[0] == roots[1]:
                continue
            current = grouped.get(roots)
            if current is None or distance < current[2]:
                grouped[roots] = (first, second, distance)
        return list(grouped.values())

    def dedupe_city_edges(
        edges: list[tuple[SourcePlace, SourcePlace, str]],
    ) -> list[tuple[SourcePlace, SourcePlace, str]]:
        grouped: dict[
            tuple[tuple[str, str], tuple[str, str]],
            tuple[SourcePlace, SourcePlace, str],
        ] = {}
        for first, second, city in edges:
            roots = tuple(sorted((union.find(first.key), union.find(second.key))))
            if roots[0] == roots[1]:
                continue
            grouped.setdefault(roots, (first, second, city))
        return list(grouped.values())

    def edge_degree(edges: list[tuple[SourcePlace, SourcePlace, Any]]) -> dict:
        counts: dict[tuple[str, str], int] = {}
        for first, second, *_ in edges:
            for key in (union.find(first.key), union.find(second.key)):
                counts[key] = counts.get(key, 0) + 1
        return counts

    for group in name_groups.values():
        near_edges: list[tuple[SourcePlace, SourcePlace, float]] = []
        city_edges: list[tuple[SourcePlace, SourcePlace, str]] = []
        for index, first in enumerate(group):
            for second in group[index + 1 :]:
                if first.source == second.source:
                    continue
                if first.key in standalone or second.key in standalone:
                    continue
                if osm_conflict(first, second):
                    distance = (
                        haversine_m(first.lat, first.lng, second.lat, second.lng)
                        if first.has_coords and second.has_coords
                        else None
                    )
                    if (
                        distance is None
                        or distance <= OSM_CONFLICT_CANDIDATE_DISTANCE_M
                    ):
                        add_candidate(
                            first,
                            second,
                            "osm_conflict",
                            "high",
                            distance,
                            first.city or second.city,
                        )
                    continue
                first_city = normalize_name(first.city)
                second_city = normalize_name(second.city)
                if first.has_coords and second.has_coords:
                    distance = haversine_m(first.lat, first.lng, second.lat, second.lng)
                    if distance <= MATCH_DISTANCE_M:
                        near_edges.append((first, second, distance))
                    elif first_city and first_city == second_city:
                        add_candidate(
                            first,
                            second,
                            "name_city_far_coords",
                            "low",
                            distance,
                            first.city or second.city,
                        )
                    continue
                if first_city and first_city == second_city:
                    city_edges.append((first, second, first.city or second.city))

        near_edges = dedupe_near_edges(near_edges)
        near_degree = edge_degree(near_edges)
        for first, second, distance in near_edges:
            roots = (union.find(first.key), union.find(second.key))
            if roots[0] == roots[1]:
                continue
            if near_degree.get(roots[0]) == 1 and near_degree.get(roots[1]) == 1:
                union.union(first.key, second.key)
                methods.setdefault(first.key, "name_coords")
                methods.setdefault(second.key, "name_coords")
            else:
                add_candidate(
                    first, second, "name_coords_ambiguous", "medium", distance, None
                )

        city_edges = dedupe_city_edges(city_edges)
        city_degree = edge_degree(city_edges)
        for first, second, city in city_edges:
            roots = (union.find(first.key), union.find(second.key))
            if roots[0] == roots[1]:
                continue
            if city_degree.get(roots[0]) == 1 and city_degree.get(roots[1]) == 1:
                union.union(first.key, second.key)
                methods.setdefault(first.key, "name_city")
                methods.setdefault(second.key, "name_city")
            else:
                add_candidate(first, second, "name_city_ambiguous", "low", None, city)

        coordless = [
            place
            for place in group
            if not place.has_coords
            and not place.city
            and place.comment_only
            and place.key not in standalone
        ]
        candidates_unique = [
            place
            for place in group
            if place not in coordless and place.key not in standalone
        ]
        if len(coordless) == 1 and len(candidates_unique) == 1:
            first, second = coordless[0], candidates_unique[0]
            if first.source != second.source and not osm_conflict(first, second):
                union.union(first.key, second.key)
                methods.setdefault(first.key, "name_unique")
                methods.setdefault(second.key, "name_unique")

    fuzzy_edges: list[tuple[SourcePlace, SourcePlace, float, float]] = []
    fuzzy_places = [
        place for place in places.values() if place.key not in standalone
    ]
    for first, second, distance in _near_pairs(fuzzy_places, FUZZY_DISTANCE_M):
        if first.source == second.source:
            continue
        if union.find(first.key) == union.find(second.key):
            continue
        similarity = name_similarity(first.name, second.name)
        if similarity < FUZZY_CANDIDATE_SIMILARITY:
            continue
        if osm_conflict(first, second):
            add_candidate(
                first,
                second,
                "osm_conflict",
                "high",
                distance,
                first.city or second.city,
            )
            continue
        compatible = _category_compatible(first, second)
        if similarity >= FUZZY_AUTO_SIMILARITY and (
            compatible or similarity >= 0.97
        ):
            fuzzy_edges.append((first, second, distance, similarity))
        else:
            add_candidate(
                first,
                second,
                "name_fuzzy_coords",
                "medium" if compatible else "low",
                distance,
                first.city or second.city,
            )

    fuzzy_groups: dict[
        tuple[tuple[str, str], tuple[str, str]],
        tuple[SourcePlace, SourcePlace, float, float],
    ] = {}
    for first, second, distance, similarity in fuzzy_edges:
        roots = tuple(sorted((union.find(first.key), union.find(second.key))))
        if roots[0] == roots[1]:
            continue
        current = fuzzy_groups.get(roots)
        if current is None or (similarity, -distance) > (current[3], -current[2]):
            fuzzy_groups[roots] = (first, second, distance, similarity)
    fuzzy_degree: dict[tuple[str, str], int] = {}
    for roots in fuzzy_groups:
        for root in roots:
            fuzzy_degree[root] = fuzzy_degree.get(root, 0) + 1
    for roots, (first, second, distance, _) in fuzzy_groups.items():
        if fuzzy_degree.get(roots[0]) == 1 and fuzzy_degree.get(roots[1]) == 1:
            union.union(first.key, second.key)
            methods.setdefault(first.key, "name_fuzzy_coords")
            methods.setdefault(second.key, "name_fuzzy_coords")
        else:
            add_candidate(
                first,
                second,
                "name_fuzzy_coords_ambiguous",
                "medium",
                distance,
                first.city or second.city,
            )

    canonical: list[CanonicalPlace] = []
    for keys in union.groups().values():
        members = [places[key] for key in keys if key in places]
        if not members:
            continue
        primary = _select_primary(members)
        osm_refs = {member.osm_ref for member in members if member.has_osm}
        if len(osm_refs) == 1:
            osm_type, osm_id = next(iter(osm_refs))
            canonical_key = osm_key(osm_type, osm_id)
            match_status = "osm"
        else:
            osm_type, osm_id = None, None
            canonical_key = build_local_key(primary.source, primary.source_place_id)
            match_status = "conflict" if len(osm_refs) > 1 else "local"
        canonical.append(
            CanonicalPlace(
                canonical_key=canonical_key,
                primary=primary,
                members=members,
                methods={
                    member.key: methods.get(member.key, "single") for member in members
                },
                osm_type=osm_type,
                osm_id=osm_id,
                match_status=match_status,
            )
        )

    canonical.sort(key=lambda place: place.canonical_key)
    return canonical, list(candidates.values())


def open_rules_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(RULES_SCHEMA)
    conn.commit()
    return conn


def load_rules(path: Path) -> list[PlaceRule]:
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(
                "SELECT source, source_place_id, action, target_source, "
                "target_source_place_id, note, created_at FROM place_rules"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    return [
        PlaceRule(
            source=row["source"],
            source_place_id=row["source_place_id"],
            action=row["action"],
            target_source=row["target_source"],
            target_source_place_id=row["target_source_place_id"],
            note=row["note"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


def upsert_rule(path: Path, rule: PlaceRule) -> None:
    conn = open_rules_db(path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO place_rules (source, source_place_id, action, "
                "target_source, target_source_place_id, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, source_place_id) DO UPDATE SET "
                "action=excluded.action, target_source=excluded.target_source, "
                "target_source_place_id=excluded.target_source_place_id, "
                "note=excluded.note, created_at=excluded.created_at",
                (
                    rule.source,
                    rule.source_place_id,
                    rule.action,
                    rule.target_source,
                    rule.target_source_place_id,
                    rule.note,
                    rule.created_at or now_iso(),
                ),
            )
    finally:
        conn.close()


def delete_rule(path: Path, source: str, source_place_id: str) -> bool:
    if not path.exists():
        return False
    conn = open_rules_db(path)
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM place_rules WHERE source=? AND source_place_id=?",
                (source, source_place_id),
            )
        return cursor.rowcount > 0
    finally:
        conn.close()
