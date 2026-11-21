# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spot_history.core.identity import (
    delete_osm_assignment,
    load_aliases,
    load_osm_assignments,
    local_key,
    parse_osm_ref,
    record_aliases,
    upsert_osm_assignment,
    validate_osm,
)


class IdentityTest(unittest.TestCase):
    def test_validate_osm(self):
        self.assertEqual(validate_osm("Node", "123"), ("node", 123))
        self.assertEqual(validate_osm("relation", 456), ("relation", 456))
        with self.assertRaises(ValueError):
            validate_osm("building", 1)
        with self.assertRaises(ValueError):
            validate_osm("node", "abc")
        with self.assertRaises(ValueError):
            validate_osm("node", 0)

    def test_parse_osm_ref(self):
        self.assertEqual(parse_osm_ref("node/123"), ("node", 123))
        self.assertEqual(parse_osm_ref("way-456"), ("way", 456))
        self.assertEqual(parse_osm_ref("osm-relation-789"), ("relation", 789))
        self.assertEqual(
            parse_osm_ref("https://www.openstreetmap.org/way/247857140"),
            ("way", 247857140),
        )
        with self.assertRaises(ValueError):
            parse_osm_ref("123")

    def test_local_key_is_url_safe(self):
        self.assertEqual(local_key("foursquare", "abc123"), "local-foursquare-abc123")
        key = local_key("yelp", "a b/c")
        self.assertNotIn(" ", key)
        self.assertNotIn("/", key)
        self.assertTrue(key.startswith("local-yelp-"))

    def test_assignments_and_aliases_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "places" / "identity.db"
            self.assertEqual(load_osm_assignments(path), {})
            self.assertEqual(load_aliases(path), {})

            assignment = upsert_osm_assignment(
                path, "foursquare", "v1", "way", 247857140, note="test"
            )
            self.assertEqual(assignment.key, "osm-way-247857140")
            stored = load_osm_assignments(path)
            self.assertEqual(stored[("foursquare", "v1")].osm_id, 247857140)

            record_aliases(path, {"local-foursquare-v1": "osm-way-247857140"})
            self.assertEqual(
                load_aliases(path),
                {"local-foursquare-v1": "osm-way-247857140"},
            )

            self.assertTrue(delete_osm_assignment(path, "foursquare", "v1"))
            self.assertEqual(load_osm_assignments(path), {})
            self.assertFalse(delete_osm_assignment(path, "foursquare", "v1"))


if __name__ == "__main__":
    unittest.main()
