from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from ..base import BaseConnector, Visit
from ...core.config import ROOT

SOURCE = "foursquare"


class FoursquareConnector(BaseConnector):
    name = SOURCE
    kind = "sync"

    def add_parser(self, subparsers: argparse._SubParsersAction) -> None:
        parser = subparsers.add_parser(self.name, help="Foursquare/Swarm Check-ins")
        parser.set_defaults(connector=self)
        commands = parser.add_subparsers(dest="command", required=True)

        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--data-dir", help="Datenverzeichnis des Connectors")
        common.add_argument("--db", help="Pfad zur SQLite-Datei")
        common.add_argument("--sleep", type=float, help="Pause zwischen API-Requests")
        common.add_argument("--no-photos", action="store_true", help="keine Bilddateien laden")
        common.add_argument("--no-export", action="store_true", help="keine JSON/CSV-Exporte")
        common.add_argument("--verbose", action="store_true", help="mehr Ausgaben")

        for command, help_text in (
            ("sync", "inkrementell synchronisieren (Backfill wenn leer)"),
            ("backfill", "komplette Historie (erneut) laden"),
            ("photos", "offene Fotos herunterladen"),
            ("export", "JSON/CSV aus der Datenbank schreiben"),
            ("stats", "Zusammenfassung anzeigen"),
        ):
            sub = commands.add_parser(command, parents=[common], help=help_text)
            sub.set_defaults(func=self.run)

    def run(self, args: argparse.Namespace) -> int:
        from .api import FoursquareAPI
        from .config import build_config
        from .db import Database
        from .export import export_all
        from .photos import download_pending
        from .sync import Sync

        config = build_config(
            data_dir=args.data_dir,
            db_path=args.db,
            sleep=args.sleep,
            download_photos=not args.no_photos,
            export=not args.no_export,
            verbose=args.verbose,
        )
        config.ensure_dirs()
        db = Database(config.db_path).open()
        try:
            if args.command == "stats":
                self.print_stats(db)
                return 0
            if args.command == "export":
                export_all(db, config)
                return 0
            if args.command == "photos":
                download_pending(db, config)
                export_all(db, config)
                return 0

            api = FoursquareAPI(
                token=config.token,
                version=config.api_version,
                base_url=config.base_url,
                timeout=config.timeout,
                sleep=config.sleep,
                retries=config.retries,
                verbose=config.verbose,
            )
            sync = Sync(api, db, config)
            if args.command == "backfill":
                sync.run("backfill")
            else:
                sync.run("auto")
            self.print_stats(db)
            return 0
        finally:
            db.close()

    @staticmethod
    def print_stats(db) -> None:
        from .db import iso_utc

        stats = db.stats()
        print(f"Check-ins:        {stats['total']}")
        if stats["oldest"]:
            print(f"Zeitraum:         {iso_utc(stats['oldest'])} .. {iso_utc(stats['newest'])}")
        print(f"Venues:           {stats['venues']}")
        print(f"Fotos:            {stats['photos_downloaded']}/{stats['photos_total']} geladen")
        if stats["standalone_photos"]:
            print(f"Standalone-Fotos: {stats['standalone_photos']}")
        print(f"Kommentare:       {stats['comments']}")
        print(f"Likes:            {stats['likes']}")
        print(f"Venue-History:    {stats['venue_history']}")
        print(f"Listen:           {stats['lists']} ({stats['list_items']} Einträge)")
        print(f"Tipps:            {stats['tips']}")
        print(f"Letzter Sync:     {stats['last_sync_at'] or '-'}")
        if stats["last_max_created_at"]:
            print(f"Daten bis:        {iso_utc(stats['last_max_created_at'])}")
        print(f"Backfill fertig:  {'ja' if stats['backfill_done'] == '1' else 'nein'}")
        if stats["api_total"]:
            print(f"API-Gesamtzahl:   {stats['api_total']}")
        for label, key in (
            ("Top Kategorien", "top_categories"),
            ("Top Städte", "top_cities"),
            ("Top Venues", "top_venues"),
        ):
            rows = stats[key]
            if rows:
                print(f"\n{label}:")
                for row in rows:
                    print(f"  {row['n']:>5}  {row['name']}")

    def iter_visits(self):
        from .config import build_config

        config = build_config(require_token=False)
        db_path = Path(config.db_path)
        if not db_path.exists():
            return
        photos: dict[str, list] = {}
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            for row in conn.execute(
                "SELECT checkin_id, id, url, downloaded_path, created_at FROM photos"
            ):
                local_path = None
                if row["downloaded_path"]:
                    absolute = config.data_dir / row["downloaded_path"]
                    try:
                        local_path = str(absolute.relative_to(ROOT))
                    except ValueError:
                        local_path = str(absolute)
                from ..base import Photo

                photos.setdefault(row["checkin_id"], []).append(
                    Photo(
                        source_photo_id=row["id"],
                        url=row["url"],
                        local_path=local_path,
                        taken_at=row["created_at"],
                    )
                )
            rows = conn.execute(
                "SELECT id, created_at, lat, lng, venue_id, venue_name, category_name, "
                "address, city, state, postal_code, country_code, shout, canonical_url, "
                "time_zone_offset FROM checkins ORDER BY created_at"
            )
            for row in rows:
                yield Visit(
                    source=SOURCE,
                    source_visit_id=row["id"],
                    visited_at=int(row["created_at"]),
                    lat=row["lat"],
                    lng=row["lng"],
                    place_name=row["venue_name"],
                    source_place_id=row["venue_id"],
                    category=row["category_name"],
                    address=row["address"],
                    city=row["city"],
                    state=row["state"],
                    postal_code=row["postal_code"],
                    country_code=row["country_code"],
                    note=row["shout"],
                    source_url=row["canonical_url"],
                    tz_offset=row["time_zone_offset"],
                    photos=photos.get(row["id"], []),
                )
        finally:
            conn.close()


CONNECTOR = FoursquareConnector()
