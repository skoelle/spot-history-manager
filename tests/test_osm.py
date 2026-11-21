# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spot_history.connectors.base import BaseConnector, Visit
from spot_history.connectors.osm import db as osm_db
from spot_history.connectors.osm.matcher import (
    IndexPlace,
    decide,
    direct_candidates,
    name_similarity,
    rank_matches,
)
from spot_history.connectors.osm.overpass import (
    OverpassError,
    parse_nominatim_results,
    parse_overpass_elements,
)
from spot_history.connectors.osm.sync import sync
from spot_history.core import identity
from spot_history.core.config import build_paths
from spot_history.core.index import build_index
from spot_history.core.osm_coords import load_osm_coords


class FakeConnector(BaseConnector):
    name = "fake"
    kind = "import"

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
        )


def place(name="Testort", lat=48.1, lng=11.5, city="München"):
    return IndexPlace(
        canonical_id=1,
        key="local-fake-p1",
        name=name,
        lat=lat,
        lng=lng,
        city=city,
        members=[("fake", "p1")],
    )


def poi(name="Testort", lat=48.1001, lng=11.5, osm_id=123):
    return osm_db.Poi(
        osm_type="way",
        osm_id=osm_id,
        name=name,
        lat=lat,
        lng=lng,
        tags={"amenity": "cafe", "addr:city": "München"},
    )


class MatcherTest(unittest.TestCase):
    def test_name_similarity(self):
        self.assertEqual(name_similarity("Testort", "testort"), 1.0)
        self.assertGreater(name_similarity("Testort", "Testort Bar"), 0.4)
        self.assertEqual(name_similarity("Testort", "Ganz anders"), 0.0)
        self.assertEqual(name_similarity(None, "Testort"), 0.0)

    def test_exact_name_and_close_distance_is_auto(self):
        matches = rank_matches(place(), [(poi(), 11.0)])
        decision, match = decide(matches)
        self.assertEqual(decision, "auto")
        self.assertIsNotNone(match)
        self.assertGreaterEqual(match.score, 0.8)

    def test_far_place_becomes_candidate(self):
        matches = rank_matches(place(), [(poi(lat=48.1009), 100.0)])
        decision, match = decide(matches)
        self.assertEqual(decision, "candidate")
        self.assertIsNotNone(match)

    def test_no_name_match_is_ignored(self):
        matches = rank_matches(place(), [(poi(name="Ganz anders"), 11.0)])
        self.assertEqual(decide(matches), ("none", None))

    def test_direct_nominatim_candidates_use_city(self):
        matches = rank_matches(place(), direct_candidates([poi()]))
        decision, match = decide(matches)
        self.assertEqual(decision, "auto")
        self.assertEqual(match.poi.osm_id, 123)


class ParserTest(unittest.TestCase):
    def test_parse_overpass_elements(self):
        payload = {
            "elements": [
                {
                    "type": "node",
                    "id": 1,
                    "lat": 48.1,
                    "lon": 11.5,
                    "tags": {"name": "Testort", "amenity": "cafe"},
                },
                {
                    "type": "way",
                    "id": 2,
                    "center": {"lat": 48.2, "lon": 11.6},
                    "tags": {"name": "Anderer Ort"},
                },
                {"type": "node", "id": 3, "lat": 48.0, "lon": 11.0, "tags": {}},
            ]
        }
        pois = parse_overpass_elements(payload)
        self.assertEqual({(p.osm_type, p.osm_id) for p in pois}, {("node", 1), ("way", 2)})

    def test_load_osm_coords_and_missing_cache(self):
        self.assertEqual(load_osm_coords(Path("/nonexistent/osm.db")), {})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "osm" / "osm.db"
            conn = osm_db.open_db(path)
            try:
                osm_db.save_pois(conn, [poi(osm_id=123)])
            finally:
                conn.close()
            coords = load_osm_coords(path)
            self.assertEqual(set(coords), {("way", 123)})
            self.assertAlmostEqual(coords[("way", 123)][0], 48.1001)
            self.assertAlmostEqual(coords[("way", 123)][1], 11.5)

    def test_poi_locations_by_osm_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "osm" / "osm.db"
            conn = osm_db.open_db(path)
            try:
                osm_db.save_pois(conn, [poi(osm_id=123)])
            finally:
                conn.close()
            locations = osm_db.poi_locations(path, {("way", 123), ("way", 999)})
            self.assertEqual(set(locations), {("way", 123)})
            self.assertAlmostEqual(locations[("way", 123)]["lat"], 48.1001)

    def test_parse_nominatim_results(self):
        payload = [
            {
                "osm_type": "way",
                "osm_id": 247857140,
                "lat": "40.73",
                "lon": "-73.98",
                "display_name": "Webster Hall, New York",
                "extratags": {"opening_hours": "Mo-Su 19:00-23:00"},
                "address": {"city": "New York", "postcode": "10003"},
            }
        ]
        pois = parse_nominatim_results(payload)
        self.assertEqual(len(pois), 1)
        self.assertEqual(pois[0].osm_type, "way")
        self.assertEqual(pois[0].city, "New York")
        self.assertEqual(pois[0].postcode, "10003")
        self.assertEqual(pois[0].tags["opening_hours"], "Mo-Su 19:00-23:00")


