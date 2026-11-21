from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..base import BaseConnector, Visit
from ..osm.db import Poi
from .config import GoogleConfig, build_config
from .db import Database, open_read_only
from .scoring import poi_category

SOURCE = "google"


def _place_url(place_id: str | None) -> str | None:
    if not place_id:
        return None
    return f"https://www.google.com/maps/search/?api=1&query_place_id={place_id}"


def _iso_date(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(int(timestamp), timezone.utc).strftime("%Y-%m-%d")


def _load_poi(
    config: GoogleConfig, osm_type: str, osm_id: int
) -> Poi | None:
    paths = [
        config.osm_cache_path,
        config.data_dir.parent / "osm" / "osm.db",
    ]
    for path in paths:
        if not path.exists():
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT osm_type, osm_id, name, lat, lng, tags_json FROM pois "
                "WHERE osm_type=? AND osm_id=?",
                (osm_type, int(osm_id)),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            continue
        try:
            tags = json.loads(row["tags_json"]) if row["tags_json"] else {}
        except (TypeError, ValueError):
            tags = {}
        return Poi(
            osm_type=row["osm_type"],
            osm_id=int(row["osm_id"]),
            name=row["name"],
            lat=row["lat"],
            lng=row["lng"],
            tags=tags if isinstance(tags, dict) else {},
        )
    return None


class GoogleConnector(BaseConnector):
    name = SOURCE
    kind = "import"

    def add_parser(self, subparsers: argparse._SubParsersAction) -> None:
        parser = subparsers.add_parser(
            self.name, help="Google Timeline exportieren/importieren"
        )
        parser.set_defaults(connector=self)
        commands = parser.add_subparsers(dest="command", required=True)

        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--input", help="JSON-Datei oder Verzeichnis")
        common.add_argument("--data-dir", help="Datenverzeichnis des Connectors")
        common.add_argument("--db", help="Pfad zur SQLite-Datei")
        common.add_argument("--osm-cache", help="Pfad zum OSM-Enrichment-Cache")
        common.add_argument("--verbose", action="store_true", help="mehr Ausgaben")

        scan_parser = commands.add_parser(
            "scan", parents=[common], help="Exportdateien prüfen"
        )
        scan_parser.set_defaults(func=self.run)

        import_parser = commands.add_parser(
            "import", parents=[common], help="Timeline importieren und DB neu bauen"
        )
        import_parser.set_defaults(func=self.run)

        osm_parser = commands.add_parser(
            "osm", parents=[common], help="Namen und Kategorien über OSM anreichern"
        )
        osm_parser.add_argument(
            "--provider",
            choices=("auto", "overpass", "nominatim"),
            default="auto",
            help="auto: Cache, Overpass, Nominatim (default)",
        )
        osm_parser.add_argument("--radius", type=int, default=55)
        osm_parser.add_argument("--chunk", type=int, default=8)
        osm_parser.add_argument("--limit", type=int, help="maximale Anzahl Orte")
        osm_parser.add_argument("--force", action="store_true", help="Cache ignorieren")
        osm_parser.add_argument(
            "--dry-run", action="store_true", help="nur anzeigen, nichts schreiben"
        )
        osm_parser.add_argument("--sleep", type=float, default=2.0)
        osm_parser.set_defaults(func=self.run)

        stats_parser = commands.add_parser(
            "stats", parents=[common], help="Zusammenfassung anzeigen"
        )
        stats_parser.set_defaults(func=self.run)

        candidates_parser = commands.add_parser(
            "osm-candidates", parents=[common], help="offene OSM-Namenskandidaten"
        )
        candidates_parser.add_argument("--limit", type=int)
        candidates_parser.add_argument("--json", action="store_true")
        candidates_parser.set_defaults(func=self.run)

        name_parser = commands.add_parser(
            "name", parents=[common], help="einem Ort manuell einen Namen geben"
        )
        name_parser.add_argument("place", help="cluster_key oder Google place_id")
        name_parser.add_argument("name", nargs="?", help="neuer Name")
        name_parser.add_argument("--address")
        name_parser.add_argument("--city")
        name_parser.add_argument("--country-code")
        name_parser.add_argument("--category")
        name_parser.add_argument("--clear", action="store_true")
        name_parser.set_defaults(func=self.run)

        accept_parser = commands.add_parser(
            "osm-accept", parents=[common], help="OSM-Kandidat als Namen übernehmen"
        )
        accept_parser.add_argument("place", help="cluster_key")
        accept_parser.add_argument("--osm", help="Kandidat auswählen (node/123 ...)")
        accept_parser.set_defaults(func=self.run)

    def _config(self, args: argparse.Namespace) -> GoogleConfig:
        return build_config(
            inbox=getattr(args, "input", None),
            data_dir=getattr(args, "data_dir", None),
            db_path=getattr(args, "db", None),
            osm_cache_path=getattr(args, "osm_cache", None),
        )

    def run(self, args: argparse.Namespace) -> int:
        config = self._config(args)
        if args.command == "scan":
            return self._scan(config, args)
        if args.command == "import":
            return self._import(config, args)
        if args.command == "osm":
            return self._osm(config, args)
        if args.command == "stats":
            return self._stats(config)
        if args.command == "osm-candidates":
            return self._candidates(config, args)
        if args.command == "name":
            return self._name(config, args)
        if args.command == "osm-accept":
            return self._accept(config, args)
        print(f"Unbekannter google-Befehl: {args.command}")
        return 2

    def _scan(self, config: GoogleConfig, args: argparse.Namespace) -> int:
        from .importer import scan

        source = Path(args.input) if args.input else config.inbox
        if not source.exists():
            print(f"Eingabe nicht gefunden: {source}")
            return 1
        scan(source)
        return 0

    def _import(self, config: GoogleConfig, args: argparse.Namespace) -> int:
        from .importer import run_import

        source = Path(args.input) if args.input else config.inbox
        if not source.exists():
            print(f"Eingabe nicht gefunden: {source}")
            return 1
        summary = run_import(config, input_path=source)
        print(f"Dateien:          {summary.files}")
        print(f"Visits:           {summary.visits}")
        print(f"Quell-Orte:       {summary.places}")
        print(f"Google-Cluster:   {summary.clusters} ({summary.named} benannt)")
        print(f"Home/Work:        {summary.home_work}")
        print(f"Records:          {summary.records}")
        print(
            f"Zeitraum:         {_iso_date(summary.first_at) or '-'} .. "
            f"{_iso_date(summary.last_at) or '-'}"
        )
        print(f"Datenbank:        {config.db_path}")
        return 0

    def _osm(self, config: GoogleConfig, args: argparse.Namespace) -> int:
        from .osm_enrich import enrich

        if not config.db_path.exists():
            print(f"Keine Google-Datenbank: {config.db_path}")
            return 1
        summary = enrich(
            config,
            provider=args.provider,
            radius=args.radius,
            chunk=args.chunk,
            limit=args.limit,
            force=args.force,
            dry_run=args.dry_run,
            sleep=args.sleep,
            verbose=args.verbose,
        )
        print(f"Orte gesamt:      {summary.total}")
        print(f"Offen:            {summary.pending}")
        print(f"Übersprungen:     {summary.skipped}")
        print(f"Cache-Treffer:    {summary.cache_hits}")
        print(f"Overpass:         {summary.overpass_queries} Abfragen, "
              f"{summary.overpass_pois} POIs")
        print(f"Nominatim:        {summary.nominatim_queries} Abfragen")
        print(f"Automatisch:      {summary.auto_named}")
        print(f"Adressen:         {summary.addresses}")
        print(f"Kandidaten:       {summary.candidates}")
        print(f"Ohne Treffer:     {summary.unmatched}")
        print(f"Fehler:           {summary.errors}")
        if args.dry_run:
            print("Dry-Run: keine Namen oder Kandidaten geschrieben.")
        print("Danach: python3 -m spot_history index build")
        return 0

    def _stats(self, config: GoogleConfig) -> int:
        if not config.db_path.exists():
            print(f"Keine Google-Datenbank: {config.db_path}")
            return 1
        db = Database(config.db_path).open()
        try:
            data = db.stats()
        finally:
            db.close()
        counts = data["counts"]
        print(f"Datenbank:        {config.db_path}")
        print(f"Cluster:          {counts['clusters']} ({data['named']} benannt)")
        print(f"Quell-Orte:       {counts['places']}")
        print(f"Visits:           {counts['visits']}")
        print(f"Records:          {counts['source_records']}")
        print(f"Dateien:          {counts['imported_files']}")
        print("Namensquellen:")
        for source, count in data["name_sources"].items():
            print(f"  {source!s:<16} {count}")
        print(f"OSM-Kandidaten:   {data['candidates']}")
        print(
            f"Zeitraum:         {_iso_date(data['first']) or '-'} .. "
            f"{_iso_date(data['last']) or '-'}"
        )
        cache = config.osm_cache_path
        if cache.exists():
            conn = sqlite3.connect(f"file:{cache}?mode=ro", uri=True)
            try:
                pois = conn.execute("SELECT COUNT(*) FROM pois").fetchone()[0]
                queries = conn.execute(
                    "SELECT COUNT(*) FROM query_cache"
                ).fetchone()[0]
                reverse = conn.execute(
                    "SELECT COUNT(*) FROM nominatim_cache"
                ).fetchone()[0]
            finally:
                conn.close()
            print(f"OSM-Cache:        {cache}")
            print(f"  POIs:           {pois}")
            print(f"  Abfragen:       {queries}")
            print(f"  Reverse-Cache:  {reverse}")
        return 0

    def _candidates(self, config: GoogleConfig, args: argparse.Namespace) -> int:
        if not config.db_path.exists():
            print(f"Keine Google-Datenbank: {config.db_path}")
            return 1
        db = Database(config.db_path).open()
        try:
            rows = db.candidates(limit=args.limit)
        finally:
            db.close()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if not rows:
            print("Keine OSM-Kandidaten.")
            return 0
        for row in rows:
            distance = (
                f"{row['distance_m']:.0f} m"
                if row["distance_m"] is not None
                else "-"
            )
            current = row["cluster_name"] or row["address"] or "-"
            print(
                f"{row['cluster_key']}  {row['osm_name'] or '-'}  "
                f"[{row['category'] or '-'}, {distance}]  "
                f"aktuell: {current}"
            )
        return 0

    def _name(self, config: GoogleConfig, args: argparse.Namespace) -> int:
        if not config.db_path.exists():
            print(f"Keine Google-Datenbank: {config.db_path}")
            return 1
        db = Database(config.db_path).open()
        try:
            resolved = db.representative_for(args.place)
            if resolved is None:
                print(f"Ort nicht gefunden: {args.place}")
                return 1
            cluster_key, representative = resolved
            if args.clear:
                db.delete_override(representative)
                row = db.connection.execute(
                    "SELECT semantic_type FROM clusters WHERE cluster_key=?",
                    (cluster_key,),
                ).fetchone()
                semantic = row["semantic_type"] if row else None
                name = (
                    {"Home": "Zuhause", "Work": "Arbeit", "Inferred Work": "Arbeit"}
                    .get(semantic)
                )
                db.update_cluster_enrichment(
                    cluster_key,
                    name=name,
                    name_source="semantic" if name else None,
                )
                print(f"Überschreibung entfernt: {cluster_key}")
                return 0
            if not args.name:
                print("Fehler: Name fehlt (oder --clear verwenden).")
                return 2
            db.set_override(
                representative,
                name=args.name,
                address=args.address,
                city=args.city,
                country_code=args.country_code,
                category=args.category,
            )
            db.update_cluster_enrichment(
                cluster_key,
                name=args.name,
                address=args.address,
                city=args.city,
                country_code=args.country_code,
                category=args.category,
                name_source="manual",
            )
            print(f"Name gespeichert: {cluster_key} -> {args.name}")
            print("Danach: python3 -m spot_history index build")
            return 0
        finally:
            db.close()

    def _accept(self, config: GoogleConfig, args: argparse.Namespace) -> int:
        if not config.db_path.exists():
            print(f"Keine Google-Datenbank: {config.db_path}")
            return 1
        db = Database(config.db_path).open()
        try:
            resolved = db.representative_for(args.place)
            if resolved is None:
                print(f"Ort nicht gefunden: {args.place}")
                return 1
            cluster_key, representative = resolved
            rows = [
                row
                for row in db.candidates()
                if row["cluster_key"] == cluster_key
            ]
            if args.osm:
                from ...core.identity import parse_osm_ref

                try:
                    osm_type, osm_id = parse_osm_ref(args.osm)
                except ValueError as exc:
                    print(f"Fehler: {exc}")
                    return 2
                rows = [
                    row
                    for row in rows
                    if row["osm_type"] == osm_type and int(row["osm_id"]) == osm_id
                ]
            if not rows:
                print(f"Kein passender Kandidat für {cluster_key}.")
                return 1
            if not args.osm and len(rows) > 1:
                print(
                    f"Fehler: {len(rows)} Kandidaten; bitte mit --osm auswählen."
                )
                return 2
            candidate = rows[0]
            poi = _load_poi(
                config, candidate["osm_type"], int(candidate["osm_id"])
            )
            name = candidate["osm_name"] or (poi.name if poi else None)
            category = candidate["category"] or (poi_category(poi) if poi else None)
            address = poi.address if poi else None
            city = poi.city if poi else None
            db.set_override(
                representative,
                name=name,
                address=address,
                city=city,
                category=category,
            )
            db.update_cluster_enrichment(
                cluster_key,
                name=name,
                address=address,
                city=city,
                category=category,
                name_source="manual",
                osm_type=candidate["osm_type"],
                osm_id=int(candidate["osm_id"]),
                distance_m=candidate["distance_m"],
                score=candidate["score"],
            )
            db.clear_candidates(cluster_key)
            print(f"OSM-Kandidat übernommen: {cluster_key} -> {name}")
            print("Danach: python3 -m spot_history index build")
            return 0
        finally:
            db.close()

    def iter_visits(self):
        config = build_config()
        conn = open_read_only(config.db_path)
        if conn is None:
            return
        try:
            rows = conn.execute(
                "SELECT v.id, v.place_id, v.cluster_key, v.visited_at, "
                "v.ended_at, v.tz_offset, v.lat, v.lng, v.semantic_type, "
                "v.probability, v.end_iso, c.name, c.address, c.city, "
                "c.country_code, c.category, c.name_source, c.osm_type, "
                "c.osm_id, c.representative_place_id "
                "FROM visits v JOIN clusters c ON c.cluster_key = v.cluster_key "
                "ORDER BY v.visited_at"
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            place_id = row["representative_place_id"] or row["place_id"]
            private = row["semantic_type"] in (
                "Home",
                "Work",
                "Inferred Work",
            )
            yield Visit(
                source=SOURCE,
                source_visit_id=row["id"],
                visited_at=int(row["visited_at"]),
                lat=row["lat"],
                lng=row["lng"],
                place_name=row["name"],
                source_place_id=row["cluster_key"],
                category=row["category"],
                address=None if private else row["address"],
                city=None if private else row["city"],
                country_code=None if private else row["country_code"],
                place_url=_place_url(place_id),
                tz_offset=row["tz_offset"],
                extra={
                    "semantic_type": row["semantic_type"],
                    "probability": row["probability"],
                    "end_time": row["end_iso"],
                    "name_source": row["name_source"],
                    "osm_type": row["osm_type"],
                    "osm_id": row["osm_id"],
                    "private": private,
                },
            )

    def iter_records(self):
        config = build_config()
        conn = open_read_only(config.db_path)
        if conn is None:
            return
        try:
            rows = conn.execute(
                "SELECT record_id, kind, source_place_id, created_at, "
                "payload_json FROM source_records ORDER BY created_at"
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                payload = {}
            yield {
                "source": SOURCE,
                "record_id": row["record_id"],
                "kind": row["kind"],
                "source_place_id": row["source_place_id"],
                "created_at": row["created_at"],
                "payload": payload,
            }


CONNECTOR = GoogleConnector()
