# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .connectors import discover
from .core import audit as place_audit
from .core import identity
from .core import places as place_core
from .core.backup import backup_databases
from .core.config import build_paths
from .core.index import build_index
from .core.thumbs import generate_thumbnails


def _run_index(args: argparse.Namespace, connectors) -> int:
    paths = build_paths(args.data_root)
    summary = build_index(connectors, paths)
    print(f"Quellen:  {', '.join(summary['sources']) or '-'}")
    print(f"Visits:   {summary['visits']}")
    print(f"Fotos:    {summary['photos']}")
    print(f"Punkte:   {summary['features']}")
    print(
        f"Orte:     {summary['places']} Quell-Orte -> "
        f"{summary['canonical_places']} kanonische Orte "
        f"({summary['place_links']} Links, "
        f"{summary['merge_candidates']} Vorschläge)"
    )
    print(f"Index:    {summary['index_db']}")
    print(f"GeoJSON:  {summary['geojson']}")
    print(
        f"OSM:      {summary['matched_osm']} mit ID, "
        f"{summary['address_places']} Adresspunkte, "
        f"{summary['without_osm_suggestion']} ohne Vorschlag"
    )
    return 0


def _rebuild_index(args: argparse.Namespace, connectors) -> int:
    paths = build_paths(args.data_root)
    summary = build_index(connectors, paths)
    print(
        f"Kanonische Orte: {summary['canonical_places']} "
        f"(Quell-Orte: {summary['places']}, Links: {summary['place_links']}, "
        f"Vorschläge: {summary['merge_candidates']})"
    )
    return 0


def _run_places(args: argparse.Namespace, connectors) -> int:
    paths = build_paths(args.data_root)
    command = args.command
    if command == "candidates":
        return _run_place_candidates(args, paths)
    if command == "stats":
        return _run_place_stats(args, paths)
    if command == "assign-osm":
        return _run_assign_osm(args, paths, connectors)
    if command == "unassign-osm":
        return _run_unassign_osm(args, paths, connectors)
    if command == "osm-candidates":
        return _run_osm_candidates(args, paths)
    if command == "osm-accept":
        return _run_osm_accept(args, paths, connectors)
    if command == "unmatched":
        return _run_unmatched(args, paths)
    if command == "osm-state":
        return _run_set_osm_state(args, paths, connectors)
    if command == "osm-state-clear":
        return _run_clear_osm_state(args, paths, connectors)
    if command == "osm-states":
        return _run_osm_states(args, paths)
    if command == "mark-unresolved":
        return _run_mark_unresolved(args, paths, connectors)
    if command == "audit":
        return _run_place_audit(args, paths)
    if command == "merge":
        try:
            source_a, place_a = place_core.parse_place_ref(args.first)
            source_b, place_b = place_core.parse_place_ref(args.second)
        except ValueError as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            return 2
        if (source_a, place_a) == (source_b, place_b):
            print("Fehler: Quelle und Ziel sind identisch.", file=sys.stderr)
            return 2
        place_core.upsert_rule(
            paths.place_rules_db,
            place_core.PlaceRule(
                source=source_a,
                source_place_id=place_a,
                action="merge",
                target_source=source_b,
                target_source_place_id=place_b,
                note=args.note,
            ),
        )
        print(f"Regel gespeichert: {args.first} -> {args.second}")
        return 0 if args.no_build else _rebuild_index(args, connectors)
    if command in ("unmerge", "reset"):
        try:
            source, source_place_id = place_core.parse_place_ref(args.place)
        except ValueError as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            return 2
        if command == "unmerge":
            place_core.upsert_rule(
                paths.place_rules_db,
                place_core.PlaceRule(
                    source=source,
                    source_place_id=source_place_id,
                    action="standalone",
                    note=args.note,
                ),
            )
            print(f"Auto-Merge deaktiviert: {args.place}")
        else:
            deleted = place_core.delete_rule(
                paths.place_rules_db, source, source_place_id
            )
            print(
                f"Regel entfernt: {args.place}"
                if deleted
                else f"Keine Regel vorhanden: {args.place}"
            )
        return 0 if args.no_build else _rebuild_index(args, connectors)
    print(f"Unbekannter places-Befehl: {command}", file=sys.stderr)
    return 2


