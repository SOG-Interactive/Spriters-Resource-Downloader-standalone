"""Application entry point: two pywebview windows and the JS-facing API.

Window 1 ("the app") shows ui/index.html and talks to :class:`Api`.
Window 2 ("the browser") is a real Edge WebView2 tab on the site. The user
passes the site's human check there; every page and file is then fetched
inside that tab, see :mod:`srd_gui.session`.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import traceback
import webbrowser
from urllib.parse import quote

import webview

from . import __version__, config, downloader
from .parser import Catalog, InvalidGameUrl, normalize_game_url
from .session import Cancelled, SessionError, SiteSession


class Api:
    """Methods callable from JavaScript as ``window.pywebview.api.<name>``."""

    def __init__(self) -> None:
        self._ui = None
        self._site = None
        self._session: SiteSession | None = None
        self._catalog: Catalog | None = None
        self.allow_site_close = False
        self._busy = threading.Lock()

    def attach(self, ui, site) -> None:
        self._ui = ui
        self._site = site
        self._session = SiteSession(site, config.BASE_URL, on_status=lambda d: self._emit({"type": "session", **d}))

    # ----- plumbing ------------------------------------------------------
    def _emit(self, event: dict) -> None:
        payload = json.dumps(event)  # ensure_ascii keeps it safe to embed
        try:
            self._ui.evaluate_js(f"window.App && App.handleEvent({payload}); true")
        except Exception:
            pass

    def _run(self, fn, *args) -> dict:
        if not self._busy.acquire(blocking=False):
            return {"ok": False, "error": "Something is already running. Cancel it first."}

        def wrapper():
            try:
                self._session.cancel_event.clear()
                fn(*args)
            except Cancelled:
                self._emit({"type": "cancelled"})
            except (SessionError, InvalidGameUrl) as exc:
                self._emit({"type": "error", "text": str(exc)})
            except Exception as exc:  # keep the UI alive on anything unexpected
                self._emit({"type": "error", "text": f"Unexpected error: {exc}", "detail": traceback.format_exc()})
            finally:
                self._busy.release()

        downloader.run_in_thread(wrapper)
        return {"ok": True}

    # ----- simple calls --------------------------------------------------
    def app_info(self) -> dict:
        return {"version": __version__, "base_url": config.BASE_URL, "profile_dir": config.profile_dir(),
                "settings_path": config.settings_path()}

    def get_settings(self) -> dict:
        return config.load_settings()

    def save_settings(self, data: dict) -> dict:
        return config.save_settings(data)

    def validate_url(self, url: str) -> dict:
        try:
            canonical, console, game = normalize_game_url(url, config.BASE_URL)
            return {"ok": True, "url": canonical, "console": console, "game": game}
        except InvalidGameUrl as exc:
            return {"ok": False, "error": str(exc)}

    def pick_folder(self, current: str = "") -> str | None:
        start = current if current and os.path.isdir(current) else os.path.expanduser("~")
        result = self._ui.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    def open_folder(self, path: str) -> dict:
        if path and os.path.isdir(path):
            os.startfile(path)  # type: ignore[attr-defined]
            return {"ok": True}
        return {"ok": False, "error": "Folder does not exist yet."}

    def open_url(self, url: str) -> dict:
        """Open a credits / help link in the user's normal web browser."""
        if isinstance(url, str) and url.startswith("https://"):
            webbrowser.open(url)
            return {"ok": True}
        return {"ok": False, "error": "Only https links can be opened."}

    def show_browser(self) -> dict:
        try:
            self._site.show()
            self._site.restore()
        except Exception:
            pass
        return {"ok": True}

    def cancel(self) -> dict:
        self._session.cancel_event.set()
        return {"ok": True}

    # ----- jobs ----------------------------------------------------------
    def start_inventory(self, url: str, options: dict) -> dict:
        return self._run(self._inventory, url, options or {})

    def start_download(self, url: str, options: dict, ids: list | None) -> dict:
        return self._run(self._download, url, options or {}, ids)

    def _inventory(self, url: str, options: dict, with_thumbs: bool | None = None) -> Catalog:
        canonical, _, _ = normalize_game_url(url, config.BASE_URL)
        catalog = downloader.build_inventory(self._session, canonical, config.BASE_URL, self._emit)
        catalog = self._apply_nsfw(catalog, options)
        self._catalog = catalog
        if with_thumbs if with_thumbs is not None else options.get("thumbs", True):
            downloader.load_thumbnails(self._session, catalog, self._emit)
        self._emit({"type": "inventory_done", "item_count": len(catalog.items)})
        return catalog

    def _apply_nsfw(self, catalog: Catalog, options: dict) -> Catalog:
        """Best effort: flip the site's own NSFW toggle when the user asked
        for the other state, then re-read the page. Never blocks the flow."""
        want = options.get("nsfw")
        if want is None or catalog.nsfw_enabled is None or bool(want) == catalog.nsfw_enabled:
            return catalog
        try:
            path = "/" + catalog.url.split("/", 3)[3]
            self._emit({"type": "status", "text": "Switching the site's NSFW setting..."})
            self._session.ensure_ready(f"{config.BASE_URL}/process/setnsfw/?return={quote(path, safe='')}", timeout=90)
            self._session.ensure_ready(catalog.url, timeout=90)
            updated = downloader.build_inventory(self._session, catalog.url, config.BASE_URL, self._emit)
            return updated
        except SessionError as exc:
            self._emit({"type": "log", "level": "warn", "text": f"Could not change the NSFW setting: {exc}"})
            return catalog

    def _download(self, url: str, options: dict, ids: list | None) -> None:
        canonical, _, _ = normalize_game_url(url, config.BASE_URL)
        catalog = self._catalog
        if catalog is None or catalog.url != canonical:
            catalog = self._inventory(canonical, options, with_thumbs=False)
        if ids is None:
            items = list(catalog.items)
        else:
            wanted = {str(i) for i in ids}
            items = [i for i in catalog.items if i.id in wanted]
        if not items:
            raise SessionError("Nothing is selected.")
        dest = (options.get("dest") or "").strip() or config.DEFAULT_SETTINGS["dest"]
        job = downloader.DownloadJob(self._session, catalog, items, dest, options, config.BASE_URL, self._emit)
        job.run()


