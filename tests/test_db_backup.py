# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from spot_history.connectors.foursquare.config import build_config as fs_config
from spot_history.connectors.google.config import build_config as google_config
from spot_history.connectors.yelp.config import build_config as yelp_config
from spot_history.core.backup import backup_databases, prune_backups
from spot_history.core.config import ROOT, build_paths


class DbRootTest(unittest.TestCase):
    def test_default_roots_are_separate(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPOT_HISTORY_DATA_ROOT", None)
            os.environ.pop("SPOT_HISTORY_DB_ROOT", None)
            paths = build_paths()
        self.assertEqual(paths.data_root, ROOT / "data")
        self.assertEqual(paths.db_root, ROOT / "db")
        self.assertEqual(paths.index_db, ROOT / "db" / "index" / "index.db")

    def test_explicit_data_root_keeps_databases_below_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SPOT_HISTORY_DB_ROOT", None)
                paths = build_paths(tmp)
            self.assertEqual(paths.data_root, Path(tmp))
            self.assertEqual(paths.db_root, Path(tmp))

    def test_db_root_env_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(
                os.environ, {"SPOT_HISTORY_DB_ROOT": str(root)}, clear=False
            ):
                paths = build_paths(root / "data")
                config = fs_config(require_token=False)
            self.assertEqual(paths.db_root, root)
            self.assertEqual(
                config.db_path, root / "foursquare" / "foursquare.db"
            )
            self.assertEqual(config.data_dir, ROOT / "data" / "foursquare")

    def test_google_uses_db_root_for_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(
                os.environ, {"SPOT_HISTORY_DB_ROOT": str(root)}, clear=False
            ):
                config = google_config()
            self.assertEqual(config.db_path, root / "google" / "google.db")
            self.assertEqual(
                config.osm_cache_path, root / "google" / "osm_cache.db"
            )
            self.assertEqual(config.osm_db_path, root / "osm" / "osm.db")

    def test_explicit_data_dir_keeps_old_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SPOT_HISTORY_DB_ROOT", None)
                fs = fs_config(data_dir=root, require_token=False)
                yelp = yelp_config(data_dir=root)
            self.assertEqual(fs.db_path, root / "foursquare.db")
            self.assertEqual(yelp.db_path, root / "yelp.db")


class BackupTest(unittest.TestCase):
    def test_backup_copies_databases_and_skips_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_root = root / "db"
            dest = root / "data" / "backup"
            source = db_root / "foursquare" / "foursquare.db"
            source.parent.mkdir(parents=True)
            conn = sqlite3.connect(source)
            conn.execute("CREATE TABLE checkins (id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO checkins VALUES ('c1')")
            conn.commit()
            conn.close()
            (source.parent / "foursquare.db-wal").write_bytes(b"")
            (source.parent / "foursquare.db-shm").write_bytes(b"")

            summary = backup_databases(db_root, dest, keep_days=3, log=lambda _: None)

            today = datetime.now(tz=timezone.utc).astimezone().date()
            target = dest / today.isoformat() / "foursquare" / "foursquare.db"
            self.assertEqual(summary.errors, [])
            self.assertTrue(target.is_file())
            self.assertFalse(target.with_name("foursquare.db-wal").exists())
            conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
            try:
                rows = conn.execute("SELECT id FROM checkins").fetchall()
            finally:
                conn.close()
            self.assertEqual(rows, [("c1",)])

    def test_prune_keeps_configured_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            today = date(2026, 11, 22)
            for offset in range(1, 6):
                day = today - timedelta(days=offset)
                directory = root / day.isoformat()
                directory.mkdir()
                (directory / "db.db").write_bytes(b"x")
            removed = prune_backups(root, keep_days=3, today=today)
            self.assertEqual(
                sorted(removed),
                ["2026-11-17", "2026-11-18", "2026-11-19"],
            )
            remaining = sorted(path.name for path in root.iterdir())
            self.assertEqual(
                remaining,
                ["2026-11-20", "2026-11-21"],
            )

    def test_prune_requires_positive_keep_days(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            prune_backups(tmp, keep_days=0)


if __name__ == "__main__":
    unittest.main()
