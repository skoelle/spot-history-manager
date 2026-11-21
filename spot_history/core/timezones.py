# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .places import normalize_name

COUNTRY_TIMEZONES = {
    "AT": "Europe/Vienna",
    "BE": "Europe/Brussels",
    "CH": "Europe/Zurich",
    "CZ": "Europe/Prague",
    "DE": "Europe/Berlin",
    "DK": "Europe/Copenhagen",
    "EE": "Europe/Tallinn",
    "ES": "Europe/Madrid",
    "FI": "Europe/Helsinki",
    "FR": "Europe/Paris",
    "GB": "Europe/London",
    "GR": "Europe/Athens",
    "HR": "Europe/Zagreb",
    "HU": "Europe/Budapest",
    "IE": "Europe/Dublin",
    "IS": "Atlantic/Reykjavik",
    "IT": "Europe/Rome",
    "LT": "Europe/Vilnius",
    "LU": "Europe/Luxembourg",
    "LV": "Europe/Riga",
    "NL": "Europe/Amsterdam",
    "NO": "Europe/Oslo",
    "PK": "Asia/Karachi",
    "PL": "Europe/Warsaw",
    "PT": "Europe/Lisbon",
    "SE": "Europe/Stockholm",
    "SI": "Europe/Ljubljana",
    "SK": "Europe/Bratislava",
    "TR": "Europe/Istanbul",
}

CITY_TIMEZONES = {
    "alta": "Europe/Oslo",
    "aschheim": "Europe/Berlin",
    "augsburg": "Europe/Berlin",
    "aying": "Europe/Berlin",
    "bodo": "Europe/Oslo",
    "daly city": "America/Los_Angeles",
    "dasing": "Europe/Berlin",
    "glonn": "Europe/Berlin",
    "hammerfest": "Europe/Oslo",
    "ingolstadt": "Europe/Berlin",
    "istanbul": "Europe/Istanbul",
    "jonkoping": "Europe/Stockholm",
    "kirchheim bei muenchen": "Europe/Berlin",
    "leipzig": "Europe/Berlin",
    "muenchen": "Europe/Berlin",
    "narvik": "Europe/Oslo",
    "neubiberg": "Europe/Berlin",
    "neusaess": "Europe/Berlin",
    "new york": "America/New_York",
    "nordkapp": "Europe/Oslo",
    "oberschleissheim": "Europe/Berlin",
    "pullach im isartal": "Europe/Berlin",
    "samerberg": "Europe/Berlin",
    "san francisco": "America/Los_Angeles",
    "sel": "Europe/Oslo",
    "strasslach-dingharting": "Europe/Berlin",
    "stuttgart": "Europe/Berlin",
    "trondheim": "Europe/Oslo",
    "ulm": "Europe/Berlin",
    "unterfoehring": "Europe/Berlin",
    "zuerich": "Europe/Zurich",
}

US_STATE_TIMEZONES = {
    "az": "America/Phoenix",
    "ca": "America/Los_Angeles",
    "co": "America/Denver",
    "dc": "America/New_York",
    "fl": "America/New_York",
    "il": "America/Chicago",
    "ma": "America/New_York",
    "nv": "America/Los_Angeles",
    "nj": "America/New_York",
    "ny": "America/New_York",
    "or": "America/Los_Angeles",
    "pa": "America/New_York",
    "tx": "America/Chicago",
    "wa": "America/Los_Angeles",
}

SAME_PLACE_WINDOW_SECONDS = 30 * 60
MAJORITY_WINDOW_SECONDS = 30 * 24 * 60 * 60
NEAREST_CHECKIN_WINDOW_SECONDS = 60 * 24 * 60 * 60


def resolve_timezone(
    city: str | None,
    state: str | None,
    country_code: str | None,
) -> str | None:
    city_key = normalize_name(city)
    state_key = normalize_name(state)
    country = (country_code or "").strip().upper()

    if city_key in CITY_TIMEZONES:
        return CITY_TIMEZONES[city_key]
    if country == "US" and state_key in US_STATE_TIMEZONES:
        return US_STATE_TIMEZONES[state_key]
    if country in COUNTRY_TIMEZONES:
        return COUNTRY_TIMEZONES[country]
    return None