# ----- process plumbing ----------------------------------------------------

_lock_handle = None


def _acquire_instance_lock() -> bool:
    """Hold a lock file in the profile folder for the life of the process.
    WebView2 refuses to start when another process already uses the profile,
    so give a friendly message instead of a cryptic failure."""
    global _lock_handle
    path = os.path.join(config.profile_dir(), "app.lock")
    try:
        fh = open(path, "a+")
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        _lock_handle = fh
        return True
    except OSError:
        return False


def _message_box(title: str, text: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    else:
        print(f"{title}: {text}", file=sys.stderr)


def build_windows(api: Api):
    ui = webview.create_window(
        "SOG Interactive - Spriters Resource Downloader",
        os.path.join(config.UI_DIR, "index.html"),
        js_api=api,
        width=1240, height=860, min_size=(900, 620),
        text_select=True,
    )
    site = webview.create_window(
        "Site browser - leave this window open (SOG Interactive Spriters Resource Downloader)",
        config.BASE_URL + "/",
        width=1000, height=760, x=120, y=80,
        focus=False,
    )
    api.attach(ui, site)

    def hide_instead_of_close():
        if api.allow_site_close:
            return True
        try:
            site.hide()
        except Exception:
            pass
        return False

    site.events.closing += hide_instead_of_close

    def shutdown():
        api.allow_site_close = True
        try:
            api._session.cancel_event.set()
            site.destroy()
        except Exception:
            pass

    ui.events.closed += shutdown
    return ui, site


def main() -> None:
    if not _acquire_instance_lock():
        _message_box("SOG Interactive - Spriters Resource Downloader",
                     "The downloader is already running (or its browser profile is in use).\n"
                     "Close the other copy and try again.")
        sys.exit(1)
    api = Api()
    build_windows(api)
    debug = os.environ.get("SRD_DEBUG") == "1"
    webview.start(private_mode=False, storage_path=config.profile_dir(), debug=debug)


if __name__ == "__main__":
    main()
