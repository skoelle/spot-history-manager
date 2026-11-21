# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from spot_history.connectors.base import BaseConnector, Photo, Visit
from spot_history.connectors.osm import db as osm_db
from spot_history.core.config import build_paths
from spot_history.core.identity import upsert_osm_assignment
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

    def iter_records(self):
        yield {
            "source": "fake",
            "record_id": "r1",
            "kind": "survey_answer",
            "source_place_id": "p1",
            "created_at": 1700000000,
            "payload": {"question": "Q", "answer": "A"},
        }
        yield {
            "source": "fake",
            "record_id": "r2",
            "kind": "account",
            "source_place_id": None,
            "created_at": None,
            "payload": {"Vorname": "Test"},
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
            place_url="https://example.invalid/fake/p1",
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


class OtherConnector(BaseConnector):
    name = "other"
    kind = "sync"

    def iter_visits(self):
        yield Visit(
            source="other",
            source_visit_id="o1",
            visited_at=1700000200,
            lat=48.10005,
            lng=11.50005,
            place_name="Testort",
            source_place_id="op1",
            category="Café",
            city="München",
            country_code="DE",
            place_url="https://example.invalid/other/op1",
        )


class GoogleExtraConnector(BaseConnector):
    name = "google"
    kind = "import"

    def iter_visits(self):
        yield Visit(
            source="google",
            source_visit_id="gv1",
            visited_at=1700000300,
            lat=48.116781,
            lng=11.604361,
            place_name="Inklusive Monte Balan",
            source_place_id="g-c3284762f109",
            extra={"osm_type": "node", "osm_id": 1255559552},
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
                records = conn.execute(
                    "SELECT source, record_id, kind, source_place_id, payload_json "
                    "FROM source_records ORDER BY record_id"
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
            self.assertEqual(summary["records"], 2)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0][2], "survey_answer")
            self.assertEqual(records[0][3], "p1")
            self.assertEqual(json.loads(records[0][4])["answer"], "A")
            self.assertEqual(records[1][2], "account")
            self.assertIsNone(records[1][3])
            self.assertEqual(json.loads(records[1][4]), {"Vorname": "Test"})

            payload = json.loads(Path(paths.map_geojson).read_text(encoding="utf-8"))
            self.assertEqual(payload["type"], "FeatureCollection")
            self.assertEqual(len(payload["features"]), 1)
            feature = payload["features"][0]
            self.assertEqual(feature["geometry"]["coordinates"], [11.5, 48.1])
            self.assertEqual(feature["properties"]["source"], "fake")
            self.assertEqual(feature["properties"]["place"], "Testort")

    def test_build_merges_sources_into_canonical_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            summary = build_index(
                {"fake": FakeConnector(), "other": OtherConnector()},
                paths,
                log=lambda _: None,
            )
            self.assertEqual(summary["places"], 2)
            self.assertEqual(summary["canonical_places"], 1)
            self.assertEqual(summary["place_links"], 2)
            self.assertEqual(summary["canonical_places"], 1)

            conn = sqlite3.connect(paths.index_db)
            conn.row_factory = sqlite3.Row
            try:
                canonical = conn.execute(
                    "SELECT * FROM canonical_places"
                ).fetchall()
                links = conn.execute(
                    "SELECT * FROM place_links ORDER BY source"
                ).fetchall()
                visit_rows = conn.execute(
                    "SELECT source, canonical_place_id, place_id FROM visits "
                    "WHERE source_visit_id IN ('v1', 'o1') ORDER BY source"
                ).fetchall()
            finally:
                conn.close()

            self.assertEqual(len(canonical), 1)
            self.assertEqual(
                {row["source"] for row in links}, {"fake", "other"}
            )
            self.assertEqual(
                {row["url"] for row in links},
                {
                    "https://example.invalid/fake/p1",
                    "https://example.invalid/other/op1",
                },
            )
            self.assertEqual(visit_rows[0]["canonical_place_id"], canonical[0]["id"])
            self.assertEqual(visit_rows[1]["canonical_place_id"], canonical[0]["id"])

            payload = json.loads(Path(paths.map_geojson).read_text(encoding="utf-8"))
            self.assertEqual(len(payload["features"]), 2)
            for feature in payload["features"]:
                self.assertEqual(feature["properties"]["sources"], ["fake", "other"])
                self.assertEqual(feature["properties"]["place_id"], canonical[0]["id"])

    def test_index_uses_local_and_osm_keys_with_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index(
                {"fake": FakeConnector(), "other": OtherConnector()},
                paths,
                log=lambda _: None,
            )
            conn = sqlite3.connect(paths.index_db)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute("SELECT * FROM canonical_places").fetchone()
                alias_rows = conn.execute(
                    "SELECT alias_key, canonical_key FROM canonical_aliases"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(row["canonical_key"], "local-fake-p1")
            self.assertEqual(row["match_status"], "local")
            self.assertIsNone(row["osm_type"])
            aliases = {item["alias_key"]: item["canonical_key"] for item in alias_rows}
            self.assertEqual(aliases["fake:p1"], "local-fake-p1")

            upsert_osm_assignment(
                paths.place_identity_db, "other", "op1", "way", 247857140
            )
            osm_conn = osm_db.open_db(paths.osm_db)
            try:
                osm_db.save_pois(
                    osm_conn,
                    [osm_db.Poi("way", 247857140, "Testort", 48.2, 11.6)],
                )
            finally:
                osm_conn.close()
            summary = build_index(
                {"fake": FakeConnector(), "other": OtherConnector()},
                paths,
                log=lambda _: None,
            )
            self.assertEqual(summary["matched_osm"], 1)
            conn = sqlite3.connect(paths.index_db)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute("SELECT * FROM canonical_places").fetchone()
                aliases = {
                    item["alias_key"]: item["canonical_key"]
                    for item in conn.execute(
                        "SELECT alias_key, canonical_key FROM canonical_aliases"
                    )
                }
            finally:
                conn.close()
            self.assertEqual(row["canonical_key"], "osm-way-247857140")
            self.assertEqual(row["match_status"], "osm")
            self.assertEqual(row["osm_type"], "way")
            self.assertEqual(row["osm_id"], 247857140)
            self.assertEqual(row["osm_lat"], 48.2)
            self.assertEqual(row["osm_lng"], 11.6)
            self.assertEqual(aliases["local-fake-p1"], "osm-way-247857140")

            payload = json.loads(Path(paths.map_geojson).read_text(encoding="utf-8"))
            feature = payload["features"][0]
            self.assertEqual(
                feature["properties"]["place_key"], "osm-way-247857140"
            )
            self.assertEqual(feature["geometry"]["coordinates"], [11.6, 48.2])
            self.assertEqual(feature["properties"]["coords_source"], "osm")
            self.assertEqual(feature["properties"]["lat"], 48.1)
            self.assertEqual(feature["properties"]["osm_lat"], 48.2)

    def test_connector_osm_extra_sets_canonical_and_aliases_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            summary = build_index(
                {"google": GoogleExtraConnector()},
                paths,
                log=lambda _: None,
            )
            self.assertEqual(summary["matched_osm"], 1)
            conn = sqlite3.connect(paths.index_db)
            conn.row_factory = sqlite3.Row
            try:
                venue = conn.execute(
                    "SELECT * FROM canonical_places"
                ).fetchone()
                aliases = {
                    item["alias_key"]: item["canonical_key"]
                    for item in conn.execute(
                        "SELECT alias_key, canonical_key FROM canonical_aliases"
                    )
                }
            finally:
                conn.close()
            self.assertEqual(venue["canonical_key"], "osm-node-1255559552")
            self.assertEqual(venue["match_status"], "osm")
            self.assertEqual(
                aliases["local-google-g-c3284762f109"],
                "osm-node-1255559552",
            )
            self.assertEqual(
                aliases["google:g-c3284762f109"],
                "osm-node-1255559552",
            )

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
