from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from ..base import BaseConnector, Photo, Visit
from .config import build_config
from .db import Database, iso_range

SOURCE = "yelp"


class YelpConnector(BaseConnector):
    name = SOURCE
    kind = "import"

    def add_parser(self, subparsers: argparse._SubParsersAction) -> None:
        parser = subparsers.add_parser(self.name, help="Yelp HTML-Export importieren")
        parser.set_defaults(connector=self)
        commands = parser.add_subparsers(dest="command", required=True)

        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--export-dir", help="Yelp-Exportverzeichnis")
        common.add_argument("--data-dir", help="Datenverzeichnis des Connectors")
        common.add_argument("--db", help="Pfad zur SQLite-Datei")
        common.add_argument("--no-media", action="store_true", help="keine Mediendateien kopieren")
        common.add_argument("--verbose", action="store_true", help="mehr Ausgaben")
        common.add_argument("--geocode", action="store_true", help="nach dem Import Geocoding ausführen")

        for command, help_text in (
            ("import", "Export einlesen und in data/yelp/yelp.db schreiben"),
            ("stats", "Zusammenfassung anzeigen"),
        ):
            sub = commands.add_parser(command, parents=[common], help=help_text)
            sub.set_defaults(func=self.run)

        geocode = commands.add_parser(
            "geocode", parents=[common], help="Städte ermitteln und cachen"
        )
        geocode.add_argument("--force", action="store_true", help="Cache ignorieren")
        geocode.add_argument("--sleep", type=float, default=1.0, help="Sekunden zwischen Nominatim-Requests")
        geocode.add_argument("--limit", type=int, help="maximale Anzahl Reverse-Geocoding-Requests")
        geocode.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts schreiben")
        geocode.add_argument("--no-reverse", action="store_true", help="kein Nominatim-Reverse-Geocoding")
        geocode.add_argument("--no-name-match", action="store_true", help="keine Namensmatches")
        geocode.add_argument("--index-db", help="Pfad zum zentralen Index (Foursquare-Namensmatch)")
        geocode.set_defaults(func=self.run)

    def run(self, args: argparse.Namespace) -> int:
        config = build_config(
            export_dir=args.export_dir,
            data_dir=args.data_dir,
            db_path=args.db,
            copy_media=not args.no_media,
        )
        if args.command == "stats":
            return self.print_stats(config)
        if args.command == "geocode":
            return self.run_geocode(config, args)
        from .importer import run_import

        if getattr(args, "verbose", False):
            print(f"Export:           {config.export_dir}")
            print(f"Daten:            {config.data_dir}")
            print(f"Datenbank:        {config.db_path}")
        summary = run_import(config)
        print(f"Check-ins:        {summary.counts.get('checkins', 0)}")
        print(f"Kommentare:       {summary.counts.get('comments', 0)} "
              f"({summary.linked_comments} verknüpft)")
        print(f"Reviews:          {summary.counts.get('reviews', 0)}")
        print(f"Lesezeichen:      {summary.counts.get('bookmarks', 0)}")
        print(f"Sammlungen:       {summary.counts.get('collections', 0)} "
              f"({summary.counts.get('collection_items', 0)} Einträge)")
        print(f"Gespeicherte Orte:{summary.counts.get('saved_locations', 0):>4}")
        print(f"Freunde:          {summary.counts.get('friends', 0)}")
        print(f"Reactions:        {summary.counts.get('reactions', 0)}")
        print(f"Suchen:           {summary.counts.get('searches', 0)}")
        print(f"Survey-Antworten: {summary.counts.get('survey_answers', 0)}")
        print(f"Interaktionen:    {summary.counts.get('business_interactions', 0)}")
        print(f"Review-Feedback:  {summary.counts.get('review_feedback', 0)}")
        print(f"Foto-Feedback:    {summary.counts.get('photo_feedback', 0)}")
        print(f"Komplimente:      {summary.counts.get('compliments', 0)}")
        print(f"Nachrichten:      {summary.counts.get('messages', 0)}")
        print(f"Milestones:       {summary.counts.get('milestones', 0)}")
        print(f"Forum/Profil:     {summary.counts.get('talk_topics', 0)} / "
              f"{summary.counts.get('profile_questions', 0)}")
        print(f"Quelltabellen:    {summary.counts.get('source_tables', 0)}")
        print(f"Fotos/Medien:     {summary.counts.get('photos', 0)} "
              f"({summary.linked_photos} verknüpft)")
        print(f"Medien kopiert:   {summary.media_copied} "
              f"(übersprungen: {summary.media_skipped})")
        if summary.missing_media:
            print(f"Fehlende Medien:  {len(summary.missing_media)}")
        print(f"Datenbank:        {config.db_path}")
        if getattr(args, "geocode", False):
            return self.run_geocode(config, args)
        return 0

    @staticmethod
    def run_geocode(config, args) -> int:
        from .geocode import geocode_places

        db_path = Path(config.db_path)
        if not db_path.exists():
            print(f"Keine Yelp-Datenbank: {db_path}")
            return 1
        db = Database(db_path).open()
        index_db = getattr(args, "index_db", None) or (
            Path(config.data_dir).parent / "index" / "index.db"
        )
        try:
            summary = geocode_places(
                db,
                index_db=index_db,
                force=getattr(args, "force", False),
                sleep=getattr(args, "sleep", 1.0),
                limit=getattr(args, "limit", None),
                dry_run=getattr(args, "dry_run", False),
                reverse=not getattr(args, "no_reverse", False),
                name_match=not getattr(args, "no_name_match", False),
            )
        finally:
            db.close()
        print(f"Reverse ok:       {summary.reverse_ok}")
        print(f"Reverse Cache:    {summary.reverse_cached}")
        print(f"Reverse leer:     {summary.reverse_not_found}")
        print(f"Reverse Fehler:   {summary.reverse_errors}")
        print(f"Foursquare ok:    {summary.foursquare_ok} "
              f"(mehrdeutig: {summary.foursquare_ambiguous})")
        print(f"URL ok:           {summary.url_ok} "
              f"(Konflikte: {summary.url_conflict}, mehrdeutig: {summary.url_ambiguous})")
        if not getattr(args, "dry_run", False):
            print(f"Orte mit Stadt:   {summary.places_with_city}")
            print(f"Visits mit Stadt: {summary.visits_with_city}")
            print(f"Ohne Stadt:       {summary.unknown_places} Orte / "
                  f"{summary.unknown_visits} Visits")
        return 0

    @staticmethod
    def print_stats(config) -> int:
        db_path = Path(config.db_path)
        if not db_path.exists():
            print(f"Keine Yelp-Datenbank: {db_path}")
            return 1
        db = Database(db_path).open()
        try:
            stats = db.stats()
        finally:
            db.close()
        print(f"Check-ins:        {stats['checkins']}")
        print(f"Mit Koordinaten:  {stats['with_coords']}")
        print(f"Zeitraum:         {iso_range(stats)}")
        print(f"Orte:             {stats['places']}")
        print(f"Stadt:            {stats['visits_with_city']}/{stats['checkins']} Visits, "
              f"{stats['places_with_city']}/{stats['places']} Orte")
        for row in stats.get("geocode_sources", []):
            print(f"  {row['source']:<28} {row['status']:<12} {row['count']}")
        if stats.get("cities"):
            print("Top-Städte:")
            for row in stats["cities"]:
                print(f"  {row['value']:<24} {row['count']}")
        print(f"Kommentare:       {stats['comments']} ({stats['linked_comments']} verknüpft)")
        print(f"Reviews:          {stats['reviews']}")
        print(f"Lesezeichen:      {stats['bookmarks']}")
        print(f"Sammlungen:       {stats['collections']} ({stats['collection_items']} Einträge)")
        print(f"Gespeicherte Orte:{stats['saved_locations']:>4}")
        print(f"Freunde:          {stats['friends']}")
        print(f"Reactions:        {stats['reactions']}")
        print(f"Suchen:           {stats['searches']}")
        print(f"Survey-Antworten: {stats['survey_answers']}")
        print(f"Interaktionen:    {stats['business_interactions']}")
        print(f"Review-Feedback:  {stats['review_feedback']}")
        print(f"Foto-Feedback:    {stats['photo_feedback']}")
        print(f"Komplimente:      {stats['compliments']}")
        print(f"Nachrichten:      {stats['messages']}")
        print(f"Milestones:       {stats['milestones']}")
        print(f"Forum/Profil:     {stats['talk_topics']} / {stats['profile_questions']}")
        print(f"Quelltabellen:    {stats['source_tables']}")
        print(f"Fotos/Medien:     {stats['photos']} ({stats['linked_photos']} verknüpft)")
        media_dir = Path(config.media_dir)
        if media_dir.exists():
            files = sum(1 for item in media_dir.rglob("*") if item.is_file())
            print(f"Mediendateien:    {files}")
        print(f"Datenbank:        {db_path}")
        run = stats.get("last_run")
        if run and run.get("finished_at"):
            print(f"Letzter Import:   {run['finished_at']}")
            if not run.get("ok"):
                print(f"Importfehler:     {run.get('error') or '-'}")
        return 0

    ACCOUNT_FIELDS = ("Vorname", "Nachname", "Geburtstag", "Geschlecht")

    def iter_records(self):
        config = build_config(copy_media=False)
        db_path = Path(config.db_path)
        if not db_path.exists():
            return
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            queries = (
                (
                    "survey_answer",
                    "SELECT id, answered_at, place_key, place_name, question, "
                    "answer, device_type FROM survey_answers",
                    lambda row: (
                        row["place_key"],
                        row["answered_at"],
                        {
                            "place_name": row["place_name"],
                            "question": row["question"],
                            "answer": row["answer"],
                            "device_type": row["device_type"],
                        },
                    ),
                ),
                (
                    "business_interaction",
                    "SELECT id, created_at, place_key, place_name, interaction "
                    "FROM business_interactions",
                    lambda row: (
                        row["place_key"],
                        row["created_at"],
                        {
                            "place_name": row["place_name"],
                            "interaction": row["interaction"],
                        },
                    ),
                ),
                (
                    "unlinked_comment",
                    "SELECT id, created_at, place_name, text, status "
                    "FROM checkin_comments WHERE checkin_id IS NULL",
                    lambda row: (
                        None,
                        row["created_at"],
                        {
                            "place_name": row["place_name"],
                            "text": row["text"],
                            "status": row["status"],
                        },
                    ),
                ),
                (
                    "unlinked_photo",
                    "SELECT id, created_at, place_name, media_type, filename, "
                    "local_path, caption, status FROM photos "
                    "WHERE checkin_id IS NULL",
                    lambda row: (
                        None,
                        row["created_at"],
                        {
                            "place_name": row["place_name"],
                            "media_type": row["media_type"],
                            "filename": row["filename"],
                            "local_path": row["local_path"],
                            "caption": row["caption"],
                            "status": row["status"],
                        },
                    ),
                ),
                (
                    "review_feedback",
                    "SELECT id, created_at, content_url, business_ref, helpful, "
                    "funny, cool FROM review_feedback",
                    lambda row: (
                        None,
                        row["created_at"],
                        {
                            "content_url": row["content_url"],
                            "business_ref": row["business_ref"],
                            "helpful": row["helpful"],
                            "funny": row["funny"],
                            "cool": row["cool"],
                        },
                    ),
                ),
                (
                    "photo_feedback",
                    "SELECT id, updated_at, photo_url, liked, useful FROM photo_feedback",
                    lambda row: (
                        None,
                        row["updated_at"],
                        {
                            "photo_url": row["photo_url"],
                            "liked": row["liked"],
                            "useful": row["useful"],
                        },
                    ),
                ),
                (
                    "compliment",
                    "SELECT id, created_at, direction, name, content_type, "
                    "compliment_type, message, status FROM compliments",
                    lambda row: (
                        None,
                        row["created_at"],
                        {
                            "direction": row["direction"],
                            "name": row["name"],
                            "content_type": row["content_type"],
                            "compliment_type": row["compliment_type"],
                            "message": row["message"],
                            "status": row["status"],
                        },
                    ),
                ),
                (
                    "message",
                    "SELECT id, created_at, recipient, subject, place_key, "
                    "place_name, text, status, read_at FROM messages",
                    lambda row: (
                        row["place_key"],
                        row["created_at"],
                        {
                            "recipient": row["recipient"],
                            "subject": row["subject"],
                            "place_name": row["place_name"],
                            "text": row["text"],
                            "status": row["status"],
                            "read_at": row["read_at"],
                        },
                    ),
                ),
                (
                    "milestone",
                    "SELECT id, created_at, subject, text FROM milestones",
                    lambda row: (
                        None,
                        row["created_at"],
                        {"subject": row["subject"], "text": row["text"]},
                    ),
                ),
                (
                    "profile_question",
                    "SELECT id, question, answer, status FROM profile_questions",
                    lambda row: (
                        None,
                        None,
                        {
                            "question": row["question"],
                            "answer": row["answer"],
                            "status": row["status"],
                        },
                    ),
                ),
                (
                    "talk_topic",
                    "SELECT id, created_at, topic, comment, status FROM talk_topics",
                    lambda row: (
                        None,
                        row["created_at"],
                        {
                            "topic": row["topic"],
                            "comment": row["comment"],
                            "status": row["status"],
                        },
                    ),
                ),
            )
            for kind, sql, mapper in queries:
                try:
                    rows = conn.execute(sql).fetchall()
                except sqlite3.OperationalError:
                    continue
                for row in rows:
                    place_id, created_at, payload = mapper(row)
                    yield {
                        "source": SOURCE,
                        "record_id": row["id"],
                        "kind": kind,
                        "source_place_id": place_id,
                        "created_at": created_at,
                        "payload": payload,
                    }
            try:
                account = {
                    row["field"]: row["value"]
                    for row in conn.execute(
                        "SELECT field, value FROM account_information"
                    )
                    if row["field"] in self.ACCOUNT_FIELDS and row["value"]
                }
            except sqlite3.OperationalError:
                account = {}
            if account:
                yield {
                    "source": SOURCE,
                    "record_id": "account",
                    "kind": "account",
                    "source_place_id": None,
                    "created_at": None,
                    "payload": account,
                }
        finally:
            conn.close()

    def iter_visits(self):
        config = build_config(copy_media=False)
        db_path = Path(config.db_path)
        if not db_path.exists():
            return
        photos: dict[str, list[Photo]] = {}
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            place_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(places)")
            }
            place_url_column = (
                "p.place_url" if "place_url" in place_columns else "NULL AS place_url"
            )
            for row in conn.execute(
                "SELECT id, checkin_id, created_at, local_path FROM photos "
                "WHERE checkin_id IS NOT NULL ORDER BY created_at"
            ):
                photos.setdefault(row["checkin_id"], []).append(
                    Photo(
                        source_photo_id=row["id"],
                        local_path=row["local_path"],
                        taken_at=row["created_at"],
                    )
                )
            rows = conn.execute(
                "SELECT c.id, c.visited_at, c.place_key, c.place_name, "
                "c.lat, c.lng, c.note, c.status, "
                f"p.city, p.state, p.country_code, {place_url_column} FROM checkins c "
                "LEFT JOIN places p ON p.place_key = c.place_key "
                "ORDER BY c.place_name, (c.lat IS NOT NULL), c.visited_at"
            )
            for row in rows:
                has_coords = row["lat"] is not None and row["lng"] is not None
                yield Visit(
                    source=SOURCE,
                    source_visit_id=row["id"],
                    visited_at=int(row["visited_at"]),
                    lat=row["lat"],
                    lng=row["lng"],
                    place_name=row["place_name"],
                    source_place_id=row["place_key"],
                    city=row["city"],
                    state=row["state"],
                    country_code=row["country_code"],
                    note=row["note"],
                    source_url=row["place_url"],
                    place_url=row["place_url"],
                    tz_offset=None,
                    photos=photos.get(row["id"], []),
                    extra={"status": row["status"], "has_coords": has_coords},
                )
        finally:
            conn.close()


CONNECTOR = YelpConnector()
