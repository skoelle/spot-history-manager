# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import argparse
from pathlib import Path

from ...core import identity
from ...core.config import build_paths
from ..base import BaseConnector, Visit

SOURCE = "osm"


class OsmConnector(BaseConnector):
    name = SOURCE
    kind = "import"

    def add_parser(self, subparsers: argparse._SubParsersAction) -> None:
        parser = subparsers.add_parser(
            self.name, help="OpenStreetMap-Zuordnung und POI-Cache"
        )
        parser.set_defaults(connector=self)
        commands = parser.add_subparsers(dest="command", required=True)

        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--data-root", help="Zentrales Datenverzeichnis (default: ./data)")
        common.add_argument("--db", help="Pfad zur OSM-Cache-DB")

        sync_parser = commands.add_parser(
            "sync", parents=[common], help="offene Orte über Overpass/Nominatim matchen"
        )
        sync_parser.add_argument(
            "--radius", type=int, default=120, help="Suchradius in Metern (default: 120)"
        )
        sync_parser.add_argument(
            "--chunk", type=int, default=40, help="Punkte pro Overpass-Anfrage"
        )
        sync_parser.add_argument("--force", action="store_true", help="Cache erneut abfragen")
        sync_parser.add_argument("--limit", type=int, help="maximale Anzahl Orte")
        sync_parser.add_argument(
            "--dry-run", action="store_true", help="nur anzeigen, nichts schreiben"
        )
        sync_parser.add_argument(
            "--sleep", type=float, default=2.0, help="Pause zwischen Overpass-Anfragen"
        )
        sync_parser.add_argument(
            "--provider",
            choices=("auto", "overpass", "nominatim"),
            default="auto",
            help="auto: Overpass mit Nominatim-Fallback (default)",
        )
        sync_parser.add_argument("--verbose", action="store_true", help="mehr Ausgaben")
        sync_parser.set_defaults(func=self.run)

        rematch_parser = commands.add_parser(
            "rematch",
            parents=[common],
            help="offene Orte offline gegen den POI-Cache matchen",
        )
        rematch_parser.add_argument(
            "--radius", type=int, default=120, help="Suchradius in Metern (default: 120)"
        )
        rematch_parser.add_argument("--limit", type=int, help="maximale Anzahl Orte")
        rematch_parser.add_argument(
            "--dry-run", action="store_true", help="nur anzeigen, nichts schreiben"
        )
        rematch_parser.add_argument("--verbose", action="store_true", help="mehr Ausgaben")
        rematch_parser.set_defaults(func=self.run)

        stats_parser = commands.add_parser(
            "stats", parents=[common], help="Cache- und Zuordnungszahlen anzeigen"
        )
        stats_parser.set_defaults(func=self.run)

    def _db_path(self, args: argparse.Namespace, paths) -> Path:
        return Path(args.db) if args.db else paths.connector_dir("osm") / "osm.db"

    def run(self, args: argparse.Namespace) -> int:
        paths = build_paths(args.data_root)
        if args.command == "stats":
            return self._stats(args, paths)
        if args.command == "sync":
            return self._sync(args, paths)
        if args.command == "rematch":
            return self._rematch(args, paths)
        print(f"Unbekannter osm-Befehl: {args.command}")
        return 2

    def _sync(self, args: argparse.Namespace, paths) -> int:
        from .sync import sync

        try:
            summary = sync(
                paths,
                radius=args.radius,
                chunk=args.chunk,
                force=args.force,
                limit=args.limit,
                dry_run=args.dry_run,
                sleep=args.sleep,
                verbose=args.verbose,
                provider=args.provider,
                osm_db_path=self._db_path(args, paths),
            )
        except FileNotFoundError as exc:
            print(f"Fehler: {exc}")
            return 1
        print(f"Orte gesamt:       {summary.places}")
        print(f"Offen:             {summary.pending}")
        print(f"Abfragen:          {summary.queries} neu, {summary.cached_queries} aus Cache")
        print(f"POIs gespeichert:  {summary.pois}")
        print(f"Automatisch:       {summary.auto_matched}")
        print(f"Kandidaten:        {summary.candidates}")
        print(f"Adresspunkte:      {summary.addresses}")
        print(f"Fehler:            {summary.errors}")
        if args.dry_run:
            print("Dry-Run: keine Zuordnungen geschrieben.")
        print("Danach: python3 -m spot_history index build")
        return 0

    def _rematch(self, args: argparse.Namespace, paths) -> int:
        from .sync import rematch

        try:
            summary = rematch(
                paths,
                radius=args.radius,
                limit=args.limit,
                dry_run=args.dry_run,
                verbose=args.verbose,
                osm_db_path=self._db_path(args, paths),
            )
        except FileNotFoundError as exc:
            print(f"Fehler: {exc}")
            return 1
        print(f"Orte gesamt:       {summary.places}")
        print(f"Offen:             {summary.pending}")
        print(f"Automatisch:       {summary.auto_matched}")
        print(f"Kandidaten:        {summary.candidates}")
        print(f"Ohne Treffer:      {summary.none}")
        print(f"Adresspunkte:      {summary.addresses}")
        if args.dry_run:
            print("Dry-Run: keine Zuordnungen/Kandidaten geschrieben.")
        print("Danach: python3 -m spot_history index build")
        return 0

    def _stats(self, args: argparse.Namespace, paths) -> int:
        db_path = self._db_path(args, paths)
        if not db_path.exists():
            print(f"Kein OSM-Cache vorhanden: {db_path}")
        else:
            from . import db as osm_db

            conn = osm_db.open_db(db_path)
            try:
                data = osm_db.stats(conn)
            finally:
                conn.close()
            counts = data["counts"]
            print(f"OSM-Cache:         {db_path}")
            print(f"POIs:              {counts['pois']}")
            print(f"Overpass-Queries:  {counts['query_cache']}")
            print(f"Nominatim-Queries: {counts['nominatim_cache']}")
            print(f"Sync-Läufe:        {counts['sync_runs']}")
            last = data["last_run"]
            if last:
                print(
                    f"Letzter Lauf:      {last['started_at']} "
                    f"({last['auto_matched']} auto, {last['candidates']} Kandidaten)"
                )
        assignments = identity.load_osm_assignments(paths.place_identity_db)
        by_method: dict[str, int] = {}
        for assignment in assignments.values():
            by_method[assignment.method] = by_method.get(assignment.method, 0) + 1
        candidates = identity.load_osm_candidates(paths.place_identity_db)
        print(f"Zuordnungen:       {len(assignments)}")
        for method in sorted(by_method):
            print(f"  {method:<15}  {by_method[method]}")
        print(f"Offene Kandidaten: {len(candidates)}")
        print(f"Identitäts-DB:     {paths.place_identity_db}")
        return 0

    def iter_visits(self) -> list[Visit]:
        return []


CONNECTOR = OsmConnector()
