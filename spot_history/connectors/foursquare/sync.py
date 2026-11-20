from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from .api import AuthError, FoursquareAPI
from .config import FoursquareConfig
from .db import Database, iso_now, iso_utc
from .export import export_all
from .photos import download_pending

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
        return comments, photos

    def sync_extras(self) -> None:
        for name, func in (
            ("Fotos", self.sync_all_photos),
            ("Venue-History", self.sync_venue_history),
            ("Listen", self.sync_lists),
            ("Tipps", self.sync_tips),
        ):
            try:
                func()
            except AuthError:
                raise
            except Exception as exc:
                self.log(f"{name}: übersprungen ({exc})")

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
