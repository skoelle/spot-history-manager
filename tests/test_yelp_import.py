from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spot_history.connectors.yelp import connector as connector_module
from spot_history.connectors.yelp.config import build_config
from spot_history.connectors.yelp.connector import YelpConnector
from spot_history.connectors.yelp.db import Database
from spot_history.connectors.yelp.ids import place_key
from spot_history.connectors.yelp.importer import run_import

FIXTURES = Path(__file__).parent / "fixtures" / "yelp" / "html"
MEDIA_FILES = [
    "biz_photo/bar.jpg",
    "biz_photo/other.jpg",
    "profile_photo/profile.jpg",
    "video/clip.mp4",
]


class YelpImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.export = self.root / "export"
        shutil.copytree(FIXTURES, self.export / "html")
        for relative in MEDIA_FILES:
            path = self.export / "media" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        self.config = build_config(export_dir=self.export, data_dir=self.root / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def _import(self):
        return run_import(self.config, log=lambda _: None)

    def _stats(self):
        db = Database(self.config.db_path).open()
        try:
            return db.stats()
        finally:
            db.close()

    def test_import_counts_and_links(self):
        summary = self._import()
        self.assertEqual(
            summary.counts,
            {
                "checkins": 4,
                "comments": 2,
                "reviews": 1,
                "bookmarks": 1,
                "collections": 1,
                "collection_items": 1,
                "saved_locations": 1,
                "friends": 1,
                "reactions": 1,
                "searches": 1,
                "photos": 4,
                "media_copied": 4,
                "media_skipped": 0,
                "linked_photos": 2,
                "linked_comments": 1,
            },
        )
        self.assertEqual(summary.missing_media, [])
        self.assertEqual(summary.warnings, [])

        stats = self._stats()
        self.assertEqual(stats["checkins"], 4)
        self.assertEqual(stats["with_coords"], 3)
        self.assertEqual(stats["places"], 3)
        self.assertEqual(stats["linked_photos"], 2)
        self.assertEqual(stats["linked_comments"], 1)

        for relative in MEDIA_FILES:
            self.assertTrue((self.config.media_dir / relative).exists())

        db = Database(self.config.db_path).open()
        try:
            place = db.conn.execute(
                "SELECT place_url FROM places WHERE name=?", ("Test Café",)
            ).fetchone()
        finally:
            db.close()
        self.assertEqual(place["place_url"], "https://example.invalid/test-cafe")

    def test_import_is_idempotent(self):
        self._import()
        first = self._stats()
        self._import()
        second = self._stats()
        self.assertEqual(first["checkins"], second["checkins"])
        self.assertEqual(first["places"], second["places"])
        self.assertEqual(first["photos"], second["photos"])
        self.assertEqual(second["checkins"], 4)
        self.assertEqual(second["photos"], 4)

        db = Database(self.config.db_path).open()
        try:
            ids = {row["id"] for row in db.conn.execute("SELECT id FROM checkins")}
        finally:
            db.close()
        self.assertEqual(len(ids), 4)

    def test_missing_media_is_reported(self):
        (self.export / "media" / "video" / "clip.mp4").unlink()
        summary = self._import()
        self.assertEqual(summary.missing_media, ["video/clip.mp4"])
        db = Database(self.config.db_path).open()
        try:
            row = db.conn.execute(
                "SELECT local_path FROM photos WHERE id=?", ("video/clip.mp4",)
            ).fetchone()
        finally:
            db.close()
        self.assertIsNone(row["local_path"])

    def test_iter_visits_reads_db_read_only(self):
        self._import()
        with mock.patch.object(connector_module, "build_config", return_value=self.config):
            visits = list(YelpConnector().iter_visits())

        self.assertEqual(len(visits), 4)
        self.assertEqual({visit.source for visit in visits}, {"yelp"})

        old = next(visit for visit in visits if visit.place_name == "Alte Bäckerei")
        self.assertIsNone(old.lat)
        self.assertIsNone(old.lng)
        self.assertEqual(old.tz_offset, 0)
        self.assertFalse(old.extra["has_coords"])
        self.assertEqual(old.source_place_id, place_key("Alte Bäckerei"))

        bar = next(visit for visit in visits if visit.place_name == "Beispiel Bar")
        self.assertAlmostEqual(bar.lat, 48.2)
        self.assertAlmostEqual(bar.lng, 11.6)
        self.assertEqual(len(bar.photos), 2)
        self.assertTrue(bar.extra["has_coords"])

        cafe = next(visit for visit in visits if visit.note == "Zweiter Besuch")
        self.assertEqual(cafe.place_name, "Test Café")
        self.assertEqual(cafe.source, "yelp")
        self.assertEqual(cafe.place_url, "https://example.invalid/test-cafe")
        self.assertEqual(cafe.source_url, "https://example.invalid/test-cafe")

    def test_iter_visits_without_db_is_empty(self):
        empty = build_config(export_dir=self.export, data_dir=self.root / "empty")
        with mock.patch.object(connector_module, "build_config", return_value=empty):
            self.assertEqual(list(YelpConnector().iter_visits()), [])

    def test_no_media_copy_keeps_metadata(self):
        config = build_config(
            export_dir=self.export,
            data_dir=self.root / "data-no-media",
            copy_media=False,
        )
        summary = run_import(config, log=lambda _: None)
        self.assertEqual(summary.counts["media_copied"], 0)
        self.assertEqual(summary.counts["photos"], 4)
        self.assertFalse(any(config.media_dir.rglob("*.jpg")))
        db = Database(config.db_path).open()
        try:
            paths = [
                row["local_path"]
                for row in db.conn.execute("SELECT local_path FROM photos")
            ]
        finally:
            db.close()
        self.assertTrue(paths)
        self.assertTrue(all(path is None for path in paths))


if __name__ == "__main__":
    unittest.main()
