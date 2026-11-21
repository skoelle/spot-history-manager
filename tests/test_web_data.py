from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone

from spot_history.connectors.base import BaseConnector, Photo, Visit
from spot_history.connectors.osm import db as osm_db
from spot_history.core.config import build_paths
from spot_history.core.identity import upsert_osm_assignment
from spot_history.core.index import build_index
from spot_history.core.media import thumb_relpath
from spot_history.core.places import PlaceRule, upsert_rule
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
            "payload": {
                "Vorname": "Test",
                "Nachname": "User",
                "Geburtstag": "01.01.1990",
            },
        }
        yield {
            "source": "fake",
            "record_id": "rec4",
            "kind": "unlinked_comment",
            "source_place_id": "p1",
            "created_at": 1550000000,
            "payload": {
                "place_name": "Ohne Check-in",
                "text": "Kommentar ohne Verknüpfung",
                "status": "Angezeigt",
            },
        }
        yield {
            "source": "fake",
            "record_id": "rec5",
            "kind": "unlinked_photo",
            "source_place_id": "p1",
            "created_at": 1540000000,
            "payload": {
                "place_name": "Ohne Check-in",
                "media_type": "biz_photo",
                "filename": "orphan.jpg",
                "local_path": "data/fake/media/orphan.jpg",
                "caption": None,
                "status": "Angezeigt",
            },
        }
        yield {
            "source": "fake",
            "record_id": "rec6",
            "kind": "unlinked_photo",
            "source_place_id": None,
            "created_at": 1530000000,
            "payload": {
                "place_name": None,
                "media_type": "profile_photo",
                "filename": "profile.jpg",
                "local_path": "data/fake/media/profile.jpg",
                "caption": None,
                "status": "Angezeigt",
            },
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
            extra={"end_time": "2023-11-14T22:58:20+00:00"},
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


class NearAlphaConnector(BaseConnector):
    name = "alpha"
    kind = "import"

    def iter_visits(self):
        yield Visit(
            source="alpha",
            source_visit_id="alpha-1",
            visited_at=1700000000,
            lat=48.1,
            lng=11.5,
            place_name="Alpha",
            source_place_id="a1",
        )


class NearBetaConnector(BaseConnector):
    name = "beta"
    kind = "sync"

    def iter_visits(self):
        yield Visit(
            source="beta",
            source_visit_id="beta-1",
            visited_at=1700000100,
            lat=48.1001,
            lng=11.5001,
            place_name="Beta",
            source_place_id="b1",
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


class CommentConnector(BaseConnector):
    name = "comment"
    kind = "import"

    def iter_visits(self):
        yield Visit(
            source="comment",
            source_visit_id="c1",
            visited_at=1700000000,
            lat=48.1,
            lng=11.5,
            place_name="Kommentar Ort",
            source_place_id="cp1",
            city="München",
            country_code="DE",
            note="Check-in",
            extra={"comments": ["Kommentar am Check-in"]},
        )
        yield Visit(
            source="comment",
            source_visit_id="comment:c2",
            visited_at=1690000000,
            place_name="Kommentar Ort",
            source_place_id="cp1",
            city="München",
            country_code="DE",
            note="Nur Kommentar",
            extra={"comment_only": True, "has_coords": False},
        )


class FoursquareActivityConnector(BaseConnector):
    name = "foursquare"
    kind = "sync"

    def iter_records(self):
        yield {
            "source": "foursquare",
            "record_id": "tip:t1",
            "kind": "tip",
            "source_place_id": "v1",
            "created_at": 1700000000,
            "payload": {
                "venue_name": "Michaeligarten",
                "text": "Sehr guter Steckerlfisch",
                "canonical_url": "https://foursquare.com/tip/t1",
                "lang": "de",
                "saves_count": 2,
                "title": "Stefan's tip",
            },
        }
        yield {
            "source": "foursquare",
            "record_id": "list:l1",
            "kind": "list",
            "source_place_id": None,
            "created_at": 1700000000,
            "payload": {
                "name": "München",
                "type": "created",
                "visibility": "public",
                "count": 2,
                "item_count": 2,
                "items": [
                    {"venue_id": "v1", "venue_name": "Michaeligarten", "note": ""},
                    {"venue_id": "v2", "venue_name": "Nicht besucht", "note": None},
                ],
            },
        }
        yield {
            "source": "foursquare",
            "record_id": "comments:c1",
            "kind": "foursquare_comment",
            "source_place_id": "v1",
            "created_at": 1700000000,
            "payload": {
                "checkin_id": "c1",
                "venue_name": "Michaeligarten",
                "comments": [
                    {
                        "id": "cc1",
                        "author": "Alex",
                        "text": "Prost",
                        "created_at": 1700000000,
                        "date": "14.11.2023",
                    }
                ],
            },
        }
        yield {
            "source": "foursquare",
            "record_id": "likes:c1",
            "kind": "foursquare_like",
            "source_place_id": "v1",
            "created_at": 1700000000,
            "payload": {
                "checkin_id": "c1",
                "venue_name": "Michaeligarten",
                "count": 2,
                "people": ["Alex", "Sam"],
            },
        }
        yield {
            "source": "foursquare",
            "record_id": "taste:ta1",
            "kind": "taste",
            "source_place_id": None,
            "created_at": None,
            "payload": {"text": "Espresso", "on_user": True},
        }

    def iter_visits(self):
        yield Visit(
            source="foursquare",
            source_visit_id="c1",
            visited_at=1700000000,
            lat=48.1,
            lng=11.5,
            place_name="Michaeligarten",
            source_place_id="v1",
            category="Beer Garden",
            city="München",
            country_code="DE",
            extra={
                "comments": ["Alex: Prost"],
                "like_count": 2,
                "likes": ["Alex", "Sam"],
            },
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
        beer_group = web_data.Filters(category_group="Biergarten")
        self.assertEqual(web_data.count_visits(self.conn, beer_group), 1)
        search = web_data.Filters(q="Berlin")
        self.assertEqual(web_data.count_visits(self.conn, search), 1)
        options = web_data.load_options(self.conn)
        self.assertIn(
            "Biergarten",
            [group["value"] for group in options["category_groups"]],
        )
        self.assertEqual(
            web_data.count_venues(
                self.conn, web_data.VenueFilters(category_group="Biergarten")
            ),
            1,
        )

    def test_visit_duration_and_end_time(self):
        visits = web_data.query_visits(
            self.conn, web_data.Filters(), limit=10, offset=0
        )
        visit = next(item for item in visits if item["source_visit_id"] == "m1")
        self.assertEqual(visit["duration"], "45 Min")
        self.assertEqual(visit["duration_minutes"], 45)
        self.assertEqual(visit["end_time"], "23:58")
        other = next(item for item in visits if item["source_visit_id"] == "m2")
        self.assertIsNone(other["duration"])
        self.assertIsNone(other["duration_minutes"])
        self.assertIsNone(other["end_time"])

    def test_format_end_label(self):
        self.assertEqual(
            web_data._format_end_label(
                datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 1, 23, 30, tzinfo=timezone.utc),
            ),
            "23:30",
        )
        self.assertEqual(
            web_data._format_end_label(
                datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 2, 3, 0, tzinfo=timezone.utc),
            ),
            "02.01. 03:00",
        )
        self.assertIsNone(
            web_data._format_end_label(
                datetime(2024, 1, 1, tzinfo=timezone.utc), None
            )
        )

    def test_format_duration(self):
        self.assertEqual(web_data._format_duration(45 * 60), "45 Min")
        self.assertEqual(web_data._format_duration(60 * 60), "1 Std")
        self.assertEqual(
            web_data._format_duration((2 * 60 + 11) * 60), "2 Std 11 Min"
        )
        self.assertEqual(web_data._format_duration(26 * 60 * 60), "1 Tag 2 Std")
        self.assertIsNone(web_data._format_duration(0))
        self.assertIsNone(web_data._format_duration(-5))
        self.assertIsNone(web_data._format_duration(None))

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
        self.assertNotIn("unlinked_comment", venue["records"])
        self.assertEqual(len(venue["records"]["unlinked_photo"]), 1)
        self.assertTrue(venue["records"]["unlinked_photo"][0]["without_checkin"])
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

    def test_foursquare_activity_and_venue_tips(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index(
                {"foursquare": FoursquareActivityConnector()},
                paths,
                log=lambda _: None,
            )
            conn = web_data.connect(paths.index_db)
            try:
                groups = web_data.load_foursquare_activity(conn)
                visits = web_data.query_visits(
                    conn, web_data.Filters(source="foursquare"), limit=10, offset=0
                )
                venue = web_data.get_venue_by_key(conn, "foursquare:v1")
            finally:
                conn.close()

        self.assertEqual(
            [group["kind"] for group in groups],
            ["tip", "list", "foursquare_comment", "foursquare_like", "taste"],
        )
        tip = groups[0]["records"][0]
        self.assertEqual(tip["payload"]["text"], "Sehr guter Steckerlfisch")
        self.assertTrue(tip["place_key"])
        listing = groups[1]["records"][0]
        self.assertEqual(listing["payload"]["name"], "München")
        self.assertTrue(listing["payload"]["items"][0]["place_key"])
        self.assertIsNone(listing["payload"]["items"][1]["place_key"])
        self.assertEqual(
            groups[2]["records"][0]["payload"]["comments"][0]["author"], "Alex"
        )
        self.assertEqual(groups[3]["records"][0]["payload"]["people"], ["Alex", "Sam"])
        self.assertEqual(groups[4]["records"][0]["payload"]["text"], "Espresso")

        self.assertEqual(visits[0]["comments"], ["Alex: Prost"])
        self.assertEqual(visits[0]["like_count"], 2)
        self.assertEqual(visits[0]["likes"], ["Alex", "Sam"])
        self.assertIsNotNone(venue)
        self.assertEqual(venue["records"]["tip"][0]["payload"]["venue_name"], "Michaeligarten")

    def test_activity_groups_records(self):
        groups = web_data.load_activity(self.conn, source="fake")
        labels = {group["kind"]: group["label"] for group in groups}
        self.assertEqual(labels["account"], "Konto")
        self.assertEqual(labels["message"], "Nachrichten")
        account = next(group for group in groups if group["kind"] == "account")
        self.assertEqual(
            account["records"][0]["payload"],
            {"Vorname": "Test", "Nachname": "User", "Geburtstag": "01.01.1990"},
        )
        message = next(group for group in groups if group["kind"] == "message")
        self.assertEqual(message["records"][0]["payload"]["text"], "Servus")
        self.assertEqual(labels["unlinked_comment"], "Kommentare ohne Check-in")
        self.assertEqual(labels["unlinked_photo"], "Fotos ohne Check-in")
        comment = next(
            group for group in groups if group["kind"] == "unlinked_comment"
        )
        self.assertEqual(
            comment["records"][0]["payload"]["text"],
            "Kommentar ohne Verknüpfung",
        )
        photo = next(group for group in groups if group["kind"] == "unlinked_photo")
        self.assertEqual(photo["records"][0]["url"], "/media/data/fake/media/orphan.jpg")
        self.assertEqual(
            photo["records"][0]["thumb_url"], "/media/data/fake/media/orphan.jpg"
        )
        self.assertEqual(photo["records"][0]["place_name"], "Ohne Check-in")
        self.assertFalse(photo["records"][0]["is_video"])
        self.assertEqual(web_data.load_activity(self.conn, source="unknown"), [])

    def test_split_activity_moves_profile_photo_and_account(self):
        activity = web_data.split_activity(
            web_data.load_activity(self.conn, source="fake")
        )
        self.assertEqual(
            activity["account"][0]["payload"],
            {"Vorname": "Test", "Nachname": "User", "Geburtstag": "01.01.1990"},
        )
        self.assertEqual(activity["profile_name"], "Test User")
        self.assertEqual(activity["profile_facts"], [("Geburtstag", "01.01.1990")])
        self.assertIsNotNone(activity["profile_photo"])
        self.assertEqual(activity["profile_photo"]["id"], "rec6")
        self.assertEqual(
            activity["profile_photo"]["url"], "/media/data/fake/media/profile.jpg"
        )
        self.assertEqual(activity["profile_photo"]["place_name"], "Yelp-Profilfoto")
        kinds = [group["kind"] for group in activity["groups"]]
        self.assertNotIn("account", kinds)
        photo = next(
            group for group in activity["groups"] if group["kind"] == "unlinked_photo"
        )
        self.assertEqual([record["id"] for record in photo["records"]], ["rec5"])

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
        self.assertNotIn("unlinked_comment", detail["records"])
        self.assertEqual(len(detail["records"]["unlinked_photo"]), 1)

    def test_comment_visits_are_marked_and_derive_coords(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"comment": CommentConnector()}, paths, log=lambda _: None)
            conn = web_data.connect(paths.index_db)
            try:
                visits = web_data.query_visits(
                    conn, web_data.Filters(), limit=10, offset=0
                )
                points = web_data.query_map_points(conn, web_data.Filters())
            finally:
                conn.close()

        by_id = {visit["source_visit_id"]: visit for visit in visits}
        self.assertTrue(by_id["c1"]["is_comment_only"] is False)
        self.assertEqual(by_id["c1"]["comments"], ["Kommentar am Check-in"])
        self.assertTrue(by_id["comment:c2"]["is_comment_only"])
        self.assertEqual(by_id["comment:c2"]["note"], "Nur Kommentar")
        self.assertTrue(by_id["comment:c2"]["coords_derived"])
        self.assertEqual(len(points), 2)
        self.assertEqual(sum(1 for point in points if point["is_comment_only"]), 1)

    def test_nearby_venues_and_source_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index(
                {
                    "alpha": NearAlphaConnector(),
                    "beta": NearBetaConnector(),
                },
                paths,
                log=lambda _: None,
            )
            conn = web_data.connect(paths.index_db)
            try:
                alpha = web_data.query_venues(
                    conn, web_data.VenueFilters(q="Alpha"), limit=10, offset=0
                )[0]
                beta = web_data.query_venues(
                    conn, web_data.VenueFilters(q="Beta"), limit=10, offset=0
                )[0]
                nearby = web_data.nearby_venues(
                    conn, alpha["id"], radius_m=50, min_similarity=0.0
                )
                filtered = web_data.nearby_venues(
                    conn, alpha["id"], radius_m=50, min_similarity=0.99
                )
                alpha_ref = web_data.venue_source_ref(conn, alpha["id"])
                beta_ref = web_data.venue_source_ref(conn, beta["id"])
            finally:
                conn.close()
        self.assertEqual([item["key"] for item in nearby], [beta["key"]])
        self.assertGreater(nearby[0]["distance_m"], 0)
        self.assertLessEqual(nearby[0]["distance_m"], 50)
        self.assertEqual(nearby[0]["sources"], ["beta"])
        self.assertEqual(filtered, [])
        self.assertEqual(alpha_ref, {"source": "alpha", "source_place_id": "a1"})
        self.assertEqual(beta_ref, {"source": "beta", "source_place_id": "b1"})

    def test_merge_and_unmerge_rules_rebuild_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            connectors = {
                "alpha": NearAlphaConnector(),
                "beta": NearBetaConnector(),
            }
            build_index(connectors, paths, log=lambda _: None)
            conn = web_data.connect(paths.index_db)
            try:
                alpha = web_data.query_venues(
                    conn, web_data.VenueFilters(q="Alpha"), limit=10, offset=0
                )[0]
                beta = web_data.query_venues(
                    conn, web_data.VenueFilters(q="Beta"), limit=10, offset=0
                )[0]
                alpha_ref = web_data.venue_source_ref(conn, alpha["id"])
                beta_ref = web_data.venue_source_ref(conn, beta["id"])
            finally:
                conn.close()
            upsert_rule(
                paths.place_rules_db,
                PlaceRule(
                    source=alpha_ref["source"],
                    source_place_id=alpha_ref["source_place_id"],
                    action="merge",
                    target_source=beta_ref["source"],
                    target_source_place_id=beta_ref["source_place_id"],
                ),
            )
            build_index(connectors, paths, log=lambda _: None)
            conn = web_data.connect(paths.index_db)
            try:
                merged = web_data.get_venue_by_key(conn, alpha["key"])
                merged_count = web_data.count_venues(
                    conn, web_data.VenueFilters()
                )
            finally:
                conn.close()
            self.assertEqual(merged_count, 1)
            self.assertEqual(merged["sources"], ["alpha", "beta"])

            upsert_rule(
                paths.place_rules_db,
                PlaceRule(
                    source=alpha_ref["source"],
                    source_place_id=alpha_ref["source_place_id"],
                    action="standalone",
                ),
            )
            build_index(connectors, paths, log=lambda _: None)
            conn = web_data.connect(paths.index_db)
            try:
                split_count = web_data.count_venues(
                    conn, web_data.VenueFilters()
                )
            finally:
                conn.close()
            self.assertEqual(split_count, 2)

    def test_thumb_relpath_is_stable(self):
        first = thumb_relpath("foursquare", "abc")
        self.assertEqual(first, thumb_relpath("foursquare", "abc"))
        self.assertNotEqual(first, thumb_relpath("foursquare", "abd"))
        self.assertTrue(first.startswith("data/foursquare/thumbs/"))
        self.assertTrue(first.endswith(".jpg"))


if __name__ == "__main__":
    unittest.main()
