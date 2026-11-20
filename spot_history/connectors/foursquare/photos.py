from __future__ import annotations

import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from ...core.config import ROOT
from .config import FoursquareConfig
from .db import Database, photo_url


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def profile_photo_url(photo: dict) -> str:
    prefix = photo.get("prefix") or ""
    suffix = photo.get("suffix") or ""
    if not prefix or not suffix:
        return ""
    return f"{prefix}300x300{suffix}"


def download_profile_photo(
    cfg: FoursquareConfig, user: dict, log=print
) -> str | None:
    photo = user.get("photo") or {}
    url = profile_photo_url(photo)
    if not url:
        return None
    user_id = str(user.get("id") or "self")
    suffix = Path(photo.get("suffix") or "").suffix
    extension = suffix if suffix and len(suffix) <= 6 else ".jpg"
    dest = cfg.data_dir / "profile" / f"{user_id}{extension}"
    if dest.exists() and dest.stat().st_size > 0:
        return _relative_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _fetch(url, dest, cfg.timeout)
    except Exception as exc:
        log(f"Profilbild: Download fehlgeschlagen ({exc})")
        return None
    return _relative_path(dest)


def _extension(row) -> str:
    suffix = row["suffix"] or ""
    ext = Path(suffix).suffix
    if not ext or len(ext) > 6:
        return ".jpg"
    return ext


def photo_destination(cfg: FoursquareConfig, row) -> Path:
    checkin_id = row["checkin_id"] or "_standalone"
    return cfg.photos_dir / str(checkin_id) / f"{row['id']}{_extension(row)}"


def _fetch(url: str, dest: Path, timeout: float, retries: int = 3) -> int:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        tmp = dest.with_name(dest.name + ".part")
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "spot-history-manager/1.0",
                    "Accept": "image/*",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                with tmp.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
            tmp.replace(dest)
            return dest.stat().st_size
        except urllib.error.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            if exc.code in (404, 410):
                raise
            last = exc
            if exc.code == 429 or 500 <= exc.code < 600:
                time.sleep(min(2 ** attempt, 20))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            tmp.unlink(missing_ok=True)
            last = exc
            time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"Download fehlgeschlagen: {last}")


def download_pending(db: Database, cfg: FoursquareConfig, log=print, sleep: float = 0.05) -> int:
    rows = db.pending_photos()
    total = len(rows)
    if total == 0:
        log("Fotos: keine offenen Downloads")
        return 0

    free = shutil.disk_usage(cfg.data_dir).free
    log(f"Fotos: {total} offen, freier Platz {free / 1024 ** 3:.1f} GiB")

    done = 0
    failed = 0
    downloaded_bytes = 0
    for index, row in enumerate(rows, 1):
        url = row["url"] or photo_url(dict(row))
        dest = photo_destination(cfg, row)
        if dest.exists() and dest.stat().st_size > 0:
            db.set_photo_path(row["id"], str(dest.relative_to(cfg.data_dir)))
            done += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = _fetch(url, dest, cfg.timeout)
            db.set_photo_path(row["id"], str(dest.relative_to(cfg.data_dir)))
            downloaded_bytes += size
            done += 1
        except Exception as exc:
            failed += 1
            log(f"Foto {index}/{total} fehlgeschlagen ({row['id']}): {exc}")
        if index % 25 == 0 or index == total:
            log(
                f"Fotos: {index}/{total} verarbeitet, {done} ok, {failed} Fehler, "
                f"{downloaded_bytes / 1024 ** 2:.1f} MiB geladen"
            )
        time.sleep(sleep)
    return done
