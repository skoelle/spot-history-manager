# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import hashlib
import unicodedata
from datetime import datetime, timezone


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(text.split())


def stable_id(*parts: object) -> str:
    payload = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def visit_id(name: str | None, iso_timestamp: str, lat: float | None, lng: float | None) -> str:
    lat_part = f"{lat:.5f}" if lat is not None else ""
    lng_part = f"{lng:.5f}" if lng is not None else ""
    return stable_id("checkin", normalize_name(name), iso_timestamp, lat_part, lng_part)


def place_key(name: str | None) -> str:
    return stable_id("place", normalize_name(name))


def parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_iso8601(value: str | None) -> tuple[int, int, str] | None:
    """Return (unix timestamp, offset minutes, canonical ISO string) or None."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    offset = int(parsed.utcoffset().total_seconds() // 60)
    return int(parsed.timestamp()), offset, parsed.isoformat(timespec="seconds")


def iso_utc(timestamp: int | float) -> str:
    return datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat(timespec="seconds")
