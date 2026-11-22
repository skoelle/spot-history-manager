# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def load_env(path: Path | None = None) -> Path | None:
    env_path = Path(path) if path else ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)
    return env_path


def get_env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Paths:
    root: Path = ROOT
    data_root: Path = ROOT / "data"
    db_root: Path = ROOT / "db"

    @property
    def index_db(self) -> Path:
        return self.db_root / "index" / "index.db"

    @property
    def map_geojson(self) -> Path:
        return self.data_root / "exports" / "visits.geojson"

    @property
    def place_rules_db(self) -> Path:
        return self.db_root / "places" / "place_rules.db"

    @property
    def place_identity_db(self) -> Path:
        return self.db_root / "places" / "identity.db"

    @property
    def osm_db(self) -> Path:
        return self.db_root / "osm" / "osm.db"

    @property
    def backup_dir(self) -> Path:
        return _env_path("SPOT_HISTORY_BACKUP_ROOT") or self.data_root / "backup"

    def connector_dir(self, name: str) -> Path:
        return self.data_root / name

    def db_dir(self, name: str) -> Path:
        return self.db_root / name

    def connector_db(self, name: str, filename: str) -> Path:
        return self.db_dir(name) / filename


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


def connector_data_dir(
    name: str,
    explicit_dir: str | Path | None = None,
    env_name: str | None = None,
) -> tuple[Path, bool]:
    """Return the connector data directory and whether it was set explicitly."""
    if explicit_dir is not None:
        return Path(explicit_dir), True
    if env_name:
        env_dir = _env_path(env_name)
        if env_dir:
            return env_dir, True
    data_root = _env_path("SPOT_HISTORY_DATA_ROOT")
    if data_root:
        return data_root / name, True
    return ROOT / "data" / name, False


def db_root_path(
    data_dir: Path | None = None,
    data_dir_explicit: bool = True,
) -> Path:
    env_root = _env_path("SPOT_HISTORY_DB_ROOT")
    if env_root:
        return env_root
    if data_dir_explicit:
        return Path(data_dir) if data_dir else ROOT / "data"
    return ROOT / "db"


def connector_db_path(
    name: str,
    filename: str,
    data_dir: Path,
    data_dir_explicit: bool,
) -> Path:
    env_root = _env_path("SPOT_HISTORY_DB_ROOT")
    if env_root:
        return env_root / name / filename
    if data_dir_explicit:
        return Path(data_dir) / filename
    return ROOT / "db" / name / filename


def build_paths(
    data_root: str | Path | None = None,
    db_root: str | Path | None = None,
) -> Paths:
    if data_root:
        resolved_data = Path(data_root)
        data_explicit = True
    else:
        env_data = _env_path("SPOT_HISTORY_DATA_ROOT")
        resolved_data = env_data or ROOT / "data"
        data_explicit = env_data is not None
    if db_root:
        resolved_db = Path(db_root)
    else:
        resolved_db = _env_path("SPOT_HISTORY_DB_ROOT") or (
            resolved_data if data_explicit else ROOT / "db"
        )
    return Paths(root=ROOT, data_root=resolved_data, db_root=resolved_db)
