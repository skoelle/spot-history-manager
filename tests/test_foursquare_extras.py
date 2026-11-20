from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spot_history.connectors.foursquare.api import ApiError, AuthError
from spot_history.connectors.foursquare.config import FoursquareConfig
from spot_history.connectors.foursquare.connector import CONNECTOR
from spot_history.connectors.foursquare.db import Database
from spot_history.connectors.foursquare.photos import photo_destination
from spot_history.connectors.foursquare.sync import Sync


def make_checkin(checkin_id: str, created_at: int = 1000) -> dict:
    return {
        "id": checkin_id,
        "createdAt": created_at,
        "timeZoneOffset": 0,
        "type": "checkin",
        "venue": {
            "id": f"v-{checkin_id}",
            "name": "Venue",
            "location": {"lat": 1.0, "lng": 2.0},
        },
        "likes": {"count": 0, "groups": []},
        "comments": {"count": 0},
        "photos": {"count": 0, "items": []},
    }


def make_photo(photo_id: str, checkin_id: str | None = None) -> dict:
    return {
        "id": photo_id,
        "createdAt": 2000,
        "width": 100,
        "height": 200,
        "prefix": "https://example.invalid/",
        "suffix": "/x.jpg",
        "visibility": "public",
        "checkin": {"id": checkin_id} if checkin_id else None,
        "venue": {"id": "v1", "name": "Venue"},
    }


class FakeAPI:
    def __init__(self) -> None:
        self.photo_pages: dict[int, list[dict]] = {}
        self.photo_total: int | None = None
        self.photo_offsets: list[int] = []
        self.venue_pages: dict[int, list[dict]] = {}
        self.venue_total: int | None = None
        self.venue_offsets: list[int] = []
        self.list_payload: list[dict] = []
        self.list_detail_payload: dict[str, dict] = {}
        self.list_detail_calls: list[tuple[str, int, int]] = []
        self.tip_pages: dict[int, list[dict]] = {}
        self.tip_total: int | None = None
        self.tip_offsets: list[int] = []
        self.fail: dict[str, Exception] = {}
        self.profile: dict = {
            "id": "185614",
            "displayName": "Stefan Koelle",
            "firstName": "Stefan",
            "lastName": "Koelle",
            "handle": "stefankoelle",
            "homeCity": "Munich, Germany",
            "countryCode": "DE",
            "bio": "",
            "photo": {
                "prefix": "https://example.invalid/user/",
                "suffix": "/avatar.jpg",
            },
            "checkins": {"count": 2339},
            "friends": {"count": 159},
            "followers": {"count": 196},
            "following": {"count": 183},
            "lists": {"count": 5},
            "photos": {"count": 577},
            "tips": {"count": 11},
            "mayorships": {"count": 0},
        }

    def self_user(self):
        if "profile" in self.fail:
            raise self.fail["profile"]
        return self.profile

    def photos(self, limit=500, offset=0):
        if "photos" in self.fail:
            raise self.fail["photos"]
        self.photo_offsets.append(offset)
        return self.photo_pages.get(offset, []), self.photo_total

    def venue_history(self, limit=500, offset=0):
        if "venue_history" in self.fail:
            raise self.fail["venue_history"]
        self.venue_offsets.append(offset)
        return self.venue_pages.get(offset, []), self.venue_total

    def lists(self, limit=50):
        if "lists" in self.fail:
            raise self.fail["lists"]
        return self.list_payload

    def list_details(self, list_id, limit=0, offset=0):
        self.list_detail_calls.append((list_id, limit, offset))
        payload = self.list_detail_payload.get(list_id, {})
        if not payload:
            return {}
        block = payload.get("listItems") or {}
        all_items = block.get("items") or []
        effective = limit or 30
        return {
            **payload,
            "listItems": {
                "count": block.get("count", len(all_items)),
                "items": all_items[offset:offset + effective],
            },
        }

    def tips(self, limit=50, offset=0):
        if "tips" in self.fail:
            raise self.fail["tips"]
        self.tip_offsets.append(offset)
        return self.tip_pages.get(offset, []), self.tip_total


class ExtrasTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = FoursquareConfig(
            token="dummy",
            data_dir=Path(self.tmp.name),
            download_photos=False,
            export=False,
            sleep=0,
        )
        self.db = Database(self.cfg.db_path).open()
        self.db.upsert_checkins([make_checkin("c1")])
        self.api = FakeAPI()
        self.sync = Sync(self.api, self.db, self.cfg, log=lambda _: None)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_photo_paging_dedupes_and_stops_on_empty_page(self):
        duplicate = make_photo("p2", "missing-checkin")
        self.api.photo_pages = {
            0: [make_photo("p1", "c1"), duplicate, duplicate],
            3: [duplicate, make_photo("p3")],
            5: [make_photo("p4")],
            6: [],
        }
        self.api.photo_total = 4

        self.assertEqual(self.sync.sync_all_photos(), 4)
        self.assertEqual(self.db.photo_count()[0], 4)
        self.assertEqual(self.api.photo_offsets, [0, 3, 5, 6])
        self.assertEqual(self.db.get_int_state("photos_api_total"), 4)

        rows = {row["id"]: row for row in self.db.photo_rows()}
        self.assertEqual(rows["p1"]["checkin_id"], "c1")
        self.assertIsNone(rows["p2"]["checkin_id"])
        self.assertIsNone(rows["p3"]["checkin_id"])
        self.assertIsNone(rows["p4"]["checkin_id"])

    def test_standalone_photo_uses_standalone_directory(self):
        self.api.photo_pages = {0: [make_photo("p1")], 1: []}
        self.sync.sync_all_photos()
        pending = self.db.pending_photos()
        self.assertEqual(len(pending), 1)
        destination = photo_destination(self.cfg, pending[0])
        self.assertEqual(destination.parent.name, "_standalone")

    def test_venue_history_paging(self):
        self.api.venue_pages = {
            0: [
                {"beenHere": 2, "venue": {"id": "v1", "name": "A"}},
                {"beenHere": 1, "venue": {"id": "v2", "name": "B"}},
            ],
            2: [{"beenHere": 3, "venue": {"id": "v3", "name": "C"}}],
            3: [],
        }
        self.api.venue_total = 3

        self.assertEqual(self.sync.sync_venue_history(), 3)
        self.assertEqual(self.db.stats()["venue_history"], 3)
        self.assertEqual(self.api.venue_offsets, [0, 2, 3])

    def test_lists_and_details(self):
        self.api.list_payload = [
            {
                "id": "185614/todos",
                "name": "My Saved Places",
                "type": "todos",
                "visibility": "private",
                "listItems": {"count": 2},
            }
        ]
        self.api.list_detail_payload["185614/todos"] = {
            "id": "185614/todos",
            "name": "My Saved Places",
            "listItems": {
                "count": 2,
                "items": [
                    {"venue": {"id": "v1", "name": "A"}, "note": "toll"},
                    {"venue": {"id": "v2", "name": "B"}},
                ],
            },
        }

        self.assertEqual(self.sync.sync_lists(), 1)
        stats = self.db.stats()
        self.assertEqual(stats["lists"], 1)
        self.assertEqual(stats["list_items"], 2)

    def test_lists_paginate_all_details_with_explicit_limit(self):
        all_items = [
            {"venue": {"id": f"venue-{index}", "name": f"Venue {index}"}}
            for index in range(130)
        ]
        self.api.list_payload = [
            {
                "id": "185614/venuelikes",
                "name": "My Liked Places",
                "type": "likes",
                "listItems": {"count": 131},
            }
        ]
        self.api.list_detail_payload["185614/venuelikes"] = {
            "id": "185614/venuelikes",
            "name": "My Liked Places",
            "listItems": {"count": 131, "items": all_items},
        }
        logs: list[str] = []
        sync = Sync(self.api, self.db, self.cfg, log=logs.append)

        self.assertEqual(sync.sync_lists(), 1)
        self.assertEqual(self.db.stats()["list_items"], 130)
        self.assertEqual(
            self.api.list_detail_calls,
            [
                ("185614/venuelikes", 100, 0),
                ("185614/venuelikes", 100, 100),
                ("185614/venuelikes", 100, 130),
            ],
        )
        self.assertTrue(
            any(
                "130 von 131" in line
                and "1 API-seitig nicht geliefert" in line
                for line in logs
            ),
            logs,
        )

    def test_tips_paging(self):
        self.api.tip_pages = {
            0: [
                {
                    "id": "t1",
                    "createdAt": 100,
                    "text": "hi",
                    "venue": {"id": "v1", "name": "A"},
                },
                {
                    "id": "t2",
                    "createdAt": 200,
                    "text": "da",
                    "venue": {"id": "v2", "name": "B"},
                },
            ],
            2: [
                {
                    "id": "t3",
                    "createdAt": 300,
                    "text": "weg",
                    "venue": {"id": "v3", "name": "C"},
                }
            ],
            3: [],
        }
        self.api.tip_total = 3

        self.assertEqual(self.sync.sync_tips(), 3)
        self.assertEqual(self.db.stats()["tips"], 3)
        self.assertEqual(self.api.tip_offsets, [0, 2, 3])

    def test_profile_is_persisted_without_photo_download(self):
        self.sync.sync_profile()
        profile = self.db.user_profile()
        self.assertIsNotNone(profile)
        self.assertEqual(profile["id"], "185614")
        self.assertEqual(profile["display_name"], "Stefan Koelle")
        self.assertEqual(profile["checkins_count"], 2339)
        self.assertEqual(profile["friends_count"], 159)
        self.assertEqual(profile["lists_count"], 5)
        self.assertIsNone(profile["photo_path"])
        self.assertEqual(self.db.stats()["profile_name"], "Stefan Koelle")

    def test_diagnostics_record_known_gaps(self):
        self.db.set_state("api_total", 5)
        self.db.set_state("photos_api_total", 4)
        self.db.set_state("photos_unique_total", 3)
        self.db.set_state("venue_history_total", 3)
        self.db.set_state("tips_total", 1)
        self.sync.write_diagnostics()
        diagnostics = {row["scope"]: row for row in self.db.diagnostics()}
        self.assertEqual(diagnostics["checkins"]["stored_count"], 1)
        self.assertEqual(diagnostics["checkins"]["missing_count"], 4)
        self.assertEqual(
            diagnostics["photos"]["note"], "3 eindeutige API-IDs"
        )
        self.assertEqual(
            diagnostics["venue_details"]["note"], "blocked: 402/429, keine Credits"
        )

    def test_extras_continue_after_error(self):
        self.api.fail["photos"] = RuntimeError("kaputt")
        self.api.venue_pages = {
            0: [{"beenHere": 1, "venue": {"id": "v1", "name": "A"}}],
            500: [],
        }
        self.sync.sync_extras()
        self.assertEqual(self.db.stats()["venue_history"], 1)

    def test_probe_status_mapping(self):
        self.assertEqual(CONNECTOR._probe_status(ApiError("402: Credits exhausted")), "402")
        self.assertEqual(CONNECTOR._probe_status(ApiError("404: missing")), "404")
        self.assertEqual(
            CONNECTOR._probe_status(
                ApiError("Anfrage nach 1 Versuchen fehlgeschlagen: 429: slow down")
            ),
            "429",
        )
        self.assertEqual(
            CONNECTOR._probe_status(
                AuthError("Foursquare verweigert den Zugriff (403): no")
            ),
            "403",
        )
        self.assertEqual(CONNECTOR._probe_status(RuntimeError("boom")), "ERR")

    def test_extras_abort_on_auth_error(self):
        self.api.fail["photos"] = AuthError("401")
        with self.assertRaises(AuthError):
            self.sync.sync_extras()


if __name__ == "__main__":
    unittest.main()
