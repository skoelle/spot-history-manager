from __future__ import annotations

import tempfile
import unittest

from spot_history.connectors.base import BaseConnector, Photo, Visit
from spot_history.connectors.osm import db as osm_db
from spot_history.core.config import build_paths
from spot_history.core.identity import upsert_osm_assignment
from spot_history.core.index import build_index
from spot_history.core.media import thumb_relpath
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

    def iter_place_details(self):
        yield {
            "source": "fake",
            "source_place_id": "p1",
            "name": "Michaeligarten",
            "tel": "089 123",
            "website": "example.invalid",
            "email": "info@example.invalid",
            "social_json": '{"instagram": "michi", "twitter": ""}',
            "address": "Michaelistr. 1",
            "city": "München",
            "state": "BY",
            "postal_code": "80331",
            "country_code": "DE",
            "formatted_address": "Michaelistr. 1, 80331 München",
            "lat": 48.1,
            "lng": 11.5,
            "categories_json": '["Beer Garden", "Restaurant"]',
            "date_created": "2010-01-01",
            "date_refreshed": "2026-11-20",
            "placemaker_url": "https://placemaker.invalid/p1",
            "has_related": 0,
            "fetched_at": "2026-11-20T00:00:00+00:00",
            "updated_at": "2026-11-20T00:00:00+00:00",
        }

    def iter_records(self):
        yield {
            "source": "fake",
            "record_id": "rec1",
            "kind": "survey_answer",
            "source_place_id": "p1",
            "created_at": 1700000000,
            "payload": {
                "place_name": "Michaeligarten",
                "question": "Gibt es Bier?",
                "answer": "Ja",
                "device_type": "iPhone",
            },
        }
        yield {
            "source": "fake",
            "record_id": "rec2",
            "kind": "message",
            "source_place_id": None,
            "created_at": 1600000000,
            "payload": {"recipient": "Alex", "subject": "Hallo", "text": "Servus"},
        }
        yield {
            "source": "fake",
            "record_id": "rec3",
            "kind": "account",
            "source_place_id": None,
            "created_at": None,
            "payload": {"Vorname": "Test"},
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
            place_url="https://example.invalid/fake/michi",
            photos=[
                Photo(source_photo_id="ph1", local_path="data/fake/photos/ph1.jpg"),
                Photo(source_photo_id="vid1", local_path="data/fake/media/video/vid1.mp4"),
            ],
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


class OtherConnector(BaseConnector):
    name = "other"
    kind = "sync"

    def iter_visits(self):
        yield Visit(
            source="other",
            source_visit_id="m4",
            visited_at=1700000300,
            lat=48.10005,
            lng=11.50005,
            place_name="Michaeligarten",
            source_place_id="p1-other",
            category="Beer Garden",
            city="München",
            country_code="DE",
            place_url="https://example.invalid/other/michi",
        )


class DetailsConnector(BaseConnector):
    name = "details"
    kind = "sync"

    def iter_visits(self):
        yield Visit(
            source="details",
            source_visit_id="d1",
            visited_at=1700000000,
            lat=48.1,
            lng=11.5,
            place_name="Details Ort",
            source_place_id="dp1",
            city="München",
            country_code="DE",
        )

    def iter_place_details(self):
        yield {
            "source_place_id": "dp1",
            "name": "Details Ort",
            "tel": "089 123",
            "website": "example.org",
            "email": None,
            "social_json": "{}",
            "address": None,
            "city": "München",
            "state": None,
            "postal_code": None,
            "country_code": "DE",
            "formatted_address": "Teststr. 1",
            "lat": 48.1,
            "lng": 11.5,
            "categories_json": "[]",
            "date_created": None,
            "date_refreshed": "2026-11-20",
            "placemaker_url": None,
            "has_related": 0,
            "fetched_at": None,
            "updated_at": None,
        }


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
        details = venue["details"]
        self.assertEqual(details["tel"], "089 123")
        self.assertEqual(details["website"], "https://example.invalid")
        self.assertEqual(details["email"], "info@example.invalid")
        self.assertEqual(details["social"], {"instagram": "michi"})
        self.assertEqual(details["categories"], ["Beer Garden", "Restaurant"])
        self.assertEqual(details["date_refreshed"], "2026-11-20")
        self.assertEqual(len(venue["records"]["survey_answer"]), 1)
        self.assertEqual(venue["records"]["survey_answer"][0]["payload"]["answer"], "Ja")
        self.assertNotIn("account", venue["records"])
        self.assertNotIn("message", venue["records"])
        visits = web_data.query_visits(
            self.conn, web_data.Filters(place_id=venue["id"]), limit=10, offset=0
        )
        self.assertEqual(len(visits), 1)

    def test_map_points_and_pagination(self):
        filters = web_data.Filters()
        points = web_data.query_map_points(self.conn, filters)
        self.assertEqual(len(points), 2)
        self.assertTrue(all(p["lat"] is not None for p in points))
        self.assertTrue(all(p["place_id"] is not None for p in points))
        self.assertTrue(any(p["photo_count"] for p in points))
        page_one = web_data.query_visits(self.conn, filters, limit=2, offset=0)
        page_two = web_data.query_visits(self.conn, filters, limit=2, offset=2)
        self.assertEqual(len(page_one), 2)
        self.assertEqual(len(page_two), 1)
        self.assertEqual(len({v["id"] for v in page_one + page_two}), 3)

    def test_photos(self):
        self.assertEqual(web_data.count_photos(self.conn, web_data.PhotoFilters()), 2)
        photos = web_data.query_photos(
            self.conn, web_data.PhotoFilters(), limit=10, offset=0
        )
        self.assertEqual(len(photos), 2)
        self.assertTrue(any(photo["is_video"] for photo in photos))
        self.assertTrue(all(photo["place_name"] == "Michaeligarten" for photo in photos))
        grouped = web_data.photos_for_visits(self.conn, [photos[0]["visit_id"]])
        self.assertEqual(len(grouped[photos[0]["visit_id"]]), 2)

        venue = web_data.query_venues(
            self.conn, web_data.VenueFilters(q="Michaeligarten"), limit=10, offset=0
        )[0]
        self.assertEqual(len(web_data.photos_for_place(self.conn, venue["id"])), 2)

    def test_venue_details_keep_empty_social_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"details": DetailsConnector()}, paths, log=lambda _: None)
            conn = web_data.connect(paths.index_db)
            try:
                venue = web_data.query_venues(
                    conn, web_data.VenueFilters(), limit=10, offset=0
                )[0]
                venue = web_data.get_venue(conn, venue["id"])
            finally:
                conn.close()

        details = venue["details"]
        self.assertEqual(details["social"], {})
        self.assertEqual(details["categories"], [])
        self.assertEqual(details["tel"], "089 123")
        self.assertEqual(details["website"], "https://example.org")

    def test_merged_venue_exposes_all_sources_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index(
                {"fake": FakeConnector(), "other": OtherConnector()},
                paths,
                log=lambda _: None,
            )
            conn = web_data.connect(paths.index_db)
            try:
                venues = web_data.query_venues(
                    conn,
                    web_data.VenueFilters(q="Michaeligarten"),
                    limit=10,
                    offset=0,
                )
                self.assertEqual(len(venues), 1)
                venue = venues[0]
                self.assertEqual(venue["sources"], ["fake", "other"])
                self.assertEqual(venue["visits_count"], 2)
                self.assertEqual(venue["photo_count"], 2)
                self.assertEqual(
                    {link["source"] for link in venue["links"]}, {"fake", "other"}
                )
                self.assertEqual(
                    {link["url"] for link in venue["links"]},
                    {
                        "https://example.invalid/fake/michi",
                        "https://example.invalid/other/michi",
                    },
                )
                self.assertEqual(
                    web_data.count_venues(
                        conn,
                        web_data.VenueFilters(source="other", q="Michaeligarten"),
                    ),
                    1,
                )
                detail = web_data.get_venue(conn, venue["id"])
                visits = web_data.query_visits(
                    conn,
                    web_data.Filters(place_id=venue["id"]),
                    limit=10,
                    offset=0,
                )
            finally:
                conn.close()
        self.assertEqual(len(detail["links"]), 2)
        self.assertEqual({visit["source"] for visit in visits}, {"fake", "other"})

    def test_venue_match_filters_members_and_counts(self):
        counts = web_data.load_venue_match_counts(self.conn)
        self.assertEqual(counts["all"], 3)
        self.assertEqual(counts["local"], 3)
        self.assertEqual(counts["osm"], 0)
        self.assertEqual(
            web_data.count_venues(
                self.conn, web_data.VenueFilters(match="local")
            ),
            3,
        )
        self.assertEqual(
            web_data.count_venues(
                self.conn, web_data.VenueFilters(match="unmatched")
            ),
            3,
        )
        self.assertEqual(
            web_data.count_venues(self.conn, web_data.VenueFilters(match="osm")),
            0,
        )
        venues = web_data.query_venues(
            self.conn, web_data.VenueFilters(q="Michaeligarten"), limit=10, offset=0
        )
        members = web_data.venue_members(self.conn, venues[0]["id"])
        self.assertEqual(
            [(member["source"], member["source_place_id"]) for member in members],
            [("fake", "p1")],
        )

    def test_osm_filter_after_manual_assignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            upsert_osm_assignment(
                paths.place_identity_db, "fake", "p1", "node", 42
            )
            osm_conn = osm_db.open_db(paths.osm_db)
            try:
                osm_db.save_pois(
                    osm_conn,
                    [osm_db.Poi("node", 42, "Michaeligarten", 48.2, 11.6)],
                )
            finally:
                osm_conn.close()
            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            conn = web_data.connect(paths.index_db)
            try:
                counts = web_data.load_venue_match_counts(conn)
                self.assertEqual(counts["osm"], 1)
                venues = web_data.query_venues(
                    conn, web_data.VenueFilters(match="osm"), limit=10, offset=0
                )
                venue = web_data.get_venue_by_key(conn, "osm-node-42")
                points = web_data.query_map_points(conn, web_data.Filters())
            finally:
                conn.close()
        self.assertEqual(len(venues), 1)
        self.assertEqual(venues[0]["key"], "osm-node-42")
        self.assertEqual(venues[0]["match_status"], "osm")
        self.assertEqual(venues[0]["osm_url"], "https://www.openstreetmap.org/node/42")
        self.assertEqual(venues[0]["lat"], 48.2)
        self.assertEqual(venues[0]["lng"], 11.6)
        self.assertEqual(venues[0]["source_lat"], 48.1)
        self.assertEqual(venues[0]["coords_source"], "osm")
        self.assertEqual(venue["lat"], 48.2)
        self.assertEqual(venue["coords_source"], "osm")
        self.assertTrue(any(point["coords_source"] == "osm" for point in points))
        self.assertTrue(any(point["lat"] == 48.2 for point in points))

    def test_venue_lookup_by_key_and_legacy(self):
        venues = web_data.query_venues(
            self.conn, web_data.VenueFilters(q="Michaeligarten"), limit=10, offset=0
        )
        self.assertEqual(len(venues), 1)
        key = venues[0]["key"]
        self.assertEqual(key, "local-fake-p1")
        self.assertEqual(venues[0]["match_status"], "local")
        self.assertIsNone(venues[0]["osm_url"])

        by_key = web_data.get_venue_by_key(self.conn, key)
        self.assertIsNotNone(by_key)
        self.assertEqual(by_key["key"], key)
        self.assertEqual(by_key["id"], venues[0]["id"])

        by_legacy = web_data.get_venue_by_key(self.conn, "fake:p1")
        self.assertIsNotNone(by_legacy)
        self.assertEqual(by_legacy["key"], key)

        by_numeric = web_data.get_venue_by_key(self.conn, str(venues[0]["id"]))
        self.assertIsNotNone(by_numeric)
        self.assertEqual(by_numeric["key"], key)

        self.assertIsNone(web_data.get_venue_by_key(self.conn, "osm-way-1"))

    def test_activity_groups_records(self):
        groups = web_data.load_activity(self.conn, source="fake")
        labels = {group["kind"]: group["label"] for group in groups}
        self.assertEqual(labels["account"], "Konto")
        self.assertEqual(labels["message"], "Nachrichten")
        account = next(group for group in groups if group["kind"] == "account")
        self.assertEqual(account["records"][0]["payload"], {"Vorname": "Test"})
        message = next(group for group in groups if group["kind"] == "message")
        self.assertEqual(message["records"][0]["payload"]["text"], "Servus")
        self.assertEqual(web_data.load_activity(self.conn, source="unknown"), [])

    def test_merged_venue_aggregates_records_from_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index(
                {"fake": FakeConnector(), "other": OtherConnector()},
                paths,
                log=lambda _: None,
            )
            conn = web_data.connect(paths.index_db)
            try:
                venue = web_data.query_venues(
                    conn,
                    web_data.VenueFilters(q="Michaeligarten"),
                    limit=10,
                    offset=0,
                )[0]
                detail = web_data.get_venue(conn, venue["id"])
            finally:
                conn.close()
        self.assertEqual(len(detail["links"]), 2)
        self.assertEqual(len(detail["records"]["survey_answer"]), 1)
        self.assertEqual(
            detail["records"]["survey_answer"][0]["payload"]["question"],
            "Gibt es Bier?",
        )

    def test_thumb_relpath_is_stable(self):
        first = thumb_relpath("foursquare", "abc")
        self.assertEqual(first, thumb_relpath("foursquare", "abc"))
        self.assertNotEqual(first, thumb_relpath("foursquare", "abd"))
        self.assertTrue(first.startswith("data/foursquare/thumbs/"))
        self.assertTrue(first.endswith(".jpg"))


if __name__ == "__main__":
    unittest.main()