def _index_connection(path):
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _run_place_candidates(args: argparse.Namespace, paths) -> int:
    conn = _index_connection(paths.index_db)
    if conn is None:
        print(f"Kein Index vorhanden: {paths.index_db}", file=sys.stderr)
        return 1
    try:
        clauses = []
        params: list = []
        if args.source:
            clauses.append("(source_a = ? OR source_b = ?)")
            params.extend([args.source, args.source])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = f" LIMIT {int(args.limit)}" if args.limit else ""
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT source_a, source_place_id_a, name_a, source_b, "
                "source_place_id_b, name_b, reason, confidence, distance_m, city "
                f"FROM merge_candidates{where} "
                "ORDER BY confidence, name_a, name_b"
                f"{limit}",
                params,
            )
        ]
    finally:
        conn.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("Keine Merge-Vorschläge.")
        return 0
    for row in rows:
        distance = (
            f"{row['distance_m']:.0f} m" if row["distance_m"] is not None else "-"
        )
        city = row["city"] or "-"
        print(
            f"{row['source_a']}:{row['source_place_id_a']}  {row['name_a']}\n"
            f"    <-> {row['source_b']}:{row['source_place_id_b']}  "
            f"{row['name_b']}  [{row['reason']}, {row['confidence']}, "
            f"{distance}, {city}]"
        )
    return 0


