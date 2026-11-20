from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ...core.config import ROOT
from .config import YelpConfig
from .db import Database, iso_now
from .html_tables import Cell, read_tables, select_table
from .ids import normalize_name, parse_float, parse_iso8601, place_key, stable_id, visit_id

CHECKIN_HEADERS = (
    "Datum", "Name des Unternehmens", "Kommentar", "Längengrad", "Breitengrad", "Status",
)
COMMENT_HEADERS = ("Datum", "Name des Unternehmens", "Kommentar", "Status")


@dataclass
class ImportSummary:
    counts: dict[str, int] = field(default_factory=dict)
    media_copied: int = 0
    media_skipped: int = 0
    linked_photos: int = 0
    linked_comments: int = 0
    missing_media: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _text(row: dict[str, Cell], key: str) -> str:
    cell = row.get(key)
    return cell.text if cell else ""


def _raw_row(row: dict[str, Cell]) -> str:
    return json.dumps(
        {key: cell.text for key, cell in row.items()},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _date(row: dict[str, Cell], key: str = "Datum"):
    return parse_iso8601(_text(row, key))


def _rows(
    html_dir: Path,
    filename: str,
    required: tuple[str, ...],
    warnings: list[str],
) -> list[dict[str, Cell]]:
    path = html_dir / filename
    if not path.exists():
        warnings.append(f"{filename}: Datei fehlt")
        return []
    try:
        table = select_table(read_tables(path), required, filename)
    except ValueError as exc:
        warnings.append(str(exc))
        return []
    return table.dicts()


def _link_index(checkins: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for checkin in checkins:
        key = (normalize_name(checkin["place_name"]), checkin["visited_at_iso"][:10])
        index.setdefault(key, []).append(checkin)
    return index


def _link(
    index: dict[tuple[str, str], list[dict[str, Any]]],
    name: str | None,
    iso_timestamp: str | None,
) -> tuple[str | None, str | None]:
    if not name or not iso_timestamp:
        return None, None
    candidates = index.get((normalize_name(name), iso_timestamp[:10]), [])
    if len(candidates) == 1:
        return candidates[0]["id"], "same_day"
    exact = [item for item in candidates if item["visited_at_iso"] == iso_timestamp]
    if len(exact) == 1:
        return exact[0]["id"], "exact_timestamp"
    return None, None


def parse_checkins(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(html_dir, "check_in.html", CHECKIN_HEADERS, warnings)
    now = iso_now()
    result: list[dict[str, Any]] = []
    for row in rows:
        parsed_date = _date(row)
        name = _text(row, "Name des Unternehmens")
        if parsed_date is None or not name:
            warnings.append("check_in.html: Zeile ohne Datum oder Namen übersprungen")
            continue
        timestamp, offset, iso_timestamp = parsed_date
        lng = parse_float(_text(row, "Längengrad"))
        lat = parse_float(_text(row, "Breitengrad"))
        result.append(
            {
                "id": visit_id(name, iso_timestamp, lat, lng),
                "visited_at": timestamp,
                "visited_at_iso": iso_timestamp,
                "tz_offset": offset,
                "place_key": place_key(name),
                "place_name": name,
                "lat": lat,
                "lng": lng,
                "note": _text(row, "Kommentar") or None,
                "status": _text(row, "Status") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_checkin_comments(
    html_dir: Path,
    index: dict[tuple[str, str], list[dict[str, Any]]],
    warnings: list[str],
) -> tuple[list[dict[str, Any]], int]:
    rows = _rows(html_dir, "check_in.html", COMMENT_HEADERS, warnings)
    now = iso_now()
    result: list[dict[str, Any]] = []
    linked = 0
    for row in rows:
        parsed_date = _date(row)
        name = _text(row, "Name des Unternehmens")
        text = _text(row, "Kommentar")
        if parsed_date is None or not name:
            warnings.append("check_in.html: Kommentarzeile ohne Datum oder Namen übersprungen")
            continue
        timestamp, offset, iso_timestamp = parsed_date
        checkin_id, method = _link(index, name, iso_timestamp)
        if checkin_id:
            linked += 1
        result.append(
            {
                "id": stable_id("comment", normalize_name(name), iso_timestamp, text),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "tz_offset": offset,
                "place_name": name,
                "text": text or None,
                "status": _text(row, "Status") or None,
                "checkin_id": checkin_id,
                "link_method": method,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result, linked


def parse_reviews(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "user_review.html",
        ("Datum", "Name des Unternehmens", "Bewertung", "Kommentar", "IP Adresse", "Status"),
        warnings,
    )
    now = iso_now()
    result = []
    for row in rows:
        parsed_date = _date(row)
        name = _text(row, "Name des Unternehmens")
        text = _text(row, "Kommentar")
        if parsed_date is None or not name:
            warnings.append("user_review.html: Zeile ohne Datum oder Namen übersprungen")
            continue
        timestamp, offset, iso_timestamp = parsed_date
        result.append(
            {
                "id": stable_id("review", normalize_name(name), iso_timestamp, text),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "tz_offset": offset,
                "place_name": name,
                "rating": parse_float(_text(row, "Bewertung")),
                "text": text or None,
                "ip": _text(row, "IP Adresse") or None,
                "status": _text(row, "Status") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_bookmarks(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "bookmark.html",
        ("Datum", "Name des Unternehmens", "URL des Untermehmenseintrags", "Hinweis", "Status"),
        warnings,
    )
    now = iso_now()
    result = []
    for row in rows:
        parsed_date = _date(row)
        name = _text(row, "Name des Unternehmens")
        if parsed_date is None or not name:
            warnings.append("bookmark.html: Zeile ohne Datum oder Namen übersprungen")
            continue
        timestamp, offset, iso_timestamp = parsed_date
        result.append(
            {
                "id": stable_id("bookmark", normalize_name(name), iso_timestamp),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "place_name": name,
                "place_url": _text(row, "URL des Untermehmenseintrags") or None,
                "note": _text(row, "Hinweis") or None,
                "status": _text(row, "Status") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_collections(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "collection.html",
        ("Datum", "Name", "Beschreibung", "Öffentlich auffindbar", "Status"),
        warnings,
    )
    now = iso_now()
    result = []
    for row in rows:
        parsed_date = _date(row)
        name = _text(row, "Name")
        if parsed_date is None or not name:
            warnings.append("collection.html: Zeile ohne Datum oder Namen übersprungen")
            continue
        timestamp, _, iso_timestamp = parsed_date
        result.append(
            {
                "id": stable_id("collection", normalize_name(name), iso_timestamp),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "name": name,
                "description": _text(row, "Beschreibung") or None,
                "is_public": _text(row, "Öffentlich auffindbar") or None,
                "status": _text(row, "Status") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_collection_items(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "collection_item.html",
        (
            "Datum", "Name des Unternehmens", "URL des Untermehmenseintrags",
            "Name der Sammlung", "URL der Sammlung", "Hinweis", "Status",
        ),
        warnings,
    )
    now = iso_now()
    result = []
    for row in rows:
        parsed_date = _date(row)
        name = _text(row, "Name des Unternehmens")
        collection_name = _text(row, "Name der Sammlung")
        if parsed_date is None or not name:
            warnings.append("collection_item.html: Zeile ohne Datum oder Namen übersprungen")
            continue
        timestamp, _, iso_timestamp = parsed_date
        result.append(
            {
                "id": stable_id(
                    "collection_item", normalize_name(collection_name),
                    normalize_name(name), iso_timestamp,
                ),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "place_name": name,
                "place_url": _text(row, "URL des Untermehmenseintrags") or None,
                "collection_name": collection_name or None,
                "collection_url": _text(row, "URL der Sammlung") or None,
                "note": _text(row, "Hinweis") or None,
                "status": _text(row, "Status") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_saved_locations(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "user_saved_location.html",
        ("Datum", "Name", "Standort", "Primär", "Gespeichert", "Status"),
        warnings,
    )
    now = iso_now()
    result = []
    for row in rows:
        parsed_date = _date(row)
        name = _text(row, "Name")
        if parsed_date is None or not name:
            warnings.append("user_saved_location.html: Zeile ohne Datum oder Namen übersprungen")
            continue
        timestamp, _, iso_timestamp = parsed_date
        result.append(
            {
                "id": stable_id("saved_location", normalize_name(name), iso_timestamp),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "name": name,
                "location": _text(row, "Standort") or None,
                "is_primary": _text(row, "Primär") or None,
                "is_saved": _text(row, "Gespeichert") or None,
                "status": _text(row, "Status") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_friends(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "friend_and_follower.html",
        ("Datum", "Name", "URL", "Beziehung", "Status"),
        warnings,
    )
    now = iso_now()
    result = []
    for row in rows:
        parsed_date = _date(row)
        name = _text(row, "Name")
        if parsed_date is None or not name:
            warnings.append("friend_and_follower.html: Zeile ohne Datum oder Namen übersprungen")
            continue
        timestamp, _, iso_timestamp = parsed_date
        result.append(
            {
                "id": stable_id("friend", normalize_name(name), iso_timestamp),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "name": name,
                "url": _text(row, "URL") or None,
                "relation": _text(row, "Beziehung") or None,
                "status": _text(row, "Status") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_reactions(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "reaction.html",
        ("Reaction Type", "Content URL", "Gerätetyp", "Erstellungsdatum"),
        warnings,
    )
    now = iso_now()
    result = []
    for row in rows:
        parsed_date = _date(row, "Erstellungsdatum")
        content_url = _text(row, "Content URL")
        if parsed_date is None or not content_url:
            warnings.append("reaction.html: Zeile ohne Datum oder Content-URL übersprungen")
            continue
        timestamp, _, iso_timestamp = parsed_date
        result.append(
            {
                "id": stable_id("reaction", content_url, iso_timestamp, _text(row, "Reaction Type")),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "reaction_type": _text(row, "Reaction Type") or None,
                "content_url": content_url,
                "device_type": _text(row, "Gerätetyp") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_searches(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "search.html",
        ("Datum", "Suchtext", "Suchkategorien", "Breitengrad", "Längengrad"),
        warnings,
    )
    now = iso_now()
    result = []
    for row in rows:
        parsed_date = _date(row)
        if parsed_date is None:
            warnings.append("search.html: Zeile ohne Datum übersprungen")
            continue
        timestamp, _, iso_timestamp = parsed_date
        search_text = _text(row, "Suchtext")
        categories = _text(row, "Suchkategorien")
        lat = parse_float(_text(row, "Breitengrad"))
        lng = parse_float(_text(row, "Längengrad"))
        result.append(
            {
                "id": stable_id(
                    "search", iso_timestamp, search_text, categories,
                    f"{lat:.6f}" if lat is not None else "",
                    f"{lng:.6f}" if lng is not None else "",
                ),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "search_text": search_text or None,
                "categories": categories or None,
                "lat": lat,
                "lng": lng,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def _media_filename(cell: Cell | None) -> str | None:
    if cell is None:
        return None
    for link in cell.links:
        name = Path(urlsplit(link).path).name
        if name:
            return name
    text = cell.text.strip()
    if text and "/" not in text and "\\" not in text:
        return Path(text).name
    return None


def _relative_to_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def parse_photos(
    html_dir: Path,
    config: YelpConfig,
    index: dict[tuple[str, str], list[dict[str, Any]]],
    warnings: list[str],
    missing_media: list[str],
) -> tuple[list[dict[str, Any]], int]:
    specs = [
        (
            "biz_photo.html",
            ("Datum", "Name des Unternehmens", "Medien", "Bildunterschrift", "Status"),
            "biz_photo",
            "Name des Unternehmens",
        ),
        (
            "profile_photo.html",
            ("Datum", "Medien", "Bildunterschrift", "Primär", "Status"),
            "profile_photo",
            None,
        ),
        (
            "video.html",
            ("Datum", "Name des Unternehmens", "Medien", "Bildunterschrift", "Status"),
            "video",
            "Name des Unternehmens",
        ),
    ]
    now = iso_now()
    result: list[dict[str, Any]] = []
    linked = 0
    for filename, required, media_type, name_column in specs:
        rows = _rows(html_dir, filename, required, warnings)
        for row in rows:
            parsed_date = _date(row)
            name = _text(row, name_column) if name_column else ""
            if parsed_date is None:
                warnings.append(f"{filename}: Zeile ohne Datum übersprungen")
                continue
            timestamp, _, iso_timestamp = parsed_date
            media_name = _media_filename(row.get("Medien"))
            if not media_name:
                warnings.append(f"{filename}: Zeile ohne Mediendatei übersprungen")
                continue
            source_file = config.media_source / media_type / media_name
            destination = config.media_dir / media_type / media_name
            local_path = None
            if destination.is_file():
                local_path = _relative_to_root(destination)
            elif not source_file.is_file():
                missing_media.append(f"{media_type}/{media_name}")
            checkin_id, method = _link(index, name or None, iso_timestamp)
            if checkin_id:
                linked += 1
            result.append(
                {
                    "id": f"{media_type}/{media_name}",
                    "created_at": timestamp,
                    "created_at_iso": iso_timestamp,
                    "place_name": name or None,
                    "place_key": place_key(name) if name else None,
                    "media_type": media_type,
                    "filename": media_name,
                    "local_path": local_path,
                    "caption": _text(row, "Bildunterschrift") or None,
                    "status": _text(row, "Status") or None,
                    "checkin_id": checkin_id,
                    "link_method": method,
                    "raw_row": _raw_row(row),
                    "imported_at": now,
                }
            )
    return result, linked


def copy_media(config: YelpConfig, log: Callable[[str], None] = print) -> tuple[int, int]:
    if not config.copy_media or not config.media_source.exists():
        return 0, 0
    copied = skipped = 0
    for source in sorted(config.media_source.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(config.media_source)
        destination = config.media_dir / relative
        if destination.exists() and destination.stat().st_size == source.stat().st_size:
            skipped += 1
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1
    if copied or skipped:
        log(f"Medien: {copied} kopiert, {skipped} bereits vorhanden")
    return copied, skipped


def run_import(config: YelpConfig, log: Callable[[str], None] = print) -> ImportSummary:
    if not config.export_dir.exists():
        raise SystemExit(f"Yelp-Export nicht gefunden: {config.export_dir}")
    if not config.html_dir.is_dir():
        raise SystemExit(f"HTML-Verzeichnis fehlt: {config.html_dir}")

    config.ensure_dirs()
    warnings: list[str] = []
    missing_media: list[str] = []

    checkins = parse_checkins(config.html_dir, warnings)
    index = _link_index(checkins)
    comments, linked_comments = parse_checkin_comments(config.html_dir, index, warnings)
    reviews = parse_reviews(config.html_dir, warnings)
    bookmarks = parse_bookmarks(config.html_dir, warnings)
    collections = parse_collections(config.html_dir, warnings)
    collection_items = parse_collection_items(config.html_dir, warnings)
    saved_locations = parse_saved_locations(config.html_dir, warnings)
    friends = parse_friends(config.html_dir, warnings)
    reactions = parse_reactions(config.html_dir, warnings)
    searches = parse_searches(config.html_dir, warnings)

    copied, skipped = copy_media(config, log)
    photos, linked_photos = parse_photos(
        config.html_dir, config, index, warnings, missing_media
    )

    counts = {
        "checkins": len(checkins),
        "comments": len(comments),
        "reviews": len(reviews),
        "bookmarks": len(bookmarks),
        "collections": len(collections),
        "collection_items": len(collection_items),
        "saved_locations": len(saved_locations),
        "friends": len(friends),
        "reactions": len(reactions),
        "searches": len(searches),
        "photos": len(photos),
        "media_copied": copied,
        "media_skipped": skipped,
        "linked_photos": linked_photos,
        "linked_comments": linked_comments,
    }

    db = Database(config.db_path).open()
    run_id = db.start_run(str(config.export_dir), str(config.data_dir))
    try:
        db.upsert("checkins", checkins)
        db.upsert("checkin_comments", comments)
        db.upsert("reviews", reviews)
        db.upsert("bookmarks", bookmarks)
        db.upsert("collections", collections)
        db.upsert("collection_items", collection_items)
        db.upsert("saved_locations", saved_locations)
        db.upsert("friends", friends)
        db.upsert("reactions", reactions)
        db.upsert("searches", searches)
        db.upsert("photos", photos)
        db.rebuild_places()
        db.set_meta("last_import_at", iso_now())
        db.set_meta("last_export_dir", str(config.export_dir))
        db.set_meta("last_media_dir", str(config.media_dir))
        db.finish_run(run_id, counts, ok=True)
    except Exception as exc:
        db.finish_run(run_id, counts, ok=False, error=str(exc))
        raise
    finally:
        db.close()

    for warning in warnings:
        log(f"Warnung: {warning}")
    return ImportSummary(
        counts=counts,
        media_copied=copied,
        media_skipped=skipped,
        linked_photos=linked_photos,
        linked_comments=linked_comments,
        missing_media=missing_media,
        warnings=warnings,
    )
