# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spot_history.core.identity import (
    delete_osm_assignment,
    delete_osm_state,
    load_aliases,
    load_osm_assignments,
    load_osm_states,
    local_key,
    parse_osm_ref,
    record_aliases,
    set_osm_state,
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

    def test_osm_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "places" / "identity.db"
            self.assertEqual(load_osm_states(path), {})
            set_osm_state(
                path, "yelp", "y1", "no_coords", reason="auto:no_coords"
            )
            states = load_osm_states(path)
            self.assertEqual(states[("yelp", "y1")]["state"], "no_coords")
            self.assertEqual(
                states[("yelp", "y1")]["reason"], "auto:no_coords"
            )
            with self.assertRaises(ValueError):
                set_osm_state(path, "yelp", "y1", "unknown")
            self.assertTrue(delete_osm_state(path, "yelp", "y1"))
            self.assertEqual(load_osm_states(path), {})
            self.assertFalse(delete_osm_state(path, "yelp", "y1"))


if __name__ == "__main__":
    unittest.main()