def _run_place_audit(args: argparse.Namespace, paths) -> int:
    try:
        summary, matches = place_audit.audit_index(
            paths.index_db,
            source=args.source,
            radius_m=args.radius,
            min_similarity=args.min_similarity,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary.as_dict(),
                    "matches": [match.as_dict() for match in matches],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(
        f"Audit {summary.source}: {summary.source_only} Orts-Gruppen nur aus "
        f"dieser Quelle, {summary.duplicates} Verdachtsfälle "
        f"({summary.within_25m} <=25 m, {summary.within_50m} <=50 m)"
    )
    if summary.duplicates == 0:
        return 0
    labels = {"exact": "exakt", "variant": "Variante", "weak": "schwach"}
    classification = ", ".join(
        f"{labels.get(key, key)}: {value}"
        for key, value in sorted(summary.by_classification.items())
    )
    reasons = ", ".join(
        f"{key}: {value}" for key, value in sorted(summary.by_reason.items())
    )
    print(f"  Kategorie: {classification}")
    print(f"  Ursache:   {reasons}")
    limit = args.limit if args.limit is not None else len(matches)
    for match in matches[:limit]:
        print(
            f"  {match.distance_m:5.1f} m  sim {match.similarity:.2f}  "
            f"[{match.classification}/{match.reason}]\n"
            f"    {'/'.join(match.place.sources)}:"
            f"{match.place.canonical_key}  {match.place.name!r}\n"
            f"    <-> {'/'.join(match.other.sources)}:"
            f"{match.other.canonical_key}  {match.other.name!r}"
        )
    if limit < len(matches):
        print(f"  ... {len(matches) - limit} weitere (--limit erhöhen)")
    return 0


def _run_place_stats(args: argparse.Namespace, paths) -> int:
    conn = _index_connection(paths.index_db)
    if conn is None:
        print(f"Kein Index vorhanden: {paths.index_db}", file=sys.stderr)
        return 1
    try:
        counts = {}
        for table in ("places", "canonical_places", "place_links", "merge_candidates"):
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        merged = conn.execute(
            "SELECT COUNT(*) FROM canonical_places WHERE source_count > 1"
        ).fetchone()[0]
        linked = conn.execute(
            "SELECT COUNT(*) FROM place_links WHERE url IS NOT NULL AND url != ''"
        ).fetchone()[0]
        match_counts = {
            row["match_status"]: row["n"]
            for row in conn.execute(
                "SELECT match_status, COUNT(*) AS n FROM canonical_places "
                "GROUP BY match_status"
            )
        }
        address_places = conn.execute(
            "SELECT COUNT(*) FROM canonical_places WHERE place_kind = 'address'"
        ).fetchone()[0]
        without_suggestion = conn.execute(
            "SELECT COUNT(*) FROM canonical_places "
            "WHERE match_status != 'osm' AND has_osm_candidate = 0"
        ).fetchone()[0]
        candidate_places = conn.execute(
            "SELECT COUNT(*) FROM canonical_places "
            "WHERE match_status != 'osm' AND has_osm_candidate = 1"
        ).fetchone()[0]
        state_counts = {
            row["place_state"]: int(row["n"])
            for row in conn.execute(
                "SELECT place_state, COUNT(*) AS n FROM canonical_places "
                "GROUP BY place_state"
            )
        }
    finally:
        conn.close()
    rules = place_core.load_rules(paths.place_rules_db)
    manual_merges = sum(1 for rule in rules if rule.action == "merge")
    standalone = sum(1 for rule in rules if rule.action == "standalone")
    print(f"Quell-Orte:        {counts['places']}")
    print(f"Kanonische Orte:   {counts['canonical_places']}")
    print(f"Gemergte Orte:     {merged}")
    print(f"Mit OSM-ID:        {match_counts.get('osm', 0)}")
    print(f"Ohne OSM-ID:       {match_counts.get('local', 0)}")
    print(f"OSM-Konflikte:     {match_counts.get('conflict', 0)}")
    print(f"Adresspunkte:      {address_places}")
    print(f"OSM-Kandidaten:    {candidate_places}")
    print(f"Ohne Vorschlag:    {without_suggestion}")
    for state in ("open", "no_osm", "closed", "no_coords", "ignored"):
        print(f"Status {state:<10} {state_counts.get(state, 0)}")
    print(f"Quell-Links:       {counts['place_links']} ({linked} mit URL)")
    print(f"Merge-Vorschläge:  {counts['merge_candidates']}")
    print(f"Manuelle Merges:   {manual_merges}")
    print(f"Standalone-Regeln: {standalone}")
    print(f"Regel-DB:          {paths.place_rules_db}")
    print(f"Identitäts-DB:     {paths.place_identity_db}")
    return 0


def _run_assign_osm(args: argparse.Namespace, paths, connectors) -> int:
    try:
        source, source_place_id = place_core.parse_place_ref(args.place)
        osm_type, osm_id = identity.parse_osm_ref(args.osm)
    except ValueError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    identity.upsert_osm_assignment(
        paths.place_identity_db,
        source,
        source_place_id,
        osm_type,
        osm_id,
        method="manual",
        confidence="manual",
        note=args.note,
    )
    identity.clear_osm_candidates(paths.place_identity_db, source, source_place_id)
    print(f"OSM-Zuordnung gespeichert: {args.place} -> {osm_type}/{osm_id}")
    return 0 if args.no_build else _rebuild_index(args, connectors)


def _run_unassign_osm(args: argparse.Namespace, paths, connectors) -> int:
    try:
        source, source_place_id = place_core.parse_place_ref(args.place)
    except ValueError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    deleted = identity.delete_osm_assignment(
        paths.place_identity_db, source, source_place_id
    )
    print(
        f"OSM-Zuordnung entfernt: {args.place}"
        if deleted
        else f"Keine OSM-Zuordnung vorhanden: {args.place}"
    )
    return 0 if args.no_build else _rebuild_index(args, connectors)


def _run_osm_candidates(args: argparse.Namespace, paths) -> int:
    rows = identity.load_osm_candidates(paths.place_identity_db, limit=args.limit)
    if args.source:
        rows = [row for row in rows if row["source"] == args.source]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("Keine OSM-Kandidaten.")
        return 0
    for row in rows:
        score = f"{row['score']:.2f}" if row["score"] is not None else "-"
        distance = (
            f"{row['distance_m']:.0f} m"
            if row["distance_m"] is not None
            else "-"
        )
        print(
            f"{row['source']}:{row['source_place_id']}  {row['name'] or '-'}\n"
            f"    ~ {row['osm_type']}/{row['osm_id']}  {row['osm_name'] or '-'}  "
            f"[{score}, {distance}, {row['reason'] or '-'}]"
        )
    return 0


def _run_osm_accept(args: argparse.Namespace, paths, connectors) -> int:
    try:
        source, source_place_id = place_core.parse_place_ref(args.place)
    except ValueError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    candidates = [
        row
        for row in identity.load_osm_candidates(paths.place_identity_db)
        if row["source"] == source and row["source_place_id"] == source_place_id
    ]
    if args.osm:
        try:
            osm_type, osm_id = identity.parse_osm_ref(args.osm)
        except ValueError as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            return 2
    elif len(candidates) == 1:
        osm_type = candidates[0]["osm_type"]
        osm_id = int(candidates[0]["osm_id"])
    else:
        print(
            f"Fehler: {len(candidates)} Kandidaten für {args.place}; "
            "bitte mit --osm auswählen.",
            file=sys.stderr,
        )
        return 2
    identity.upsert_osm_assignment(
        paths.place_identity_db,
        source,
        source_place_id,
        osm_type,
        osm_id,
        method="manual",
        confidence="manual",
        note=args.note,
    )
    identity.clear_osm_candidates(paths.place_identity_db, source, source_place_id)
    print(f"OSM-Kandidat übernommen: {args.place} -> {osm_type}/{osm_id}")
    return 0 if args.no_build else _rebuild_index(args, connectors)


def _run_unmatched(args: argparse.Namespace, paths) -> int:
    conn = _index_connection(paths.index_db)
    if conn is None:
        print(f"Kein Index vorhanden: {paths.index_db}", file=sys.stderr)
        return 1
    clauses = ["match_status != 'osm'"]
    params: list = []
    if args.city:
        clauses.append("city = ?")
        params.append(args.city)
    kind = getattr(args, "kind", None)
    if kind:
        clauses.append("place_kind = ?")
        params.append(kind)
    without_candidate = getattr(args, "without_candidate", False)
    if without_candidate:
        clauses.append("has_osm_candidate = 0")
    state = getattr(args, "state", None)
    if state:
        clauses.append("place_state = ?")
        params.append(state)
    limit = f" LIMIT {int(args.limit)}" if args.limit else ""
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT canonical_key, name, city, match_status, osm_type, osm_id, "
                "place_kind, has_osm_candidate, place_state "
                "FROM canonical_places "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY city, name"
                f"{limit}",
                params,
            )
        ]
    finally:
        conn.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("Keine passenden Orte ohne OSM-ID.")
        return 0
    for row in rows:
        suffix = " [Adresse]" if row["place_kind"] == "address" else ""
        proposal = " +Kandidat" if row["has_osm_candidate"] else ""
        state = "" if row["place_state"] == "open" else f" [{row['place_state']}]"
        print(
            f"{row['canonical_key']}  {row['name'] or '-'}  "
            f"[{row['city'] or '-'}, {row['match_status']}{suffix}{proposal}{state}]"
        )
    return 0


