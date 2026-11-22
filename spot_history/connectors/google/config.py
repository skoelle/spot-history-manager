# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ...core.config import (
    ROOT,
    connector_data_dir,
    connector_db_path,
    db_root_path,
    load_env,
)

DEFAULT_INBOX = ROOT / "data" / "google" / "inbox"


@dataclass(frozen=True)
class GoogleConfig:
    inbox: Path
    data_dir: Path
    db_path: Path
    osm_cache_path: Path
    osm_db_path: Path

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def source_name(self) -> str:
        return "google"


def build_config(
    inbox: str | Path | None = None,
    data_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    osm_cache_path: str | Path | None = None,
    osm_db_path: str | Path | None = None,
) -> GoogleConfig:
    load_env()
    env_inbox = os.environ.get("GOOGLE_TIMELINE_INBOX", "").strip()
    if inbox is None:
        inbox = Path(env_inbox) if env_inbox else DEFAULT_INBOX
    data_path, data_explicit = connector_data_dir(
        "google", data_dir, "GOOGLE_DATA_DIR"
    )
    resolved_db_path = (
        Path(db_path)
        if db_path
        else connector_db_path("google", "google.db", data_path, data_explicit)
    )
    resolved_cache_path = (
        Path(osm_cache_path)
        if osm_cache_path
        else connector_db_path(
            "google", "osm_cache.db", data_path, data_explicit
        )
    )
    resolved_osm_db_path = (
        Path(osm_db_path)
        if osm_db_path
        else db_root_path(data_path.parent, data_explicit) / "osm" / "osm.db"
    )
    return GoogleConfig(
        inbox=Path(inbox),
        data_dir=data_path,
        db_path=resolved_db_path,
        osm_cache_path=resolved_cache_path,
        osm_db_path=resolved_osm_db_path,
    )
