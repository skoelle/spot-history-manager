# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spot_history.connectors.google.parser import (
    iter_files,
    load_file,
    parse_iso,
)

FIXTURES = Path(__file__).parent / "fixtures" / "google"


class GoogleParserTest(unittest.TestCase):
    def test_ios_array_visits(self):
        parsed = load_file(FIXTURES / "ios_location_history.json")
        self.assertEqual(parsed.format, "timeline")
        self.assertEqual(len(parsed.visits), 6)
        first = parsed.visits[0]
        self.assertEqual(first.place_id, "ChIJioscafe")
        self.assertAlmostEqual(first.lat, 48.1)
        self.assertAlmostEqual(first.lng, 11.5)
        self.assertEqual(first.tz_offset, 120)
        fallback = next(
            v for v in parsed.visits if v.place_id == "ChIJiofallback"
        )
        self.assertAlmostEqual(fallback.lat, 48.3)
        self.assertAlmostEqual(fallback.lng, 11.7)
        geo = next(v for v in parsed.visits if v.place_id.startswith("geo:"))
        self.assertAlmostEqual(geo.lat, 48.4)
        self.assertAlmostEqual(geo.lng, 11.8)

    def test_android_semantic_segments(self):
        parsed = load_file(FIXTURES / "android_timeline.json")
        self.assertEqual(parsed.format, "timeline")
        self.assertEqual(len(parsed.visits), 1)
        visit = parsed.visits[0]
        self.assertEqual(visit.place_id, "ChIJandroid")
        self.assertEqual(visit.place_name, "Android Café")
        self.assertEqual(visit.tz_offset, 120)
        self.assertAlmostEqual(visit.lat, 48.12)
        self.assertAlmostEqual(visit.lng, 11.52)

    def test_legacy_place_visit_and_wrapped_longitude(self):
        parsed = load_file(FIXTURES / "takeout_timeline.json")
        self.assertEqual(parsed.format, "legacy")
        self.assertEqual(len(parsed.visits), 2)
        legacy = parsed.visits[0]
        self.assertEqual(legacy.place_name, "Legacy Café")
        self.assertEqual(legacy.address, "Legacy Street 1")
        self.assertAlmostEqual(legacy.lat, 48.13)
        self.assertAlmostEqual(legacy.lng, 11.53)
        wrapped = parsed.visits[1]
        self.assertAlmostEqual(wrapped.lat, 48.0)
        self.assertAlmostEqual(wrapped.lng, -11.5)

    def test_records_file_is_detected_and_skipped(self):
        parsed = load_file(FIXTURES / "records.json")
        self.assertEqual(parsed.format, "records")
        self.assertEqual(parsed.visits, [])
        self.assertEqual(parsed.records, [])

    def test_geojson_review_record(self):
        parsed = load_file(FIXTURES / "reviews.json")
        self.assertEqual(parsed.format, "geojson")
        self.assertEqual(len(parsed.records), 1)
        record = parsed.records[0]
        self.assertEqual(record.kind, "review")
        self.assertEqual(record.place_name, "Test Café")
        self.assertAlmostEqual(record.lat, 48.1)
        self.assertAlmostEqual(record.lng, 11.5)
        self.assertEqual(record.payload["rating"], 5.0)

    def test_iter_files_skips_synology_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "@eaDir").mkdir()
            (root / "@eaDir" / "Timeline.json").write_text("{}", encoding="utf-8")
            (root / ".hidden.json").write_text("{}", encoding="utf-8")
            (root / "Timeline.json").write_text("[]", encoding="utf-8")
            self.assertEqual([path.name for path in iter_files(root)], ["Timeline.json"])

    def test_parse_iso_offset(self):
        parsed = parse_iso("2024-04-03T08:13:57.000+02:00")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[1], 120)
        self.assertTrue(parsed[2].endswith("+00:00"))


if __name__ == "__main__":
    unittest.main()