def _run_set_osm_state(args: argparse.Namespace, paths, connectors) -> int:
    try:
        source, source_place_id = place_core.parse_place_ref(args.place)
        identity.set_osm_state(
            paths.place_identity_db,
            source,
            source_place_id,
            args.state,
            reason=args.reason,
            note=args.note,
        )
    except ValueError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    print(f"OSM-Status gesetzt: {args.place} -> {args.state}")
    return 0 if args.no_build else _rebuild_index(args, connectors)


def _run_clear_osm_state(args: argparse.Namespace, paths, connectors) -> int:
    try:
        source, source_place_id = place_core.parse_place_ref(args.place)
    except ValueError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    deleted = identity.delete_osm_state(
        paths.place_identity_db, source, source_place_id
    )
    print(
        f"OSM-Status entfernt: {args.place}"
        if deleted
        else f"Kein OSM-Status vorhanden: {args.place}"
    )
    return 0 if args.no_build else _rebuild_index(args, connectors)


def _run_osm_states(args: argparse.Namespace, paths) -> int:
    states = identity.load_osm_states(paths.place_identity_db)
    rows = [
        {"source": source, "source_place_id": place_id, **info}
        for (source, place_id), info in sorted(states.items())
        if not args.state or info["state"] == args.state
    ]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    for state in sorted(counts):
        print(f"{state:<12} {counts[state]}")
    print(f"Gesamt:      {len(rows)}")
    return 0


