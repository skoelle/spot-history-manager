# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from spot_history.web import sync_jobs

ACTION_KEYS = {
    "foursquare-sync",
    "foursquare-backfill",
    "foursquare-photos",
    "yelp-import",
    "yelp-geocode",
    "google-import",
    "google-osm",
    "osm-sync",
    "osm-rematch",
    "osm-net-scan",
    "osm-address-scan",
    "osm-contact-scan",
    "osm-deep-scan",
    "yelp-worklist",
    "yelp-apply-research",
    "backup",
    "index-build",
    "index-thumbs",
}


class ActionTests(unittest.TestCase):
    def test_action_keys(self):
        actions = sync_jobs.actions_by_key(Path("/data"))
        self.assertEqual(set(actions), ACTION_KEYS)

    def test_sync_actions_append_index_build(self):
        for action in (
            "foursquare-sync",
            "foursquare-backfill",
            "foursquare-photos",
            "yelp-import",
            "yelp-geocode",
            "google-import",
            "google-osm",
            "osm-sync",
            "osm-rematch",
            "osm-net-scan",
            "osm-address-scan",
            "osm-contact-scan",
            "osm-deep-scan",
            "yelp-apply-research",
        ):
            commands = sync_jobs.build_commands(action, Path("/data"))
            self.assertGreaterEqual(len(commands), 2, action)
            self.assertEqual(commands[-1], ["index", "build", "--data-root", "/data"])

    def test_worklist_is_single_step(self):
        commands = sync_jobs.build_commands("yelp-worklist", Path("/data"))
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][:3], ["places", "research-worklist", "--source"])
        self.assertIn("/data/exports/yelp-research.csv", commands[0])

    def test_deep_scan_runs_backup_and_index(self):
        commands = sync_jobs.build_commands("osm-deep-scan", Path("/data"))
        self.assertEqual(commands[0], ["backup", "--keep-days", "3"])
        self.assertEqual(commands[1][:2], ["osm", "deep-scan"])
        self.assertEqual(commands[-1], ["index", "build", "--data-root", "/data"])

    def test_research_decisions_path(self):
        self.assertEqual(
            sync_jobs.research_decisions_path(Path("/data")),
            Path("/data/exports/yelp-research-decisions.csv"),
        )

    def test_apply_research_runs_backup_and_index(self):
        commands = sync_jobs.build_commands("yelp-apply-research", Path("/data"))
        self.assertEqual(commands[0], ["backup", "--keep-days", "3"])
        self.assertIn(
            "/data/exports/yelp-research-decisions.csv", commands[1]
        )
        self.assertIn("--no-build", commands[1])
        self.assertEqual(commands[-1], ["index", "build", "--data-root", "/data"])

    def test_backup_is_single_step(self):
        self.assertEqual(
            sync_jobs.build_commands("backup", Path("/data")),
            [["backup", "--keep-days", "3"]],
        )

    def test_index_thumbs_is_single_step(self):
        commands = sync_jobs.build_commands("index-thumbs", Path("/data"))
        self.assertEqual(
            commands,
            [["index", "thumbs", "--data-root", "/data"]],
        )

    def test_data_dirs_are_explicit(self):
        foursquare = sync_jobs.build_commands("foursquare-sync", Path("/data"))[0]
        self.assertIn("/data/foursquare", foursquare)
        yelp = sync_jobs.build_commands("yelp-import", Path("/data"))[0]
        self.assertIn("/data/yelp", yelp)
        google = sync_jobs.build_commands("google-import", Path("/data"))[0]
        self.assertIn("/data/google", google)

    def test_action_groups_cover_all_actions(self):
        groups = sync_jobs.action_groups(Path("/data"))
        grouped = {action.key for group in groups for action in group["actions"]}
        self.assertEqual(grouped, ACTION_KEYS)
        self.assertEqual(
            [group["name"] for group in groups],
            [
                "Foursquare",
                "Yelp",
                "Google",
                "OpenStreetMap",
                "Index",
                "Wartung",
            ],
        )
        self.assertEqual(
            [group["key"] for group in groups],
            ["foursquare", "yelp", "google", "osm", "index", "maintenance"],
        )


class JobRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.runner = sync_jobs.JobRunner()

    def tearDown(self):
        self.runner.shutdown()
        self.tmp.cleanup()

    def wait_for_finish(self, timeout=15.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.runner.status()
            if not status["active"] and status["job"] is not None:
                return status
            time.sleep(0.05)
        self.fail("Job wurde nicht rechtzeitig fertig")

    def test_success_and_log_file(self):
        self.runner.start_raw(
            "Test",
            [[sys.executable, "-c", "print('hallo')"]],
            self.data,
        )
        status = self.wait_for_finish()
        job = status["job"]
        self.assertEqual(job["status"], "success")
        self.assertIn("hallo", "\n".join(job["log_tail"]))
        self.assertIsNotNone(job["log_path"])
        self.assertTrue(Path(job["log_path"]).exists())

    def test_busy_while_running(self):
        self.runner.start_raw(
            "Sleep",
            [[sys.executable, "-c", "import time; time.sleep(30)"]],
            self.data,
        )
        with self.assertRaises(sync_jobs.JobBusy):
            self.runner.start_raw(
                "Again",
                [[sys.executable, "-c", "print('nope')"]],
                self.data,
            )
        self.assertTrue(self.runner.stop())
        self.wait_for_finish()

    def test_stop_cancels_job(self):
        self.runner.start_raw(
            "Sleep",
            [[sys.executable, "-c", "import time; time.sleep(30)"]],
            self.data,
        )
        deadline = time.time() + 5
        while time.time() < deadline and not self.runner.status()["active"]:
            time.sleep(0.05)
        self.assertTrue(self.runner.stop())
        status = self.wait_for_finish()
        self.assertEqual(status["job"]["status"], "cancelled")

    def test_unknown_action(self):
        with self.assertRaises(KeyError):
            self.runner.start(self.data, "does-not-exist")

    def test_failed_step_marks_error(self):
        self.runner.start_raw(
            "Fail",
            [[sys.executable, "-c", "import sys; sys.exit(3)"]],
            self.data,
        )
        status = self.wait_for_finish()
        self.assertEqual(status["job"]["status"], "error")
        self.assertEqual(status["job"]["steps"][0]["returncode"], 3)


if __name__ == "__main__":
    unittest.main()
