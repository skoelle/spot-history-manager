from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from spot_history.connectors.base import BaseConnector, Visit
from spot_history.core.config import build_paths
from spot_history.core.index import build_index
from spot_history.web import data as web_data


class DerivedConnector(BaseConnector):
    name = "fake"
    kind = "import"

    def iter_visits(self):
        yield Visit(
            source="fake",
            source_visit_id="derived",
            visited_at=1600000000,
            lat=None,
            lng=None,
            place_name="Abgeleiteter Ort",
            source_place_id="p1",
        )
        yield Visit(
            source="fake",
            source_visit_id="source",
            visited_at=1700000000,
            lat=48.1,
            lng=11.5,
            place_name="Abgeleiteter Ort",
            source_place_id="p1",
            city="München",
            state="Bayern",
            country_code="DE",
        )
        yield Visit(
            source="fake",
            source_visit_id="unknown",
            visited_at=1500000000,
            lat=None,
            lng=None,
            place_name="Ohne Koordinaten",
            source_place_id="p2",
        )
        yield Visit(
            source="fake",
            source_visit_id="berlin",
            visited_at=1400000000,
            lat=52.5,
            lng=13.4,
            place_name="Berlin Ort",
            source_place_id="p3",
            city="Berlin",
            state="Berlin",
            country_code="DE",
        )


class DerivedIndexTest(unittest.TestCase):
    def test_derived_flags_and_consistency(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            summary = build_index({"fake": DerivedConnector()}, paths, log=lambda _: None)
            self.assertEqual(summary["visits"], 4)
            self.assertEqual(summary["features"], 3)

            conn = sqlite3.connect(paths.index_db)
            conn.row_factory = sqlite3.Row
            try:
                rows = {
                    row["source_visit_id"]: dict(row)
                    for row in conn.execute(
                        "SELECT source_visit_id, has_coords, coords_derived FROM visits"
                    )
                }
            finally:
                conn.close()

            self.assertEqual((rows["source"]["has_coords"], rows["source"]["coords_derived"]), (1, 0))
            self.assertEqual((rows["derived"]["has_coords"], rows["derived"]["coords_derived"]), (0, 1))
            self.assertEqual((rows["unknown"]["has_coords"], rows["unknown"]["coords_derived"]), (0, 0))
            self.assertEqual((rows["berlin"]["has_coords"], rows["berlin"]["coords_derived"]), (1, 0))

            payload = json.loads(Path(paths.map_geojson).read_text(encoding="utf-8"))
            features = payload["features"]
            self.assertEqual(len(features), 3)
            derived = [f for f in features if f["properties"]["coords_derived"]]
            self.assertEqual(len(derived), 1)
            self.assertEqual(derived[0]["properties"]["visit_id"], "derived")
            self.assertFalse(derived[0]["properties"]["has_coords"])
            self.assertEqual(derived[0]["properties"]["city"], "München")
            self.assertEqual(derived[0]["geometry"]["coordinates"], [11.5, 48.1])

            web_conn = web_data.connect(paths.index_db)
            try:
                points = web_data.query_map_points(web_conn, web_data.Filters())
                visits = web_data.query_visits(web_conn, web_data.Filters(), limit=10, offset=0)
            finally:
                web_conn.close()
            self.assertEqual(len(points), len(features))
            self.assertEqual(sum(1 for point in points if point["coords_derived"]), 1)
            self.assertEqual(len(visits), 4)
            derived_visit = next(v for v in visits if v["source_visit_id"] == "derived")
            self.assertTrue(derived_visit["coords_derived"])
            self.assertEqual(derived_visit["city"], "München")


if __name__ == "__main__":
    unittest.main()
