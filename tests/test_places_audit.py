# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import unittest

from spot_history.core.audit import (
    AuditPlace,
    find_near_duplicates,
    summarize,
)
from spot_history.core.naming import (
    core_name,
    fold_name,
    name_similarity,
)


def place(
    place_id,
    key,
    name,
    lat,
    lng,
    sources,
    match_status="local",
    osm_type=None,
    osm_id=None,
):
    return AuditPlace(
        id=place_id,
        canonical_key=key,
        name=name,
        lat=lat,
        lng=lng,
        sources=tuple(sources),
        match_status=match_status,
        osm_type=osm_type,
        osm_id=osm_id,
    )


class NamingTest(unittest.TestCase):
    def test_fold_name_handles_quotes_and_umlauts(self):
        self.assertEqual(fold_name("L’Osteria"), fold_name("L'Osteria"))
        self.assertEqual(fold_name("Femörefortet"), fold_name("Femorefortet"))

    def test_core_name_strips_generics_and_maps_numbers(self):
        self.assertEqual(core_name(fold_name("Wirtshaus Aumeister")), "aumeister")
        self.assertEqual(core_name(fold_name("Restaurant Al Shaam")), "al shaam")
        self.assertEqual(
            core_name(fold_name("Steinheil Sechzehn")), "steinheil 16"
        )
        self.assertEqual(
            core_name(fold_name("Gartencenter Seebauer")), "seebauer"
        )

    def test_name_similarity_flags_variants(self):
        self.assertGreaterEqual(
            name_similarity("L’Osteria", "L'Osteria"), 0.9
        )
        self.assertGreaterEqual(
            name_similarity("Femörefortet", "Femorefortet"), 0.9
        )
        self.assertGreaterEqual(
            name_similarity("Steinheil Sechzehn", "Steinheil 16"), 0.9
        )
        self.assertGreaterEqual(
            name_similarity("Wirtshaus Aumeister", "Aumeister"), 0.9
        )
        self.assertGreaterEqual(
            name_similarity("Das Wirtshaus (Michaeligarten)", "Michaeligarten"),
            0.9,
        )
        self.assertGreaterEqual(
            name_similarity("Trinachia - Der Sizilianer", "Der Sizilianer"), 0.9
        )
        self.assertGreaterEqual(
            name_similarity("Waldwirtschaft", "Waldwirtschaft Großhesselohe"),
            0.9,
        )
        self.assertLess(name_similarity("Brot und Sinne", "Venu Mangi"), 0.78)
        self.assertLess(name_similarity("SIXT", "Hamburgerei Zwei"), 0.78)


class AuditTest(unittest.TestCase):
    def test_find_near_duplicates_classifies_and_ignores_merged(self):
        places = [
            place(1, "local-google-a", "L'Osteria", 48.1, 11.5, ["google"]),
            place(
                2,
                "local-foursquare-b",
                "L’Osteria",
                48.10001,
                11.50001,
                ["foursquare"],
            ),
            place(3, "local-google-c", "Aral", 48.2, 11.6, ["google"], "osm", "node", 11),
            place(
                4,
                "osm-node-12",
                "Aral",
                48.20001,
                11.60001,
                ["foursquare"],
                "osm",
                "node",
                12,
            ),
            place(5, "local-google-d", "Brot und Sinne", 48.3, 11.7, ["google"]),
            place(6, "local-foursquare-e", "Venu Mangi", 48.30001, 11.70001, ["foursquare"]),
            place(
                7,
                "osm-node-20",
                "Aral",
                48.2,
                11.6,
                ["foursquare", "google"],
                "osm",
                "node",
                20,
            ),
        ]
        matches = find_near_duplicates(places)
        by_source = {match.place.id: match for match in matches}
        self.assertEqual(set(by_source), {1, 3})
        self.assertEqual(by_source[1].classification, "variant")
        self.assertEqual(by_source[1].reason, "both_local")
        self.assertEqual(by_source[3].classification, "exact")
        self.assertEqual(by_source[3].reason, "osm_conflict")

        summary = summarize(places, matches)
        self.assertEqual(summary.source_only, 3)
        self.assertEqual(summary.duplicates, 2)
        self.assertEqual(summary.by_classification, {"exact": 1, "variant": 1})
        self.assertEqual(summary.by_reason, {"both_local": 1, "osm_conflict": 1})

    def test_radius_and_similarity_thresholds(self):
        places = [
            place(1, "g1", "Test Cafe", 48.1, 11.5, ["google"]),
            place(2, "f1", "Test Cafe", 48.101, 11.5, ["foursquare"]),
        ]
        self.assertEqual(find_near_duplicates(places, radius_m=50), [])
        self.assertEqual(
            len(find_near_duplicates(places, radius_m=200)), 1
        )
        self.assertEqual(
            find_near_duplicates(places, radius_m=200, min_similarity=1.1), []
        )


if __name__ == "__main__":
    unittest.main()
