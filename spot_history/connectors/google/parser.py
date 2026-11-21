# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ParsedVisit:
    source_visit_id: str
    place_id: str
    start_at: int
    end_at: int | None
    start_iso: str
    end_iso: str | None
    tz_offset: int | None
    lat: float | None
    lng: float | None
    semantic_type: str | None
    probability: float | None
    place_name: str | None = None
    address: str | None = None
    city: str | None = None
    country_code: str | None = None
    category: str | None = None
    hierarchy_level: int | None = None


@dataclass
class ParsedRecord:
    record_id: str
    kind: str
    created_at: int | None
    payload: dict[str, Any]
    lat: float | None = None
    lng: float | None = None
    place_name: str | None = None
    source_place_id: str | None = None


@dataclass
class ParsedFile:
    path: Path
    format: str
    visits: list[ParsedVisit]
    records: list[ParsedRecord]


def stable_id(*parts: object) -> str:
    payload = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def iso_utc(timestamp: float) -> str:
    return datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat(
        timespec="seconds"
    )


def parse_iso(value: Any) -> tuple[int, int, str] | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 1e11:
            stamp /= 1000.0
        return int(stamp), 0, iso_utc(stamp)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        stamp = int(text)
        if stamp > 10**11:
            stamp /= 1000.0
        return int(stamp), 0, iso_utc(stamp)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    offset = int(parsed.utcoffset().total_seconds() // 60)
    return int(parsed.timestamp()), offset, parsed.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    )


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("text") or value.get("name")
    text = str(value).strip()
    return text or None


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def parse_coord(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        coordinates = value.get("coordinates")
        if isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
            lng_f, lat_f = _float(coordinates[0]), _float(coordinates[1])
            if lat_f is not None and lng_f is not None:
                return lat_f, lng_f
        for key in ("latLng", "placeLocation", "point", "location"):
            if key in value:
                parsed = parse_coord(value[key])
                if parsed is not None:
                    return parsed
        lat = _first(
            value.get("lat"), value.get("latitude"), value.get("Latitude")
        )
        lng = _first(
            value.get("lng"),
            value.get("lon"),
            value.get("longitude"),
            value.get("Longitude"),
        )
        lat_f, lng_f = _float(lat), _float(lng)
        if lat_f is not None and lng_f is not None:
            return lat_f, lng_f
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        lat_f, lng_f = _float(value[0]), _float(value[1])
        if lat_f is not None and lng_f is not None:
            return lat_f, lng_f
        return None
    text = str(value).strip()
    text = text.removeprefix("geo:")
    text = text.replace("°", "").replace(";", ",")
    parts = [part.strip() for part in text.split(",")]
    if len(parts) < 2:
        return None
    lat_f, lng_f = _float(parts[0]), _float(parts[1])
    if lat_f is None or lng_f is None:
        return None
    return lat_f, lng_f


def e7(value: Any, limit: int) -> float | None:
    number = _int(value)
    if number is None:
        return None
    if number > limit:
        number -= 2**32
    return number / 10_000_000.0


def legacy_coord(location: dict[str, Any]) -> tuple[float, float] | None:
    lat = e7(
        _first(
            location.get("latitudeE7"),
            location.get("centerLatE7"),
            location.get("latE7"),
            location.get("lat"),
        ),
        900_000_000,
    )
    lng = e7(
        _first(
            location.get("longitudeE7"),
            location.get("centerLngE7"),
            location.get("lngE7"),
            location.get("lng"),
            location.get("lon"),
        ),
        1_800_000_000,
    )
    if lat is None or lng is None:
        return parse_coord(location)
    return lat, lng


def geo_place_id(lat: float, lng: float) -> str:
    return f"geo:{lat:.5f}:{lng:.5f}"


def _visit_id(place_id: str, start_iso: str, end_iso: str | None) -> str:
    return stable_id("visit", place_id, start_iso, end_iso or "")


def _visit_from_candidate(
    *,
    place_id: str | None,
    coord: tuple[float, float] | None,
    start: tuple[int, int, str],
    end: tuple[int, int, str] | None,
    tz_offset: int | None,
    semantic_type: str | None,
    probability: float | None,
    place_name: str | None,
    address: str | None,
    city: str | None,
    country_code: str | None,
    category: str | None,
    hierarchy_level: int | None,
) -> ParsedVisit | None:
    if not place_id and coord is None:
        return None
    if not place_id and coord is not None:
        place_id = geo_place_id(*coord)
    assert place_id is not None
    if tz_offset is None:
        tz_offset = start[1]
    return ParsedVisit(
        source_visit_id=_visit_id(place_id, start[2], end[2] if end else None),
        place_id=place_id,
        start_at=start[0],
        end_at=end[0] if end else None,
        start_iso=start[2],
        end_iso=end[2] if end else None,
        tz_offset=tz_offset,
        lat=coord[0] if coord else None,
        lng=coord[1] if coord else None,
        semantic_type=semantic_type,
        probability=probability,
        place_name=place_name,
        address=address,
        city=city,
        country_code=country_code,
        category=category,
        hierarchy_level=hierarchy_level,
    )


def parse_timeline_segments(segments: list[Any]) -> list[ParsedVisit]:
    visits: list[ParsedVisit] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        visit = segment.get("visit")
        if not isinstance(visit, dict):
            continue
        candidate = visit.get("topCandidate") or {}
        if not isinstance(candidate, dict):
            candidate = {}
        place_id = _text(
            _first(candidate.get("placeID"), candidate.get("placeId"), visit.get("placeID"))
        )
        coord = parse_coord(candidate.get("placeLocation"))
        semantic_type = _text(
            _first(candidate.get("semanticType"), visit.get("semanticType"))
        )
        place_name = _text(_first(candidate.get("displayName"), candidate.get("name")))
        address = _text(candidate.get("address"))
        category = _text(candidate.get("category"))
        if coord is None or not place_id:
            for other in visit.get("otherCandidates") or []:
                if not isinstance(other, dict):
                    continue
                if coord is None:
                    coord = parse_coord(other.get("placeLocation"))
                if not place_id:
                    place_id = _text(_first(other.get("placeID"), other.get("placeId")))
                if place_name is None:
                    place_name = _text(_first(other.get("displayName"), other.get("name")))
                if address is None:
                    address = _text(other.get("address"))
                if coord is not None and place_id:
                    break
        start = parse_iso(segment.get("startTime"))
        if start is None:
            continue
        end = parse_iso(segment.get("endTime"))
        tz_offset = _int(segment.get("startTimeTimezoneUtcOffsetMinutes"))
        parsed = _visit_from_candidate(
            place_id=place_id,
            coord=coord,
            start=start,
            end=end,
            tz_offset=tz_offset,
            semantic_type=semantic_type,
            probability=_float(
                _first(candidate.get("probability"), visit.get("probability"))
            ),
            place_name=place_name,
            address=address,
            city=None,
            country_code=None,
            category=category,
            hierarchy_level=_int(visit.get("hierarchyLevel")),
        )
        if parsed is not None:
            visits.append(parsed)
    return visits


def parse_legacy_objects(objects: list[Any]) -> list[ParsedVisit]:
    visits: list[ParsedVisit] = []
    for item in objects:
        if not isinstance(item, dict):
            continue
        place_visit = item.get("placeVisit")
        if not isinstance(place_visit, dict):
            continue
        location = place_visit.get("location") or {}
        if not isinstance(location, dict):
            location = {}
        duration = place_visit.get("duration") or {}
        if not isinstance(duration, dict):
            duration = {}
        start = parse_iso(
            _first(duration.get("startTimestamp"), duration.get("startTimestampMs"))
        )
        if start is None:
            continue
        end = parse_iso(
            _first(duration.get("endTimestamp"), duration.get("endTimestampMs"))
        )
        coord = legacy_coord(location)
        place_id = _text(_first(location.get("placeId"), location.get("placeID")))
        place_name = _text(location.get("name"))
        address = _text(location.get("address"))
        semantic_type = _text(location.get("semanticType"))
        category = _text(location.get("category"))
        if coord is None or not place_id:
            for other in place_visit.get("otherCandidateLocations") or []:
                if not isinstance(other, dict):
                    continue
                if coord is None:
                    coord = legacy_coord(other)
                if not place_id:
                    place_id = _text(_first(other.get("placeId"), other.get("placeID")))
                if place_name is None:
                    place_name = _text(other.get("name"))
                if address is None:
                    address = _text(other.get("address"))
                if coord is not None and place_id:
                    break
        parsed = _visit_from_candidate(
            place_id=place_id,
            coord=coord,
            start=start,
            end=end,
            tz_offset=None,
            semantic_type=semantic_type,
            probability=None,
            place_name=place_name,
            address=address,
            city=_text(location.get("city")),
            country_code=_text(location.get("country_code")),
            category=category,
            hierarchy_level=None,
        )
        if parsed is not None:
            visits.append(parsed)
    return visits


def _geojson_kind(props: dict[str, Any], filename: str) -> str:
    if (
        "Star Rating" in props
        or "Review Comment" in props
        or "Bewertung" in filename
    ):
        return "review"
    return "saved_place"


def parse_geojson(payload: dict[str, Any], filename: str) -> list[ParsedRecord]:
    records: list[ParsedRecord] = []
    for feature in payload.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        if not isinstance(props, dict):
            props = {}
        kind = _geojson_kind(props, filename)
        location = props.get("Location") or {}
        if not isinstance(location, dict):
            location = {}
        geo = location.get("Geo Coordinates") or {}
        if not isinstance(geo, dict):
            geo = {}
        coord = parse_coord(feature.get("geometry")) or parse_coord(geo)
        name = _text(
            _first(
                location.get("Business Name"),
                props.get("Title"),
                props.get("Name"),
            )
        )
        address = _text(location.get("Address")) or _text(props.get("Address"))
        country_code = _text(location.get("Country Code"))
        url = _text(props.get("Google Maps URL"))
        published = parse_iso(_first(props.get("Published"), props.get("Updated")))
        rating = _float(_first(props.get("Star Rating"), props.get("Rating")))
        comment = _text(_first(props.get("Review Comment"), props.get("Comment")))
        if not name and not url:
            continue
        payload_data = {
            "name": name,
            "address": address,
            "country_code": country_code,
            "url": url,
            "rating": rating,
            "comment": comment,
        }
        records.append(
            ParsedRecord(
                record_id=stable_id(
                    kind,
                    url or "",
                    name or "",
                    address or "",
                    published[2] if published else "",
                ),
                kind=kind,
                created_at=published[0] if published else None,
                payload={key: value for key, value in payload_data.items() if value is not None},
                lat=coord[0] if coord else None,
                lng=coord[1] if coord else None,
                place_name=name,
            )
        )
    return records


def detect_format(payload: Any) -> str:
    if isinstance(payload, list):
        for item in payload[:5]:
            if isinstance(item, dict) and (
                "startTime" in item
                or "visit" in item
                or "activity" in item
                or "timelinePath" in item
            ):
                return "timeline"
        return "unknown"
    if isinstance(payload, dict):
        if "semanticSegments" in payload:
            return "timeline"
        if "timelineObjects" in payload:
            return "legacy"
        if "locations" in payload:
            return "records"
        if payload.get("type") == "FeatureCollection":
            return "geojson"
    return "unknown"


def parse_payload(payload: Any, filename: str) -> tuple[str, list[ParsedVisit], list[ParsedRecord]]:
    fmt = detect_format(payload)
    if fmt == "timeline":
        segments = payload if isinstance(payload, list) else payload.get("semanticSegments") or []
        return fmt, parse_timeline_segments(segments), []
    if fmt == "legacy":
        return fmt, parse_legacy_objects(payload.get("timelineObjects") or []), []
    if fmt == "geojson":
        return fmt, [], parse_geojson(payload, filename)
    return fmt, [], []


def load_file(path: Path) -> ParsedFile:
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    fmt, visits, records = parse_payload(payload, path.name)
    return ParsedFile(path=path, format=fmt, visits=visits, records=records)


def iter_files(input_path: str | Path) -> list[Path]:
    path = Path(input_path)
    if path.is_file():
        return [path]
    if not path.exists():
        return []
    files: list[Path] = []
    for candidate in sorted(path.rglob("*.json")):
        if any(part.startswith(("@", ".")) for part in candidate.parts):
            continue
        if candidate.is_file():
            files.append(candidate)
    return files
