# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from spot_history.connectors.base import Visit
from spot_history.core.timezones import (
    apply_timezone_offsets,
    offset_for_timestamp,
    resolve_timezone,
)


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


class TimezoneTest(unittest.TestCase):
    def test_resolve_timezone(self):
        self.assertEqual(resolve_timezone("München", None, None), "Europe/Berlin")
        self.assertEqual(resolve_timezone(None, "NY", "US"), "America/New_York")
        self.assertEqual(
            resolve_timezone("San Francisco", "CA", "US"), "America/Los_Angeles"
        )
        self.assertEqual(resolve_timezone("Narvik", None, "NO"), "Europe/Oslo")
        self.assertEqual(resolve_timezone("Zürich", None, "CH"), "Europe/Zurich")
        self.assertIsNone(resolve_timezone("Unbekannt", None, "ZZ"))

    def test_offset_for_timestamp_handles_dst(self):
        self.assertEqual(
            offset_for_timestamp(timestamp("2013-09-01T14:35:00"), "Europe/Berlin"),
            120,
        )
        self.assertEqual(
            offset_for_timestamp(timestamp("2013-12-01T12:00:00"), "Europe/Berlin"),
            60,
        )
        self.assertEqual(
            offset_for_timestamp(timestamp("2013-12-06T00:19:53"), "America/New_York"),
            -300,
        )
        self.assertIsNone(offset_for_timestamp(0, "Nope/Nowhere"))

    def test_same_place_uses_foursquare_offset(self):
        fsq = Visit(
            source="foursquare",
            source_visit_id="f1",
            visited_at=timestamp("2013-09-01T14:34:35"),
            source_place_id="v1",
            tz_offset=120,
            time_zone="Europe/Berlin",
        )
        yelp = Visit(
            source="yelp",
            source_visit_id="y1",
            visited_at=timestamp("2013-09-01T14:35:00"),
            source_place_id="y1",
            city="München",
        )
        canonical = {("foursquare", "v1"): 1, ("yelp", "y1"): 1}
        counts = apply_timezone_offsets(
            [fsq, yelp], canonical, {1: SimpleNamespace(city="München")}
        )
        self.assertEqual(yelp.tz_offset, 120)
        self.assertEqual(counts["same_place"], 1)

    def test_venue_timezone_is_used_beyond_window(self):
        fsq = Visit(
            source="foursquare",
            source_visit_id="f1",
            visited_at=timestamp("2013-01-01T12:00:00"),
            source_place_id="v1",
            tz_offset=60,
            time_zone="Europe/Berlin",
        )
        yelp = Visit(
            source="yelp",
            source_visit_id="y1",
            visited_at=timestamp("2013-09-01T14:35:00"),
            source_place_id="y1",
            city="München",
        )
        canonical = {("foursquare", "v1"): 1, ("yelp", "y1"): 1}
        counts = apply_timezone_offsets(
            [fsq, yelp], canonical, {1: SimpleNamespace(city="München")}
        )
        self.assertEqual(yelp.tz_offset, 120)
        self.assertEqual(counts["venue_timezone"], 1)

    def test_city_fallback_and_unresolved(self):
        munich = Visit(
            source="yelp",
            source_visit_id="y1",
            visited_at=timestamp("2013-09-01T14:35:00"),
            source_place_id="y1",
            city="München",
        )
        unknown = Visit(
            source="yelp",
            source_visit_id="y2",
            visited_at=timestamp("2013-09-01T14:35:00"),
            source_place_id="y2",
        )
        counts = apply_timezone_offsets([munich, unknown], {})
        self.assertEqual(munich.tz_offset, 120)
        self.assertIsNone(unknown.tz_offset)
        self.assertEqual(counts["city_timezone"], 1)
        self.assertEqual(counts["unresolved"], 1)

    def test_nearby_majority_fallback(self):
        yelp = Visit(
            source="yelp",
            source_visit_id="y1",
            visited_at=timestamp("2014-10-25T08:53:08"),
            source_place_id="y1",
        )
        visits = [
            Visit(
                source="foursquare",
                source_visit_id=f"f{index}",
                visited_at=timestamp("2014-10-20T12:00:00") + index * 86400,
                source_place_id=f"v{index}",
                tz_offset=120,
                time_zone=time_zone,
            )
            for index, time_zone in enumerate(
                ["Europe/Berlin", "Europe/Berlin", "America/New_York"]
            )
        ]
        counts = apply_timezone_offsets([yelp, *visits], {("yelp", "y1"): 99})
        self.assertEqual(yelp.tz_offset, 120)
        self.assertEqual(counts["nearby_majority"], 1)

    def test_nearest_foursquare_fallback(self):
        fsq = Visit(
            source="foursquare",
            source_visit_id="f1",
            visited_at=timestamp("2013-10-27T21:17:09"),
            source_place_id="v1",
            tz_offset=-240,
            time_zone="America/New_York",
        )
        yelp = Visit(
            source="yelp",
            source_visit_id="y1",
            visited_at=timestamp("2013-12-06T00:19:53"),
            source_place_id="y1",
        )
        counts = apply_timezone_offsets(
            [fsq, yelp], {("foursquare", "v1"): 1, ("yelp", "y1"): 2}
        )
        self.assertEqual(yelp.tz_offset, -300)
        self.assertEqual(counts["nearest_fsq"], 1)


if __name__ == "__main__":
    unittest.main()