def _run_mark_unresolved(args: argparse.Namespace, paths, connectors) -> int:
    conn = _index_connection(paths.index_db)
    if conn is None:
        print(f"Kein Index vorhanden: {paths.index_db}", file=sys.stderr)
        return 1
    clauses = [
        "match_status != 'osm'",
        "place_kind = 'venue'",
        "has_osm_candidate = 0",
        "place_state = 'open'",
    ]
    params: list = []
    if args.source:
        clauses.append(
            "EXISTS (SELECT 1 FROM place_links pl "
            "WHERE pl.canonical_id = p.id AND pl.source = ?)"
        )
        params.append(args.source)
    limit = f" LIMIT {int(args.limit)}" if args.limit else ""
    try:
        rows = conn.execute(
            "SELECT p.id, p.canonical_key, p.name FROM canonical_places p "
            f"WHERE {' AND '.join(clauses)} ORDER BY p.name{limit}",
            params,
        ).fetchall()
        members: list[tuple[str, str]] = []
        for row in rows:
            for link in conn.execute(
                "SELECT source, source_place_id FROM place_links "
                "WHERE canonical_id = ?",
                (row["id"],),
            ):
                members.append((link["source"], link["source_place_id"]))
    finally:
        conn.close()
    if args.json:
        print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("Keine offenen, ungelösten Venues gefunden.")
        return 0
    print(f"{len(rows)} Orte -> Status {args.state}")
    if args.dry_run:
        for row in rows:
            print(f"  {row['canonical_key']}  {row['name'] or '-'}")
        print("Dry-Run: keine Statusänderungen geschrieben.")
        return 0
    for source, source_place_id in members:
        identity.set_osm_state(
            paths.place_identity_db,
            source,
            source_place_id,
            args.state,
            reason="mark-unresolved",
            note=args.note,
        )
    return 0 if args.no_build else _rebuild_index(args, connectors)


