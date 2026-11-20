from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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


@dataclass
class Config:
    token: str
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    db_path: Path | None = None
    api_version: str = "20240101"
    base_url: str = "https://api.foursquare.com/v2"
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
            self.db_path = self.data_dir / "checkins.db"
        else:
            self.db_path = Path(self.db_path)
        self.photos_dir = self.data_dir / "photos"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.photos_dir.mkdir(parents=True, exist_ok=True)


def build_config(
    data_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    sleep: float | None = None,
    download_photos: bool = True,
    export: bool = True,
    verbose: bool = False,
) -> Config:
    load_env()
    token = os.environ.get("FOURSQUARE_OAUTH_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "FOURSQUARE_OAUTH_TOKEN fehlt. Bitte in .env eintragen "
            "oder zuerst den OAuth-Flow ausführen."
        )
    return Config(
        token=token,
        data_dir=Path(data_dir) if data_dir else ROOT / "data",
        db_path=Path(db_path) if db_path else None,
        sleep=0.3 if sleep is None else sleep,
        download_photos=download_photos,
        export=export,
        verbose=verbose,
    )
