# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in the project root for details.
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spot_history.connectors.base import BaseConnector, Visit
from spot_history.core import identity, research
from spot_history.core import places as place_core
from spot_history.core.config import build_paths
from spot_history.core.index import build_index


class YelpConnector(BaseConnector):
    name = "yelp"
    kind = "import"

    def iter_visits(self):
        yield Visit(
            source="yelp",
            source_visit_id="y1",
            visited_at=1700000000,
            lat=None,
            lng=None,
            place_name="Testort",
            source_place_id="y1",
        )


class FoursquareConnector(BaseConnector):
    name = "foursquare"
    kind = "import"

    def iter_visits(self):
        yield Visit(
            source="foursquare",
            source_visit_id="f1",
            visited_at=1700000600,
            lat=48.1,
            lng=11.5,
            place_name="Testort",
            source_place_id="f1",
            city="München",
        )


class ResearchTest(unittest.TestCase):
    def _build(self, tmp, connectors):
        paths = build_paths(tmp)
        build_index(connectors, paths, log=lambda _: None)
        return paths

    def test_worklist_suggests_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._build(tmp, {"yelp": YelpConnector()})
            identity.upsert_osm_candidate(
                paths.place_identity_db,
                "yelp",
                "y1",
                "node",
                123,
                name="Testort",
                osm_name="Testort",
                score=0.7,
                reason="name_city",
            )
            rows = research.build_worklist(paths)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["ref"], "yelp:y1")
            self.assertEqual(row["suggested_action"], "assign")
            self.assertEqual(row["suggested_osm"], "node/123")
            self.assertEqual(row["candidate_osm"], "node/123")

    def test_worklist_uses_same_day_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            place_core.upsert_rule(
                paths.place_rules_db,
                place_core.PlaceRule(
                    source="yelp",
                    source_place_id="y1",
                    action="standalone",
                ),
            )
            build_index(
                {
                    "yelp": YelpConnector(),
                    "foursquare": FoursquareConnector(),
                },
                paths,
                log=lambda _: None,
            )
            rows = research.build_worklist(paths)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["same_day_ref"], "foursquare:f1")
            self.assertEqual(row["same_day_similarity"], 1.0)
            self.assertEqual(row["suggested_action"], "merge")
            self.assertEqual(row["suggested_target"], "foursquare:f1")

    def test_apply_research_text_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._build(tmp, {"yelp": YelpConnector()})
            text = (
                "ref,action,target,osm,state,lat,lng,confidence,note\n"
                "yelp:y1,assign,,node/123,,, ,high,verified\n"
                "yelp:y1,coords,,,,,,,\n"
                "yelp:y1,state,,,unknown,,,,\n"
            )
            preview = research.apply_research_text(paths, text, dry_run=True)
            self.assertEqual(preview.applied, 1)
            self.assertEqual(len(preview.actions), 1)
            self.assertEqual(len(preview.errors), 2)
            self.assertEqual(
                identity.load_osm_assignments(paths.place_identity_db), {}
            )

    def test_apply_research_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._build(tmp, {"yelp": YelpConnector()})
            text = (
                "ref,action,target,osm,state,lat,lng,address,city,"
                "postal_code,confidence,note\n"
                "yelp:y1,address,,,,,,Welfenstraße 24,München,81541,high,office\n"
            )
            preview = research.apply_research_text(paths, text, dry_run=True)
            self.assertEqual(preview.applied, 1)
            self.assertEqual(preview.actions[0]["address"], "Welfenstraße 24")
            self.assertEqual(identity.load_manual_addresses(paths.place_identity_db), {})
            summary = research.apply_research_text(paths, text)
            self.assertEqual(summary.applied, 1)
            stored = identity.load_manual_addresses(paths.place_identity_db)
            self.assertEqual(
                stored[("yelp", "y1")]["address"], "Welfenstraße 24"
            )
            self.assertEqual(stored[("yelp", "y1")]["postal_code"], "81541")

    def test_write_and_apply_research(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._build(tmp, {"yelp": YelpConnector()})
            csv_path = Path(tmp) / "decisions.csv"
            csv_path.write_text(
                "ref,action,target,osm,state,lat,lng,confidence,note\n"
                "yelp:y1,assign,,node/123,,, ,high,verified\n"
                "yelp:y1,coords,,,,48.1,11.5,,\n"
                "yelp:y1,state,,,no_osm,,,,\n"
                "yelp:y1,merge,foursquare:f1,,,,,medium,test\n",
                encoding="utf-8",
            )
            dry = research.apply_research(paths, csv_path, dry_run=True)
            self.assertEqual(dry.applied, 4)
            self.assertEqual(identity.load_osm_assignments(paths.place_identity_db), {})
            summary = research.apply_research(paths, csv_path)
            self.assertEqual(summary.applied, 4)
            assignments = identity.load_osm_assignments(paths.place_identity_db)
            self.assertEqual(assignments[("yelp", "y1")].osm_id, 123)
            coords = identity.load_manual_coords(paths.place_identity_db)
            self.assertAlmostEqual(coords[("yelp", "y1")]["lat"], 48.1)
            states = identity.load_osm_states(paths.place_identity_db)
            self.assertEqual(states[("yelp", "y1")]["state"], "no_osm")
            rules = place_core.load_rules(paths.place_rules_db)
            self.assertTrue(
                any(
                    rule.action == "merge"
                    and rule.target_source == "foursquare"
                    and rule.target_source_place_id == "f1"
                    for rule in rules
                )
            )


if __name__ == "__main__":
    unittest.main()
