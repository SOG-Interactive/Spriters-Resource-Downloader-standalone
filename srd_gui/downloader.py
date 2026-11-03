"""Inventory and download jobs. Runs on a worker thread; reports via events."""
from __future__ import annotations

import io
import json
import os
import threading
import time
import zipfile
from datetime import datetime, timezone
from typing import Callable

from . import naming
from .parser import Catalog, Item, parse_asset_page, parse_game_page
from .session import Cancelled, ChallengeRequired, SessionError, SiteSession

Emit = Callable[[dict], None]
THUMB_BATCH = 20
RETRIES = 3


def build_inventory(session: SiteSession, url: str, base_url: str, emit: Emit) -> Catalog:
    """Load the game page in the session window and parse it."""
    emit({"type": "status", "text": "Opening the game page..."})
    session.ensure_ready(url)
    html = session.page_html()
    catalog = parse_game_page(html, url, base_url)
    if not catalog.items:
        raise SessionError(
            "No sprite sheets were found on that page. Check the address, or the site layout may have changed."
        )
    mismatches = [s.name for s in catalog.sections if s.declared_count is not None and s.declared_count != len(s.items)]
    emit({"type": "catalog", "catalog": catalog.to_dict(), "warnings": (
        [f"Section count differs from what the page declares: {', '.join(mismatches)}"] if mismatches else [])})
    return catalog


def load_thumbnails(session: SiteSession, catalog: Catalog, emit: Emit) -> None:
    """Fetch thumbnails through the session and push them to the UI in batches."""
    items = [i for i in catalog.items if i.thumb]
    for start in range(0, len(items), THUMB_BATCH):
        if session.cancel_event.is_set():
            return
        batch = items[start:start + THUMB_BATCH]
        try:
            data = session.fetch_many_data_urls([i.thumb for i in batch])
        except SessionError:
            data = {}
        emit({"type": "thumbs", "thumbs": {i.id: data.get(i.thumb) for i in batch},
              "done": min(start + THUMB_BATCH, len(items)), "total": len(items)})


