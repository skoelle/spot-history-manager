from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spot_history.core.places import (
    PlaceRule,
    SourcePlace,
    delete_rule,
    haversine_m,
    load_rules,
    normalize_name,
    resolve_canonical_places,
    upsert_rule,
)


def place(
    source,
    source_place_id,
    name,
    lat=None,
    lng=None,
    city=None,
    url=None,
    osm_type=None,
    osm_id=None,
):
    return SourcePlace(
        source=source,
        source_place_id=source_place_id,
        name=name,
        lat=lat,
        lng=lng,
        city=city,
        place_url=url,
        osm_type=osm_type,
        osm_id=osm_id,
    )


class PlacesMatchTest(unittest.TestCase):
    def test_normalize_name(self):
        self.assertEqual(normalize_name("  Test   Café "), "test cafe")
        self.assertEqual(normalize_name("Straße"), "strasse")
        self.assertEqual(normalize_name("München"), "muenchen")
        self.assertEqual(normalize_name(None), "")

    def test_haversine(self):
        self.assertAlmostEqual(haversine_m(48.1, 11.5, 48.1, 11.5), 0.0)
        self.assertGreater(haversine_m(48.1, 11.5, 48.101, 11.5), 90)
        self.assertLess(haversine_m(48.1, 11.5, 48.101, 11.5), 120)

    def test_name_and_coords_merge(self):
        places = [
            place("foursquare", "v1", "Test Café", 48.1, 11.5, url="https://fsq/1"),
            place("yelp", "y1", "Test Cafe", 48.1001, 11.5001, url="https://yelp/1"),
        ]
        canonical, candidates = resolve_canonical_places(places)
        self.assertEqual(len(canonical), 1)
        self.assertEqual(len(canonical[0].members), 2)
        self.assertEqual(candidates, [])
        self.assertEqual(canonical[0].primary_source, "foursquare")
        self.assertEqual(
            canonical[0].methods[("yelp", "y1")], "name_coords"
        )

    def test_far_apart_places_are_not_merged(self):
        places = [
            place("foursquare", "v1", "Test Café", 48.1, 11.5),
            place("yelp", "y1", "Test Café", 48.2, 11.6),
        ]
        canonical, candidates = resolve_canonical_places(places)
        self.assertEqual(len(canonical), 2)
        self.assertEqual(candidates, [])

    def test_name_and_city_merge_without_yelp_coords(self):
        places = [
            place("foursquare", "v1", "Test Café", 48.1, 11.5, city="München"),
            place("yelp", "y1", "Test Cafe", city="München"),
        ]
        canonical, candidates = resolve_canonical_places(places)
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0].methods[("yelp", "y1")], "name_city")
        self.assertEqual(candidates, [])

    def test_far_coords_same_city_becomes_candidate(self):
        places = [
            place("foursquare", "v1", "Test Café", 48.1, 11.5, city="München"),
            place("yelp", "y1", "Test Café", 48.2, 11.6, city="München"),
        ]
        canonical, candidates = resolve_canonical_places(places)
        self.assertEqual(len(canonical), 2)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].reason, "name_city_far_coords")

    def test_ambiguous_city_match_becomes_candidate(self):
        places = [
            place("foursquare", "v1", "Test Café", 48.1, 11.5, city="München"),
            place("foursquare", "v2", "Test Café", 48.2, 11.6, city="München"),
            place("yelp", "y1", "Test Café", city="München"),
        ]
        canonical, candidates = resolve_canonical_places(places)
        self.assertEqual(len(canonical), 3)
        self.assertEqual(len(candidates), 2)
        self.assertTrue(
            all(candidate.reason == "name_city_ambiguous" for candidate in candidates)
        )

    def test_manual_merge_and_standalone(self):
        places = [
            place("foursquare", "v1", "Test Café", 48.1, 11.5),
            place("yelp", "y1", "Ganz anderer Name", 48.1, 11.5),
        ]
        rules = [
            PlaceRule(
                source="yelp",
                source_place_id="y1",
                action="merge",
                target_source="foursquare",
                target_source_place_id="v1",
            )
        ]
        canonical, _ = resolve_canonical_places(places, rules)
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0].methods[("yelp", "y1")], "manual")

        rules = [
            PlaceRule(
                source="yelp",
                source_place_id="y1",
                action="standalone",
            )
        ]
        places[1].name = "Test Café"
        canonical, _ = resolve_canonical_places(places, rules)
        self.assertEqual(len(canonical), 2)

    def test_local_key_is_derived_from_primary(self):
        places = [
            place("yelp", "y1", "Test Café", 48.1, 11.5),
            place("foursquare", "v1", "Test Café", 48.1001, 11.5001),
        ]
        canonical, _ = resolve_canonical_places(places)
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0].canonical_key, "local-foursquare-v1")
        self.assertEqual(canonical[0].local_fallback_key, "local-foursquare-v1")
        self.assertEqual(canonical[0].match_status, "local")

    def test_same_osm_id_merges_across_sources(self):
        places = [
            place("foursquare", "v1", "Webster Hall", 40.73, -73.98, osm_type="way", osm_id=247857140),
            place("yelp", "y1", "Webster Hall NYC", 40.73, -73.98, osm_type="way", osm_id=247857140),
        ]
        canonical, _ = resolve_canonical_places(places)
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0].canonical_key, "osm-way-247857140")
        self.assertEqual(canonical[0].match_status, "osm")
        self.assertEqual(canonical[0].osm_type, "way")
        self.assertEqual(canonical[0].osm_id, 247857140)

    def test_different_osm_ids_are_not_merged(self):
        places = [
            place("foursquare", "v1", "Test Café", 48.1, 11.5, osm_type="node", osm_id=1),
            place("yelp", "y1", "Test Café", 48.1, 11.5, osm_type="node", osm_id=2),
        ]
        canonical, candidates = resolve_canonical_places(places)
        self.assertEqual(len(canonical), 2)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].reason, "osm_conflict")
        self.assertEqual(
            {place.canonical_key for place in canonical},
            {"osm-node-1", "osm-node-2"},
        )

    def test_osm_assignments_attach_to_source_places(self):
        from spot_history.core.identity import OsmAssignment

        places = [
            place("foursquare", "v1", "Test Café", 48.1, 11.5),
            place("yelp", "y1", "Test Café", 48.1001, 11.5001),
        ]
        assignments = {
            ("foursquare", "v1"): OsmAssignment(osm_type="node", osm_id=99)
        }
        canonical, _ = resolve_canonical_places(places, osm_assignments=assignments)
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0].canonical_key, "osm-node-99")
        self.assertEqual(canonical[0].match_status, "osm")

    def test_rules_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "places" / "place_rules.db"
            self.assertEqual(load_rules(path), [])
            upsert_rule(
                path,
                PlaceRule(
                    source="yelp",
                    source_place_id="y1",
                    action="merge",
                    target_source="foursquare",
                    target_source_place_id="v1",
                    note="test",
                ),
            )
            rules = load_rules(path)
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0].target_key, ("foursquare", "v1"))
            self.assertTrue(delete_rule(path, "yelp", "y1"))
            self.assertEqual(load_rules(path), [])


if __name__ == "__main__":
    unittest.main()
