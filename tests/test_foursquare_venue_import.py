from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from spot_history.connectors.foursquare.db import Database
from spot_history.connectors.foursquare.venue_import import (
    import_venue_details,
    load_venue_details,
)


def venue(venue_id: str, **overrides) -> dict:
    payload = {
        "id": venue_id,
        "status": 200,
        "name": f"Venue {venue_id}",
        "tel": "089 123",
        "website": "https://example.invalid",
        "email": "info@example.invalid",
        "social": {"instagram": "test", "twitter": ""},
        "address": "Teststr. 1",
        "locality": "München",
        "region": "BY",
        "postcode": "80331",
        "country": "DE",
        "formatted_address": "Teststr. 1, 80331 München",
        "lat": 48.1,
        "lng": 11.5,
        "categories": ["Café", "Bakery"],
        "date_created": "2010-01-01",
        "date_refreshed": "2026-11-20",
        "placemaker": "https://placemaker.invalid/p1",
        "related": True,
        "fetched_at": "2026-11-20T00:00:00+00:00",
    }
    payload.update(overrides)
    return payload


class VenueImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "foursquare.db").open()
        self.path = Path(self.tmp.name) / "details.json"

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def write(self, payload) -> None:
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_import_stores_details_and_skips_errors(self):
        self.write([venue("v1"), venue("v2", tel=None), {"id": "v3", "status": 429}])
        stored = import_venue_details(self.db, self.path, log=lambda _: None)
        self.assertEqual(stored, 2)

        rows = self.db.venue_detail_rows()
        self.assertEqual([row["venue_id"] for row in rows], ["v1", "v2"])
        first = rows[0]
        self.assertEqual(first["name"], "Venue v1")
        self.assertEqual(first["tel"], "089 123")
        self.assertEqual(json.loads(first["social_json"]), {"instagram": "test", "twitter": ""})
        self.assertEqual(json.loads(first["categories_json"]), ["Café", "Bakery"])
        self.assertEqual(first["city"], "München")
        self.assertEqual(first["state"], "BY")
        self.assertEqual(first["postal_code"], "80331")
        self.assertEqual(first["country_code"], "DE")
        self.assertEqual(first["has_related"], 1)
        self.assertTrue(self.db.get_state("venue_details_imported_at"))
        self.assertTrue(self.db.get_state("venue_details_source_file"))

    def test_import_is_idempotent_and_updates(self):
        self.write([venue("v1")])
        import_venue_details(self.db, self.path, log=lambda _: None)
        self.write([venue("v1", tel="030 456", name="Neu")])
        import_venue_details(self.db, self.path, log=lambda _: None)
        rows = self.db.venue_detail_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tel"], "030 456")
        self.assertEqual(rows[0]["name"], "Neu")

    def test_load_accepts_dict_payload_and_skips_invalid(self):
        self.write({"venues": [venue("v1"), {"status": 200}, "kaputt"]})
        items = load_venue_details(self.path, log=lambda _: None)
        self.assertEqual([item["id"] for item in items], ["v1"])

    def test_missing_file_raises(self):
        with self.assertRaises(SystemExit):
            import_venue_details(self.db, self.path, log=lambda _: None)


if __name__ == "__main__":
    unittest.main()
