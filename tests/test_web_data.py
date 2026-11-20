from __future__ import annotations

import tempfile
import unittest

from spot_history.connectors.base import BaseConnector, Visit
from spot_history.core.config import build_paths
from spot_history.core.index import build_index
from spot_history.web import data as web_data


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
            "photo_path": None,
            "counts": {"checkins": 3, "photos": 0},
            "updated_at": "2026-11-20T00:00:00+00:00",
        }

    def iter_visits(self):
        yield Visit(
            source="fake",
            source_visit_id="m1",
            visited_at=1700000000,
            lat=48.1,
            lng=11.5,
            place_name="Michaeligarten",
            source_place_id="p1",
            category="Beer Garden",
            city="München",
            country_code="DE",
            note="Biergarten",
        )
        yield Visit(
            source="fake",
            source_visit_id="m2",
            visited_at=1600000000,
            lat=None,
            lng=None,
            place_name="Ohne Koordinaten",
            source_place_id="p2",
            category="Café",
            city="München",
            country_code="DE",
        )
        yield Visit(
            source="fake",
            source_visit_id="b1",
            visited_at=1500000000,
            lat=52.5,
            lng=13.4,
            place_name="Berlin Ort",
            source_place_id="p3",
            category="Bar",
            city="Berlin",
            country_code="DE",
        )


class WebDataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = build_paths(self.tmp.name)
        build_index({"fake": FakeConnector()}, self.paths, log=lambda _: None)
        self.conn = web_data.connect(self.paths.index_db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_default_city_and_counts(self):
        options = web_data.load_options(self.conn)
        city = web_data.resolve_city(None, options)
        self.assertEqual(city, "München")
        filters = web_data.Filters(city=city)
        self.assertEqual(web_data.count_visits(self.conn, filters), 2)
        stats = web_data.load_stats(self.conn, filters)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["places"], 2)

    def test_filters(self):
        all_visits = web_data.Filters()
        self.assertEqual(web_data.count_visits(self.conn, all_visits), 3)
        berlin = web_data.Filters(city="Berlin")
        self.assertEqual(web_data.count_visits(self.conn, berlin), 1)
        beer = web_data.Filters(category="Beer Garden")
        self.assertEqual(web_data.count_visits(self.conn, beer), 1)
        search = web_data.Filters(q="Berlin")
        self.assertEqual(web_data.count_visits(self.conn, search), 1)

    def test_profiles_and_venues(self):
        profiles = web_data.load_profiles(self.conn)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["display_name"], "Test User")
        self.assertEqual(profiles[0]["initials"], "TU")
        self.assertEqual(profiles[0]["counts"]["checkins"], 3)

        venues = web_data.query_venues(
            self.conn, web_data.VenueFilters(), limit=10, offset=0
        )
        self.assertEqual(len(venues), 3)
        self.assertEqual(web_data.count_venues(self.conn, web_data.VenueFilters()), 3)

        munich = web_data.VenueFilters(city="München")
        self.assertEqual(web_data.count_venues(self.conn, munich), 2)
        self.assertEqual(web_data.load_venue_options(self.conn)["cities"][0]["value"], "München")

        venue = web_data.get_venue(self.conn, venues[0]["id"])
        self.assertIsNotNone(venue)
        self.assertEqual(venue["id"], venues[0]["id"])
        self.assertEqual(venue["visits_count"], 1)
        visits = web_data.query_visits(
            self.conn, web_data.Filters(place_id=venue["id"]), limit=10, offset=0
        )
        self.assertEqual(len(visits), 1)

    def test_map_points_and_pagination(self):
        filters = web_data.Filters()
        points = web_data.query_map_points(self.conn, filters)
        self.assertEqual(len(points), 2)
        self.assertTrue(all(p["lat"] is not None for p in points))
        page_one = web_data.query_visits(self.conn, filters, limit=2, offset=0)
        page_two = web_data.query_visits(self.conn, filters, limit=2, offset=2)
        self.assertEqual(len(page_one), 2)
        self.assertEqual(len(page_two), 1)
        self.assertEqual(len({v["id"] for v in page_one + page_two}), 3)


if __name__ == "__main__":
    unittest.main()
