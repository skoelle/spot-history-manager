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
    load_index_places,
    name_similarity,
    rank_matches,
)
from spot_history.connectors.osm.overpass import (
    OverpassError,
    parse_nominatim_results,
    parse_overpass_elements,
)
from spot_history.connectors.osm.sync import (
    address_scan,
    contact_scan,
    deep_scan,
    mark_missing_coords,
    net_scan,
    rematch,
    sync,
)
from spot_history.core import identity
from spot_history.core.config import build_paths
from spot_history.core.index import build_index
from spot_history.core.osm_coords import load_osm_cities, load_osm_coords


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
            address="Teststraße 1",
            city="München",
            country_code="DE",
        )


class AddressConnector(BaseConnector):
    name = "addr"
    kind = "import"

    def iter_visits(self):
        yield Visit(
            source="addr",
            source_visit_id="a1",
            visited_at=1700000000,
            lat=48.2,
            lng=11.6,
            place_name="Veilchenweg",
            source_place_id="a1",
            city="Vaterstetten",
            country_code="DE",
        )


class NoCoordConnector(BaseConnector):
    name = "nocoord"
    kind = "import"

    def iter_visits(self):
        yield Visit(
            source="nocoord",
            source_visit_id="n1",
            visited_at=1700000000,
            lat=None,
            lng=None,
            place_name="Testort",
            source_place_id="n1",
            category="Café",
        )