def _run_thumbs(args: argparse.Namespace) -> int:
    paths = build_paths(args.data_root)
    try:
        summary = generate_thumbnails(
            paths,
            size=args.size,
            force=args.force,
            limit=args.limit,
        )
    except ModuleNotFoundError as exc:
        print(
            f"Thumbnails benötigen Pillow ({exc}). Bitte im venv installieren: "
            ".venv/bin/pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    print(f"Bilder:    {summary['total']}")
    print(f"Neu:       {summary['generated']}")
    print(f"Vorhanden: {summary['skipped']}")
    print(f"Videos:    {summary['videos']}")
    print(f"Fehlend:   {summary['missing']}")
    print(f"Fehler:    {len(summary['errors'])}")
    return 0


def _run_backup(args: argparse.Namespace) -> int:
    paths = build_paths(args.data_root, args.db_root)
    dest = Path(args.dest) if args.dest else paths.backup_dir
    summary = backup_databases(
        paths.db_root,
        dest,
        keep_days=args.keep_days,
    )
    if summary.errors:
        print(
            f"Backup mit {len(summary.errors)} Fehler(n) beendet.",
            file=sys.stderr,
        )
        return 1
    print(
        f"Backup: {len(summary.databases)} Datenbank(en) nach {dest}, "
        f"{len(summary.removed)} alte Tagesordner entfernt."
    )
    return 0


def _run_web(args: argparse.Namespace) -> int:
    if args.data_root:
        os.environ["SPOT_HISTORY_DATA_ROOT"] = args.data_root
    if args.db_root:
        os.environ["SPOT_HISTORY_DB_ROOT"] = args.db_root
    try:
        import uvicorn
    except ImportError:
        print(
            "FastAPI/uvicorn fehlen. Bitte im venv installieren: "
            ".venv/bin/pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    uvicorn.run(
        "spot_history.web.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    connectors = discover()
    parser = argparse.ArgumentParser(
        prog="spot_history",
        description="Check-in-Historie mehrerer Dienste verwalten und zentral indexieren",
    )
    subparsers = parser.add_subparsers(dest="component", required=True)

    for connector in connectors.values():
        connector.add_parser(subparsers)

    index_parser = subparsers.add_parser("index", help="zentralen Index bauen")
    index_commands = index_parser.add_subparsers(dest="command", required=True)
    build_parser = index_commands.add_parser("build", help="Index + GeoJSON neu bauen")
    build_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis (default: ./data)")
    build_parser.set_defaults(func=lambda args: _run_index(args, connectors))

    thumbs_parser = index_commands.add_parser(
        "thumbs", help="Vorschaubilder für die Web-UI erzeugen"
    )
    thumbs_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis (default: ./data)")
    thumbs_parser.add_argument("--size", type=int, default=320, help="maximale Kantenlänge")
    thumbs_parser.add_argument("--force", action="store_true", help="vorhandene Thumbnails neu erzeugen")
    thumbs_parser.add_argument("--limit", type=int, help="maximale Anzahl Bilder")
    thumbs_parser.set_defaults(func=_run_thumbs)

    places_parser = subparsers.add_parser(
        "places", help="kanonische Orte und Quell-Links verwalten"
    )
    place_commands = places_parser.add_subparsers(dest="command", required=True)

    candidates_parser = place_commands.add_parser(
        "candidates", help="vorgeschlagene, nicht automatische Merges anzeigen"
    )
    candidates_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    candidates_parser.add_argument("--source", help="nur Paare dieser Quelle")
    candidates_parser.add_argument("--limit", type=int, help="maximale Anzahl")
    candidates_parser.add_argument("--json", action="store_true")
    candidates_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    merge_parser = place_commands.add_parser(
        "merge", help="zwei Quell-Orte dauerhaft zusammenführen"
    )
    merge_parser.add_argument("first", help="source:id des ersten Orts")
    merge_parser.add_argument("second", help="source:id des Ziel-Orts")
    merge_parser.add_argument("--note", help="Notiz zur Entscheidung")
    merge_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    merge_parser.add_argument(
        "--no-build", action="store_true", help="Index nicht sofort neu bauen"
    )
    merge_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    unmerge_parser = place_commands.add_parser(
        "unmerge", help="Auto-Merge für einen Quell-Ort deaktivieren"
    )
    unmerge_parser.add_argument("place", help="source:id des Orts")
    unmerge_parser.add_argument("--note", help="Notiz zur Entscheidung")
    unmerge_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    unmerge_parser.add_argument(
        "--no-build", action="store_true", help="Index nicht sofort neu bauen"
    )
    unmerge_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    reset_parser = place_commands.add_parser(
        "reset", help="manuelle Regel für einen Quell-Ort entfernen"
    )
    reset_parser.add_argument("place", help="source:id des Orts")
    reset_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    reset_parser.add_argument(
        "--no-build", action="store_true", help="Index nicht sofort neu bauen"
    )
    reset_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    stats_parser = place_commands.add_parser(
        "stats", help="Zahlen zu kanonischen Orten und Regeln anzeigen"
    )
    stats_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    stats_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    assign_parser = place_commands.add_parser(
        "assign-osm", help="einem Quell-Ort manuell eine OSM-ID zuordnen"
    )
    assign_parser.add_argument("place", help="source:id des Quell-Orts")
    assign_parser.add_argument("osm", help="node/123, way-123 oder OSM-URL")
    assign_parser.add_argument("--note", help="Notiz zur Entscheidung")
    assign_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    assign_parser.add_argument(
        "--no-build", action="store_true", help="Index nicht sofort neu bauen"
    )
    assign_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    unassign_parser = place_commands.add_parser(
        "unassign-osm", help="OSM-Zuordnung eines Quell-Orts entfernen"
    )
    unassign_parser.add_argument("place", help="source:id des Quell-Orts")
    unassign_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    unassign_parser.add_argument(
        "--no-build", action="store_true", help="Index nicht sofort neu bauen"
    )
    unassign_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    osm_candidates_parser = place_commands.add_parser(
        "osm-candidates", help="offene OSM-Match-Kandidaten anzeigen"
    )
    osm_candidates_parser.add_argument("--source", help="nur diese Quelle")
    osm_candidates_parser.add_argument("--limit", type=int, help="maximale Anzahl")
    osm_candidates_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    osm_candidates_parser.add_argument("--json", action="store_true")
    osm_candidates_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    osm_accept_parser = place_commands.add_parser(
        "osm-accept", help="OSM-Kandidat als Zuordnung übernehmen"
    )
    osm_accept_parser.add_argument("place", help="source:id des Quell-Orts")
    osm_accept_parser.add_argument("--osm", help="Kandidat auswählen (node/123 ...)")
    osm_accept_parser.add_argument("--note", help="Notiz zur Entscheidung")
    osm_accept_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    osm_accept_parser.add_argument(
        "--no-build", action="store_true", help="Index nicht sofort neu bauen"
    )
    osm_accept_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    unmatched_parser = place_commands.add_parser(
        "unmatched", help="Orte ohne OSM-ID anzeigen"
    )
    unmatched_parser.add_argument("--city", help="nur diese Stadt")
    unmatched_parser.add_argument("--limit", type=int, help="maximale Anzahl")
    unmatched_parser.add_argument(
        "--kind", choices=("venue", "address"), help="nur Venues oder Adresspunkte"
    )
    unmatched_parser.add_argument(
        "--without-candidate",
        action="store_true",
        help="nur Orte ohne OSM-Kandidat",
    )
    unmatched_parser.add_argument("--state", choices=identity.OSM_STATES)
    unmatched_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    unmatched_parser.add_argument("--json", action="store_true")
    unmatched_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    osm_state_parser = place_commands.add_parser(
        "osm-state", help="OSM-Status eines Quell-Orts setzen"
    )
    osm_state_parser.add_argument("place", help="source:id des Quell-Orts")
    osm_state_parser.add_argument(
        "state",
        choices=identity.OSM_STATES,
        help="open, no_osm, closed, no_coords oder ignored",
    )
    osm_state_parser.add_argument("--reason", help="Grund")
    osm_state_parser.add_argument("--note", help="Notiz")
    osm_state_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    osm_state_parser.add_argument(
        "--no-build", action="store_true", help="Index nicht sofort neu bauen"
    )
    osm_state_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    osm_state_clear_parser = place_commands.add_parser(
        "osm-state-clear", help="OSM-Status eines Quell-Orts entfernen"
    )
    osm_state_clear_parser.add_argument("place", help="source:id des Quell-Orts")
    osm_state_clear_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    osm_state_clear_parser.add_argument(
        "--no-build", action="store_true", help="Index nicht sofort neu bauen"
    )
    osm_state_clear_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    osm_states_parser = place_commands.add_parser(
        "osm-states", help="gesetzte OSM-Status anzeigen"
    )
    osm_states_parser.add_argument("--state", choices=identity.OSM_STATES)
    osm_states_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    osm_states_parser.add_argument("--json", action="store_true")
    osm_states_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    mark_parser = place_commands.add_parser(
        "mark-unresolved",
        help="offene Venues ohne OSM/Kandidat gesammelt markieren",
    )
    mark_parser.add_argument(
        "--state",
        choices=("no_osm", "closed", "ignored"),
        default="no_osm",
        help="Zielstatus (default: no_osm)",
    )
    mark_parser.add_argument("--source", help="nur Orte dieser Quelle")
    mark_parser.add_argument("--limit", type=int, help="maximale Anzahl Orte")
    mark_parser.add_argument("--note", help="Notiz")
    mark_parser.add_argument("--dry-run", action="store_true")
    mark_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    mark_parser.add_argument("--json", action="store_true")
    mark_parser.add_argument(
        "--no-build", action="store_true", help="Index nicht sofort neu bauen"
    )
    mark_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    audit_parser = place_commands.add_parser(
        "audit", help="verdächtige, noch nicht gemergte Orts-Duplikate anzeigen"
    )
    audit_parser.add_argument(
        "--source", default="google", help="nur Orte dieser Quelle prüfen"
    )
    audit_parser.add_argument(
        "--radius", type=float, default=50.0, help="Suchradius in Metern"
    )
    audit_parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.78,
        help="minimale Namens-Ähnlichkeit",
    )
    audit_parser.add_argument("--limit", type=int, help="maximale Anzahl Paare")
    audit_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis")
    audit_parser.add_argument("--json", action="store_true")
    audit_parser.set_defaults(func=lambda args: _run_places(args, connectors))

    backup_parser = subparsers.add_parser(
        "backup", help="SQLite-Datenbanken konsistent sichern"
    )
    backup_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis (default: ./data)")
    backup_parser.add_argument("--db-root", help="SQLite-Verzeichnis (default: ./db)")
    backup_parser.add_argument(
        "--dest",
        help="Zielverzeichnis (default: SPOT_HISTORY_BACKUP_ROOT, sonst <data-root>/backup)",
    )
    backup_parser.add_argument(
        "--keep-days",
        type=int,
        default=3,
        help="Anzahl aufzubewahrender Tages-Backups (default: 3)",
    )
    backup_parser.set_defaults(func=_run_backup)

    web_parser = subparsers.add_parser("web", help="Web-Oberfläche starten")
    web_parser.add_argument("--host", default="0.0.0.0")
    web_parser.add_argument("--port", type=int, default=8345)
    web_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis (default: ./data)")
    web_parser.add_argument("--db-root", help="SQLite-Verzeichnis (default: ./db)")
    web_parser.add_argument("--reload", action="store_true")
    web_parser.set_defaults(func=_run_web)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
