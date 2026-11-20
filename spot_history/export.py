from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .db import Database, iso_utc

CHECKIN_FIELDS = [
    "id", "created_at", "created_at_utc", "local_datetime", "year", "month",
    "day_of_week", "venue_id", "venue_name", "category", "category_short",
    "category_code", "address", "cross_street", "city", "state", "postal_code",
    "country", "country_code", "neighborhood", "lat", "lng", "shout", "type",
    "visibility", "source_name", "is_mayor", "likes_count", "comments_count",
    "photos_count", "canonical_url",
]

PHOTO_FIELDS = [
    "id", "checkin_id", "created_at", "created_at_utc", "width", "height",
    "visibility", "url", "downloaded_path", "canonical_url", "user_id",
    "user_display_name",
]

COMMENT_FIELDS = [
    "id", "checkin_id", "created_at", "created_at_utc", "user_id",
    "user_display_name", "text",
]

LIKE_FIELDS = [
    "checkin_id", "user_id", "display_name", "handle", "city", "state",
    "country_code", "is_service_user",
]


def _write_json(path: Path, payload) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    tmp.replace(path)


def _write_csv(path: Path, fields: list[str], rows: list[list]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        writer.writerows(rows)
    tmp.replace(path)


def _date_parts(row) -> tuple[int | None, int | None, str | None]:
    local = row["local_datetime"]
    if local:
        try:
            moment = datetime.fromisoformat(local)
            return moment.year, moment.month, moment.strftime("%A")
        except ValueError:
            pass
    if row["created_at"]:
        moment = datetime.fromtimestamp(int(row["created_at"]), timezone.utc)
        return moment.year, moment.month, moment.strftime("%A")
    return None, None, None


def export_all(db: Database, cfg: Config, log=print) -> dict[str, Path]:
    data_dir = cfg.data_dir
    paths: dict[str, Path] = {}

    raw = [json.loads(value) for value in db.checkins_raw()]
    path = data_dir / "checkins.json"
    _write_json(path, raw)
    paths["checkins_json"] = path

    rows = []
    for row in db.checkin_rows():
        year, month, weekday = _date_parts(row)
        rows.append([
            row["id"], row["created_at"], row["created_at_utc"], row["local_datetime"],
            year, month, weekday, row["venue_id"], row["venue_name"],
            row["category_name"], row["category_short_name"], row["category_code"],
            row["address"], row["cross_street"], row["city"], row["state"],
            row["postal_code"], row["country"], row["country_code"],
            row["neighborhood"], row["lat"], row["lng"], row["shout"], row["type"],
            row["visibility"], row["source_name"], row["is_mayor"],
            row["likes_count"], row["comments_count"], row["photos_count"],
            row["canonical_url"],
        ])
    path = data_dir / "checkins.csv"
    _write_csv(path, CHECKIN_FIELDS, rows)
    paths["checkins_csv"] = path

    rows = []
    for row in db.photo_rows():
        rows.append([
            row["id"], row["checkin_id"], row["created_at"],
            iso_utc(row["created_at"]) if row["created_at"] else None,
            row["width"], row["height"], row["visibility"], row["url"],
            row["downloaded_path"], row["canonical_url"], row["user_id"],
            row["user_display_name"],
        ])
    path = data_dir / "photos.csv"
    _write_csv(path, PHOTO_FIELDS, rows)
    paths["photos_csv"] = path

    rows = []
    for row in db.comment_rows():
        rows.append([
            row["id"], row["checkin_id"], row["created_at"],
            iso_utc(row["created_at"]) if row["created_at"] else None,
            row["user_id"], row["user_display_name"], row["text"],
        ])
    path = data_dir / "comments.csv"
    _write_csv(path, COMMENT_FIELDS, rows)
    paths["comments_csv"] = path

    like_rows = db.conn.execute(
        "SELECT * FROM likes ORDER BY checkin_id, display_name"
    ).fetchall()
    rows = [
        [
            row["checkin_id"], row["user_id"], row["display_name"], row["handle"],
            row["city"], row["state"], row["country_code"], row["is_service_user"],
        ]
        for row in like_rows
    ]
    path = data_dir / "likes.csv"
    _write_csv(path, LIKE_FIELDS, rows)
    paths["likes_csv"] = path

    log(f"Export: {len(raw)} Check-ins, {len(rows)} Likes -> {data_dir}")
    return paths
