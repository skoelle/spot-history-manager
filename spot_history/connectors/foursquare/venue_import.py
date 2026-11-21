# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import Database, iso_now


def load_venue_details(path: str | Path, log=print) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        raise SystemExit(f"Datei nicht gefunden: {source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("venues") or payload.get("items") or []
    items: dict[str, dict[str, Any]] = {}
    skipped = 0
    for entry in payload:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        if entry.get("status") not in (None, 200):
            skipped += 1
            continue
        venue_id = entry.get("id") or entry.get("venue_id")
        if not venue_id:
            skipped += 1
            continue
        items[venue_id] = entry
    if skipped:
        log(f"Import: {skipped} Einträge ohne Status 200 übersprungen")
    return list(items.values())


def import_venue_details(
    db: Database,
    path: str | Path,
    log=print,
    fetched_at: str | None = None,
) -> int:
    source = Path(path)
    items = load_venue_details(source, log=log)
    if not items:
        log(f"Import: keine Venue-Details in {source}")
        return 0
    stored = db.upsert_venue_details(items)
    stamp = fetched_at or iso_now()
    db.set_state("venue_details_imported_at", stamp)
    db.set_state("venue_details_source_file", str(source))
    db.set_diagnostic(
        "venue_details", None, stored, f"Places-API-Baseline ({source.name})"
    )
    log(f"Import: {stored} Venue-Details aus {source.name}")
    return stored
