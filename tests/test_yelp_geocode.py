from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spot_history.connectors.yelp import connector as connector_module
from spot_history.connectors.yelp.config import build_config
from spot_history.connectors.yelp.connector import YelpConnector
from spot_history.connectors.yelp.db import Database, iso_now
from spot_history.connectors.yelp.geocode import GeocodeResult, geocode_places
from spot_history.connectors.yelp.ids import place_key


class FakeGeocoder:
    def __init__(self, values=None, fail_lat=None):
        self.values = values or {}
        self.fail_lat = fail_lat
        self.calls = []

    def reverse(self, lat, lng):
        self.calls.append((lat, lng))
        if self.fail_lat is not None and abs(lat - self.fail_lat) < 1e-9:
            raise RuntimeError("boom")
        city = self.values.get((round(lat, 5), round(lng, 5)))
        if city:
            return GeocodeResult(city=city, state="Bayern", country_code="DE")
        return None


def add_checkin(db, checkin_id, name, timestamp, lat=None, lng=None, iso=None):
    db.upsert(
        "checkins",
        [
            {
                "id": checkin_id,
                "visited_at": timestamp,
                "visited_at_iso": iso or f"{timestamp}",
                "tz_offset": 0,
                "place_key": place_key(name),
                "place_name": name,
                "lat": lat,
                "lng": lng,
                "note": None,
                "status": "Angezeigt",
                "raw_row": "{}",
                "imported_at": iso_now(),
            }
        ],
    )


def add_bookmark(db, bookmark_id, name, url):
    db.upsert(
        "bookmarks",
        [
            {
                "id": bookmark_id,
                "created_at": 1600000000,
                "created_at_iso": "2020-09-13T12:26:40+00:00",
                "place_name": name,
                "place_url": url,
                "note": None,
                "status": "Angezeigt",
                "raw_row": "{}",
                "imported_at": iso_now(),
            }
        ],
    )


def make_index(path: Path, venues):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE places (source TEXT, name TEXT, city TEXT, "
        "state TEXT, country_code TEXT)"
    )
    conn.executemany(
        "INSERT INTO places (source, name, city, state, country_code) "
        "VALUES ('foursquare', ?, ?, ?, ?)",
        venues,
    )
    conn.commit()
    conn.close()


class YelpGeocodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = build_config(
            export_dir=self.root / "export",
            data_dir=self.root / "data",
            copy_media=False,
        )
        self.db = Database(self.config.db_path).open()
        add_checkin(
            self.db, "c-coords", "Koordinaten Ort", 1600000000,
            lat=48.1, lng=11.5, iso="2020-09-13T12:26:40+00:00",
        )
        add_checkin(
            self.db, "c-fs-1", "Foursquare Ort", 1600000001,
            iso="2020-09-13T12:26:41+00:00",
        )
        add_checkin(
            self.db, "c-fs-2", "Foursquare Ort", 1600000002,
            iso="2020-09-13T12:26:42+00:00",
        )
        add_checkin(
            self.db, "c-url", "URL Ort", 1600000003,
            iso="2020-09-13T12:26:43+00:00",
        )
        add_checkin(
            self.db, "c-ambiguous", "Ambiguous Ort", 1600000004,
            iso="2020-09-13T12:26:44+00:00",
        )
        add_checkin(
            self.db, "c-conflict", "Conflict Ort", 1600000005,
            iso="2020-09-13T12:26:45+00:00",
        )
        add_checkin(
            self.db, "c-unknown", "Unbekannt Ort", 1600000006,
            iso="2020-09-13T12:26:46+00:00",
        )
        add_bookmark(
            self.db, "b-url", "URL Ort",
            "https://www.yelp.com/biz/url-ort-m%C3%BCnchen",
        )
        add_bookmark(
            self.db, "b-conflict", "Conflict Ort",
            "https://www.yelp.com/biz/conflict-ort-m%C3%BCnchen",
        )
        self.db.rebuild_places()

        self.index_path = self.root / "index.db"
        make_index(
            self.index_path,
            [
                ("Foursquare Ort", "München", "Bayern", "DE"),
                ("Ambiguous Ort", "München", "Bayern", "DE"),
                ("Ambiguous Ort", "Berlin", "Berlin", "DE"),
                ("Conflict Ort", "Berlin", "Berlin", "DE"),
            ],
        )
        self.geocoder = FakeGeocoder({(48.1, 11.5): "München"})

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_reverse_cache_and_name_matches(self):
        summary = geocode_places(
            self.db,
            self.geocoder,
            index_db=self.index_path,
            sleep=0,
            log=lambda _: None,
        )
        self.assertEqual(len(self.geocoder.calls), 1)
        self.assertEqual(summary.reverse_ok, 1)
        self.assertEqual(summary.reverse_cached, 0)
        self.assertEqual(summary.foursquare_ok, 1)
        self.assertEqual(summary.foursquare_ambiguous, 1)
        self.assertEqual(summary.url_ok, 1)
        self.assertEqual(summary.url_conflict, 1)
        self.assertEqual(summary.places_with_city, 3)
        self.assertEqual(summary.visits_with_city, 4)
        self.assertEqual(summary.unknown_places, 3)
        self.assertEqual(summary.unknown_visits, 3)

        cities = {
            row["name"]: row["city"]
            for row in self.db.conn.execute("SELECT name, city FROM places")
        }
        self.assertEqual(cities["Koordinaten Ort"], "München")
        self.assertEqual(cities["Foursquare Ort"], "München")
        self.assertEqual(cities["URL Ort"], "München")
        self.assertIsNone(cities["Ambiguous Ort"])
        self.assertIsNone(cities["Conflict Ort"])
        self.assertIsNone(cities["Unbekannt Ort"])

        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM place_geocodes WHERE status='ambiguous'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM place_geocodes WHERE status='conflict'"
            ).fetchone()[0],
            1,
        )

        calls = len(self.geocoder.calls)
        second = geocode_places(
            self.db,
            self.geocoder,
            index_db=self.index_path,
            sleep=0,
            log=lambda _: None,
        )
        self.assertEqual(len(self.geocoder.calls), calls)
        self.assertEqual(second.reverse_cached, 1)
        self.assertEqual(second.places_with_city, 3)

        forced = geocode_places(
            self.db,
            self.geocoder,
            index_db=self.index_path,
            sleep=0,
            force=True,
            log=lambda _: None,
        )
        self.assertEqual(forced.reverse_ok, 1)
        self.assertEqual(len(self.geocoder.calls), calls + 1)

    def test_iter_visits_receives_city_for_all_place_visits(self):
        geocode_places(
            self.db,
            self.geocoder,
            index_db=self.index_path,
            sleep=0,
            log=lambda _: None,
        )
        with mock.patch.object(connector_module, "build_config", return_value=self.config):
            visits = list(YelpConnector().iter_visits())
        fs_visits = [v for v in visits if v.place_name == "Foursquare Ort"]
        self.assertEqual(len(fs_visits), 2)
        self.assertTrue(all(v.city == "München" for v in fs_visits))
        self.assertTrue(all(v.country_code == "DE" for v in fs_visits))

    def test_errors_do_not_stop_other_places(self):
        add_checkin(
            self.db, "c-coords-2", "Zweiter Ort", 1600000007,
            lat=48.2, lng=11.6, iso="2020-09-13T12:26:47+00:00",
        )
        self.db.rebuild_places()
        geocoder = FakeGeocoder(
            {(48.1, 11.5): "München", (48.2, 11.6): "München"},
            fail_lat=48.1,
        )
        summary = geocode_places(
            self.db,
            geocoder,
            index_db=self.index_path,
            sleep=0,
            log=lambda _: None,
        )
        self.assertEqual(summary.reverse_errors, 1)
        self.assertEqual(summary.reverse_ok, 1)
        row = self.db.conn.execute(
            "SELECT city FROM places WHERE name='Zweiter Ort'"
        ).fetchone()
        self.assertEqual(row["city"], "München")


if __name__ == "__main__":
    unittest.main()
