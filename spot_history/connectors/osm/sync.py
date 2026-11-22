# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ...core import identity
from ...core.categories import resolve_category_group
from ...core.config import Paths
from ...core.places import looks_like_address
from . import db as osm_db
from .matcher import (
    CANDIDATE_SCORE,
    WEAK_CANDIDATE_SCORE,
    IndexPlace,
    decide,
    direct_candidates,
    distance_between,
    load_index_places,
    name_similarity,
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
    addresses: int = 0


@dataclass
class RematchSummary:
    places: int = 0
    pending: int = 0
    auto_matched: int = 0
    candidates: int = 0
    none: int = 0
    addresses: int = 0


@dataclass
class NetScanSummary:
    places: int = 0
    pending: int = 0
    queries: int = 0
    cached_queries: int = 0
    candidates: int = 0
    none: int = 0
    addresses: int = 0
    errors: int = 0


@dataclass
class StrategySummary:
    places: int = 0
    pending: int = 0
    queries: int = 0
    cached_queries: int = 0
    candidates: int = 0
    none: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass
class DeepScanSummary:
    rematch: RematchSummary | None = None
    name: NetScanSummary | None = None
    address: StrategySummary | None = None
    contacts: StrategySummary | None = None
    no_coords: int = 0


ADDRESS_RADIUS_M = 150.0
CONTACT_RADIUS_M = 2000.0
_VALID_POI_TAGS = (
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
_HARD_REJECT_TAGS = ("highway", "place", "boundary", "waterway", "railway")
_LOW_VALUE_AMENITIES = {
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
_OSM_GROUP_HINTS = {
    "restaurant": "Restaurants",
    "fast_food": "Restaurants",
    "food_court": "Restaurants",
    "cafe": "Cafés",
    "ice_cream": "Cafés",
    "bakery": "Cafés",
    "bar": "Bars & Nightlife",
    "pub": "Bars & Nightlife",
    "nightclub": "Bars & Nightlife",
    "biergarten": "Biergarten",
    "hotel": "Hotels & Unterkünfte",
    "hostel": "Hotels & Unterkünfte",
    "guest_house": "Hotels & Unterkünfte",
    "motel": "Hotels & Unterkünfte",
    "camp_site": "Hotels & Unterkünfte",
    "museum": "Kultur & Sehenswürdigkeiten",
    "theatre": "Kultur & Sehenswürdigkeiten",
    "cinema": "Kultur & Sehenswürdigkeiten",
    "arts_centre": "Kultur & Sehenswürdigkeiten",
    "gallery": "Kultur & Sehenswürdigkeiten",
    "attraction": "Kultur & Sehenswürdigkeiten",
    "place_of_worship": "Kultur & Sehenswürdigkeiten",
    "monument": "Kultur & Sehenswürdigkeiten",
    "memorial": "Kultur & Sehenswürdigkeiten",
    "castle": "Kultur & Sehenswürdigkeiten",
    "library": "Kultur & Sehenswürdigkeiten",
    "park": "Natur & Outdoor",
    "garden": "Natur & Outdoor",
    "nature_reserve": "Natur & Outdoor",
    "beach": "Natur & Outdoor",
    "fitness_centre": "Sport & Freizeit",
    "sports_centre": "Sport & Freizeit",
    "swimming_pool": "Sport & Freizeit",
    "stadium": "Sport & Freizeit",
    "golf_course": "Sport & Freizeit",
    "miniature_golf": "Sport & Freizeit",
    "supermarket": "Einkaufen",
    "convenience": "Einkaufen",
    "department_store": "Einkaufen",
    "mall": "Einkaufen",
    "clothes": "Einkaufen",
    "shoes": "Einkaufen",
    "books": "Einkaufen",
    "electronics": "Einkaufen",
    "furniture": "Einkaufen",
    "hardware": "Einkaufen",
    "florist": "Einkaufen",
    "butcher": "Einkaufen",
    "beverages": "Einkaufen",
    "airport": "Reisen & Verkehr",
    "aerodrome": "Reisen & Verkehr",
    "station": "Reisen & Verkehr",
    "ferry_terminal": "Reisen & Verkehr",
    "school": "Arbeit & Bildung",
    "university": "Arbeit & Bildung",
    "college": "Arbeit & Bildung",
    "office": "Arbeit & Bildung",
    "company": "Arbeit & Bildung",
}
_SOCIAL_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "youtube.com",
    "linkedin.com",
    "tripadvisor.com",
    "tripadvisor.de",
    "yelp.com",
    "yelp.de",
    "google.com",
    "goo.gl",
    "maps.google.com",
    "foursquare.com",
    "linktr.ee",
    "wa.me",
    "t.me",
    "bit.ly",
}


def _is_venue_poi(poi: osm_db.Poi) -> bool:
    if not poi.name or not poi.name.strip():
        return False
    if any(poi.tags.get(key) for key in _HARD_REJECT_TAGS):
        return False
    if not any(poi.tags.get(key) for key in _VALID_POI_TAGS):
        return False
    if poi.tags.get("amenity") in _LOW_VALUE_AMENITIES:
        return False
    if poi.tags.get("shop") == "vacant":
        return False
    return not poi.name.strip().isdigit()


def _poi_group(poi: osm_db.Poi) -> str:
    category = poi.category
    if category and category in _OSM_GROUP_HINTS:
        return _OSM_GROUP_HINTS[category]
    group, _ = resolve_category_group(category, poi.name)
    return group


def _family(group: str) -> str:
    if group in ("Restaurants", "Cafés", "Biergarten", "Bars & Nightlife"):
        return "food"
    if group in ("Kultur & Sehenswürdigkeiten", "Events"):
        return "culture"
    if group in ("Natur & Outdoor", "Sport & Freizeit"):
        return "outdoor"
    if group == "Hotels & Unterkünfte":
        return "stay"
    if group == "Einkaufen":
        return "shopping"
    if group == "Reisen & Verkehr":
        return "travel"
    if group == "Arbeit & Bildung":
        return "work"
    return "other"


def _category_compatible(place: IndexPlace, poi: osm_db.Poi) -> bool:
    source_group, _ = resolve_category_group(place.category, place.name)
    source_family = _family(source_group)
    poi_family = _family(_poi_group(poi))
    if source_family == poi_family:
        return True
    if "other" in (source_family, poi_family):
        return name_similarity(place.name, poi.name) >= 0.5
    return False


def _address_query(place: IndexPlace) -> str | None:
    if not place.address:
        return None
    if place.city:
        return f"{place.address}, {place.city}"
    return place.address


def _address_key(place: IndexPlace) -> str:
    query = _address_query(place) or ""
    return "address:" + query.casefold()


def _normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        host = urlsplit(text if "://" in text else f"http://{text}").hostname or ""
    except ValueError:
        return None
    host = host.casefold().removeprefix("www.")
    if "." not in host:
        return None
    if any(host == domain or host.endswith("." + domain) for domain in _SOCIAL_DOMAINS):
        return None
    return host


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value).lstrip("0")
    if len(digits) < 7:
        return None
    return digits


def _phones_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    length = min(len(left), len(right), 10)
    return length >= 7 and left[-length:] == right[-length:]


def _contact_score(distance_m: float) -> float:
    ratio = max(0.0, 1.0 - distance_m / CONTACT_RADIUS_M)
    return round(0.30 + 0.15 * ratio, 3)


def _address_score(distance_m: float) -> float:
    ratio = max(0.0, 1.0 - distance_m / ADDRESS_RADIUS_M)
    return round(0.30 + 0.15 * ratio, 3)


def _contact_values(poi: osm_db.Poi) -> tuple[set[str], set[str], set[str]]:
    domains = {
        domain
        for key in ("website", "contact:website")
        if (domain := _normalize_domain(poi.tags.get(key)))
    }
    phones = {
        phone
        for key in ("phone", "contact:phone")
        if (phone := _normalize_phone(poi.tags.get(key)))
    }
    emails = {
        str(value).strip().casefold()
        for key in ("email", "contact:email")
        if (value := poi.tags.get(key))
    }
    return domains, phones, emails


def _load_contact_details(index_db: Path) -> dict[tuple[str, str], dict]:
    if not index_db.exists():
        return {}
    conn = sqlite3.connect(f"file:{index_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT source, source_place_id, website, tel, email "
            "FROM place_details"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    return {
        (row["source"], row["source_place_id"]): {
            "domains": {
                domain
                for domain in (_normalize_domain(row["website"]),)
                if domain
            },
            "phones": {
                phone for phone in (_normalize_phone(row["tel"]),) if phone
            },
            "emails": (
                {str(row["email"]).strip().casefold()} if row["email"] else set()
            ),
        }
        for row in rows
    }


def _nominatim_query(place: IndexPlace) -> str:
    parts = [place.name]
    if place.city:
        parts.append(place.city)
    if place.address:
        parts.append(place.address)
    return ", ".join(parts)


def _nominatim_fallback_query(place: IndexPlace) -> str:
    parts = [place.name]
    if place.city:
        parts.append(place.city)
    elif place.address:
        parts.append(place.address)
    return ", ".join(parts)


def _nominatim_key(place: IndexPlace) -> str:
    return "nominatim:" + _nominatim_query(place).casefold()


def _nominatim_fallback_key(place: IndexPlace) -> str:
    return "nominatim2:" + _nominatim_fallback_query(place).casefold()


def _weak_eligible(match) -> bool:
    if match.score < WEAK_CANDIDATE_SCORE:
        return False
    if match.distance_m is not None and match.distance_m > 150:
        return True
    return match.variant or match.fuzzy or match.name_tag != "name"


def _decide_with_weak(matches):
    decision, match = decide(matches)
    if decision == "none" and matches and _weak_eligible(matches[0]):
        match = matches[0]
        match.reason += "_weak"
        match.alternate = True
        return "candidate", match
    return decision, match


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
        if place.place_state != "open":
            continue
        if looks_like_address(place.name):
            summary.addresses += 1
            continue
        pending.append(place)
    if limit is not None:
        pending = pending[: max(0, limit)]
    summary.pending = len(pending)
    if not pending:
        log("OSM: keine offenen Orte zu matchen.")
        return summary

    db_path = osm_db_path or paths.osm_db
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
                matches = rank_matches(
                    place,
                    nearby_candidates(place, poi_rows),
                    max_distance_m=max(radius, 150),
                )
            else:
                payload = osm_db.get_nominatim_cache(conn, _nominatim_key(place))
                if not payload:
                    continue
                matches = rank_matches(
                    place, direct_candidates(parse_nominatim_results(payload))
                )
            decision, match = _decide_with_weak(matches)
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


def rematch(
    paths: Paths,
    *,
    radius: int = 500,
    limit: int | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    osm_db_path: Path | None = None,
    log=print,
) -> RematchSummary:
    """Match open places against the existing POI cache without network access."""
    summary = RematchSummary()
    places = load_index_places(paths.index_db)
    summary.places = len(places)
    assignments = identity.load_osm_assignments(paths.place_identity_db)
    pending = [
        place
        for place in places
        if place.members
        and place.place_state == "open"
        and not (place.osm_type and place.osm_id is not None)
        and not any(member in assignments for member in place.members)
    ]
    if limit is not None:
        pending = pending[: max(0, limit)]
    summary.pending = len(pending)
    if not pending:
        log("OSM: keine offenen Orte für den Offline-Rematch.")
        return summary

    db_path = osm_db_path or paths.osm_db
    if not db_path.exists():
        raise FileNotFoundError(f"Kein OSM-Cache vorhanden: {db_path}")
    conn = osm_db.open_db(db_path)
    try:
        for place in pending:
            if looks_like_address(place.name):
                summary.addresses += 1
                if not dry_run:
                    for source, source_place_id in place.members:
                        identity.clear_osm_candidates(
                            paths.place_identity_db, source, source_place_id
                        )
                continue
            if place.has_coords:
                poi_rows = osm_db.pois_near(
                    conn, place.lat, place.lng, max(radius, 150)
                )
                matches = rank_matches(
                    place,
                    nearby_candidates(place, poi_rows),
                    max_distance_m=max(radius, 150),
                )
            else:
                payload = osm_db.get_nominatim_cache(conn, _nominatim_key(place))
                if not payload:
                    summary.none += 1
                    continue
                matches = rank_matches(
                    place, direct_candidates(parse_nominatim_results(payload))
                )
            decision, match = _decide_with_weak(matches)
            if decision == "none" or match is None:
                summary.none += 1
                continue
            if decision == "auto":
                summary.auto_matched += 1
                log(
                    f"OSM: {place.name!r} -> {match.poi.osm_type}/{match.poi.osm_id} "
                    f"({match.poi.name!r}, {match.score:.2f}, Cache)"
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
                if verbose:
                    log(
                        f"OSM: Kandidat {place.name!r} ~ "
                        f"{match.matched_name or match.poi.name!r} "
                        f"({match.poi.osm_type}/{match.poi.osm_id}, "
                        f"{match.score:.2f}, {match.reason})"
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
        conn.close()
    return summary


def net_scan(
    paths: Paths,
    *,
    limit: int | None = None,
    max_distance_m: float = 2000.0,
    force: bool = False,
    dry_run: bool = False,
    sleep: float = 1.1,
    verbose: bool = False,
    osm_db_path: Path | None = None,
    log=print,
) -> NetScanSummary:
    """Search Nominatim for venues that still have no OSM ID and no candidate."""
    summary = NetScanSummary()
    places = load_index_places(paths.index_db)
    summary.places = len(places)
    assignments = identity.load_osm_assignments(paths.place_identity_db)
    candidate_keys = identity.load_osm_candidate_keys(paths.place_identity_db)
    pending: list[IndexPlace] = []
    for place in places:
        if not place.members:
            continue
        if place.osm_type and place.osm_id is not None:
            continue
        if place.place_state != "open":
            continue
        if any(member in assignments for member in place.members):
            continue
        if any(member in candidate_keys for member in place.members):
            continue
        if looks_like_address(place.name):
            summary.addresses += 1
            continue
        pending.append(place)
    if limit is not None:
        pending = pending[: max(0, limit)]
    summary.pending = len(pending)
    if not pending:
        log("OSM: kein offener Venue ohne Vorschlag für den Netzwerk-Scan.")
        return summary

    db_path = osm_db_path or paths.osm_db
    conn = osm_db.open_db(db_path)
    nominatim = NominatimClient(sleep=sleep, verbose=verbose)
    try:
        for position, place in enumerate(pending, start=1):
            if position % 25 == 0:
                log(f"OSM: Netzwerk-Scan {position}/{len(pending)}")
            key = _nominatim_key(place)
            payload = None if force else osm_db.get_nominatim_cache(conn, key)
            if payload is None:
                try:
                    payload = nominatim.search_raw(_nominatim_query(place), limit=5)
                except NominatimError as exc:
                    summary.errors += 1
                    log(f"OSM: Netzwerk-Scan übersprungen für {place.name!r} ({exc})")
                    continue
                summary.queries += 1
                osm_db.set_nominatim_cache(conn, key, payload)
                osm_db.save_pois(conn, parse_nominatim_results(payload))
            else:
                summary.cached_queries += 1

            fallback_query = _nominatim_fallback_query(place)
            if not payload and fallback_query.casefold() != _nominatim_query(place).casefold():
                fallback_key = _nominatim_fallback_key(place)
                fallback = (
                    None if force else osm_db.get_nominatim_cache(conn, fallback_key)
                )
                if fallback is None:
                    try:
                        fallback = nominatim.search_raw(fallback_query, limit=5)
                    except NominatimError as exc:
                        summary.errors += 1
                        log(
                            f"OSM: Netzwerk-Scan übersprungen für "
                            f"{place.name!r} ({exc})"
                        )
                        continue
                    summary.queries += 1
                    osm_db.set_nominatim_cache(conn, fallback_key, fallback)
                    osm_db.save_pois(conn, parse_nominatim_results(fallback))
                else:
                    summary.cached_queries += 1
                payload = fallback

            pois = parse_nominatim_results(payload)
            if not pois:
                summary.none += 1
                continue
            if place.has_coords:
                candidates = [
                    (poi, distance_between(place, poi)) for poi in pois
                ]
                matches = rank_matches(
                    place, candidates, max_distance_m=max_distance_m
                )
            else:
                matches = rank_matches(place, direct_candidates(pois))

            stored = 0
            for match in matches[:3]:
                if match.score < CANDIDATE_SCORE and not _weak_eligible(match):
                    continue
                reason = match.reason
                if match.score < CANDIDATE_SCORE:
                    reason += "_weak"
                if match.distance_m is not None and match.distance_m > 150:
                    reason += "_far"
                if verbose:
                    log(
                        f"OSM: Netzwerk-Kandidat {place.name!r} ~ "
                        f"{match.poi.name!r} ({match.poi.osm_type}/"
                        f"{match.poi.osm_id}, {match.score:.2f}, {reason})"
                    )
                stored += 1
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
                    reason=reason,
                )
            if stored:
                summary.candidates += stored
            else:
                summary.none += 1
    finally:
        conn.close()
    return summary


def address_scan(
    paths: Paths,
    *,
    limit: int | None = None,
    max_distance_m: float = ADDRESS_RADIUS_M,
    force: bool = False,
    dry_run: bool = False,
    sleep: float = 1.1,
    verbose: bool = False,
    osm_db_path: Path | None = None,
    log=print,
) -> StrategySummary:
    """Find successor venues at the same address via Nominatim."""
    summary = StrategySummary()
    places = load_index_places(paths.index_db)
    summary.places = len(places)
    assignments = identity.load_osm_assignments(paths.place_identity_db)
    candidate_keys = identity.load_osm_candidate_keys(paths.place_identity_db)
    pending: list[IndexPlace] = []
    for place in places:
        if not place.members:
            continue
        if place.osm_type and place.osm_id is not None:
            continue
        if place.place_state != "open":
            continue
        if any(member in assignments for member in place.members):
            continue
        if any(member in candidate_keys for member in place.members):
            continue
        if looks_like_address(place.name):
            continue
        if not place.has_coords or not place.address:
            summary.skipped += 1
            continue
        pending.append(place)
    if limit is not None:
        pending = pending[: max(0, limit)]
    summary.pending = len(pending)
    if not pending:
        log("OSM: kein offener Venue mit Adresse für den Adress-Scan.")
        return summary

    db_path = osm_db_path or paths.osm_db
    conn = osm_db.open_db(db_path)
    nominatim = NominatimClient(sleep=sleep, verbose=verbose)
    try:
        for position, place in enumerate(pending, start=1):
            if position % 25 == 0:
                log(f"OSM: Adress-Scan {position}/{len(pending)}")
            query = _address_query(place)
            if not query:
                summary.skipped += 1
                continue
            key = _address_key(place)
            payload = None if force else osm_db.get_nominatim_cache(conn, key)
            if payload is None:
                try:
                    payload = nominatim.search_raw(query, limit=5)
                except NominatimError as exc:
                    summary.errors += 1
                    log(f"OSM: Adress-Scan übersprungen für {place.name!r} ({exc})")
                    continue
                summary.queries += 1
                osm_db.set_nominatim_cache(conn, key, payload)
                osm_db.save_pois(conn, parse_nominatim_results(payload))
            else:
                summary.cached_queries += 1

            matches: list[tuple[float, float, osm_db.Poi]] = []
            for poi in parse_nominatim_results(payload):
                distance = distance_between(place, poi)
                if distance is None or distance > max_distance_m:
                    continue
                if not _is_venue_poi(poi) or not _category_compatible(place, poi):
                    continue
                similarity = name_similarity(place.name, poi.name)
                matches.append((distance, similarity, poi))
            matches.sort(key=lambda item: (item[0], -item[1]))
            stored = 0
            for distance, similarity, poi in matches[:3]:
                if verbose:
                    log(
                        f"OSM: Adress-Kandidat {place.name!r} ~ {poi.name!r} "
                        f"({poi.osm_type}/{poi.osm_id}, {distance:.0f} m, "
                        f"Name {similarity:.2f})"
                    )
                stored += 1
                if dry_run or not place.members:
                    continue
                source, source_place_id = place.members[0]
                identity.upsert_osm_candidate(
                    paths.place_identity_db,
                    source,
                    source_place_id,
                    poi.osm_type,
                    poi.osm_id,
                    name=place.name,
                    city=place.city,
                    osm_name=poi.name,
                    distance_m=round(distance, 1),
                    score=_address_score(distance),
                    reason="address_match",
                )
            if stored:
                summary.candidates += stored
            else:
                summary.none += 1
    finally:
        conn.close()
    return summary


def contact_scan(
    paths: Paths,
    *,
    limit: int | None = None,
    radius: float = CONTACT_RADIUS_M,
    dry_run: bool = False,
    verbose: bool = False,
    osm_db_path: Path | None = None,
    log=print,
) -> StrategySummary:
    """Match venues via website, phone or e-mail against cached OSM POIs."""
    summary = StrategySummary()
    places = load_index_places(paths.index_db)
    summary.places = len(places)
    assignments = identity.load_osm_assignments(paths.place_identity_db)
    candidate_keys = identity.load_osm_candidate_keys(paths.place_identity_db)
    details = _load_contact_details(paths.index_db)
    pending: list[IndexPlace] = []
    for place in places:
        if not place.members:
            continue
        if place.osm_type and place.osm_id is not None:
            continue
        if place.place_state != "open":
            continue
        if any(member in assignments for member in place.members):
            continue
        if any(member in candidate_keys for member in place.members):
            continue
        if looks_like_address(place.name):
            continue
        if not place.has_coords:
            summary.skipped += 1
            continue
        infos = [details[member] for member in place.members if member in details]
        domains: set[str] = set()
        phones: set[str] = set()
        emails: set[str] = set()
        for info in infos:
            domains |= info["domains"]
            phones |= info["phones"]
            emails |= info["emails"]
        if not (domains or phones or emails):
            summary.skipped += 1
            continue
        pending.append(place)
    if limit is not None:
        pending = pending[: max(0, limit)]
    summary.pending = len(pending)
    if not pending:
        log("OSM: kein offener Venue mit Kontaktdaten für den Contact-Scan.")
        return summary

    db_path = osm_db_path or paths.osm_db
    conn = osm_db.open_db(db_path)
    try:
        for place in pending:
            infos = [details[member] for member in place.members if member in details]
            domains: set[str] = set()
            phones: set[str] = set()
            emails: set[str] = set()
            for info in infos:
                domains |= info["domains"]
                phones |= info["phones"]
                emails |= info["emails"]
            matches: list[tuple[float, osm_db.Poi, str]] = []
            for poi, distance in osm_db.pois_near(
                conn, place.lat, place.lng, radius
            ):
                if not _is_venue_poi(poi):
                    continue
                poi_domains, poi_phones, poi_emails = _contact_values(poi)
                reason = None
                if domains & poi_domains:
                    reason = "website_match"
                elif emails & poi_emails:
                    reason = "email_match"
                elif any(
                    _phones_match(left, right)
                    for left in phones
                    for right in poi_phones
                ):
                    reason = "phone_match"
                if reason:
                    matches.append((distance, poi, reason))
            matches.sort(key=lambda item: item[0])
            stored = 0
            for distance, poi, reason in matches[:2]:
                if verbose:
                    log(
                        f"OSM: Kontakt-Kandidat {place.name!r} ~ {poi.name!r} "
                        f"({poi.osm_type}/{poi.osm_id}, {distance:.0f} m, {reason})"
                    )
                stored += 1
                if dry_run or not place.members:
                    continue
                source, source_place_id = place.members[0]
                identity.upsert_osm_candidate(
                    paths.place_identity_db,
                    source,
                    source_place_id,
                    poi.osm_type,
                    poi.osm_id,
                    name=place.name,
                    city=place.city,
                    osm_name=poi.name,
                    distance_m=round(distance, 1),
                    score=_contact_score(distance),
                    reason=reason,
                )
            if stored:
                summary.candidates += stored
            else:
                summary.none += 1
    finally:
        conn.close()
    return summary


def mark_missing_coords(
    paths: Paths,
    *,
    dry_run: bool = False,
    log=print,
) -> int:
    """Mark open venues without coordinates as 'no_coords'."""
    places = load_index_places(paths.index_db)
    assignments = identity.load_osm_assignments(paths.place_identity_db)
    count = 0
    for place in places:
        if not place.members:
            continue
        if place.place_state != "open" or place.has_coords:
            continue
        if place.osm_type and place.osm_id is not None:
            continue
        if any(member in assignments for member in place.members):
            continue
        count += 1
        if dry_run:
            continue
        for source, source_place_id in place.members:
            identity.set_osm_state(
                paths.place_identity_db,
                source,
                source_place_id,
                "no_coords",
                reason="auto:no_coords",
            )
    if count:
        log(f"OSM: {count} Venues ohne Geodaten als 'no_coords' markiert.")
    return count


def deep_scan(
    paths: Paths,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    name_scan_enabled: bool = True,
    address_scan_enabled: bool = True,
    contact_scan_enabled: bool = True,
    mark_no_coords: bool = True,
    sleep: float = 1.1,
    verbose: bool = False,
    osm_db_path: Path | None = None,
    log=print,
) -> DeepScanSummary:
    summary = DeepScanSummary()
    summary.rematch = rematch(
        paths,
        limit=limit,
        dry_run=dry_run,
        verbose=verbose,
        osm_db_path=osm_db_path,
        log=log,
    )
    if name_scan_enabled:
        summary.name = net_scan(
            paths,
            limit=limit,
            dry_run=dry_run,
            sleep=sleep,
            verbose=verbose,
            osm_db_path=osm_db_path,
            log=log,
        )
    if address_scan_enabled:
        summary.address = address_scan(
            paths,
            limit=limit,
            dry_run=dry_run,
            sleep=sleep,
            verbose=verbose,
            osm_db_path=osm_db_path,
            log=log,
        )
    if contact_scan_enabled:
        summary.contacts = contact_scan(
            paths,
            limit=limit,
            dry_run=dry_run,
            verbose=verbose,
            osm_db_path=osm_db_path,
            log=log,
        )
    if mark_no_coords:
        summary.no_coords = mark_missing_coords(
            paths, dry_run=dry_run, log=log
        )
    return summary
