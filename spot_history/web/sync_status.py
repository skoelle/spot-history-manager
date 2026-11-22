# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class RunInfo:
    key: str
    label: str
    raw: str | None = None
    display: str | None = None


def format_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%d.%m.%Y %H:%M")


def _query(db_path: Path, sql: str, params: tuple = ()) -> str | None:
    if not db_path.exists():
        return None
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None
    finally:
        if conn is not None:
            conn.close()
    if not row or row[0] is None:
        return None
    return str(row[0])


def _foursquare_last(data_root: Path) -> str | None:
    return _query(
        data_root / "foursquare" / "foursquare.db",
        "SELECT value FROM sync_state WHERE key = 'last_sync_at'",
    )


def _yelp_last(data_root: Path) -> str | None:
    db_path = data_root / "yelp" / "yelp.db"
    value = _query(
        db_path,
        "SELECT value FROM import_meta WHERE key = 'last_import_at'",
    )
    if value:
        return value
    return _query(
        db_path,
        "SELECT finished_at FROM import_runs "
        "WHERE finished_at IS NOT NULL AND finished_at != '' "
        "ORDER BY id DESC LIMIT 1",
    )


def _google_last(data_root: Path) -> str | None:
    return _query(
        data_root / "google" / "google.db",
        "SELECT value FROM meta WHERE key = 'last_import_at'",
    )


def _osm_last(data_root: Path) -> str | None:
    return _query(
        data_root / "osm" / "osm.db",
        "SELECT COALESCE(NULLIF(finished_at, ''), started_at) FROM sync_runs "
        "ORDER BY id DESC LIMIT 1",
    )


def _index_last(data_root: Path) -> str | None:
    return _query(
        data_root / "index" / "index.db",
        "SELECT value FROM index_meta WHERE key = 'built_at'",
    )


def source_runs(data_root: Path) -> dict[str, RunInfo]:
    root = Path(data_root)
    entries = (
        ("foursquare", "Letzter Sync", _foursquare_last(root)),
        ("yelp", "Letzter Import", _yelp_last(root)),
        ("google", "Letzter Import", _google_last(root)),
        ("osm", "Letzter Sync", _osm_last(root)),
        ("index", "Index gebaut", _index_last(root)),
    )
    return {
        key: RunInfo(
            key=key,
            label=label,
            raw=raw,
            display=format_timestamp(raw),
        )
        for key, label, raw in entries
    }
