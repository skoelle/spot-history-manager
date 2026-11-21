from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from spot_history.connectors.base import BaseConnector, Photo, Visit
from spot_history.core.config import build_paths
from spot_history.core.index import build_index


class FakeConnector(BaseConnector):
    name = "fake"
    kind = "import"

    def iter_profile(self):
        return {
            "source": "fake",
            "source_profile_id": "u1",
            "display_name": "Test User",
            "home_city": "München",
            "country_code": "DE",
            "photo_path": "data/fake/profile/u1.jpg",
            "counts": {"checkins": 2, "photos": 1},
            "updated_at": "2026-11-20T00:00:00+00:00",
        }

    def iter_place_details(self):
        yield {
            "source": "fake",
            "source_place_id": "p1",
            "name": "Testort",
            "tel": "089 123",
            "website": "https://example.invalid",
            "email": "ort@example.invalid",
            "social_json": '{"instagram": "testort"}',
            "address": "Teststr. 1",
            "city": "München",
            "state": "BY",
            "postal_code": "80331",
            "country_code": "DE",
            "formatted_address": "Teststr. 1, 80331 München",
            "lat": 48.1,
            "lng": 11.5,
            "categories_json": '["Café", "Bakery"]',
            "date_created": "2010-01-01",
            "date_refreshed": "2026-11-20",
            "placemaker_url": "https://placemaker.invalid/p1",
            "has_related": 1,
            "fetched_at": "2026-11-20T00:00:00+00:00",
            "updated_at": "2026-11-20T00:00:00+00:00",
        }

    def iter_visits(self):
        yield Visit(
            source="fake",
            source_visit_id="v1",
            visited_at=1700000000,
            lat=48.1,
            lng=11.5,
            place_name="Testort",
            source_place_id="p1",
            category="Café",
            city="München",
            country_code="DE",
            note="hallo",
            tz_offset=60,
            photos=[Photo(source_photo_id="ph1", local_path="data/fake/photos/ph1.jpg")],
        )
        yield Visit(
            source="fake",
            source_visit_id="v2",
            visited_at=1700000100,
            lat=None,
            lng=None,
            place_name="Ohne Koordinaten",
        )


class EmptyConnector(BaseConnector):
    name = "empty"
    kind = "sync"

    def iter_visits(self):
        return []


class IndexTest(unittest.TestCase):
    def test_build_creates_db_and_geojson(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            summary = build_index(
                {"fake": FakeConnector(), "empty": EmptyConnector()},
                paths,
                log=lambda _: None,
            )
            self.assertEqual(summary["sources"], ["fake"])
            self.assertEqual(summary["visits"], 2)
            self.assertEqual(summary["photos"], 1)
            self.assertEqual(summary["features"], 1)
            self.assertTrue(paths.index_db.exists())
            self.assertTrue(paths.map_geojson.exists())

            conn = sqlite3.connect(paths.index_db)
            try:
                visits = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
                places = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
                photos = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
                sources = conn.execute("SELECT name, visits FROM sources").fetchall()
                profiles = conn.execute(
                    "SELECT source, display_name, counts_json FROM source_profiles"
                ).fetchall()
                details = conn.execute(
                    "SELECT source, source_place_id, tel, categories_json "
                    "FROM place_details"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(visits, 2)
            self.assertEqual(places, 1)
            self.assertEqual(photos, 1)
            self.assertEqual(sources, [("fake", 2)])
            self.assertEqual(profiles[0][0], "fake")
            self.assertEqual(profiles[0][1], "Test User")
            self.assertIn('"checkins": 2', profiles[0][2])
            self.assertEqual(summary["place_details"], 1)
            self.assertEqual(details[0][0], "fake")
            self.assertEqual(details[0][1], "p1")
            self.assertEqual(details[0][2], "089 123")

            payload = json.loads(Path(paths.map_geojson).read_text(encoding="utf-8"))
            self.assertEqual(payload["type"], "FeatureCollection")
            self.assertEqual(len(payload["features"]), 1)
            feature = payload["features"][0]
            self.assertEqual(feature["geometry"]["coordinates"], [11.5, 48.1])
            self.assertEqual(feature["properties"]["source"], "fake")
            self.assertEqual(feature["properties"]["place"], "Testort")

    def test_rebuild_discards_stale_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            build_index({"empty": EmptyConnector()}, paths, log=lambda _: None)
            conn = sqlite3.connect(paths.index_db)
            try:
                sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
                visits = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(sources, 0)
            self.assertEqual(visits, 0)


if __name__ == "__main__":
    unittest.main()
