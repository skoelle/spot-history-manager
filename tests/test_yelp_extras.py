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


class YelpExtrasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.export = self.root / "export"
        shutil.copytree(FIXTURES, self.export / "html")
        self.config = build_config(
            export_dir=self.export,
            data_dir=self.root / "data",
            copy_media=False,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _stats(self):
        db = Database(self.config.db_path).open()
        try:
            return db.stats()
        finally:
            db.close()

    def _records(self):
        with mock.patch.object(
            connector_module, "build_config", return_value=self.config
        ):
            return list(YelpConnector().iter_records())

    def test_import_is_idempotent_for_extra_tables(self):
        first = run_import(self.config, log=lambda _: None)
        second = run_import(self.config, log=lambda _: None)
        self.assertEqual(first.counts, second.counts)
        stats = self._stats()
        self.assertEqual(stats["survey_answers"], 2)
        self.assertEqual(stats["business_interactions"], 2)
        self.assertEqual(stats["compliments"], 2)
        self.assertEqual(stats["account_information"], 5)
        self.assertEqual(stats["source_tables"], 2)

    def test_iter_records_matches_places_and_hides_sensitive_data(self):
        run_import(self.config, log=lambda _: None)
        records = self._records()
        kinds = {record["kind"] for record in records}
        self.assertIn("survey_answer", kinds)
        self.assertIn("business_interaction", kinds)
        self.assertIn("compliment", kinds)
        self.assertIn("message", kinds)
        self.assertIn("milestone", kinds)
        self.assertIn("talk_topic", kinds)
        self.assertIn("review_feedback", kinds)
        self.assertIn("photo_feedback", kinds)
        self.assertIn("account", kinds)
        self.assertNotIn("user_login", kinds)
        self.assertNotIn("flagged_content", kinds)

        survey = next(
            record
            for record in records
            if record["kind"] == "survey_answer"
            and record["payload"]["place_name"] == "Test Café"
        )
        self.assertEqual(survey["source"], "yelp")
        self.assertEqual(survey["source_place_id"], place_key("Test Café"))
        self.assertEqual(survey["payload"]["answer"], "Nein")

        account = next(record for record in records if record["kind"] == "account")
        self.assertEqual(
            set(account["payload"]),
            {"Vorname", "Nachname", "Geburtstag", "Geschlecht"},
        )
        self.assertNotIn("E-Mail-Adressen", account["payload"])

        for record in records:
            if record["kind"] in ("review_feedback", "photo_feedback"):
                self.assertNotIn("ip", record["payload"])


if __name__ == "__main__":
    unittest.main()