class DownloadJob:
    def __init__(self, session: SiteSession, catalog: Catalog, items: list[Item], dest_root: str,
                 options: dict, base_url: str, emit: Emit):
        self.session = session
        self.catalog = catalog
        self.items = items
        self.dest_root = dest_root
        self.options = options or {}
        self.base_url = base_url
        self.emit = emit
        self.results: list[dict] = []

    # ----- paths ---------------------------------------------------------
    def game_dir(self) -> str:
        return os.path.join(self.dest_root,
                            naming.sanitize(self.catalog.console_title, self.catalog.console_slug),
                            naming.sanitize(self.catalog.game_title, self.catalog.game_slug))

    def _planned_names(self) -> dict[str, str]:
        """Stable filename stems per item, unique within each section."""
        by_section: dict[str, list[tuple[str, str, str]]] = {}
        for it in self.items:
            by_section.setdefault(it.section, []).append((it.id, it.title, ""))
        out: dict[str, str] = {}
        for entries in by_section.values():
            out.update(naming.unique_names(entries))
        return out

    # ----- run -----------------------------------------------------------
    def run(self) -> dict:
        stems = self._planned_names()
        total = len(self.items)
        ok = skipped = failed = 0
        started = time.monotonic()
        self.emit({"type": "download_start", "total": total, "dest": self.game_dir()})
        for index, item in enumerate(self.items):
            if self.session.cancel_event.is_set():
                self.emit({"type": "log", "level": "warn", "text": "Cancelled."})
                break
            self.emit({"type": "progress", "index": index, "total": total, "item": item.to_dict()})
            result = self._download_one(item, stems[item.id])
            self.results.append(result)
            status = result["status"]
            if status == "ok":
                ok += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
            self.emit({"type": "item_done", "index": index, "total": total, "item": item.to_dict(), "result": result})
        summary = {"ok": ok, "skipped": skipped, "failed": failed, "total": total,
                   "seconds": round(time.monotonic() - started, 1), "dest": self.game_dir(),
                   "cancelled": self.session.cancel_event.is_set()}
        self._write_manifest(summary)
        self.emit({"type": "download_done", "summary": summary})
        return summary

    def _download_one(self, item: Item, stem: str) -> dict:
        section_dir = os.path.join(self.game_dir(), naming.sanitize(item.section))
        last_error = "unknown error"
        for attempt in range(1, RETRIES + 1):
            try:
                html = self.session.fetch_text(item.url)
                asset = parse_asset_page(html, item.url, self.base_url)
                if asset is None:
                    return {"id": item.id, "status": "failed", "error": "No downloadable file found on the page."}
                _, ext = naming.split_ext(asset.filename)
                if not ext:
                    _, ext = naming.split_ext(asset.url.split("?")[0].rsplit("/", 1)[-1])
                ext = ext or ".png"
                filename = stem + ext
                target = os.path.join(section_dir, filename)
                is_zip = ext == ".zip"
                extract_dir = os.path.join(section_dir, stem) if is_zip and self.options.get("extract_zips", True) else None

                if self.options.get("skip_existing", True):
                    if (extract_dir and os.path.isdir(extract_dir) and os.listdir(extract_dir)) or (not extract_dir and os.path.isfile(target)):
                        return {"id": item.id, "status": "skipped", "path": extract_dir or target}

                data = self.session.fetch_bytes(asset.url)
                os.makedirs(section_dir, exist_ok=True)
                if extract_dir:
                    try:
                        with zipfile.ZipFile(io.BytesIO(data)) as zf:
                            os.makedirs(extract_dir, exist_ok=True)
                            _safe_extract(zf, extract_dir)
                        return {"id": item.id, "status": "ok", "path": extract_dir, "bytes": len(data), "extracted": True}
                    except zipfile.BadZipFile:
                        self.emit({"type": "log", "level": "warn",
                                   "text": f"{item.title}: archive could not be extracted, keeping the .zip file."})
                _atomic_write(target, data)
                return {"id": item.id, "status": "ok", "path": target, "bytes": len(data)}
            except ChallengeRequired:
                self.emit({"type": "log", "level": "warn", "text": "The site asked for a human check again. Please pass it in the browser window."})
                self.session.ensure_ready(item.url)
                last_error = "human check"
            except Cancelled:
                return {"id": item.id, "status": "cancelled"}
            except SessionError as exc:
                last_error = str(exc)
                self.emit({"type": "log", "level": "warn", "text": f"{item.title}: {last_error} (attempt {attempt}/{RETRIES})"})
                time.sleep(1.5 * attempt)
            except OSError as exc:
                return {"id": item.id, "status": "failed", "error": f"Could not write file: {exc}"}
        return {"id": item.id, "status": "failed", "error": last_error}

    def _write_manifest(self, summary: dict) -> None:
        try:
            os.makedirs(self.game_dir(), exist_ok=True)
            path = os.path.join(self.game_dir(), "catalog.json")
            existing = {}
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        existing = json.load(f).get("downloads", {})
                except (OSError, ValueError):
                    existing = {}
            by_id = {i.id: i for i in self.items}
            for r in self.results:
                if r["status"] in ("ok", "skipped"):
                    it = by_id[r["id"]]
                    existing[r["id"]] = {"title": it.title, "section": it.section, "url": it.url, "path": r.get("path")}
            manifest = {
                "game": self.catalog.game_title, "console": self.catalog.console_title, "url": self.catalog.url,
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "last_run": summary,
                "sections": [{"name": s.name, "items": [{"id": i.id, "title": i.title, "url": i.url} for i in s.items]}
                             for s in self.catalog.sections],
                "downloads": existing,
            }
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as exc:
            self.emit({"type": "log", "level": "warn", "text": f"Could not write catalog.json: {exc}"})


def _atomic_write(path: str, data: bytes) -> None:
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _safe_extract(zf: zipfile.ZipFile, dest: str) -> None:
    """Extract without letting archive paths escape ``dest``."""
    root = os.path.realpath(dest)
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if not name or name.endswith("/"):
            continue
        parts = [naming.sanitize(p) for p in name.split("/") if p not in ("", ".", "..")]
        if not parts:
            continue
        target = os.path.join(root, *parts)
        if not os.path.realpath(target).startswith(root):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            dst.write(src.read())


def run_in_thread(fn, *args, **kwargs) -> threading.Thread:
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t
