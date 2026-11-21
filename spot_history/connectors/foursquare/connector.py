from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ...core.config import ROOT
from ..base import BaseConnector, Visit

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
            ("venue-import", "Places-API-Baseline aus JSON importieren"),
            ("export", "JSON/CSV aus der Datenbank schreiben"),
            ("stats", "Zusammenfassung anzeigen"),
            ("probe", "API-Endpunkte nur lesend prüfen"),
        ):
            sub = commands.add_parser(command, parents=[common], help=help_text)
            if command == "venue-import":
                sub.add_argument(
                    "--file",
                    help="JSON-Datei (Default: data/foursquare/places_api_venue_details.json)",
                )
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
            require_token=args.command != "venue-import",
        )
        config.ensure_dirs()
        db = Database(config.db_path).open()
        try:
            if args.command == "stats":
                self.print_stats(db)
                return 0
            if args.command == "venue-import":
                from .venue_import import import_venue_details

                source = (
                    Path(args.file)
                    if args.file
                    else config.data_dir / "places_api_venue_details.json"
                )
                import_venue_details(db, source)
                if config.export:
                    export_all(db, config)
                self.print_stats(db)
                return 0
            if args.command == "export":
                export_all(db, config)
                return 0
            if args.command == "photos":
                download_pending(db, config)
                export_all(db, config)
                return 0

            is_probe = args.command == "probe"
            api = FoursquareAPI(
                token=config.token,
                version=config.api_version,
                base_url=config.base_url,
                timeout=config.timeout,
                sleep=config.sleep,
                retries=1 if is_probe else config.retries,
                verbose=config.verbose,
            )
            if is_probe:
                places = None
                if config.service_api_key:
                    from .places_api import PlacesAPI

                    places = PlacesAPI(
                        service_key=config.service_api_key,
                        base_url=config.places_base_url,
                        api_version=config.places_api_version,
                        timeout=config.timeout,
                        sleep=0.0,
                        retries=1,
                        verbose=config.verbose,
                    )
                self.probe(api, db, places)
                return 0
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
    def _probe_status(exc: Exception) -> str:
        text = str(exc)
        for code in ("401", "402", "403", "404", "429"):
            if text.startswith(code) or f"({code})" in text or f"{code}:" in text:
                return code
        return "ERR"

    def probe(self, api, db, places=None) -> None:
        from .api import ApiError, AuthError
        from .places_api import PlacesApiError

        print("API-Probe (nur lesend):", flush=True)
        endpoints = [
            "users/self",
            "users/self/checkins",
            "users/self/photos",
            "users/self/venuehistory",
            "users/self/lists",
            "users/self/tips",
            "users/self/tastes",
            "users/self/activities",
            "users/self/mayorships",
            "users/self/todos",
            "users/self/friends",
            "users/self/badges",
            "users/self/events",
            "users/self/likes",
            "users/self/herenow",
        ]
        row = db.conn.execute(
            "SELECT venue_id, lat, lng FROM checkins "
            "WHERE venue_id IS NOT NULL AND lat IS NOT NULL AND lng IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        venue_id = row["venue_id"] if row else None
        ll = f"{row['lat']},{row['lng']}" if row else "48.1372,11.5756"
        if venue_id:
            endpoints.append(f"venues/{venue_id}")
        for path in endpoints:
            params = {} if path == "users/self" else {"limit": "1"}
            if path.startswith("venues/"):
                params = {}
            try:
                api._request(path, params)
                status = "OK"
            except (AuthError, ApiError) as exc:
                status = self._probe_status(exc)
            except Exception:
                status = "ERR"
            print(f"  {path:<32} {status}", flush=True)

        if places is None:
            print(
                "\nPlaces API: übersprungen (FOURSQUARE_SERVICE_API_KEY fehlt)",
                flush=True,
            )
            return

        print("\nPlaces API (nur lesend):", flush=True)
        checks: list[tuple[str, str, dict]] = []
        if venue_id:
            checks.extend(
                [
                    ("places/{id} (Baseline)", f"places/{venue_id}", {}),
                    (
                        "places/{id} (Credits)",
                        f"places/{venue_id}",
                        {"fields": "hours,photos,tips,rating,price"},
                    ),
                    ("places/{id}/photos", f"places/{venue_id}/photos", {"limit": "1"}),
                    ("places/{id}/tips", f"places/{venue_id}/tips", {"limit": "1"}),
                ]
            )
        checks.extend(
            [
                (
                    "places/search",
                    "places/search",
                    {"ll": ll, "radius": "500", "limit": "1"},
                ),
                ("autocomplete", "autocomplete", {"query": "Cafe", "ll": ll, "limit": "1"}),
            ]
        )
        for label, path, params in checks:
            try:
                places.get(path, params)
                status = "OK"
            except PlacesApiError as exc:
                status = self._probe_status(exc)
            except Exception:
                status = "ERR"
            print(f"  {label:<32} {status}", flush=True)
        print(
            "Hinweis: Nur Baseline-Details sind ohne Credits verfügbar; "
            "Foto-, Tipp- und Premium-Felder liefern 429.",
            flush=True,
        )

    @staticmethod
    def print_stats(db) -> None:
        from .db import iso_utc

        stats = db.stats()
        print(f"Check-ins:        {stats['total']}")
        if stats["oldest"]:
            print(f"Zeitraum:         {iso_utc(stats['oldest'])} .. {iso_utc(stats['newest'])}")
        print(f"Venues:           {stats['venues']}")
        if stats.get("venue_details"):
            print(
                f"Venue-Details:    {stats['venue_details']} "
                f"({stats['venue_contacts']} mit Kontakt)"
            )
        print(f"Fotos:            {stats['photos_downloaded']}/{stats['photos_total']} geladen")
        if stats["standalone_photos"]:
            print(f"Standalone-Fotos: {stats['standalone_photos']}")
        print(f"Kommentare:       {stats['comments']}")
        print(f"Likes:            {stats['likes']}")
        print(f"Venue-History:    {stats['venue_history']}")
        print(f"Listen:           {stats['lists']} ({stats['list_items']} Einträge)")
        print(f"Tipps:            {stats['tips']}")
        print(f"Tastes:           {stats['tastes']}")
        if stats["profile_name"]:
            print(f"Profil:           {stats['profile_name']}")
        print(f"Letzter Sync:     {stats['last_sync_at'] or '-'}")
        if stats["last_max_created_at"]:
            print(f"Daten bis:        {iso_utc(stats['last_max_created_at'])}")
        print(f"Backfill fertig:  {'ja' if stats['backfill_done'] == '1' else 'nein'}")
        if stats["api_total"]:
            print(f"API-Gesamtzahl:   {stats['api_total']}")
        if stats["diagnostics"]:
            print("\nDatenlücken:")
            for diag in stats["diagnostics"]:
                if diag["api_count"] is None:
                    print(f"  {diag['scope']:<14} {diag['note'] or '-'}")
                    continue
                line = (
                    f"  {diag['scope']:<14} {diag['stored_count']}/"
                    f"{diag['api_count']}"
                )
                if diag["missing_count"]:
                    line += f" ({diag['missing_count']} fehlend)"
                if diag["note"]:
                    line += f" · {diag['note']}"
                print(line)
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
            comments: dict[str, list[str]] = {}
            for row in conn.execute(
                "SELECT checkin_id, user_display_name, text FROM checkin_comments "
                "ORDER BY checkin_id, created_at, id"
            ):
                text = (row["text"] or "").strip()
                if not text:
                    continue
                author = (row["user_display_name"] or "").strip()
                comments.setdefault(row["checkin_id"], []).append(
                    f"{author}: {text}" if author else text
                )
            likes: dict[str, list[str]] = {}
            for row in conn.execute(
                "SELECT checkin_id, display_name FROM likes "
                "ORDER BY checkin_id, display_name"
            ):
                name = (row["display_name"] or "").strip()
                if name:
                    likes.setdefault(row["checkin_id"], []).append(name)
            rows = conn.execute(
                "SELECT id, created_at, lat, lng, venue_id, venue_name, category_name, "
                "address, city, state, postal_code, country_code, shout, canonical_url, "
                "time_zone_offset, time_zone FROM checkins ORDER BY created_at"
            )
            for row in rows:
                extra: dict[str, object] = {}
                if comments.get(row["id"]):
                    extra["comments"] = comments[row["id"]]
                if likes.get(row["id"]):
                    extra["like_count"] = len(likes[row["id"]])
                    extra["likes"] = likes[row["id"]]
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
                    place_url=(
                        f"https://foursquare.com/v/{row['venue_id']}"
                        if row["venue_id"] else None
                    ),
                    tz_offset=row["time_zone_offset"],
                    time_zone=row["time_zone"],
                    photos=photos.get(row["id"], []),
                    extra=extra or None,
                )
        finally:
            conn.close()

    def iter_place_details(self):
        from .config import build_config

        config = build_config(require_token=False)
        db_path = Path(config.db_path)
        if not db_path.exists():
            return
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT venue_id, name, tel, website, email, social_json, "
                "address, city, state, postal_code, country_code, "
                "formatted_address, lat, lng, categories_json, date_created, "
                "date_refreshed, placemaker_url, has_related, fetched_at, "
                "updated_at FROM venue_details"
            )
            for row in rows:
                yield {
                    "source": SOURCE,
                    "source_place_id": row["venue_id"],
                    "name": row["name"],
                    "tel": row["tel"],
                    "website": row["website"],
                    "email": row["email"],
                    "social_json": row["social_json"],
                    "address": row["address"],
                    "city": row["city"],
                    "state": row["state"],
                    "postal_code": row["postal_code"],
                    "country_code": row["country_code"],
                    "formatted_address": row["formatted_address"],
                    "lat": row["lat"],
                    "lng": row["lng"],
                    "categories_json": row["categories_json"],
                    "date_created": row["date_created"],
                    "date_refreshed": row["date_refreshed"],
                    "placemaker_url": row["placemaker_url"],
                    "has_related": row["has_related"],
                    "fetched_at": row["fetched_at"],
                    "updated_at": row["updated_at"],
                }
        finally:
            conn.close()

    def iter_records(self):
        from .config import build_config

        config = build_config(require_token=False)
        db_path = Path(config.db_path)
        if not db_path.exists():
            return
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        def date_label(value):
            if not value:
                return None
            try:
                return datetime.fromtimestamp(
                    int(value), timezone.utc
                ).strftime("%d.%m.%Y")
            except (TypeError, ValueError, OSError):
                return None

        def iso_timestamp(value):
            if not value:
                return None
            try:
                moment = datetime.fromisoformat(str(value))
            except ValueError:
                return None
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return int(moment.timestamp())

        try:
            for row in conn.execute("SELECT * FROM tips ORDER BY created_at"):
                yield {
                    "source": SOURCE,
                    "record_id": f"tip:{row['id']}",
                    "kind": "tip",
                    "source_place_id": row["venue_id"],
                    "created_at": row["created_at"],
                    "payload": {
                        "tip_id": row["id"],
                        "venue_name": row["venue_name"],
                        "text": row["text"],
                        "canonical_url": row["canonical_url"],
                        "lang": row["lang"],
                        "saves_count": row["saves_count"],
                        "title": row["title"],
                    },
                }

            items_by_list: dict[str, list[dict]] = {}
            for item in conn.execute(
                "SELECT list_id, venue_id, venue_name, note FROM list_items "
                "ORDER BY venue_name, venue_id"
            ):
                items_by_list.setdefault(item["list_id"], []).append(
                    {
                        "venue_id": item["venue_id"],
                        "venue_name": item["venue_name"],
                        "note": item["note"],
                    }
                )
            for row in conn.execute("SELECT * FROM lists ORDER BY name"):
                items = items_by_list.get(row["id"], [])
                yield {
                    "source": SOURCE,
                    "record_id": f"list:{row['id']}",
                    "kind": "list",
                    "source_place_id": None,
                    "created_at": iso_timestamp(row["updated_at"]),
                    "payload": {
                        "list_id": row["id"],
                        "name": row["name"],
                        "type": row["type"],
                        "visibility": row["visibility"],
                        "count": row["count"],
                        "item_count": len(items),
                        "items": items,
                    },
                }

            for row in conn.execute("SELECT * FROM tastes ORDER BY text"):
                yield {
                    "source": SOURCE,
                    "record_id": f"taste:{row['id']}",
                    "kind": "taste",
                    "source_place_id": None,
                    "created_at": None,
                    "payload": {
                        "taste_id": row["id"],
                        "text": row["text"],
                        "on_user": bool(row["on_user"]),
                    },
                }

            comments: dict[str, list[dict]] = {}
            meta: dict[str, dict] = {}
            for row in conn.execute(
                "SELECT c.id, c.checkin_id, c.created_at, c.user_display_name, "
                "c.text, ch.venue_id, ch.venue_name FROM checkin_comments c "
                "JOIN checkins ch ON ch.id = c.checkin_id "
                "ORDER BY c.created_at, c.id"
            ):
                checkin_id = row["checkin_id"]
                meta.setdefault(
                    checkin_id,
                    {
                        "venue_id": row["venue_id"],
                        "venue_name": row["venue_name"],
                    },
                )
                comments.setdefault(checkin_id, []).append(
                    {
                        "id": row["id"],
                        "author": row["user_display_name"],
                        "text": row["text"],
                        "created_at": row["created_at"],
                        "date": date_label(row["created_at"]),
                    }
                )
            for checkin_id, rows in comments.items():
                info = meta.get(checkin_id) or {}
                created_at = max(
                    (int(row["created_at"]) for row in rows if row["created_at"]),
                    default=None,
                )
                yield {
                    "source": SOURCE,
                    "record_id": f"comments:{checkin_id}",
                    "kind": "foursquare_comment",
                    "source_place_id": info.get("venue_id"),
                    "created_at": created_at,
                    "payload": {
                        "checkin_id": checkin_id,
                        "venue_name": info.get("venue_name"),
                        "comments": rows,
                    },
                }

            likes: dict[str, list[str]] = {}
            like_meta: dict[str, dict] = {}
            for row in conn.execute(
                "SELECT l.checkin_id, l.display_name, ch.venue_id, ch.venue_name, "
                "ch.created_at FROM likes l "
                "JOIN checkins ch ON ch.id = l.checkin_id "
                "ORDER BY l.display_name, l.user_id"
            ):
                checkin_id = row["checkin_id"]
                like_meta.setdefault(
                    checkin_id,
                    {
                        "venue_id": row["venue_id"],
                        "venue_name": row["venue_name"],
                        "created_at": row["created_at"],
                    },
                )
                if row["display_name"]:
                    likes.setdefault(checkin_id, []).append(row["display_name"])
            for checkin_id, people in likes.items():
                info = like_meta.get(checkin_id) or {}
                yield {
                    "source": SOURCE,
                    "record_id": f"likes:{checkin_id}",
                    "kind": "foursquare_like",
                    "source_place_id": info.get("venue_id"),
                    "created_at": info.get("created_at"),
                    "payload": {
                        "checkin_id": checkin_id,
                        "venue_name": info.get("venue_name"),
                        "count": len(people),
                        "people": people,
                    },
                }
        finally:
            conn.close()

    def iter_profile(self):
        from .config import build_config

        config = build_config(require_token=False)
        db_path = Path(config.db_path)
        if not db_path.exists():
            return None
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM user_profile ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()
        if not row:
            return None
        counts = {
            key: int(row[f"{key}_count"] or 0)
            for key in (
                "checkins", "friends", "followers", "following", "lists",
                "photos", "tips", "mayorships",
            )
        }
        photo_path = row["photo_path"]
        if photo_path:
            absolute = Path(photo_path)
            if not absolute.is_absolute():
                absolute = ROOT / absolute
            try:
                photo_path = str(absolute.relative_to(ROOT))
            except ValueError:
                photo_path = str(absolute)
        return {
            "source": SOURCE,
            "source_profile_id": row["id"],
            "display_name": row["display_name"],
            "home_city": row["home_city"],
            "country_code": row["country_code"],
            "photo_path": photo_path,
            "counts": counts,
            "updated_at": row["updated_at"],
            "counts_json": json.dumps(counts, ensure_ascii=False),
        }


CONNECTOR = FoursquareConnector()