def offset_for_timestamp(timestamp: int, time_zone: str | None) -> int | None:
    if not time_zone:
        return None
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    moment = datetime.fromtimestamp(int(timestamp), timezone.utc).astimezone(zone)
    offset = moment.utcoffset()
    if offset is None:
        return None
    return int(offset.total_seconds() // 60)


def _visit_key(visit: Any) -> tuple[str, str] | None:
    if not getattr(visit, "source_place_id", None):
        return None
    return (visit.source, visit.source_place_id)


def apply_timezone_offsets(
    visits: list[Any],
    canonical_ids: dict[tuple[str, str], int],
    canonical_by_id: dict[int, Any] | None = None,
    log=None,
) -> dict[str, int]:
    canonical_by_id = canonical_by_id or {}
    fsq_by_canonical: dict[int, list[tuple[int, Any]]] = defaultdict(list)
    fsq_all: list[tuple[int, Any]] = []
    for visit in visits:
        if getattr(visit, "source", None) != "foursquare":
            continue
        fsq_all.append((visit.visited_at, visit))
        key = _visit_key(visit)
        canonical_id = canonical_ids.get(key) if key else None
        if canonical_id is not None:
            fsq_by_canonical[canonical_id].append((visit.visited_at, visit))

    for bucket in fsq_by_canonical.values():
        bucket.sort(key=lambda item: item[0])
    fsq_all.sort(key=lambda item: item[0])

    counts = {
        "same_place": 0,
        "venue_timezone": 0,
        "city_timezone": 0,
        "nearby_majority": 0,
        "nearest_fsq": 0,
        "unresolved": 0,
        "known": 0,
    }

    for visit in visits:
        if visit.tz_offset is not None:
            counts["known"] += 1
            continue

        key = _visit_key(visit)
        canonical_id = canonical_ids.get(key) if key else None
        offset = None
        method = None

        if canonical_id is not None and fsq_by_canonical.get(canonical_id):
            closest = min(
                fsq_by_canonical[canonical_id],
                key=lambda item: abs(item[0] - visit.visited_at),
            )
            difference = abs(closest[0] - visit.visited_at)
            if difference <= SAME_PLACE_WINDOW_SECONDS and closest[1].tz_offset is not None:
                offset = closest[1].tz_offset
                method = "same_place"
            else:
                offset = offset_for_timestamp(
                    visit.visited_at, closest[1].time_zone
                )
                if offset is not None:
                    method = "venue_timezone"

        if offset is None:
            canonical = canonical_by_id.get(canonical_id) if canonical_id else None
            city = canonical.city if canonical else visit.city
            state = canonical.state if canonical else visit.state
            country_code = (
                canonical.country_code if canonical else visit.country_code
            )
            time_zone = resolve_timezone(city, state, country_code)
            offset = offset_for_timestamp(visit.visited_at, time_zone)
            if offset is not None:
                method = "city_timezone"

        if offset is None and fsq_all:
            nearby = [
                item
                for item in fsq_all
                if abs(item[0] - visit.visited_at) <= MAJORITY_WINDOW_SECONDS
                and item[1].time_zone
            ]
            if nearby:
                votes = Counter(item[1].time_zone for item in nearby)
                time_zone = votes.most_common(1)[0][0]
                offset = offset_for_timestamp(visit.visited_at, time_zone)
                if offset is not None:
                    method = "nearby_majority"

        if offset is None and fsq_all:
            nearest = min(
                fsq_all, key=lambda item: abs(item[0] - visit.visited_at)
            )
            if abs(nearest[0] - visit.visited_at) <= NEAREST_CHECKIN_WINDOW_SECONDS:
                offset = offset_for_timestamp(
                    visit.visited_at, nearest[1].time_zone
                )
                if offset is None:
                    offset = nearest[1].tz_offset
                if offset is not None:
                    method = "nearest_fsq"

        if offset is None:
            counts["unresolved"] += 1
            continue

        visit.tz_offset = offset
        counts[method] += 1

    if log:
        log(
            f"Zeitzonen: {counts['same_place']} gleicher Ort, "
            f"{counts['venue_timezone']} Orts-Zeitzone, "
            f"{counts['city_timezone']} Stadt/Land, "
            f"{counts['nearby_majority']} Umfeld-Mehrheit, "
            f"{counts['nearest_fsq']} naher Foursquare-Check-in, "
            f"{counts['unresolved']} ungelöst"
        )
    return counts
