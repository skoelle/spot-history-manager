# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in the project root for details.
from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import identity
from .config import Paths
from .naming import name_similarity
from .places import PlaceRule, parse_place_ref, upsert_rule

WORKLIST_FIELDS = (
    "ref",
    "name",
    "source",
    "category",
    "category_group",
    "place_state",
    "match_status",
    "city",
    "address",
    "lat",
    "lng",
    "visit_count",
    "first_visit",
    "last_visit",
    "context_cities",
    "candidate_count",
    "candidate_osm",
    "candidate_name",
    "candidate_score",
    "candidate_reason",
    "candidate_city",
    "same_day_ref",
    "same_day_name",
    "same_day_osm",
    "same_day_city",
    "same_day_similarity",
    "same_day_delta_min",
    "same_day_alternatives",
    "bookmark_url",
    "suggested_action",
    "suggested_target",
    "suggested_osm",
    "suggested_state",
    "confidence",
    "suggestion_reason",
)

APPLY_FIELDS = (
    "ref",
    "action",
    "target",
    "osm",
    "state",
    "lat",
    "lng",
    "confidence",
    "note",
)


@dataclass
class ApplySummary:
    applied: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)


def _iso(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat(
        timespec="seconds"
    )


def _source_refs(conn: sqlite3.Connection) -> dict[int, list[tuple[str, str]]]:
    refs: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for row in conn.execute(
        "SELECT canonical_id, source, source_place_id FROM place_links "
        "ORDER BY CASE source WHEN 'foursquare' THEN 0 WHEN 'yelp' THEN 1 "
        "ELSE 2 END, source, source_place_id"
    ):
        refs[int(row["canonical_id"])].append(
            (row["source"], row["source_place_id"])
        )
    return refs


def _preferred_ref(refs: list[tuple[str, str]]) -> str | None:
    if not refs:
        return None
    source, source_place_id = refs[0]
    return f"{source}:{source_place_id}"


def _bookmark_urls(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    urls: dict[str, str] = {}
    try:
        for table in ("bookmarks", "collection_items"):
            try:
                rows = conn.execute(
                    f"SELECT place_name, place_url FROM {table} "
                    "WHERE place_url IS NOT NULL AND place_url != ''"
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for row in rows:
                urls.setdefault(row["place_name"], row["place_url"])
    finally:
        conn.close()
    return urls


def _candidate_cities(path: Path, candidates: list[dict]) -> dict[tuple[str, int], str]:
    if not candidates or not path.exists():
        return {}
    keys = {
        (candidate["osm_type"], int(candidate["osm_id"]))
        for candidate in candidates
    }
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cities: dict[tuple[str, int], str] = {}
    try:
        for row in conn.execute(
            "SELECT osm_type, osm_id, tags_json FROM pois"
        ):
            key = (row["osm_type"], int(row["osm_id"]))
            if key not in keys:
                continue
            try:
                tags = json.loads(row["tags_json"]) if row["tags_json"] else {}
            except (TypeError, ValueError):
                continue
            if isinstance(tags, dict) and tags.get("addr:city"):
                cities[key] = str(tags["addr:city"])
    finally:
        conn.close()
    return cities


def build_worklist(
    paths: Paths,
    *,
    source: str = "yelp",
    states: tuple[str, ...] | None = None,
    limit: int | None = None,
) -> list[dict]:
    if not paths.index_db.exists():
        raise FileNotFoundError(f"Kein Index vorhanden: {paths.index_db}")

    conn = sqlite3.connect(f"file:{paths.index_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        clauses = [
            "p.match_status != 'osm'",
            (
                "EXISTS (SELECT 1 FROM place_links pl "
                "WHERE pl.canonical_id = p.id AND pl.source = ?)"
            ),
        ]
        params: list = [source]
        if states:
            placeholders = ", ".join("?" for _ in states)
            clauses.append(f"p.place_state IN ({placeholders})")
            params.extend(states)
        sql = (
            "SELECT p.id, p.canonical_key, p.name, p.category, "
            "p.category_group, p.place_state, p.match_status, p.city, "
            "p.address, p.lat, p.lng, p.manual_lat, p.manual_lng "
            "FROM canonical_places p "
            f"WHERE {' AND '.join(clauses)} ORDER BY p.name"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        places = [dict(row) for row in conn.execute(sql, params)]
        refs = _source_refs(conn)
        visits = [
            dict(row)
            for row in conn.execute(
                "SELECT v.canonical_place_id, v.visited_at, p.name, p.city, "
                "p.canonical_key, p.osm_type, p.osm_id, "
                "COALESCE(p.osm_lat, p.manual_lat, p.lat) AS lat, "
                "COALESCE(p.osm_lng, p.manual_lng, p.lng) AS lng "
                "FROM visits v JOIN canonical_places p "
                "ON p.id = v.canonical_place_id "
                "WHERE v.visited_at IS NOT NULL"
            )
        ]
    finally:
        conn.close()

    candidates = defaultdict(list)
    for row in identity.load_osm_candidates(paths.place_identity_db):
        candidates[(row["source"], row["source_place_id"])].append(row)
    all_candidates = [row for rows in candidates.values() for row in rows]
    candidate_cities = _candidate_cities(paths.osm_db, all_candidates)
    for row in all_candidates:
        row["city"] = candidate_cities.get(
            (row["osm_type"], int(row["osm_id"]))
        )
    urls = _bookmark_urls(paths.db_root / "yelp" / "yelp.db")

    by_date: dict[str, list[dict]] = defaultdict(list)
    for visit in visits:
        date = datetime.fromtimestamp(
            int(visit["visited_at"]), timezone.utc
        ).date().isoformat()
        by_date[date].append(visit)

    rows: list[dict] = []
    for place in places:
        place_refs = refs.get(place["id"], [])
        source_refs = [(src, pid) for src, pid in place_refs if src == source]
        if not source_refs:
            continue
        ref = f"{source}:{source_refs[0][1]}"
        place_candidates = [
            candidate
            for member in place_refs
            for candidate in candidates.get(member, [])
        ]
        place_candidates.sort(key=lambda item: item.get("score") or 0, reverse=True)

        same_day: list[dict] = []
        for timestamp in (
            row["visited_at"]
            for row in visits
            if row["canonical_place_id"] == place["id"]
        ):
            date = datetime.fromtimestamp(
                int(timestamp), timezone.utc
            ).date().isoformat()
            for other in by_date.get(date, []):
                if other["canonical_place_id"] == place["id"]:
                    continue
                if other["lat"] is None or other["lng"] is None:
                    continue
                delta = abs(int(other["visited_at"]) - int(timestamp))
                if delta > 6 * 3600:
                    continue
                similarity = name_similarity(place["name"], other["name"])
                same_day.append(
                    {
                        "ref": _preferred_ref(refs.get(other["canonical_place_id"])),
                        "name": other["name"],
                        "osm": (
                            f"{other['osm_type']}/{other['osm_id']}"
                            if other["osm_type"] and other["osm_id"] is not None
                            else None
                        ),
                        "city": other["city"],
                        "similarity": round(similarity, 3),
                        "delta_min": round(delta / 60, 1),
                    }
                )
        same_day.sort(
            key=lambda item: (-item["similarity"], item["delta_min"])
        )
        same_day = same_day[:5]
        context_cities = Counter(
            item["city"] for item in same_day if item["city"]
        )

        suggestion = _suggest(place, place_candidates, same_day, context_cities)
        bookmark_url = None
        for src, pid in source_refs:
            if src == "yelp":
                bookmark_url = urls.get(place["name"])
                if bookmark_url:
                    break

        best_candidate = place_candidates[0] if place_candidates else None
        best_same = same_day[0] if same_day else None
        rows.append(
            {
                "ref": ref,
                "name": place["name"],
                "source": source,
                "category": place["category"],
                "category_group": place["category_group"],
                "place_state": place["place_state"],
                "match_status": place["match_status"],
                "city": place["city"],
                "address": place["address"],
                "lat": place["lat"],
                "lng": place["lng"],
                "visit_count": sum(
                    1 for visit in visits
                    if visit["canonical_place_id"] == place["id"]
                ),
                "first_visit": _iso(
                    min(
                        (
                            visit["visited_at"]
                            for visit in visits
                            if visit["canonical_place_id"] == place["id"]
                        ),
                        default=None,
                    )
                ),
                "last_visit": _iso(
                    max(
                        (
                            visit["visited_at"]
                            for visit in visits
                            if visit["canonical_place_id"] == place["id"]
                        ),
                        default=None,
                    )
                ),
                "context_cities": " | ".join(
                    f"{city} ({count})"
                    for city, count in context_cities.most_common(3)
                ),
                "candidate_count": len(place_candidates),
                "candidate_osm": (
                    f"{best_candidate['osm_type']}/{best_candidate['osm_id']}"
                    if best_candidate
                    else None
                ),
                "candidate_name": (
                    best_candidate["osm_name"] if best_candidate else None
                ),
                "candidate_score": (
                    best_candidate["score"] if best_candidate else None
                ),
                "candidate_reason": (
                    best_candidate["reason"] if best_candidate else None
                ),
                "candidate_city": (
                    best_candidate["city"] if best_candidate else None
                ),
                "same_day_ref": best_same["ref"] if best_same else None,
                "same_day_name": best_same["name"] if best_same else None,
                "same_day_osm": best_same["osm"] if best_same else None,
                "same_day_city": best_same["city"] if best_same else None,
                "same_day_similarity": (
                    best_same["similarity"] if best_same else None
                ),
                "same_day_delta_min": (
                    best_same["delta_min"] if best_same else None
                ),
                "same_day_alternatives": " | ".join(
                    f"{item['name']} ({item['similarity']:.2f}, "
                    f"{item['delta_min']:.0f} min)"
                    for item in same_day[1:4]
                ),
                "bookmark_url": bookmark_url,
                **suggestion,
            }
        )
    return rows


def _suggest(
    place: dict,
    candidates: list[dict],
    same_day: list[dict],
    context_cities: Counter,
) -> dict:
    best_same = same_day[0] if same_day else None
    if (
        best_same
        and best_same["similarity"] >= 0.8
        and best_same["delta_min"] <= 180
    ):
        if best_same["osm"]:
            return {
                "suggested_action": "assign",
                "suggested_target": None,
                "suggested_osm": best_same["osm"],
                "suggested_state": None,
                "confidence": "high",
                "suggestion_reason": "same_day_osm",
            }
        if best_same["ref"]:
            return {
                "suggested_action": "merge",
                "suggested_target": best_same["ref"],
                "suggested_osm": None,
                "suggested_state": None,
                "confidence": "medium",
                "suggestion_reason": "same_day_merge",
            }

    if candidates:
        best = candidates[0]
        candidate_city = None
        for candidate in candidates:
            if candidate.get("city"):
                candidate_city = candidate["city"]
                break
        city_ok = (
            not context_cities
            or not candidate_city
            or candidate_city in context_cities
        )
        name_ok = (
            name_similarity(place["name"], best.get("osm_name")) >= 0.8
        )
        if city_ok and (name_ok or (best.get("score") or 0) >= 0.7):
            return {
                "suggested_action": "assign",
                "suggested_target": None,
                "suggested_osm": f"{best['osm_type']}/{best['osm_id']}",
                "suggested_state": None,
                "confidence": "medium" if city_ok else "low",
                "suggestion_reason": "candidate",
            }

    state = "no_osm"
    if place.get("category_group") == "Events":
        state = "closed"
    return {
        "suggested_action": "research",
        "suggested_target": None,
        "suggested_osm": None,
        "suggested_state": state,
        "confidence": "low",
        "suggestion_reason": "manual_research",
    }


def write_worklist_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=WORKLIST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in WORKLIST_FIELDS})


def _apply_rows(
    paths: Paths,
    reader: csv.DictReader,
    *,
    dry_run: bool,
) -> ApplySummary:
    summary = ApplySummary()
    for line, row in enumerate(reader, start=2):
        ref = (row.get("ref") or "").strip()
        action = (row.get("action") or "").strip().lower()
        if not ref or not action or action in ("noop", "skip"):
            summary.skipped += 1
            continue
        try:
            source, source_place_id = parse_place_ref(ref)
            target = None
            osm = None
            state = None
            lat = None
            lng = None
            if action == "assign":
                osm_type, osm_id = identity.parse_osm_ref(
                    (row.get("osm") or "").strip()
                )
                osm = f"{osm_type}/{osm_id}"
                if not dry_run:
                    identity.upsert_osm_assignment(
                        paths.place_identity_db,
                        source,
                        source_place_id,
                        osm_type,
                        osm_id,
                        method="manual",
                        confidence=(row.get("confidence") or "manual").strip(),
                        note=(row.get("note") or "research").strip(),
                    )
                    identity.clear_osm_candidates(
                        paths.place_identity_db, source, source_place_id
                    )
            elif action == "merge":
                target_source, target_id = parse_place_ref(
                    (row.get("target") or "").strip()
                )
                target = f"{target_source}:{target_id}"
                if not dry_run:
                    upsert_rule(
                        paths.place_rules_db,
                        PlaceRule(
                            source=source,
                            source_place_id=source_place_id,
                            action="merge",
                            target_source=target_source,
                            target_source_place_id=target_id,
                            note=(row.get("note") or "research").strip(),
                        ),
                    )
            elif action == "state":
                state = (row.get("state") or "").strip()
                if state not in identity.OSM_STATES:
                    raise ValueError(f"Ungültiger Status: {state!r}")
                if not dry_run:
                    identity.set_osm_state(
                        paths.place_identity_db,
                        source,
                        source_place_id,
                        state,
                        reason="research",
                        note=(row.get("note") or "").strip() or None,
                    )
            elif action == "coords":
                lat = float(row.get("lat") or "")
                lng = float(row.get("lng") or "")
                if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    raise ValueError(f"Koordinaten außerhalb des Bereichs: {lat}, {lng}")
                if not dry_run:
                    identity.set_manual_coords(
                        paths.place_identity_db,
                        source,
                        source_place_id,
                        lat,
                        lng,
                        note=(row.get("note") or "").strip() or None,
                    )
            else:
                raise ValueError(f"Unbekannte Aktion: {action!r}")
            summary.applied += 1
            summary.actions.append(
                {
                    "line": line,
                    "ref": ref,
                    "action": action,
                    "target": target,
                    "osm": osm,
                    "state": state,
                    "lat": lat,
                    "lng": lng,
                    "confidence": (row.get("confidence") or "").strip() or None,
                    "note": (row.get("note") or "").strip() or None,
                }
            )
        except (ValueError, TypeError) as exc:
            summary.errors.append(f"Zeile {line}: {exc}")
    return summary


def apply_research(
    paths: Paths,
    csv_path: Path,
    *,
    dry_run: bool = False,
) -> ApplySummary:
    if not csv_path.exists():
        raise FileNotFoundError(f"Keine Research-CSV: {csv_path}")
    with csv_path.open(encoding="utf-8", newline="") as handle:
        return _apply_rows(paths, csv.DictReader(handle), dry_run=dry_run)


def apply_research_text(
    paths: Paths,
    text: str,
    *,
    dry_run: bool = False,
) -> ApplySummary:
    reader = csv.DictReader(io.StringIO(text))
    return _apply_rows(paths, reader, dry_run=dry_run)
