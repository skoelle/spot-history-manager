# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ...core.config import (
    connector_data_dir,
    connector_db_path,
    load_env,
)


@dataclass
class FoursquareConfig:
    token: str
    data_dir: Path
    db_path: Path | None = None
    api_version: str = "20240101"
    base_url: str = "https://api.foursquare.com/v2"
    service_api_key: str = ""
    places_base_url: str = "https://places-api.foursquare.com"
    places_api_version: str = "2025-06-17"
    page_limit: int = 250
    sleep: float = 0.3
    timeout: float = 60.0
    retries: int = 4
    download_photos: bool = True
    export: bool = True
    verbose: bool = False

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if self.db_path is None:
            self.db_path = self.data_dir / "foursquare.db"
        else:
            self.db_path = Path(self.db_path)
        self.photos_dir = self.data_dir / "photos"
        self.exports_dir = self.data_dir / "exports"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)


def build_config(
    data_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    sleep: float | None = None,
    download_photos: bool = True,
    export: bool = True,
    verbose: bool = False,
    require_token: bool = True,
) -> FoursquareConfig:
    load_env()
    token = os.environ.get("FOURSQUARE_OAUTH_TOKEN", "").strip()
    if require_token and not token:
        raise SystemExit(
            "FOURSQUARE_OAUTH_TOKEN fehlt. Bitte in .env eintragen "
            "oder zuerst den OAuth-Flow ausführen."
        )
    resolved_data_dir, data_explicit = connector_data_dir("foursquare", data_dir)
    resolved_db_path = (
        Path(db_path)
        if db_path
        else connector_db_path(
            "foursquare",
            "foursquare.db",
            resolved_data_dir,
            data_explicit,
        )
    )
    return FoursquareConfig(
        token=token,
        data_dir=resolved_data_dir,
        db_path=resolved_db_path,
        service_api_key=os.environ.get("FOURSQUARE_SERVICE_API_KEY", "").strip(),
        sleep=0.3 if sleep is None else sleep,
        download_photos=download_photos,
        export=export,
        verbose=verbose,
    )
