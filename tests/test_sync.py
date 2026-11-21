# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spot_history.connectors.foursquare.config import FoursquareConfig
from spot_history.connectors.foursquare.db import Database
from spot_history.connectors.foursquare.sync import Sync


def checkin(checkin_id: str, created_at: int):
    return {
        "id": checkin_id,
        "createdAt": created_at,
        "timeZoneOffset": 0,
        "type": "checkin",
        "venue": {"id": f"v-{checkin_id}", "name": "V", "location": {"lat": 1, "lng": 2}},
        "likes": {"count": 0, "groups": []},
        "comments": {"count": 0},
        "photos": {"count": 0, "items": []},
    }


class FakeAPI:
    def __init__(self, items, page_size=2):
        self.items = list(items)
        self.page_size = page_size
        self.calls = []

    def checkins(self, before=None, after=None, limit=250, sort=None):
        self.calls.append(before)
        pool = sorted(
            (item for item in self.items if before is None or item["createdAt"] < before),
            key=lambda item: -item["createdAt"],
        )
        return pool[: self.page_size], len(self.items)

    def comments(self, checkin_id):
        return []


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = FoursquareConfig(
            token="dummy",
            data_dir=Path(self.tmp.name),
            download_photos=False,
            export=False,
            sleep=0,
        )
        self.db = Database(self.cfg.db_path).open()
        self.items = [checkin(f"c{index}", index) for index in range(1, 6)]

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_backfill_pages_with_before_timestamp(self):
        api = FakeAPI(self.items)
        sync = Sync(api, self.db, self.cfg, log=lambda _: None)
        sync.run("backfill")
        self.assertEqual(self.db.count_checkins(), 5)
        self.assertEqual(self.db.get_int_state("last_max_created_at"), 5)
        self.assertEqual(self.db.get_state("backfill_done"), "1")
        self.assertIsNone(api.calls[0])
        self.assertTrue(all(call is not None for call in api.calls[1:]))

    def test_backfill_is_idempotent(self):
        api = FakeAPI(self.items)
        sync = Sync(api, self.db, self.cfg, log=lambda _: None)
        sync.run("backfill")
        sync.run("backfill")
        self.assertEqual(self.db.count_checkins(), 5)

    def test_incremental_adds_only_new(self):
        api = FakeAPI(self.items, page_size=3)
        sync = Sync(api, self.db, self.cfg, log=lambda _: None)
        sync.run("backfill")
        self.assertEqual(self.db.count_checkins(), 5)

        api.items.append(checkin("c6", 6))
        api.items.append(checkin("c7", 7))
        sync.run("incremental")
        self.assertEqual(self.db.count_checkins(), 7)
        self.assertEqual(self.db.get_int_state("last_max_created_at"), 7)

    def test_incremental_without_new_data(self):
        api = FakeAPI(self.items, page_size=3)
        sync = Sync(api, self.db, self.cfg, log=lambda _: None)
        sync.run("backfill")
        sync.run("incremental")
        self.assertEqual(self.db.count_checkins(), 5)
        self.assertEqual(self.db.get_int_state("last_max_created_at"), 5)


if __name__ == "__main__":
    unittest.main()
