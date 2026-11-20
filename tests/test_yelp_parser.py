from __future__ import annotations

import unittest
from pathlib import Path

from spot_history.connectors.yelp.html_tables import read_tables, select_table
from spot_history.connectors.yelp.importer import CHECKIN_HEADERS, COMMENT_HEADERS

FIXTURES = Path(__file__).parent / "fixtures" / "yelp" / "html"


class YelpParserTest(unittest.TestCase):
    def test_two_checkin_tables_are_distinguished(self):
        tables = read_tables(FIXTURES / "check_in.html")
        self.assertEqual(len(tables), 2)

        checkins = select_table(tables, CHECKIN_HEADERS)
        self.assertEqual(
            checkins.headers,
            [
                "Datum", "Name des Unternehmens", "Kommentar",
                "Längengrad", "Breitengrad", "Status",
            ],
        )
        self.assertEqual(len(checkins.rows), 4)
        rows = checkins.dicts()
        self.assertEqual(rows[0]["Name des Unternehmens"].text, "Test Café")
        self.assertEqual(rows[1]["Kommentar"].text, "Zweiter Besuch")
        self.assertEqual(rows[3]["Name des Unternehmens"].text, "Alte Bäckerei")
        self.assertEqual(rows[3]["Längengrad"].text, "")

        comments = select_table(tables, COMMENT_HEADERS)
        self.assertEqual(comments.headers, list(COMMENT_HEADERS))
        self.assertEqual(len(comments.rows), 2)
        self.assertEqual(comments.dicts()[0]["Kommentar"].text, "Lecker!")

    def test_media_links_are_preserved(self):
        tables = read_tables(FIXTURES / "biz_photo.html")
        rows = select_table(
            tables,
            ("Datum", "Name des Unternehmens", "Medien", "Bildunterschrift", "Status"),
        ).dicts()
        media = rows[0]["Medien"]
        self.assertEqual(media.text, "")
        self.assertIn("../media/biz_photo/bar.jpg", media.links)
        self.assertIn("../media/biz_photo/bar.jpg", media.srcs)

    def test_entities_and_whitespace_are_normalized(self):
        tables = read_tables(FIXTURES / "user_review.html")
        rows = select_table(
            tables,
            ("Datum", "Name des Unternehmens", "Bewertung", "Kommentar", "IP Adresse", "Status"),
        ).dicts()
        self.assertEqual(rows[0]["Name des Unternehmens"].text, "Test Café")
        self.assertEqual(rows[0]["Kommentar"].text, "Sehr gut.")

    def test_missing_table_raises(self):
        tables = read_tables(FIXTURES / "check_in.html")
        with self.assertRaises(ValueError):
            select_table(tables, ("Nicht", "Vorhanden"))


if __name__ == "__main__":
    unittest.main()
