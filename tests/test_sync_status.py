# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from spot_history.web import sync_status

TIMESTAMP_PATTERN = re.compile(r"^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$")


def make_db(path: Path, statements: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        for statement in statements:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


class SyncStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_databases(self):
        runs = sync_status.source_runs(self.data)
        self.assertEqual(
            set(runs), {"foursquare", "yelp", "google", "osm", "index"}
        )
        for run in runs.values():
            self.assertIsNone(run.raw)
            self.assertIsNone(run.display)

    def test_reads_last_runs(self):
        make_db(
            self.data / "foursquare" / "foursquare.db",
            [
                "CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT)",
                "INSERT INTO sync_state VALUES ('last_sync_at', '2026-11-20T18:42:00+00:00')",
            ],
        )
        make_db(
            self.data / "yelp" / "yelp.db",
            [
                "CREATE TABLE import_meta (key TEXT PRIMARY KEY, value TEXT)",
                "INSERT INTO import_meta VALUES ('last_import_at', '2026-11-19T10:00:00+00:00')",
            ],
        )
        make_db(
            self.data / "google" / "google.db",
            [
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)",
                "INSERT INTO meta VALUES ('last_import_at', '2026-11-18T09:30:00+00:00')",
            ],
        )
        make_db(
            self.data / "osm" / "osm.db",
            [
                "CREATE TABLE sync_runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT)",
                "INSERT INTO sync_runs VALUES (1, '2026-11-17T08:00:00+00:00', NULL)",
                "INSERT INTO sync_runs VALUES (2, '2026-11-17T09:00:00+00:00', '2026-11-17T09:05:00+00:00')",
            ],
        )
        make_db(
            self.data / "index" / "index.db",
            [
                "CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT)",
                "INSERT INTO index_meta VALUES ('built_at', '2026-11-21T12:00:00+00:00')",
            ],
        )

        runs = sync_status.source_runs(self.data)
        self.assertEqual(runs["foursquare"].raw, "2026-11-20T18:42:00+00:00")
        self.assertEqual(runs["foursquare"].label, "Letzter Sync")
        self.assertEqual(runs["yelp"].raw, "2026-11-19T10:00:00+00:00")
        self.assertEqual(runs["google"].raw, "2026-11-18T09:30:00+00:00")
        self.assertEqual(runs["osm"].raw, "2026-11-17T09:05:00+00:00")
        self.assertEqual(runs["index"].raw, "2026-11-21T12:00:00+00:00")
        for run in runs.values():
            self.assertRegex(run.display or "", TIMESTAMP_PATTERN)

    def test_yelp_falls_back_to_import_runs(self):
        make_db(
            self.data / "yelp" / "yelp.db",
            [
                "CREATE TABLE import_runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT)",
                "INSERT INTO import_runs VALUES (1, '2026-11-17T08:00:00+00:00', '2026-11-17T08:10:00+00:00')",
            ],
        )
        runs = sync_status.source_runs(self.data)
        self.assertEqual(runs["yelp"].raw, "2026-11-17T08:10:00+00:00")

    def test_unparsable_timestamp_keeps_raw_value(self):
        make_db(
            self.data / "google" / "google.db",
            [
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)",
                "INSERT INTO meta VALUES ('last_import_at', 'gestern')",
            ],
        )
        runs = sync_status.source_runs(self.data)
        self.assertEqual(runs["google"].display, "gestern")

    def test_format_timestamp_handles_empty_values(self):
        self.assertIsNone(sync_status.format_timestamp(None))
        self.assertIsNone(sync_status.format_timestamp(""))
        self.assertIsNone(sync_status.format_timestamp("   "))


if __name__ == "__main__":
    unittest.main()
