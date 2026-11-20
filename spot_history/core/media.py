from __future__ import annotations

import hashlib
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov"}
THUMB_SIZE = 320


def is_video(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def thumb_relpath(source: str, source_photo_id: str) -> str:
    digest = hashlib.sha1(f"{source}\x1f{source_photo_id}".encode("utf-8")).hexdigest()[:20]
    safe_source = "".join(ch for ch in source.lower() if ch.isalnum() or ch in "-_") or "source"
    return f"data/{safe_source}/thumbs/{digest}.jpg"