class ContactConnector(BaseConnector):
    name = "contact"
    kind = "import"

    def iter_visits(self):
        yield Visit(
            source="contact",
            source_visit_id="c1",
            visited_at=1700000000,
            lat=48.1,
            lng=11.5,
            place_name="Testort",
            source_place_id="c1",
            category="Café",
            city="München",
            country_code="DE",
        )

    def iter_place_details(self):
        yield {
            "source": "contact",
            "source_place_id": "c1",
            "website": "https://www.testort.example/",
            "tel": "+49 89 123456",
            "email": "info@testort.example",
            "address": "Teststraße 1",
            "city": "München",
        }


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

    def test_alternate_name_is_candidate_not_auto(self):
        renamed = osm_db.Poi(
            osm_type="node",
            osm_id=321,
            name="Nash",
            lat=48.1001,
            lng=11.5,
            tags={
                "amenity": "cafe",
                "addr:city": "München",
                "old_name": "Testort",
            },
        )
        matches = rank_matches(place(), [(renamed, 9.0)])
        decision, match = decide(matches)
        self.assertEqual(decision, "candidate")
        self.assertIsNotNone(match)
        self.assertTrue(match.alternate)
        self.assertEqual(match.name_tag, "old_name")
        self.assertEqual(match.matched_name, "Testort")
        self.assertTrue(match.reason.startswith("alt_name"))

    def test_fuzzy_name_is_candidate_not_auto(self):
        typo = osm_db.Poi(
            osm_type="node",
            osm_id=322,
            name="Cafe Testrot",
            lat=48.1001,
            lng=11.5,
            tags={"amenity": "cafe", "addr:city": "München"},
        )
        matches = rank_matches(place(name="Cafe Testort"), [(typo, 9.0)])
        decision, match = decide(matches)
        self.assertEqual(decision, "candidate")
        self.assertIsNotNone(match)
        self.assertTrue(match.fuzzy)
        self.assertTrue(match.reason.startswith("fuzzy"))

    def test_punctuation_and_word_order_match_as_candidate(self):
        gop = osm_db.Poi(
            osm_type="node",
            osm_id=291364396,
            name="GOP Theater und Varieté",
            lat=48.1379771,
            lng=11.5879705,
            tags={"amenity": "theatre", "addr:city": "München"},
        )
        match_place = place(
            name="GOP Varieté-Theater", lat=48.138204, lng=11.588023
        )
        from spot_history.connectors.osm.db import haversine_m

        distance = haversine_m(
            match_place.lat, match_place.lng, gop.lat, gop.lng
        )
        matches = rank_matches(match_place, [(gop, distance)])
        decision, match = decide(matches)
        self.assertEqual(decision, "candidate")
        self.assertIsNotNone(match)
        self.assertGreaterEqual(match.score, 0.55)

    def test_parenthetical_variant_is_candidate_not_auto(self):
        matches = rank_matches(
            place(name="Testort (Zweitname)"), [(poi(), 5.0)]
        )
        decision, match = decide(matches)
        self.assertEqual(decision, "candidate")
        self.assertTrue(match.variant)

    def test_address_names_are_ignored(self):
        for name in ("Veilchenweg", "Perlacher Straße 55", "A 14", "12"):
            matches = rank_matches(place(name=name), [(poi(name=name), 5.0)])
            self.assertEqual(decide(matches), ("none", None), name)


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

    def test_poi_names_by_osm_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "osm" / "osm.db"
            conn = osm_db.open_db(path)
            try:
                osm_db.save_pois(
                    conn,
                    [
                        poi(name="Testort", osm_id=123),
                        poi(name="Ohne Koordinaten", lat=None, lng=None, osm_id=456),
                    ],
                )
            finally:
                conn.close()
            names = osm_db.poi_names(path, {("way", 123), ("way", 456), ("way", 999)})
            self.assertEqual(
                names,
                {("way", 123): "Testort", ("way", 456): "Ohne Koordinaten"},
            )

    def test_load_osm_cities(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "osm" / "osm.db"
            conn = osm_db.open_db(path)
            try:
                osm_db.save_pois(
                    conn,
                    [
                        poi(osm_id=123),
                        osm_db.Poi(
                            osm_type="node",
                            osm_id=456,
                            name="Nur Bezirk",
                            lat=48.2,
                            lng=11.6,
                            tags={"addr:suburb": "Haidhausen"},
                        ),
                    ],
                )
            finally:
                conn.close()
            cities = load_osm_cities(path)
            self.assertEqual(cities, {("way", 123): "München"})

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

    def test_sync_skips_address_places(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"addr": AddressConnector()}, paths, log=lambda _: None)
            fake_overpass = mock.Mock()
            fake_overpass.fetch_around.return_value = []
            with (
                mock.patch(
                    "spot_history.connectors.osm.sync.OverpassClient",
                    return_value=fake_overpass,
                ),
                mock.patch("spot_history.connectors.osm.sync.NominatimClient"),
            ):
                summary = sync(paths, sleep=0.0, log=lambda _: None)
            self.assertEqual(summary.pending, 0)
            self.assertEqual(summary.addresses, 1)
            fake_overpass.fetch_around.assert_not_called()

    def test_net_scan_stores_candidate_without_assignment(self):
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
                    "address": {"city": "München"},
                }
            ]
            fake_nominatim = mock.Mock()
            fake_nominatim.search_raw.return_value = payload
            with mock.patch(
                "spot_history.connectors.osm.sync.NominatimClient",
                return_value=fake_nominatim,
            ):
                summary = net_scan(paths, sleep=0.0, log=lambda _: None)
            self.assertEqual(summary.queries, 1)
            self.assertEqual(summary.candidates, 1)
            self.assertEqual(identity.load_osm_assignments(paths.place_identity_db), {})
            candidates = identity.load_osm_candidates(paths.place_identity_db)
            self.assertEqual(candidates[0]["osm_id"], 456)
            self.assertEqual(candidates[0]["osm_name"], "Testort")

    def test_net_scan_far_result_is_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            payload = [
                {
                    "osm_type": "node",
                    "osm_id": 457,
                    "lat": "48.11",
                    "lon": "11.5",
                    "display_name": "Testort, München",
                    "extratags": {},
                    "address": {"city": "München"},
                }
            ]
            fake_nominatim = mock.Mock()
            fake_nominatim.search_raw.return_value = payload
            with mock.patch(
                "spot_history.connectors.osm.sync.NominatimClient",
                return_value=fake_nominatim,
            ):
                summary = net_scan(paths, sleep=0.0, log=lambda _: None)
            self.assertEqual(summary.candidates, 1)
            candidates = identity.load_osm_candidates(paths.place_identity_db)
            self.assertGreater(candidates[0]["distance_m"], 1000)
            self.assertTrue(candidates[0]["reason"].endswith("_far"))
            self.assertEqual(identity.load_osm_assignments(paths.place_identity_db), {})

    def test_net_scan_skips_places_with_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            identity.upsert_osm_candidate(
                paths.place_identity_db, "fake", "p1", "node", 9
            )
            fake_nominatim = mock.Mock()
            with mock.patch(
                "spot_history.connectors.osm.sync.NominatimClient",
                return_value=fake_nominatim,
            ):
                summary = net_scan(paths, sleep=0.0, log=lambda _: None)
            self.assertEqual(summary.pending, 0)
            fake_nominatim.search_raw.assert_not_called()

    def test_address_scan_matches_compatible_successor(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            payload = [
                {
                    "osm_type": "node",
                    "osm_id": 900,
                    "lat": "48.1001",
                    "lon": "11.5001",
                    "display_name": "Neuer Name, Teststraße 1, München",
                    "category": "amenity",
                    "type": "cafe",
                    "extratags": {"amenity": "cafe"},
                    "address": {"city": "München"},
                }
            ]
            fake_nominatim = mock.Mock()
            fake_nominatim.search_raw.return_value = payload
            with mock.patch(
                "spot_history.connectors.osm.sync.NominatimClient",
                return_value=fake_nominatim,
            ):
                summary = address_scan(paths, sleep=0.0, log=lambda _: None)
            self.assertEqual(summary.queries, 1)
            self.assertEqual(summary.candidates, 1)
            candidates = identity.load_osm_candidates(paths.place_identity_db)
            self.assertEqual(candidates[0]["osm_id"], 900)
            self.assertEqual(candidates[0]["reason"], "address_match")

    def test_address_scan_rejects_incompatible_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            payload = [
                {
                    "osm_type": "node",
                    "osm_id": 901,
                    "lat": "48.1001",
                    "lon": "11.5001",
                    "display_name": "Kleiderladen, Teststraße 1, München",
                    "category": "shop",
                    "type": "clothes",
                    "extratags": {"shop": "clothes"},
                    "address": {"city": "München"},
                }
            ]
            fake_nominatim = mock.Mock()
            fake_nominatim.search_raw.return_value = payload
            with mock.patch(
                "spot_history.connectors.osm.sync.NominatimClient",
                return_value=fake_nominatim,
            ):
                summary = address_scan(paths, sleep=0.0, log=lambda _: None)
            self.assertEqual(summary.candidates, 0)
            self.assertEqual(identity.load_osm_candidates(paths.place_identity_db), [])

    def test_contact_scan_matches_website(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"contact": ContactConnector()}, paths, log=lambda _: None)
            conn = osm_db.open_db(paths.connector_dir("osm") / "osm.db")
            osm_db.save_pois(
                conn,
                [
                    osm_db.Poi(
                        osm_type="node",
                        osm_id=902,
                        name="Testort Neu",
                        lat=48.1001,
                        lng=11.5,
                        tags={
                            "amenity": "cafe",
                            "website": "https://testort.example/kontakt",
                        },
                    )
                ],
            )
            conn.close()
            summary = contact_scan(paths, log=lambda _: None)
            self.assertEqual(summary.candidates, 1)
            candidates = identity.load_osm_candidates(paths.place_identity_db)
            self.assertEqual(candidates[0]["osm_id"], 902)
            self.assertEqual(candidates[0]["reason"], "website_match")

    def test_mark_missing_coords_sets_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"nocoord": NoCoordConnector()}, paths, log=lambda _: None)
            self.assertEqual(mark_missing_coords(paths, log=lambda _: None), 1)
            states = identity.load_osm_states(paths.place_identity_db)
            self.assertEqual(states[("nocoord", "n1")]["state"], "no_coords")
            build_index({"nocoord": NoCoordConnector()}, paths, log=lambda _: None)
            places = load_index_places(paths.index_db)
            self.assertEqual(places[0].place_state, "no_coords")

    def test_deep_scan_runs_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            osm_db.open_db(paths.connector_dir("osm") / "osm.db").close()
            payload = [
                {
                    "osm_type": "node",
                    "osm_id": 903,
                    "lat": "48.1001",
                    "lon": "11.5001",
                    "display_name": "Testort, Teststraße 1, München",
                    "category": "amenity",
                    "type": "cafe",
                    "extratags": {"amenity": "cafe"},
                    "address": {"city": "München"},
                }
            ]
            fake_nominatim = mock.Mock()
            fake_nominatim.search_raw.return_value = payload
            with mock.patch(
                "spot_history.connectors.osm.sync.NominatimClient",
                return_value=fake_nominatim,
            ):
                summary = deep_scan(
                    paths,
                    sleep=0.0,
                    contact_scan_enabled=False,
                    log=lambda _: None,
                )
            self.assertIsNotNone(summary.rematch)
            self.assertIsNotNone(summary.name)
            self.assertIsNotNone(summary.address)
            self.assertIsNone(summary.contacts)

    def test_rematch_uses_cached_pois_without_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            conn = osm_db.open_db(paths.connector_dir("osm") / "osm.db")
            osm_db.save_pois(conn, [poi()])
            conn.close()
            summary = rematch(paths, log=lambda _: None)
            self.assertEqual(summary.pending, 1)
            self.assertEqual(summary.auto_matched, 1)
            self.assertEqual(summary.candidates, 0)
            assignments = identity.load_osm_assignments(paths.place_identity_db)
            self.assertEqual(assignments[("fake", "p1")].osm_id, 123)

    def test_rematch_alt_name_stays_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"fake": FakeConnector()}, paths, log=lambda _: None)
            conn = osm_db.open_db(paths.connector_dir("osm") / "osm.db")
            osm_db.save_pois(
                conn,
                [
                    osm_db.Poi(
                        osm_type="node",
                        osm_id=321,
                        name="Nash",
                        lat=48.1001,
                        lng=11.5,
                        tags={
                            "amenity": "cafe",
                            "addr:city": "München",
                            "old_name": "Testort",
                        },
                    )
                ],
            )
            conn.close()
            summary = rematch(paths, log=lambda _: None)
            self.assertEqual(summary.auto_matched, 0)
            self.assertEqual(summary.candidates, 1)
            self.assertEqual(identity.load_osm_assignments(paths.place_identity_db), {})
            candidates = identity.load_osm_candidates(paths.place_identity_db)
            self.assertEqual(candidates[0]["osm_name"], "Nash")
            self.assertTrue(candidates[0]["reason"].startswith("alt_name"))

    def test_rematch_skips_address_places(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"addr": AddressConnector()}, paths, log=lambda _: None)
            identity.upsert_osm_candidate(
                paths.place_identity_db,
                "addr",
                "a1",
                "node",
                9,
                name="Veilchenweg",
                osm_name="Veilchenweg",
            )
            conn = osm_db.open_db(paths.connector_dir("osm") / "osm.db")
            osm_db.save_pois(
                conn,
                [
                    osm_db.Poi(
                        osm_type="way",
                        osm_id=1,
                        name="Veilchenweg",
                        lat=48.2,
                        lng=11.6,
                        tags={"highway": "residential"},
                    )
                ],
            )
            conn.close()
            summary = rematch(paths, log=lambda _: None)
            self.assertEqual(summary.addresses, 1)
            self.assertEqual(summary.auto_matched, 0)
            self.assertEqual(identity.load_osm_assignments(paths.place_identity_db), {})
            self.assertEqual(identity.load_osm_candidates(paths.place_identity_db), [])


if __name__ == "__main__":
    unittest.main()
