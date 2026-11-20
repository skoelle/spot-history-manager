from __future__ import annotations

import argparse
import sys

from .api import FoursquareAPI
from .config import build_config
from .db import Database, iso_utc
from .export import export_all
from .photos import download_pending
from .sync import Sync


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data-dir", help="Datenverzeichnis (default: ./data)")
    parser.add_argument("--db", help="Pfad zur SQLite-Datei")
    parser.add_argument("--sleep", type=float, help="Pause zwischen API-Requests")
    parser.add_argument("--no-photos", action="store_true", help="keine Bilddateien laden")
    parser.add_argument("--no-export", action="store_true", help="keine JSON/CSV-Exporte")
    parser.add_argument("--verbose", action="store_true", help="mehr Ausgaben")
    return parser


def _print_stats(db: Database) -> None:
    stats = db.stats()
    print(f"Check-ins:        {stats['total']}")
    if stats["oldest"]:
        print(f"Zeitraum:         {iso_utc(stats['oldest'])} .. {iso_utc(stats['newest'])}")
    print(f"Venues:           {stats['venues']}")
    print(f"Fotos:            {stats['photos_downloaded']}/{stats['photos_total']} geladen")
    print(f"Kommentare:       {stats['comments']}")
    print(f"Likes:            {stats['likes']}")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spot_history",
        description="Foursquare/Swarm Check-in-Historie laden und verwalten",
    )
    common = _common_parser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "sync", parents=[common], help="inkrementell synchronisieren (Backfill wenn leer)"
    )
    subparsers.add_parser(
        "backfill", parents=[common], help="komplette Historie (erneut) laden"
    )
    subparsers.add_parser(
        "photos", parents=[common], help="offene Fotos herunterladen"
    )
    subparsers.add_parser(
        "export", parents=[common], help="JSON/CSV aus der Datenbank schreiben"
    )
    subparsers.add_parser(
        "stats", parents=[common], help="Zusammenfassung anzeigen"
    )
    args = parser.parse_args(argv)

    cfg = build_config(
        data_dir=args.data_dir,
        db_path=args.db,
        sleep=args.sleep,
        download_photos=not args.no_photos,
        export=not args.no_export,
        verbose=args.verbose,
    )
    cfg.ensure_dirs()
    db = Database(cfg.db_path).open()
    try:
        if args.command == "stats":
            _print_stats(db)
            return 0
        if args.command == "export":
            export_all(db, cfg)
            return 0
        if args.command == "photos":
            download_pending(db, cfg)
            export_all(db, cfg)
            return 0

        api = FoursquareAPI(
            token=cfg.token,
            version=cfg.api_version,
            base_url=cfg.base_url,
            timeout=cfg.timeout,
            sleep=cfg.sleep,
            retries=cfg.retries,
            verbose=cfg.verbose,
        )
        sync = Sync(api, db, cfg)
        if args.command == "backfill":
            sync.run("backfill")
        else:
            sync.run("auto")
        _print_stats(db)
        return 0
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        return 130
    finally:
        db.close()
