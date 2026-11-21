# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Photo:
    source_photo_id: str
    url: str | None = None
    local_path: str | None = None
    taken_at: int | None = None


@dataclass
class Visit:
    source: str
    source_visit_id: str
    visited_at: int
    lat: float | None = None
    lng: float | None = None
    place_name: str | None = None
    source_place_id: str | None = None
    category: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    note: str | None = None
    source_url: str | None = None
    place_url: str | None = None
    tz_offset: int | None = None
    time_zone: str | None = None
    photos: list[Photo] = field(default_factory=list)
    extra: dict[str, Any] | None = None


class BaseConnector:
    name: str = ""
    kind: str = "sync"

    def add_parser(self, subparsers: Any) -> None:
        raise NotImplementedError

    def run(self, args: argparse.Namespace) -> int:
        raise NotImplementedError

    def iter_visits(self) -> Iterable[Visit]:
        return []

    def iter_records(self) -> Iterable[dict[str, Any]]:
        """Optional: detail or activity records for the index/web UI.

        Each record may contain ``source``, ``record_id``, ``kind``,
        ``source_place_id``, ``created_at`` and ``payload``.
        """
        return []
