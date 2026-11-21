# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ...core.config import ROOT, load_env

DEFAULT_INBOX = ROOT / "data" / "google" / "inbox"


@dataclass(frozen=True)
class GoogleConfig:
    inbox: Path
    data_dir: Path
    db_path: Path
    osm_cache_path: Path

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
) -> GoogleConfig:
    load_env()
    env_inbox = os.environ.get("GOOGLE_TIMELINE_INBOX", "").strip()
    env_data = os.environ.get("GOOGLE_DATA_DIR", "").strip()
    if inbox is None:
        inbox = Path(env_inbox) if env_inbox else DEFAULT_INBOX
    if data_dir is None:
        data_dir = Path(env_data) if env_data else ROOT / "data" / "google"
    data_path = Path(data_dir)
    return GoogleConfig(
        inbox=Path(inbox),
        data_dir=data_path,
        db_path=Path(db_path) if db_path else data_path / "google.db",
        osm_cache_path=(
            Path(osm_cache_path)
            if osm_cache_path
            else data_path / "osm_cache.db"
        ),
    )
