from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from ...core.config import ROOT
from .config import YelpConfig
from .db import Database, iso_now
from .html_tables import Cell, Table, read_tables, select_table
from .ids import normalize_name, parse_float, parse_iso8601, place_key, stable_id, visit_id

CHECKIN_HEADERS = (
    "Datum", "Name des Unternehmens", "Kommentar", "Längengrad", "Breitengrad", "Status",
)
COMMENT_HEADERS = ("Datum", "Name des Unternehmens", "Kommentar", "Status")

UNUSED_TABLES = (
    "business_answer.html",
    "business_question.html",
    "cbil_linked_account.html",
    "chat_storage.html",
    "deal.html",
    "device_visit_v2.html",
    "gift_certificate.html",
    "platform_order.html",
    "reservation.html",
    "review_draft.html",
    "tip.html",
    "user_business_follows.html",
    "user_facts.html",
    "user_preference.html",
    "waitlist.html",
    "year_on_yelp.html",
)


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
        timestamp, _, iso_timestamp = parsed_date
        lng = parse_float(_text(row, "Längengrad"))
        lat = parse_float(_text(row, "Breitengrad"))
        result.append(
            {
                "id": visit_id(name, iso_timestamp, lat, lng),
                "visited_at": timestamp,
                "visited_at_iso": iso_timestamp,
                "tz_offset": None,
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


def parse_survey_answers(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "survey_answer.html",
        ("Name des Unternehmens", "Frage", "Antwort", "Gerätetyp", "Beantwortet um"),
        warnings,
    )
    now = iso_now()
    result: list[dict[str, Any]] = []
    for row in rows:
        name = _text(row, "Name des Unternehmens")
        question = _text(row, "Frage")
        answer = _text(row, "Antwort")
        if not name or not question:
            warnings.append("survey_answer.html: Zeile ohne Ort oder Frage übersprungen")
            continue
        parsed_date = _date(row, "Beantwortet um")
        if parsed_date is not None:
            answered_at, _, answered_at_iso = parsed_date
        else:
            answered_at, answered_at_iso = None, None
        result.append(
            {
                "id": stable_id(
                    "survey_answer", normalize_name(name), answered_at_iso,
                    question, answer,
                ),
                "answered_at": answered_at,
                "answered_at_iso": answered_at_iso,
                "place_name": name,
                "place_key": place_key(name),
                "question": question or None,
                "answer": answer or None,
                "device_type": _text(row, "Gerätetyp") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_business_interactions(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "user_business_interaction.html",
        ("Datum", "Name des Unternehmens", "Art der Interaktion des Users mit dem Unternehmen"),
        warnings,
    )
    now = iso_now()
    result: list[dict[str, Any]] = []
    for row in rows:
        name = _text(row, "Name des Unternehmens")
        interaction = _text(row, "Art der Interaktion des Users mit dem Unternehmen")
        parsed_date = _date(row)
        if not name or parsed_date is None:
            warnings.append(
                "user_business_interaction.html: Zeile ohne Datum oder Namen übersprungen"
            )
            continue
        timestamp, _, iso_timestamp = parsed_date
        result.append(
            {
                "id": stable_id(
                    "biz_interaction", normalize_name(name), iso_timestamp, interaction
                ),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "place_name": name,
                "place_key": place_key(name),
                "interaction": interaction or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_review_feedback(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "review_feedback.html",
        (
            "Beitrags-URL", "Hilfreich", "Lustig", "Cool", "Erstellungsdatum",
            "Aktualisierungsdatum", "IP Adresse",
        ),
        warnings,
    )
    now = iso_now()
    result: list[dict[str, Any]] = []
    for row in rows:
        content_url = _text(row, "Beitrags-URL")
        parsed_date = _date(row, "Erstellungsdatum")
        if not content_url or parsed_date is None:
            warnings.append(
                "review_feedback.html: Zeile ohne Datum oder Beitrags-URL übersprungen"
            )
            continue
        timestamp, _, iso_timestamp = parsed_date
        match = re.search(r"/biz/([^/?#]+)", content_url)
        updated = _date(row, "Aktualisierungsdatum")
        result.append(
            {
                "id": stable_id(
                    "review_feedback", content_url, iso_timestamp,
                    _text(row, "Hilfreich"), _text(row, "Lustig"), _text(row, "Cool"),
                ),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "updated_at_iso": updated[2] if updated else None,
                "content_url": content_url,
                "business_ref": match.group(1) if match else None,
                "helpful": _text(row, "Hilfreich") or None,
                "funny": _text(row, "Lustig") or None,
                "cool": _text(row, "Cool") or None,
                "ip": _text(row, "IP Adresse") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_photo_feedback(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "biz_photo_feedback.html",
        (
            "Aktualisierungsdatum", "Foto-URL", 'Wurde mit "gefällt mir" markiert',
            'Angaben zu "nützlich"', "IP Adresse",
        ),
        warnings,
    )
    now = iso_now()
    result: list[dict[str, Any]] = []
    for row in rows:
        photo_url = _text(row, "Foto-URL")
        parsed_date = _date(row, "Aktualisierungsdatum")
        if not photo_url or parsed_date is None:
            warnings.append(
                "biz_photo_feedback.html: Zeile ohne Datum oder Foto-URL übersprungen"
            )
            continue
        timestamp, _, iso_timestamp = parsed_date
        result.append(
            {
                "id": stable_id("photo_feedback", photo_url, iso_timestamp),
                "updated_at": timestamp,
                "updated_at_iso": iso_timestamp,
                "photo_url": photo_url,
                "liked": _text(row, 'Wurde mit "gefällt mir" markiert') or None,
                "useful": _text(row, 'Angaben zu "nützlich"') or None,
                "ip": _text(row, "IP Adresse") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_compliments(
    html_dir: Path, filename: str, direction: str, warnings: list[str]
) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        filename,
        ("Datum", "Name", "Art des Inhalts", "Art des Kompliments", "Nachricht", "Status"),
        warnings,
    )
    now = iso_now()
    result: list[dict[str, Any]] = []
    for row in rows:
        name = _text(row, "Name")
        parsed_date = _date(row)
        if not name or parsed_date is None:
            warnings.append(f"{filename}: Zeile ohne Datum oder Namen übersprungen")
            continue
        timestamp, _, iso_timestamp = parsed_date
        message = _text(row, "Nachricht")
        compliment_type = _text(row, "Art des Kompliments")
        result.append(
            {
                "id": stable_id(
                    "compliment", direction, normalize_name(name), iso_timestamp,
                    compliment_type, message,
                ),
                "direction": direction,
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "name": name,
                "content_type": _text(row, "Art des Inhalts") or None,
                "compliment_type": compliment_type or None,
                "message": message or None,
                "status": _text(row, "Status") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_messages(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "message.html",
        (
            "Datum", "Medien", "Empfänger", "Betreff", "Name des Unternehmens",
            "Text", "Status", "Is Read (At time of export)",
        ),
        warnings,
    )
    now = iso_now()
    result: list[dict[str, Any]] = []
    for row in rows:
        parsed_date = _date(row)
        if parsed_date is None:
            warnings.append("message.html: Zeile ohne Datum übersprungen")
            continue
        timestamp, _, iso_timestamp = parsed_date
        recipient = _text(row, "Empfänger")
        subject = _text(row, "Betreff")
        text = _text(row, "Text")
        name = _text(row, "Name des Unternehmens")
        result.append(
            {
                "id": stable_id("message", iso_timestamp, recipient, subject, text, name),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "recipient": recipient or None,
                "subject": subject or None,
                "place_name": name or None,
                "place_key": place_key(name) if name else None,
                "text": text or None,
                "status": _text(row, "Status") or None,
                "read_at": _text(row, "Is Read (At time of export)") or None,
                "media": _media_filename(row.get("Medien")),
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_milestones(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(html_dir, "milestone.html", ("Betreff", "Text", "Erstellungsdatum"), warnings)
    now = iso_now()
    result: list[dict[str, Any]] = []
    for row in rows:
        subject = _text(row, "Betreff")
        text = _text(row, "Text")
        parsed_date = _date(row, "Erstellungsdatum")
        if not subject or parsed_date is None:
            warnings.append("milestone.html: Zeile ohne Datum oder Betreff übersprungen")
            continue
        timestamp, _, iso_timestamp = parsed_date
        result.append(
            {
                "id": stable_id("milestone", iso_timestamp, subject, text),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "subject": subject or None,
                "text": text or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_profile_questions(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(html_dir, "profile_question.html", ("Frage", "Antwort", "Status"), warnings)
    now = iso_now()
    result: list[dict[str, Any]] = []
    for row in rows:
        question = _text(row, "Frage")
        if not question:
            warnings.append("profile_question.html: Zeile ohne Frage übersprungen")
            continue
        answer = _text(row, "Antwort")
        result.append(
            {
                "id": stable_id("profile_question", question, answer),
                "question": question,
                "answer": answer or None,
                "status": _text(row, "Status") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_talk_topics(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(html_dir, "talk_topic.html", ("Datum", "Thema", "Kommentar", "Status"), warnings)
    now = iso_now()
    result: list[dict[str, Any]] = []
    for row in rows:
        topic = _text(row, "Thema")
        parsed_date = _date(row)
        if not topic or parsed_date is None:
            warnings.append("talk_topic.html: Zeile ohne Datum oder Thema übersprungen")
            continue
        timestamp, _, iso_timestamp = parsed_date
        comment = _text(row, "Kommentar")
        result.append(
            {
                "id": stable_id("talk_topic", iso_timestamp, topic, comment),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "topic": topic,
                "comment": comment or None,
                "status": _text(row, "Status") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_account_information(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    path = html_dir / "account_information.html"
    if not path.exists():
        warnings.append("account_information.html: Datei fehlt")
        return []
    now = iso_now()
    result: list[dict[str, Any]] = []
    for table in read_tables(path):
        if table.headers:
            continue
        for row in table.rows:
            if len(row) < 2:
                continue
            field = row[0].text
            value = row[1].text
            if not field:
                continue
            result.append(
                {
                    "id": stable_id("account", normalize_name(field)),
                    "field": field,
                    "value": value or None,
                    "raw_row": json.dumps(
                        {"field": field, "value": value},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "imported_at": now,
                }
            )
    return result


def parse_user_logins(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir, "user_login.html", ("Datum", "IP Adresse", "User Agent"), warnings
    )
    now = iso_now()
    result: list[dict[str, Any]] = []
    for row in rows:
        parsed_date = _date(row)
        if parsed_date is None:
            warnings.append("user_login.html: Zeile ohne Datum übersprungen")
            continue
        timestamp, _, iso_timestamp = parsed_date
        ip = _text(row, "IP Adresse")
        user_agent = _text(row, "User Agent")
        result.append(
            {
                "id": stable_id("login", iso_timestamp, ip, user_agent),
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "ip": ip or None,
                "user_agent": user_agent or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def parse_flagged_content(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows = _rows(
        html_dir,
        "user_flagged_content.html",
        ("Art des Inhalts", "Datum", "Grund", "Hinweis", "IP Adresse"),
        warnings,
    )
    now = iso_now()
    result: list[dict[str, Any]] = []
    for row in rows:
        parsed_date = _date(row)
        if parsed_date is None:
            warnings.append("user_flagged_content.html: Zeile ohne Datum übersprungen")
            continue
        timestamp, _, iso_timestamp = parsed_date
        content_type = _text(row, "Art des Inhalts")
        note = _text(row, "Hinweis")
        result.append(
            {
                "id": stable_id("flagged", iso_timestamp, content_type, note),
                "content_type": content_type or None,
                "created_at": timestamp,
                "created_at_iso": iso_timestamp,
                "reason": _text(row, "Grund") or None,
                "note": note or None,
                "ip": _text(row, "IP Adresse") or None,
                "raw_row": _raw_row(row),
                "imported_at": now,
            }
        )
    return result


def _table_rows(table: Table) -> list[Any]:
    if table.headers:
        return [
            {
                header: row[index].text if index < len(row) else ""
                for index, header in enumerate(table.headers)
            }
            for row in table.rows
        ]
    return [[cell.text for cell in row] for row in table.rows]


def capture_source_tables(html_dir: Path, warnings: list[str]) -> list[dict[str, Any]]:
    now = iso_now()
    result: list[dict[str, Any]] = []
    for filename in UNUSED_TABLES:
        path = html_dir / filename
        if not path.exists():
            continue
        tables = read_tables(path)
        if not tables:
            warnings.append(f"{filename}: keine Tabelle gefunden")
            continue
        for index, table in enumerate(tables, start=1):
            key = filename if len(tables) == 1 else f"{filename}#{index}"
            result.append(
                {
                    "table_name": key,
                    "headers_json": json.dumps(table.headers, ensure_ascii=False),
                    "rows_json": json.dumps(_table_rows(table), ensure_ascii=False),
                    "row_count": len(table.rows),
                    "imported_at": now,
                }
            )
    return result


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
    survey_answers = parse_survey_answers(config.html_dir, warnings)
    business_interactions = parse_business_interactions(config.html_dir, warnings)
    review_feedback = parse_review_feedback(config.html_dir, warnings)
    photo_feedback = parse_photo_feedback(config.html_dir, warnings)
    compliments = (
        parse_compliments(config.html_dir, "compliment.html", "sent", warnings)
        + parse_compliments(
            config.html_dir, "compliment_received.html", "received", warnings
        )
    )
    messages = parse_messages(config.html_dir, warnings)
    milestones = parse_milestones(config.html_dir, warnings)
    profile_questions = parse_profile_questions(config.html_dir, warnings)
    talk_topics = parse_talk_topics(config.html_dir, warnings)
    account_information = parse_account_information(config.html_dir, warnings)
    user_logins = parse_user_logins(config.html_dir, warnings)
    flagged_content = parse_flagged_content(config.html_dir, warnings)
    source_tables = capture_source_tables(config.html_dir, warnings)

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
        "survey_answers": len(survey_answers),
        "business_interactions": len(business_interactions),
        "review_feedback": len(review_feedback),
        "photo_feedback": len(photo_feedback),
        "compliments": len(compliments),
        "messages": len(messages),
        "milestones": len(milestones),
        "profile_questions": len(profile_questions),
        "talk_topics": len(talk_topics),
        "account_information": len(account_information),
        "user_logins": len(user_logins),
        "flagged_content": len(flagged_content),
        "source_tables": len(source_tables),
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
        db.upsert("survey_answers", survey_answers)
        db.upsert("business_interactions", business_interactions)
        db.upsert("review_feedback", review_feedback)
        db.upsert("photo_feedback", photo_feedback)
        db.upsert("compliments", compliments)
        db.upsert("messages", messages)
        db.upsert("milestones", milestones)
        db.upsert("profile_questions", profile_questions)
        db.upsert("talk_topics", talk_topics)
        db.upsert("account_information", account_information)
        db.upsert("user_logins", user_logins)
        db.upsert("flagged_content", flagged_content)
        db.upsert("source_tables", source_tables, key="table_name")
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
