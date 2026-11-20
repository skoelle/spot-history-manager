from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import Paths
from .media import IMAGE_EXTENSIONS, is_video, thumb_relpath


def generate_thumbnails(
    paths: Paths,
    size: int = 320,
    force: bool = False,
    limit: int | None = None,
    log=print,
) -> dict:
    from PIL import Image, ImageOps

    if not paths.index_db.exists():
        raise SystemExit(f"Index fehlt: {paths.index_db}")
    conn = sqlite3.connect(f"file:{paths.index_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT source, source_photo_id, local_path FROM photos "
            "WHERE local_path IS NOT NULL AND local_path != '' "
            "ORDER BY source, source_photo_id"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()

    generated = skipped = videos = missing = 0
    errors: list[str] = []
    for index, row in enumerate(rows, 1):
        source = row["source"]
        photo_id = row["source_photo_id"]
        local_path = row["local_path"]
        source_path = paths.root / local_path
        if not source_path.is_file():
            missing += 1
            errors.append(f"fehlt: {local_path}")
            continue
        if is_video(source_path):
            videos += 1
            continue
        if source_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        destination = paths.root / thumb_relpath(source, photo_id)
        if destination.exists() and not force:
            skipped += 1
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source_path) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((size, size), Image.Resampling.LANCZOS)
                if image.mode not in ("RGB", "L"):
                    background = Image.new("RGB", image.size, (255, 255, 255))
                    if image.mode in ("RGBA", "LA", "P"):
                        background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
                    else:
                        background.paste(image.convert("RGB"))
                    image = background
                elif image.mode == "L":
                    image = image.convert("RGB")
                image.save(destination, "JPEG", quality=82, optimize=True, progressive=True)
            generated += 1
        except Exception as exc:
            errors.append(f"{local_path}: {exc}")
        if index % 50 == 0 or index == len(rows):
            log(
                f"Thumbnails: {index}/{len(rows)} verarbeitet, "
                f"{generated} neu, {skipped} vorhanden, {videos} Videos"
            )
    log(
        f"Thumbnails fertig: {generated} neu, {skipped} vorhanden, "
        f"{videos} Videos, {missing} fehlend, {len(errors)} Fehler"
    )
    for error in errors[:10]:
        log(f"  {error}")
    return {
        "total": len(rows),
        "generated": generated,
        "skipped": skipped,
        "videos": videos,
        "missing": missing,
        "errors": errors,
    }