class SyncTest(unittest.TestCase):
    def _prepare(self, tmp):
        paths = build_paths(tmp)
        build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
        conn = osm_db.open_db(paths.connector_dir("osm") / "osm.db")
        osm_db.save_pois(conn, [poi()])
        osm_db.mark_query(conn, "around:48.1000:11.5000:r120", "around", 120, 1)
        conn.close()
        return paths

    def test_cached_poi_is_auto_assigned_and_rebuild_uses_osm_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._prepare(tmp)
            summary = sync(paths, sleep=0.0, log=lambda _: None)
            self.assertEqual(summary.pending, 1)
            self.assertEqual(summary.auto_matched, 1)
            self.assertEqual(summary.errors, 0)
            assignments = identity.load_osm_assignments(paths.place_identity_db)
            assignment = assignments[("fake", "p1")]
            self.assertEqual(assignment.osm_type, "way")
            self.assertEqual(assignment.osm_id, 123)
            self.assertEqual(assignment.method, "auto")

            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            assert paths.index_db.exists()

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._prepare(tmp)
            summary = sync(paths, sleep=0.0, dry_run=True, log=lambda _: None)
            self.assertEqual(summary.auto_matched, 1)
            self.assertEqual(identity.load_osm_assignments(paths.place_identity_db), {})

    def test_overpass_failure_falls_back_to_nominatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            payload = [
                {
                    "osm_type": "node",
                    "osm_id": 456,
                    "lat": "48.1001",
                    "lon": "11.5001",
                    "display_name": "Testort, München",
                    "extratags": {},
                    "address": {"city": "München", "postcode": "80331"},
                }
            ]
            fake_overpass = mock.Mock()
            fake_overpass.fetch_around.side_effect = OverpassError("down")
            fake_nominatim = mock.Mock()
            fake_nominatim.search_raw.return_value = payload
            with (
                mock.patch(
                    "spot_history.connectors.osm.sync.OverpassClient",
                    return_value=fake_overpass,
                ),
                mock.patch(
                    "spot_history.connectors.osm.sync.NominatimClient",
                    return_value=fake_nominatim,
                ),
            ):
                summary = sync(paths, sleep=0.0, log=lambda _: None)
            self.assertEqual(summary.auto_matched, 1)
            self.assertEqual(summary.errors, 0)
            assignments = identity.load_osm_assignments(paths.place_identity_db)
            self.assertEqual(assignments[("fake", "p1")].osm_id, 456)
            conn = osm_db.open_db(paths.connector_dir("osm") / "osm.db")
            try:
                self.assertTrue(
                    osm_db.has_query(conn, "around:48.1000:11.5000:r120")
                )
            finally:
                conn.close()

    def test_candidate_is_stored_without_assignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            conn = osm_db.open_db(paths.connector_dir("osm") / "osm.db")
            osm_db.save_pois(conn, [poi(lat=48.1009)])
            osm_db.mark_query(conn, "around:48.1000:11.5000:r120", "around", 120, 1)
            conn.close()
            summary = sync(paths, sleep=0.0, log=lambda _: None)
            self.assertEqual(summary.auto_matched, 0)
            self.assertEqual(summary.candidates, 1)
            self.assertEqual(identity.load_osm_assignments(paths.place_identity_db), {})
            candidates = identity.load_osm_candidates(paths.place_identity_db)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["osm_id"], 123)


if __name__ == "__main__":
    unittest.main()
