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

    @property
    def index_db(self) -> Path:
        return self.data_root / "index" / "index.db"

    @property
    def map_geojson(self) -> Path:
        return self.data_root / "exports" / "visits.geojson"

    def connector_dir(self, name: str) -> Path:
        return self.data_root / name


def build_paths(data_root: str | Path | None = None) -> Paths:
    root = Path(data_root) if data_root else ROOT / "data"
    return Paths(root=ROOT, data_root=root)
