from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spot_history.connectors.google import osm_enrich
from spot_history.connectors.google.cluster import aggregate_places, consolidate
from spot_history.connectors.google.config import build_config
from spot_history.connectors.google.db import Database
from spot_history.connectors.google.parser import ParsedVisit
from spot_history.connectors.google.scoring import (
    decide,
    is_valid_poi,
    nominatim_candidate,
    rank_candidates,
)
from spot_history.connectors.osm.db import Poi


def _poi(name: str, tags: dict, lat: float = 48.1, lng: float = 11.5) -> Poi:
    return Poi(
        osm_type="node",
        osm_id=1,
        name=name,
        lat=lat,
        lng=lng,
        tags=tags,
    )


class FakeOverpass:
    def __init__(self, *args, **kwargs):
        pass

    def fetch_around(self, points, radius):
        return [
            Poi(
                osm_type="node",
                osm_id=1,
                name="Test Café",
                lat=48.1,
                lng=11.5,
                tags={"amenity": "cafe"},
            )
        ]


class FakeNominatim:
    def __init__(self, *args, **kwargs):
        pass

    def reverse_raw(self, lat, lng, zoom=18):
        return {
            "name": "Nominatim Café",
            "category": "amenity",
            "type": "cafe",
            "address": {
                "road": "Teststraße",
                "house_number": "1",
                "city": "München",
                "country_code": "de",
            },
            "display_name": "Nominatim Café, Teststraße 1, München",
        }


class GoogleScoringTest(unittest.TestCase):
    def test_valid_poi_filters(self):
        self.assertFalse(is_valid_poi(_poi("Haus 06", {"building": "school"})))
        self.assertTrue(is_valid_poi(_poi("Schule", {"amenity": "school"})))
        self.assertFalse(is_valid_poi(_poi("Straße", {"highway": "residential"})))
        self.assertFalse(is_valid_poi(_poi("Parkplatz", {"amenity": "parking"})))

    def test_decide_auto_candidate_and_ambiguous(self):
        candidates = rank_candidates(
            [(_poi("Café", {"amenity": "cafe"}), 5.0), (_poi("Schule", {"amenity": "school"}), 8.0)]
        )
        self.assertEqual(decide(candidates)[0], "candidate")

        candidates = rank_candidates(
            [(_poi("Café", {"amenity": "cafe"}), 5.0), (_poi("Schule", {"amenity": "school"}), 20.0)]
        )
        self.assertEqual(decide(candidates)[0], "auto")

        candidates = rank_candidates([(_poi("Café", {"amenity": "cafe"}), 30.0)])
        self.assertEqual(decide(candidates)[0], "candidate")

    def test_nominatim_candidate(self):
        info = nominatim_candidate(
            {
                "name": "Spielplatz",
                "category": "leisure",
                "type": "playground",
                "address": {"road": "Teststraße", "house_number": "2", "city": "München"},
            }
        )
        self.assertEqual(info["name"], "Spielplatz")
        self.assertEqual(info["address"], "Teststraße 2")
        self.assertEqual(info["city"], "München")


class GoogleOsmEnrichTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = build_config(data_dir=self.root / "data")
        self._seed()

    def tearDown(self):
        self.tmp.cleanup()

    def _seed(self, semantic: str = "Unknown"):
        visit = ParsedVisit(
            source_visit_id="v1",
            place_id="ChIJosmtest",
            start_at=1712131200,
            end_at=1712134800,
            start_iso="2024-04-03T10:00:00+00:00",
            end_iso="2024-04-03T11:00:00+00:00",
            tz_offset=120,
            lat=48.1,
            lng=11.5,
            semantic_type=semantic,
            probability=0.5,
        )
        places = aggregate_places([visit])
        _, clusters = consolidate(places)
        db = Database(self.config.db_path).open()
        try:
            db.replace_data([visit], places, clusters, [], [])
        finally:
            db.close()

    def _cluster(self):
        db = Database(self.config.db_path).open()
        try:
            return db.connection.execute("SELECT * FROM clusters").fetchone()
        finally:
            db.close()

    def test_overpass_auto_name(self):
        with mock.patch.object(osm_enrich, "OverpassClient", FakeOverpass):
            summary = osm_enrich.enrich(
                self.config, provider="overpass", sleep=0, log=lambda _: None
            )
        self.assertEqual(summary.auto_named, 1)
        row = self._cluster()
        self.assertEqual(row["name"], "Test Café")
        self.assertEqual(row["name_source"], "osm")

    def test_nominatim_fallback(self):
        with mock.patch.object(osm_enrich, "NominatimClient", FakeNominatim):
            summary = osm_enrich.enrich(
                self.config, provider="nominatim", sleep=0, log=lambda _: None
            )
        self.assertEqual(summary.auto_named, 1)
        row = self._cluster()
        self.assertEqual(row["name"], "Nominatim Café")
        self.assertEqual(row["name_source"], "nominatim")

    def test_home_and_work_are_skipped(self):
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = build_config(data_dir=self.root / "data")
        self._seed(semantic="Home")
        summary = osm_enrich.enrich(
            self.config, provider="nominatim", sleep=0, log=lambda _: None
        )
        self.assertEqual(summary.total, 0)
        row = self._cluster()
        self.assertEqual(row["name"], "Zuhause")
        self.assertEqual(row["name_source"], "semantic")


if __name__ == "__main__":
    unittest.main()
