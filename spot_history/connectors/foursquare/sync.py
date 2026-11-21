# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from .api import AuthError, FoursquareAPI
from .config import FoursquareConfig
from .db import Database, iso_now, iso_utc
from .export import export_all
from .photos import download_pending, download_profile_photo

MAX_PAGES = 200
PHOTO_PAGE_LIMIT = 500
VENUE_HISTORY_PAGE_LIMIT = 500
LIST_PAGE_LIMIT = 50
LIST_DETAIL_LIMIT = 100
TIP_PAGE_LIMIT = 50


def _default_log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


class Sync:
    def __init__(
        self,
        api: FoursquareAPI,
        db: Database,
        cfg: FoursquareConfig,
        log: Callable[[str], None] = _default_log,
    ) -> None:
        self.api = api
        self.db = db
        self.cfg = cfg
        self.log = log

    def run(self, mode: str = "auto") -> None:
        if mode == "backfill":
            self.backfill()
            return
        if mode == "incremental":
            self.incremental()
            return
        if self.db.get_state("backfill_done") == "1" or self.db.count_checkins() > 0:
            self.incremental()
        else:
            self.backfill()

    def backfill(self) -> None:
        run_id = self.db.start_run("backfill")
        pages = fetched = inserted = updated = 0
        comments = photos = 0
        api_total: int | None = None
        error: str | None = None
        before: int | None = None
        last_oldest: int | None = None
        try:
            self.log("Backfill: lade komplette Check-in-Historie")
            while pages < MAX_PAGES:
                items, total = self.api.checkins(
                    before=before, limit=self.cfg.page_limit, sort="newestfirst"
                )
                if api_total is None and total is not None:
                    api_total = int(total)
                if not items:
                    self.log("Backfill: leere Seite erreicht, fertig")
                    break
                pages += 1
                fetched += len(items)
                new, changed = self.db.upsert_checkins(items)
                inserted += new
                updated += changed
                oldest = min(int(item["createdAt"]) for item in items)
                self.log(
                    f"Backfill Seite {pages}: {len(items)} Check-ins, "
                    f"{new} neu, {changed} aktualisiert, ältester {iso_utc(oldest)}"
                )
                if new == 0 and before is not None and last_oldest is not None and oldest >= before - 1:
                    self.log("Backfill: keine neuen Check-ins mehr, stoppe")
                    break
                last_oldest = oldest
                before = oldest + 1
                time.sleep(self.cfg.sleep)

            self.db.set_state("api_total", api_total or 0)
            self.db.set_state("backfill_done", "1")
            self.db.set_state("last_max_created_at", self.db.max_created_at())
            self.db.set_state("last_sync_at", iso_now())
            self.db.set_state("last_mode", "backfill")

            unique = self.db.count_checkins()
            if api_total is not None and unique != api_total:
                self.log(
                    f"Warnung: {unique} eindeutige Check-ins geladen, API meldet {api_total}"
                )
            comments, photos = self._post_processing()
        except Exception as exc:
            error = str(exc)
            self.log(f"Backfill abgebrochen: {exc}")
            raise
        finally:
            self.db.finish_run(
                run_id,
                pages=pages,
                fetched=fetched,
                inserted=inserted,
                updated=updated,
                comments_fetched=comments,
                photos_downloaded=photos,
                api_total=api_total,
                error=error,
            )
            self._export()

    def incremental(self) -> None:
        last_max = self.db.get_int_state("last_max_created_at") or self.db.max_created_at()
        run_id = self.db.start_run("incremental")
        pages = fetched = inserted = updated = 0
        comments = photos = 0
        api_total: int | None = None
        error: str | None = None
        before: int | None = None
        try:
            if last_max:
                self.log(f"Incremental: letzter Stand {iso_utc(last_max)}")
            else:
                self.log("Incremental: kein Stand vorhanden, lade neueste Seiten")
            while pages < MAX_PAGES:
                items, total = self.api.checkins(
                    before=before, limit=self.cfg.page_limit, sort="newestfirst"
                )
                if api_total is None and total is not None:
                    api_total = int(total)
                if not items:
                    break
                pages += 1
                fetched += len(items)
                new, changed = self.db.upsert_checkins(items)
                inserted += new
                updated += changed
                oldest = min(int(item["createdAt"]) for item in items)
                self.log(
                    f"Incremental Seite {pages}: {len(items)} Check-ins, "
                    f"{new} neu, {changed} aktualisiert"
                )
                if last_max and oldest < last_max:
                    break
                if last_max == 0:
                    break
                before = oldest + 1
                time.sleep(self.cfg.sleep)

            self.db.set_state("last_max_created_at", self.db.max_created_at())
            self.db.set_state("last_sync_at", iso_now())
            self.db.set_state("last_mode", "incremental")
            if api_total is not None:
                self.db.set_state("api_total", api_total)
            self.log(f"Incremental: {inserted} neu, {updated} aktualisiert")
            comments, photos = self._post_processing()
        except Exception as exc:
            error = str(exc)
            self.log(f"Incremental abgebrochen: {exc}")
            raise
        finally:
            self.db.finish_run(
                run_id,
                pages=pages,
                fetched=fetched,
                inserted=inserted,
                updated=updated,
                comments_fetched=comments,
                photos_downloaded=photos,
                api_total=api_total,
                error=error,
            )
            self._export()

    def _post_processing(self) -> tuple[int, int]:
        self.sync_extras()
        comments = self.fetch_comments()
        photos = 0
        if self.cfg.download_photos:
            photos = download_pending(self.db, self.cfg, log=self.log)
        self.write_diagnostics()
        return comments, photos

    def sync_extras(self) -> None:
        for name, func in (
            ("Profil", self.sync_profile),
            ("Fotos", self.sync_all_photos),
            ("Venue-History", self.sync_venue_history),
            ("Listen", self.sync_lists),
            ("Tipps", self.sync_tips),
            ("Tip-Details", self.sync_tip_details),
            ("Tastes", self.sync_tastes),
        ):
            try:
                func()
            except AuthError:
                raise
            except Exception as exc:
                self.log(f"{name}: übersprungen ({exc})")

    def sync_profile(self) -> None:
        user = self.api.self_user()
        photo_path = None
        if self.cfg.download_photos:
            photo_path = download_profile_photo(self.cfg, user, log=self.log)
        self.db.upsert_user_profile(user, photo_path)
        self.log(
            f"Profil: {user.get('displayName') or user.get('id')}"
            f"{' (mit Bild)' if photo_path else ''}"
        )

    def sync_all_photos(self) -> int:
        seen: set[str] = set()
        offset = 0
        api_total: int | None = None
        for _ in range(MAX_PAGES):
            items, count = self.api.photos(limit=PHOTO_PAGE_LIMIT, offset=offset)
            if api_total is None and count is not None:
                api_total = int(count)
            if not items:
                break
            new_items = [
                item
                for item in items
                if item.get("id") and item["id"] not in seen
            ]
            if not new_items:
                self.log("Fotos: keine neuen IDs mehr, stoppe")
                break
            seen.update(item["id"] for item in new_items)
            inserted = self.db.upsert_photos(new_items)
            self.log(
                f"Fotos: {len(seen)} eindeutig, {inserted} neu "
                f"(offset {offset}, {len(items)} Einträge)"
            )
            offset += len(items)
            time.sleep(self.cfg.sleep)
        self.db.set_state("photos_unique_total", len(seen))
        if api_total is not None:
            self.db.set_state("photos_api_total", api_total)
            if api_total != len(seen):
                self.log(f"Fotos: {len(seen)} eindeutig, API meldet {api_total}")
        return len(seen)

    def sync_venue_history(self) -> int:
        seen: set[str] = set()
        offset = 0
        api_total: int | None = None
        for _ in range(MAX_PAGES):
            items, count = self.api.venue_history(
                limit=VENUE_HISTORY_PAGE_LIMIT, offset=offset
            )
            if api_total is None and count is not None:
                api_total = int(count)
            if not items:
                break
            new_items = [
                item
                for item in items
                if (item.get("venue") or {}).get("id")
                and (item.get("venue") or {})["id"] not in seen
            ]
            if not new_items:
                self.log("Venue-History: keine neuen Venues mehr, stoppe")
                break
            for item in new_items:
                seen.add(item["venue"]["id"])
            stored = self.db.upsert_venue_history(new_items)
            self.log(f"Venue-History: {stored} Einträge (offset {offset})")
            offset += len(items)
            time.sleep(self.cfg.sleep)
        if api_total is not None:
            self.db.set_state("venue_history_total", api_total)
            if api_total != len(seen):
                self.log(
                    f"Venue-History: {len(seen)} eindeutig, API meldet {api_total}"
                )
        return len(seen)

    def sync_lists(self) -> int:
        items = self.api.lists(limit=LIST_PAGE_LIMIT)
        stored = self.db.upsert_lists(items)
        self.log(f"Listen: {stored} gespeichert")
        for raw in items:
            item = raw.get("list") if isinstance(raw.get("list"), dict) else raw
            list_id = item.get("id")
            if not list_id:
                continue
            expected = int((item.get("listItems") or {}).get("count") or 0)
            seen: set[str] = set()
            offset = 0
            last_details: dict[str, Any] = {}
            for _ in range(MAX_PAGES):
                try:
                    details = self.api.list_details(
                        list_id, limit=LIST_DETAIL_LIMIT, offset=offset
                    )
                except AuthError:
                    raise
                except Exception as exc:
                    self.log(f"Liste {list_id}: Details übersprungen ({exc})")
                    break
                if not details:
                    break
                last_details = details
                block = details.get("listItems") or {}
                if block.get("count") is not None:
                    expected = int(block.get("count") or expected)
                list_items = block.get("items") or []
                if not list_items:
                    break
                new_items: list[dict[str, Any]] = []
                for entry in list_items:
                    venue = entry.get("venue") or {}
                    key = venue.get("id") or entry.get("venueId") or entry.get("id")
                    if not key or str(key) in seen:
                        continue
                    seen.add(str(key))
                    new_items.append(entry)
                if not new_items:
                    self.log(f"Liste {list_id}: keine neuen Einträge mehr, stoppe")
                    break
                self.db.upsert_list_items(list_id, new_items)
                offset += len(list_items)
                time.sleep(self.cfg.sleep)
            if last_details:
                self.db.upsert_lists([last_details])
            stored_items = len(seen)
            if expected and stored_items < expected:
                self.log(
                    f"Liste {list_id}: {stored_items} von {expected}, "
                    f"{expected - stored_items} API-seitig nicht geliefert"
                )
            else:
                self.log(f"Liste {list_id}: {stored_items} Einträge")
        return stored

    def sync_tips(self) -> int:
        seen: set[str] = set()
        offset = 0
        api_total: int | None = None
        for _ in range(MAX_PAGES):
            items, count = self.api.tips(limit=TIP_PAGE_LIMIT, offset=offset)
            if api_total is None and count is not None:
                api_total = int(count)
            if not items:
                break
            new_items = [
                item for item in items if item.get("id") and item["id"] not in seen
            ]
            if not new_items:
                self.log("Tipps: keine neuen IDs mehr, stoppe")
                break
            seen.update(item["id"] for item in new_items)
            stored = self.db.upsert_tips(new_items)
            self.log(f"Tipps: {len(seen)} eindeutig, {stored} verarbeitet")
            offset += len(items)
            time.sleep(self.cfg.sleep)
        if api_total is not None:
            self.db.set_state("tips_total", api_total)
            if api_total != len(seen):
                self.log(f"Tipps: {len(seen)} eindeutig, API meldet {api_total}")
        return len(seen)

    def sync_tip_details(self) -> int:
        rows = self.db.tip_rows()
        if not rows:
            return 0
        updated = 0
        total = len(rows)
        for index, row in enumerate(rows, 1):
            try:
                tip = self.api.tip_details(row["id"])
            except AuthError:
                raise
            except Exception as exc:
                self.log(f"Tipp {row['id']}: Details übersprungen ({exc})")
                continue
            if tip and self.db.upsert_tip_details(tip):
                updated += 1
            if index % 10 == 0 or index == total:
                self.log(f"Tip-Details: {index}/{total} verarbeitet")
            time.sleep(self.cfg.sleep)
        self.log(f"Tip-Details: {updated}/{total} angereichert")
        return updated

    def sync_tastes(self) -> int:
        items = self.api.tastes()
        stored = self.db.upsert_tastes(items)
        self.db.set_state("tastes_total", stored)
        self.log(f"Tastes: {stored} gespeichert")
        return stored

    def write_diagnostics(self) -> None:
        stats = self.db.stats()
        checkin_total = self.db.get_int_state("api_total")
        checkin_missing = max(0, checkin_total - stats["total"])
        self.db.set_diagnostic(
            "checkins",
            checkin_total or None,
            stats["total"],
            "1 gelöscht/privat" if checkin_missing == 1 else None,
        )

        photos_api = self.db.get_int_state("photos_api_total")
        photos_unique = self.db.get_int_state("photos_unique_total")
        note = None
        if photos_unique and photos_api and photos_unique != photos_api:
            note = f"{photos_unique} eindeutige API-IDs"
        self.db.set_diagnostic(
            "photos", photos_api or None, stats["photos_total"], note
        )

        venue_api = self.db.get_int_state("venue_history_total")
        self.db.set_diagnostic(
            "venue_history",
            venue_api or None,
            stats["venue_history"],
            "API liefert weniger als count"
            if venue_api and stats["venue_history"] < venue_api else None,
        )
        list_api = self.db.list_items_api_count()
        self.db.set_diagnostic(
            "list_items",
            list_api or None,
            stats["list_items"],
            "API liefert weniger als count"
            if list_api and stats["list_items"] < list_api else None,
        )
        self.db.set_diagnostic(
            "tips",
            self.db.get_int_state("tips_total") or None,
            stats["tips"],
        )
        self.db.set_diagnostic(
            "tastes",
            self.db.get_int_state("tastes_total") or None,
            stats["tastes"],
        )
        self.db.set_diagnostic(
            "venue_details",
            None,
            stats["venue_details"] or None,
            "Places-API-Baseline (Import)"
            if stats["venue_details"] else "kein Import",
        )

    def fetch_comments(self) -> int:
        pending = self.db.pending_comment_checkins()
        if not pending:
            return 0
        self.log(f"Kommentare: {len(pending)} Check-ins nachladen")
        fetched = 0
        for index, checkin_id in enumerate(pending, 1):
            items = self.api.comments(checkin_id)
            fetched += self.db.upsert_comments(checkin_id, items)
            self.db.mark_comments_synced(checkin_id)
            if index % 10 == 0 or index == len(pending):
                self.log(f"Kommentare: {index}/{len(pending)} verarbeitet")
        return fetched

    def _export(self) -> None:
        if not self.cfg.export:
            return
        export_all(self.db, self.cfg, log=self.log)

    def stats(self) -> dict[str, Any]:
        return self.db.stats()
