from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from spot_history.db import Database, iso_utc, photo_url


def make_checkin(checkin_id: str, created_at: int, **overrides):
    raw = {
        "id": checkin_id,
        "createdAt": created_at,
        "timeZoneOffset": 120,
        "type": "checkin",
        "visibility": "public",
        "canonicalUrl": f"https://example.invalid/{checkin_id}",
        "venue": {
            "id": "venue-1",
            "name": "Test Venue",
            "timeZone": "Europe/Berlin",
            "createdAt": 1000,
            "location": {
                "address": "Teststr. 1",
                "city": "München",
                "state": "BY",
                "postalCode": "80331",
                "cc": "DE",
                "country": "Deutschland",
                "lat": 48.1,
                "lng": 11.5,
            },
            "categories": [
                {
                    "id": "cat-1",
                    "name": "Café",
                    "shortName": "Café",
                    "categoryCode": 13032,
                    "primary": True,
                }
            ],
        },
        "likes": {"count": 1, "groups": [{"items": [{"id": "u1", "displayName": "Anna"}]}]},
        "comments": {"count": 1},
        "photos": {
            "count": 1,
            "items": [
                {
                    "id": f"photo-{checkin_id}",
                    "createdAt": created_at + 5,
                    "width": 100,
                    "height": 200,
                    "prefix": "https://fastly.4sqi.net/img/general/",
                    "suffix": "/abc.jpg",
                    "visibility": "public",
                    "user": {"id": "185614", "displayName": "Stefan"},
                }
            ],
        },
        "source": {"name": "Swarm for iOS", "url": "https://www.swarmapp.com"},
    }
    raw.update(overrides)
    return raw


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db").open()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_upsert_and_state(self):
        inserted, updated = self.db.upsert_checkins([make_checkin("c1", 1000)])
        self.assertEqual((inserted, updated), (1, 0))
        inserted, updated = self.db.upsert_checkins([make_checkin("c1", 1000)])
        self.assertEqual((inserted, updated), (0, 1))
        self.assertEqual(self.db.count_checkins(), 1)
        self.assertEqual(self.db.max_created_at(), 1000)

        row = self.db.checkin_rows()[0]
        self.assertEqual(row["city"], "München")
        self.assertEqual(row["category_name"], "Café")
        self.assertEqual(row["local_datetime"], "1970-01-01T02:16:40")
        self.assertEqual(row["created_at_utc"], iso_utc(1000))

        self.db.set_state("last_max_created_at", 1000)
        self.assertEqual(self.db.get_int_state("last_max_created_at"), 1000)

    def test_photo_url(self):
        photo = {
            "prefix": "https://fastly.4sqi.net/img/general/",
            "suffix": "/abc.jpg",
            "width": 1440,
            "height": 1920,
        }
        self.assertEqual(
            photo_url(photo), "https://fastly.4sqi.net/img/general/1440x1920/abc.jpg"
        )

    def test_comments_flow(self):
        self.db.upsert_checkins([make_checkin("c1", 1000)])
        self.assertEqual(self.db.pending_comment_checkins(), ["c1"])
        self.db.upsert_comments(
            "c1",
            [{"id": "m1", "createdAt": 1001, "user": {"id": "u1"}, "text": "hi"}],
        )
        self.db.mark_comments_synced("c1")
        self.assertEqual(self.db.pending_comment_checkins(), [])
        self.assertEqual(self.db.stats()["comments"], 1)

    def test_raw_json_export(self):
        self.db.upsert_checkins([make_checkin("c1", 1000)])
        raw = json.loads(self.db.checkins_raw()[0])
        self.assertEqual(raw["id"], "c1")


if __name__ == "__main__":
    unittest.main()
