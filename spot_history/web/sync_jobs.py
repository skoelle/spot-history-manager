# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import ROOT

ACTIVE_STATES = ("queued", "running")


@dataclass(frozen=True)
class SyncAction:
    key: str
    label: str
    description: str
    group: str
    commands: tuple[tuple[str, ...], ...]
    confirm: str | None = None


@dataclass
class JobStep:
    command: list[str]
    argv: list[str] = field(default_factory=list)
    returncode: int | None = None

    def as_dict(self) -> dict:
        return {"command": self.command, "returncode": self.returncode}


@dataclass
class Job:
    id: str
    action: str
    label: str
    status: str
    started_at: float
    finished_at: float | None = None
    steps: list[JobStep] = field(default_factory=list)
    log_path: str | None = None
    error: str | None = None

    def as_dict(self, log_tail: list[str]) -> dict:
        now = time.time()
        end = self.finished_at or now
        return {
            "id": self.id,
            "action": self.action,
            "label": self.label,
            "status": self.status,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at) if self.finished_at else None,
            "elapsed_s": round(max(0.0, end - self.started_at), 1),
            "steps": [step.as_dict() for step in self.steps],
            "log_path": self.log_path,
            "log_tail": log_tail,
            "error": self.error,
        }


class JobBusy(RuntimeError):
    pass


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def _index_command(data_root: Path) -> tuple[str, ...]:
    return ("index", "build", "--data-root", str(data_root))


def research_decisions_path(data_root: Path) -> Path:
    return Path(data_root) / "exports" / "yelp-research-decisions.csv"


def build_commands(action: str, data_root: Path) -> list[list[str]]:
    data_root = Path(data_root)
    foursquare_dir = str(data_root / "foursquare")
    yelp_dir = str(data_root / "yelp")
    google_dir = str(data_root / "google")
    index_build = list(_index_command(data_root))
    commands: dict[str, list[list[str]]] = {
        "foursquare-sync": [
            ["foursquare", "sync", "--data-dir", foursquare_dir],
            index_build,
        ],
        "foursquare-backfill": [
            ["foursquare", "backfill", "--data-dir", foursquare_dir],
            index_build,
        ],
        "foursquare-photos": [
            ["foursquare", "photos", "--data-dir", foursquare_dir],
            index_build,
        ],
        "yelp-import": [
            ["yelp", "import", "--data-dir", yelp_dir],
            index_build,
        ],
        "yelp-geocode": [
            ["yelp", "geocode", "--data-dir", yelp_dir],
            index_build,
        ],
        "google-import": [
            ["google", "import", "--data-dir", google_dir],
            index_build,
        ],
        "google-osm": [
            ["google", "osm", "--data-dir", google_dir],
            index_build,
        ],
        "osm-sync": [
            ["osm", "sync", "--data-root", str(data_root)],
            index_build,
        ],
        "osm-rematch": [
            ["osm", "rematch", "--data-root", str(data_root)],
            index_build,
        ],
        "osm-net-scan": [
            ["osm", "net-scan", "--data-root", str(data_root)],
            index_build,
        ],
        "osm-address-scan": [
            ["osm", "address-scan", "--data-root", str(data_root)],
            index_build,
        ],
        "osm-contact-scan": [
            ["osm", "contact-scan", "--data-root", str(data_root)],
            index_build,
        ],
        "osm-deep-scan": [
            ["backup", "--keep-days", "3"],
            ["osm", "deep-scan", "--data-root", str(data_root)],
            index_build,
        ],
        "yelp-worklist": [
            [
                "places",
                "research-worklist",
                "--source",
                "yelp",
                "--output",
                str(Path(data_root) / "exports" / "yelp-research.csv"),
                "--data-root",
                str(data_root),
            ],
        ],
        "yelp-apply-research": [
            ["backup", "--keep-days", "3"],
            [
                "places",
                "apply-research",
                str(research_decisions_path(data_root)),
                "--data-root",
                str(data_root),
                "--no-build",
            ],
            index_build,
        ],
        "backup": [
            ["backup", "--keep-days", "3"],
        ],
        "index-build": [index_build],
        "index-thumbs": [
            ["index", "thumbs", "--data-root", str(data_root)],
        ],
    }
    if action not in commands:
        raise KeyError(action)
    return [[str(part) for part in command] for command in commands[action]]


