# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DATE_FORMAT = "%Y-%m-%d"
SIDECAR_SUFFIXES = ("-wal", "-shm")


def _today() -> date:
    return datetime.now(tz=timezone.utc).astimezone().date()


@dataclass
class BackupSummary:
    dest: Path
    databases: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dest": str(self.dest),
            "databases": self.databases,
            "removed": self.removed,
            "errors": self.errors,
        }


def database_files(db_root: str | Path) -> list[Path]:
    root = Path(db_root)
    if not root.is_dir():
        raise FileNotFoundError(f"Datenbankverzeichnis fehlt: {root}")
    files = []
    for path in root.rglob("*.db"):
        if not path.is_file() or path.name.endswith(SIDECAR_SUFFIXES):
            continue
        files.append(path)
    return sorted(files)


def _copy_online(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp")
    temporary.unlink(missing_ok=True)
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        target_conn = sqlite3.connect(temporary)
        try:
            source_conn.backup(target_conn)
            target_conn.execute("PRAGMA journal_mode=DELETE")
            target_conn.commit()
        finally:
            target_conn.close()
    finally:
        source_conn.close()
    for suffix in SIDECAR_SUFFIXES:
        temporary.with_name(f"{temporary.name}{suffix}").unlink(missing_ok=True)
    check_conn = sqlite3.connect(f"file:{temporary}?mode=ro", uri=True)
    try:
        row = check_conn.execute("PRAGMA quick_check").fetchone()
    finally:
        check_conn.close()
    if not row or row[0] != "ok":
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Integritätsprüfung fehlgeschlagen: {source}")
    temporary.replace(destination)
    for suffix in SIDECAR_SUFFIXES:
        destination.with_name(f"{destination.name}{suffix}").unlink(missing_ok=True)


def _dated_directories(dest_root: Path) -> list[tuple[date, Path]]:
    found: list[tuple[date, Path]] = []
    if not dest_root.is_dir():
        return found
    for child in dest_root.iterdir():
        if not child.is_dir():
            continue
        try:
            parsed = date.fromisoformat(child.name)
        except ValueError:
            continue
        found.append((parsed, child))
    return found


def prune_backups(
    dest_root: str | Path,
    keep_days: int,
    today: date | None = None,
) -> list[str]:
    if keep_days < 1:
        raise ValueError("keep_days muss mindestens 1 sein")
    root = Path(dest_root)
    current = today or _today()
    cutoff = current - timedelta(days=keep_days - 1)
    removed: list[str] = []
    for day, directory in sorted(_dated_directories(root)):
        if day >= cutoff:
            continue
        entries = sorted(
            directory.rglob("*"),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for path in entries:
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        directory.rmdir()
        removed.append(directory.name)
    return removed


def backup_databases(
    db_root: str | Path,
    dest_root: str | Path,
    keep_days: int = 3,
    log=print,
) -> BackupSummary:
    root = Path(db_root)
    dest = Path(dest_root)
    summary = BackupSummary(dest=dest)
    files = database_files(root)
    if not files:
        log(f"Backup: keine SQLite-Dateien unter {root} gefunden")
    target_dir = dest / _today().strftime(DATE_FORMAT)
    for source in files:
        relative = source.relative_to(root)
        target = target_dir / relative
        try:
            _copy_online(source, target)
        except Exception as exc:  # noqa: BLE001
            message = f"{relative}: {type(exc).__name__}: {exc}"
            summary.errors.append(message)
            log(f"Backup fehlgeschlagen: {message}")
            continue
        summary.databases.append(str(relative))
        log(f"Backup: {relative} -> {target}")
    summary.removed = prune_backups(dest, keep_days)
    for name in summary.removed:
        log(f"Backup entfernt: {name}")
    return summary
