# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import sqlite3
import tempfile
import unittest

from spot_history.connectors.base import BaseConnector, Visit
from spot_history.core.categories import (
    SOURCE_CATEGORY,
    SOURCE_NAME,
    UNKNOWN_GROUP,
    group_for_category,
    group_for_name,
    resolve_category_group,
)
from spot_history.core.config import build_paths
from spot_history.core.index import build_index


class NameOnlyConnector(BaseConnector):
    name = "nameonly"
    kind = "import"

    def iter_visits(self):
        yield Visit(
            source="nameonly",
            source_visit_id="n1",
            visited_at=1700000000,
            lat=48.1,
            lng=11.5,
            place_name="Biergarten am See",
            source_place_id="np1",
        )


class CategoryGroupTest(unittest.TestCase):
    def test_fine_categories_map_to_groups(self):
        expected = {
            "Beer Garden": "Biergarten",
            "Café": "Cafés",
            "Coffee Shop": "Cafés",
            "Bakery": "Cafés",
            "German Restaurant": "Restaurants",
            "Bavarian Restaurant": "Restaurants",
            "Pizzeria": "Restaurants",
            "Bar": "Bars & Nightlife",
            "Night Club": "Bars & Nightlife",
            "Hotel": "Hotels & Unterkünfte",
            "Museum": "Kultur & Sehenswürdigkeiten",
            "Playground": "Natur & Outdoor",
            "Mountain": "Natur & Outdoor",
            "Office": "Arbeit & Bildung",
            "Tourist Information and Service": "Reisen & Verkehr",
        }
        for category, group in expected.items():
            with self.subTest(category=category):
                self.assertEqual(group_for_category(category), group)

    def test_beer_garden_is_not_a_restaurant(self):
        self.assertEqual(group_for_category("Beer Garden"), "Biergarten")
        self.assertEqual(group_for_category("Restaurant"), "Restaurants")

    def test_name_heuristic(self):
        expected = {
            "Biergarten am Chinesischen Turm": "Biergarten",
            "Café Wölfl": "Cafés",
            "Schloßcafe Lintelo": "Cafés",
            "Waldwirtschaft Großhesselohe": "Restaurants",
            "Hofbräukeller": "Restaurants",
            "Hotel Lux": "Hotels & Unterkünfte",
            "Jüdisches Museum": "Kultur & Sehenswürdigkeiten",
            "Flughafen München": "Reisen & Verkehr",
            "Olympiapark": "Natur & Outdoor",
            "Minigolf & Pit-Pat Olympiapark": "Sport & Freizeit",
            "Hacker-Festzelt": "Events",
            "Markt am Wiener Platz": "Einkaufen",
        }
        for name, group in expected.items():
            with self.subTest(name=name):
                self.assertEqual(group_for_name(name), group)

    def test_resolve_prefers_source_category(self):
        group, source = resolve_category_group("Beer Garden", "Restaurant Test")
        self.assertEqual((group, source), ("Biergarten", SOURCE_CATEGORY))

    def test_resolve_uses_name_for_missing_category(self):
        group, source = resolve_category_group(None, "Biergarten Muffathalle")
        self.assertEqual((group, source), ("Biergarten", SOURCE_NAME))

    def test_resolve_unknown(self):
        group, source = resolve_category_group(None, "Ikaros")
        self.assertEqual((group, source), (UNKNOWN_GROUP, None))

    def test_index_uses_name_heuristic_when_category_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(tmp)
            build_index({"nameonly": NameOnlyConnector()}, paths, log=lambda _: None)
            conn = sqlite3.connect(paths.index_db)
            try:
                row = conn.execute(
                    "SELECT category, category_group, category_group_source "
                    "FROM canonical_places"
                ).fetchone()
            finally:
                conn.close()
        self.assertEqual(row, (None, "Biergarten", "name"))


if __name__ == "__main__":
    unittest.main()
