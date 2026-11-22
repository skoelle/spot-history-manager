# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ...core.config import ROOT, connector_data_dir, connector_db_path

DEFAULT_EXPORT_DIR = ROOT / "data" / "yelp" / "export"


@dataclass
class YelpConfig:
    export_dir: Path
    data_dir: Path
    db_path: Path | None = None
    copy_media: bool = True

    def __post_init__(self) -> None:
        self.export_dir = Path(self.export_dir)
        self.data_dir = Path(self.data_dir)
        if self.db_path is None:
            self.db_path = self.data_dir / "yelp.db"
        else:
            self.db_path = Path(self.db_path)
        self.html_dir = self.export_dir / "html"
        self.media_source = self.export_dir / "media"
        self.media_dir = self.data_dir / "media"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)


def build_config(
    export_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    copy_media: bool = True,
) -> YelpConfig:
    env_export = os.environ.get("YELP_EXPORT_DIR", "").strip()
    if export_dir is None:
        export_dir = Path(env_export) if env_export else DEFAULT_EXPORT_DIR
    resolved_data_dir, data_explicit = connector_data_dir(
        "yelp", data_dir, "YELP_DATA_DIR"
    )
    resolved_db_path = (
        Path(db_path)
        if db_path
        else connector_db_path(
            "yelp",
            "yelp.db",
            resolved_data_dir,
            data_explicit,
        )
    )
    return YelpConfig(
        export_dir=Path(export_dir),
        data_dir=resolved_data_dir,
        db_path=resolved_db_path,
        copy_media=copy_media,
    )
