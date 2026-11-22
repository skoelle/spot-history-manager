# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in the project root for details.
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from spot_history.core.index import index_schema_ok


class StartupIndexTest(unittest.TestCase):
    def test_missing_index_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(index_schema_ok(Path(tmp) / "index.db"))

    def test_old_schema_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.db"
            conn = sqlite3.connect(path)
            try:
                conn.execute("CREATE TABLE canonical_places (id INTEGER)")
                conn.commit()
            finally:
                conn.close()
            self.assertFalse(index_schema_ok(path))

    def test_current_schema_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.db"
            conn = sqlite3.connect(path)
            try:
                conn.execute(
                    "CREATE TABLE canonical_places ("
                    "id INTEGER, place_state TEXT, manual_lat REAL, manual_lng REAL)"
                )
                conn.commit()
            finally:
                conn.close()
            self.assertTrue(index_schema_ok(path))


if __name__ == "__main__":
    unittest.main()
