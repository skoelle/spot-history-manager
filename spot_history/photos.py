from __future__ import annotations

import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import Config
from .db import Database, photo_url


def _extension(row) -> str:
    suffix = row["suffix"] or ""
    ext = Path(suffix).suffix
    if not ext or len(ext) > 6:
        return ".jpg"
    return ext


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


def download_pending(db: Database, cfg: Config, log=print, sleep: float = 0.05) -> int:
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
        dest = cfg.photos_dir / row["checkin_id"] / f"{row['id']}{_extension(row)}"
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
