# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spot_history.connectors.google import connector as connector_module
from spot_history.connectors.google.config import build_config
from spot_history.connectors.google.db import Database
from spot_history.connectors.google.importer import run_import

FIXTURES = Path(__file__).parent / "fixtures" / "google"


class GoogleImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = build_config(
            inbox=FIXTURES,
            data_dir=self.root / "data",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _import(self):
        return run_import(self.config, log=lambda _: None)

    def _db(self):
        return Database(self.config.db_path).open()

    def test_import_counts(self):
        summary = self._import()
        self.assertEqual(summary.files, 5)
        self.assertEqual(summary.visits, 8)
        self.assertEqual(summary.places, 8)
        self.assertEqual(summary.clusters, 7)
        self.assertEqual(summary.named, 5)
        self.assertEqual(summary.home_work, 1)
        self.assertEqual(summary.records, 1)

    def test_import_is_idempotent(self):
        first = self._import()
        second = self._import()
        self.assertEqual(first.visits, second.visits)
        self.assertEqual(first.clusters, second.clusters)
        db = self._db()
        try:
            self.assertEqual(db.stats()["counts"]["visits"], 8)
            self.assertEqual(db.stats()["counts"]["clusters"], 7)
            self.assertEqual(db.stats()["counts"]["places"], 8)
        finally:
            db.close()

    def test_home_cluster_is_consolidated_and_labelled(self):
        self._import()
        db = self._db()
        try:
            row = db.connection.execute(
                "SELECT * FROM clusters WHERE name='Zuhause'"
            ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["semantic_type"], "Home")
            self.assertEqual(row["visit_count"], 2)
            self.assertEqual(row["place_count"], 2)
            self.assertIsNone(row["address"])
        finally:
            db.close()

    def test_iter_visits_uses_cluster_place(self):
        self._import()
        with mock.patch.object(
            connector_module, "build_config", return_value=self.config
        ):
            visits = list(connector_module.CONNECTOR.iter_visits())
        self.assertEqual(len(visits), 8)
        home = [visit for visit in visits if visit.place_name == "Zuhause"]
        self.assertEqual(len(home), 2)
        self.assertEqual(home[0].source, "google")
        self.assertIn("query_place_id=", home[0].place_url or "")
        self.assertIsNotNone(home[0].extra["end_time"])

    def test_manual_override_survives_reimport(self):
        self._import()
        db = self._db()
        try:
            resolved = db.representative_for("ChIJioscafe")
            self.assertIsNotNone(resolved)
            assert resolved is not None
            db.set_override(resolved[1], name="Mein Café", category="cafe")
        finally:
            db.close()
        self._import()
        db = self._db()
        try:
            row = db.connection.execute(
                "SELECT name, name_source FROM clusters WHERE cluster_key=?",
                (resolved[0],),
            ).fetchone()
            self.assertEqual(row["name"], "Mein Café")
            self.assertEqual(row["name_source"], "manual")
        finally:
            db.close()

    def test_review_record_is_linked_by_coordinate(self):
        self._import()
        db = self._db()
        try:
            rows = db.connection.execute(
                "SELECT payload_json, source_place_id FROM source_records"
            ).fetchall()
            self.assertEqual(len(rows), 1)
            payload = json.loads(rows[0]["payload_json"])
            self.assertEqual(payload["name"], "Test Café")
            self.assertIsNotNone(rows[0]["source_place_id"])
            cluster = db.connection.execute(
                "SELECT name, name_source FROM clusters WHERE cluster_key=?",
                (rows[0]["source_place_id"],),
            ).fetchone()
            self.assertEqual(cluster["name"], "Test Café")
            self.assertEqual(cluster["name_source"], "review")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