def build_actions(data_root: Path) -> tuple[SyncAction, ...]:
    return (
        SyncAction(
            key="foursquare-sync",
            label="Foursquare Sync",
            description="Inkrementeller Check-in-Sync; beim ersten Lauf automatisch Backfill.",
            group="Foursquare",
            commands=tuple(tuple(cmd) for cmd in build_commands("foursquare-sync", data_root)),
        ),
        SyncAction(
            key="foursquare-backfill",
            label="Foursquare Backfill",
            description="Lädt die komplette Check-in-Historie erneut.",
            group="Foursquare",
            commands=tuple(
                tuple(cmd) for cmd in build_commands("foursquare-backfill", data_root)
            ),
            confirm="Backfill lädt die komplette Foursquare-Historie neu. Fortfahren?",
        ),
        SyncAction(
            key="foursquare-photos",
            label="Foursquare Fotos",
            description="Lädt noch offene Fotos aus den Check-ins herunter.",
            group="Foursquare",
            commands=tuple(
                tuple(cmd) for cmd in build_commands("foursquare-photos", data_root)
            ),
        ),
        SyncAction(
            key="yelp-import",
            label="Yelp Import",
            description="Liest den HTML-Export aus data/yelp/export bzw. YELP_EXPORT_DIR ein.",
            group="Yelp",
            commands=tuple(tuple(cmd) for cmd in build_commands("yelp-import", data_root)),
            confirm="Yelp-Import baut die Yelp-Datenbank neu. Fortfahren?",
        ),
        SyncAction(
            key="yelp-geocode",
            label="Yelp Geocode",
            description="Ermittelt Städte und Koordinaten für Yelp-Orte über Nominatim.",
            group="Yelp",
            commands=tuple(tuple(cmd) for cmd in build_commands("yelp-geocode", data_root)),
        ),
        SyncAction(
            key="yelp-worklist",
            label="Yelp Recherche-Worklist",
            description="Erzeugt data/exports/yelp-research.csv mit Kandidaten und Same-Day-Hinweisen.",
            group="Yelp",
            commands=tuple(
                tuple(cmd) for cmd in build_commands("yelp-worklist", data_root)
            ),
        ),
        SyncAction(
            key="yelp-apply-research",
            label="Yelp Recherche anwenden",
            description="Wendet data/exports/yelp-research-decisions.csv an und baut den Index neu.",
            group="Yelp",
            commands=tuple(
                tuple(cmd) for cmd in build_commands("yelp-apply-research", data_root)
            ),
            confirm="Recherche-Entscheidungen anwenden? Vorher wird ein Backup erstellt.",
        ),
        SyncAction(
            key="google-import",
            label="Google Import",
            description="Importiert den Timeline-Export aus data/google/inbox.",
            group="Google",
            commands=tuple(tuple(cmd) for cmd in build_commands("google-import", data_root)),
            confirm="Google-Import baut die Google-Datenbank neu. Fortfahren?",
        ),
        SyncAction(
            key="google-osm",
            label="Google OSM-Anreicherung",
            description="Reichert Google-Orte über OSM mit Namen und Kategorien an.",
            group="Google",
            commands=tuple(tuple(cmd) for cmd in build_commands("google-osm", data_root)),
        ),
        SyncAction(
            key="osm-sync",
            label="OSM Sync",
            description="Matcht offene Orte über Overpass/Nominatim.",
            group="OpenStreetMap",
            commands=tuple(tuple(cmd) for cmd in build_commands("osm-sync", data_root)),
        ),
        SyncAction(
            key="osm-rematch",
            label="OSM Rematch",
            description="Matcht offene Orte offline gegen den POI-Cache.",
            group="OpenStreetMap",
            commands=tuple(
                tuple(cmd) for cmd in build_commands("osm-rematch", data_root)
            ),
        ),
        SyncAction(
            key="osm-net-scan",
            label="OSM Netzwerk-Scan",
            description="Sucht offene Venues ohne Vorschlag über Nominatim.",
            group="OpenStreetMap",
            commands=tuple(
                tuple(cmd) for cmd in build_commands("osm-net-scan", data_root)
            ),
        ),
        SyncAction(
            key="osm-address-scan",
            label="OSM Adress-Scan",
            description="Sucht Nachfolger an derselben Adresse über Nominatim.",
            group="OpenStreetMap",
            commands=tuple(
                tuple(cmd) for cmd in build_commands("osm-address-scan", data_root)
            ),
        ),
        SyncAction(
            key="osm-contact-scan",
            label="OSM Contact-Scan",
            description="Matcht Website, Telefon und E-Mail gegen OSM-POIs.",
            group="OpenStreetMap",
            commands=tuple(
                tuple(cmd) for cmd in build_commands("osm-contact-scan", data_root)
            ),
        ),
        SyncAction(
            key="osm-deep-scan",
            label="OSM Deep-Scan",
            description="Rematch, Name-, Adress- und Contact-Scan inkl. Backup und Index.",
            group="OpenStreetMap",
            commands=tuple(
                tuple(cmd) for cmd in build_commands("osm-deep-scan", data_root)
            ),
            confirm="Deep-Scan ausführen? Vorher wird ein Backup erstellt.",
        ),
        SyncAction(
            key="backup",
            label="Backup erstellen",
            description="Sichert alle SQLite-Datenbanken nach SPOT_HISTORY_BACKUP_ROOT.",
            group="Wartung",
            commands=tuple(tuple(cmd) for cmd in build_commands("backup", data_root)),
        ),
        SyncAction(
            key="index-build",
            label="Index bauen",
            description="Baut den zentralen Index und das GeoJSON neu.",
            group="Index",
            commands=tuple(tuple(cmd) for cmd in build_commands("index-build", data_root)),
        ),
        SyncAction(
            key="index-thumbs",
            label="Thumbnails erzeugen",
            description="Erzeugt fehlende Vorschaubilder für die Web-UI.",
            group="Index",
            commands=tuple(tuple(cmd) for cmd in build_commands("index-thumbs", data_root)),
        ),
    )


def actions_by_key(data_root: Path) -> dict[str, SyncAction]:
    return {action.key: action for action in build_actions(data_root)}


GROUP_HINTS = {
    "Foursquare": "Benötigt FOURSQUARE_OAUTH_TOKEN im Container.",
    "Yelp": "Export unter data/yelp/export bzw. YELP_EXPORT_DIR.",
    "Google": "Importiert aus data/google/inbox bzw. GOOGLE_TIMELINE_INBOX.",
    "OpenStreetMap": "Abgleich offener Orte über Overpass/Nominatim.",
    "Index": "Wird nach jedem Sync automatisch ausgeführt.",
    "Wartung": "Backups und manuelle Wartung.",
}

GROUP_KEYS = {
    "Foursquare": "foursquare",
    "Yelp": "yelp",
    "Google": "google",
    "OpenStreetMap": "osm",
    "Index": "index",
    "Wartung": "maintenance",
}


def action_groups(data_root: Path) -> list[dict]:
    actions = build_actions(data_root)
    groups: list[dict] = []
    for name in (
        "Foursquare",
        "Yelp",
        "Google",
        "OpenStreetMap",
        "Index",
        "Wartung",
    ):
        items = [action for action in actions if action.group == name]
        if items:
            groups.append(
                {
                    "key": GROUP_KEYS[name],
                    "name": name,
                    "hint": GROUP_HINTS.get(name, ""),
                    "actions": items,
                }
            )
    return groups


class JobRunner:
    def __init__(
        self,
        python: str | None = None,
        module: str = "spot_history",
        max_log_lines: int = 2000,
    ) -> None:
        self.python = python or sys.executable
        self.module = module
        self._lock = threading.Lock()
        self._log: deque[str] = deque(maxlen=max_log_lines)
        self._job: Job | None = None
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._stop_requested = False

    def start(self, data_root: Path, action: str) -> Job:
        actions = actions_by_key(Path(data_root))
        if action not in actions:
            raise KeyError(action)
        selected = actions[action]
        prefix = [self.python, "-m", self.module]
        display = [list(command) for command in selected.commands]
        full = [prefix + list(command) for command in selected.commands]
        return self._start(
            selected.label,
            display,
            full,
            Path(data_root),
            action=selected.key,
        )

    def start_raw(
        self,
        label: str,
        commands: list[list[str]],
        data_root: Path,
        action: str = "custom",
    ) -> Job:
        display = [list(command) for command in commands]
        full = [list(command) for command in commands]
        return self._start(label, display, full, Path(data_root), action=action)

    def _start(
        self,
        label: str,
        display_commands: list[list[str]],
        full_commands: list[list[str]],
        data_root: Path,
        action: str,
    ) -> Job:
        with self._lock:
            if self._job is not None and self._job.status in ACTIVE_STATES:
                raise JobBusy("Es läuft bereits ein Sync-Job. Bitte warten oder abbrechen.")
            if self._thread is not None and self._thread.is_alive():
                raise JobBusy("Es läuft bereits ein Sync-Job. Bitte warten oder abbrechen.")
            job = Job(
                id=uuid.uuid4().hex[:12],
                action=action,
                label=label,
                status="queued",
                started_at=time.time(),
                steps=[
                    JobStep(command=list(display), argv=list(full))
                    for display, full in zip(display_commands, full_commands)
                ],
            )
            self._job = job
            self._log.clear()
            self._stop_requested = False
            thread = threading.Thread(
                target=self._run,
                args=(job, data_root),
                name=f"sync-job-{job.id}",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return job

    def _run(self, job: Job, data_root: Path) -> None:
        log_file = None
        try:
            log_dir = data_root / "sync" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            log_path = log_dir / f"{stamp}-{job.action}-{job.id}.log"
            job.log_path = str(log_path)
            log_file = log_path.open("a", encoding="utf-8")
            with self._lock:
                job.status = "running"
            for step in job.steps:
                if self._stop_requested:
                    with self._lock:
                        job.status = "cancelled"
                    return
                self._append_log(log_file, f"$ {' '.join(step.command)}")
                returncode = self._execute(step, data_root, log_file)
                step.returncode = returncode
                self._append_log(log_file, f"[exit {returncode}]")
                if self._stop_requested:
                    with self._lock:
                        job.status = "cancelled"
                    return
                if returncode != 0:
                    with self._lock:
                        job.status = "error"
                        job.error = f"Schritt fehlgeschlagen (exit {returncode})."
                    return
            with self._lock:
                job.status = "success"
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
            if log_file is not None:
                self._append_log(log_file, f"Interner Fehler: {job.error}")
        finally:
            with self._lock:
                job.finished_at = time.time()
                self._process = None
            if log_file is not None:
                log_file.close()

    def _execute(self, step: JobStep, data_root: Path, log_file) -> int:
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env["PYTHONIOENCODING"] = "utf-8"
        env["SPOT_HISTORY_DATA_ROOT"] = str(data_root)
        try:
            process = subprocess.Popen(
                list(step.argv or step.command),
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            self._append_log(log_file, f"Start fehlgeschlagen: {exc}")
            return 127
        with self._lock:
            self._process = process
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    self._append_log(log_file, line.rstrip("\n"))
            return process.wait()
        finally:
            if process.stdout is not None:
                process.stdout.close()
            with self._lock:
                if self._process is process:
                    self._process = None

    def _append_log(self, log_file, line: str) -> None:
        with self._lock:
            self._log.append(line)
        if log_file is not None:
            log_file.write(line + "\n")
            log_file.flush()

    def stop(self) -> bool:
        with self._lock:
            job = self._job
            if job is None or job.status not in ACTIVE_STATES:
                return False
            self._stop_requested = True
            process = self._process
        if process is not None and process.poll() is None:
            self._terminate(process)
        return True

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                return
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def status(self) -> dict:
        with self._lock:
            job = self._job
            if job is None:
                return {"active": False, "job": None}
            return {
                "active": job.status in ACTIVE_STATES,
                "job": job.as_dict(list(self._log)[-200:]),
            }

    def shutdown(self) -> None:
        self.stop()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)
