"""Offline end-to-end run of the real app against tests/fixture_server.py.

Drives the UI window through its public ``App`` object (the same code paths
a mouse click takes) and checks the files that land on disk. The fake human
check on the fixture server is cleared by clicking the button on *our own*
page, which stands in for the user clicking the real site's check.

Run:  .venv\\Scripts\\python.exe tests\\e2e_offline.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.fixture_server import FixtureServer, asset_sha, ZIP_IDS, BIG_IDS  # noqa: E402

server = FixtureServer(challenge=True).start()
TMP = tempfile.mkdtemp(prefix="srd_e2e_")
os.environ["SRD_BASE_URL"] = server.base_url
os.environ["SRD_PROFILE_DIR"] = os.path.join(TMP, "profile")
os.environ["SRD_SETTINGS_PATH"] = os.path.join(TMP, "settings.json")
DEST = os.path.join(TMP, "downloads")

import webview  # noqa: E402
from srd_gui import config  # noqa: E402
from srd_gui.app import Api, build_windows  # noqa: E402

assert config.BASE_URL == server.base_url

api = Api()
ui, site = build_windows(api)
RESULT = {"ok": False, "error": None}
VERBOSE = "-v" in sys.argv

_orig_emit = api._emit


def _logged_emit(event):
    if VERBOSE:
        short = {k: (v if k not in ("catalog", "thumbs") else f"<{len(v)} entries>") for k, v in event.items()}
        print("  [event]", json.dumps(short)[:300], flush=True)
    _orig_emit(event)


api._emit = _logged_emit


def js(win, script):
    return win.evaluate_js(script)


def ui_state():
    s = js(ui, "JSON.stringify(App.getState())")
    return json.loads(s) if isinstance(s, str) else (s or {})


def wait_for(desc, pred, timeout=60, poll=0.3):
    start = time.monotonic()
    last = None
    while time.monotonic() - start < timeout:
        try:
            last = pred()
            if last:
                return last
        except Exception as exc:  # bridge hiccups while pages load
            last = f"exc: {exc}"
        time.sleep(poll)
    try:
        dump = json.dumps(ui_state())
    except Exception as exc:
        dump = f"<ui state unavailable: {exc}>"
    raise AssertionError(f"timeout waiting for {desc}; last={last!r}; ui={dump}; site={site_probe()}; "
                         f"server={{'cleared': server.state.cleared, 'html_requests': server.state.html_requests}}")


def pass_fake_check():
    """Stand-in for the user passing the check: flip the fixture server's flag;
    the fake page notices and navigates on by itself. The harness never
    touches the session window's JavaScript bridge."""
    server.clear()


def site_probe():
    try:
        return js(site, "JSON.stringify({title: document.title, url: location.href, ready: !!document.querySelector('div.icondisplay'), rs: document.readyState})")
    except Exception as exc:
        return f"<probe failed: {exc}>"


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def driver():
    print("Running offline end-to-end test. Please leave the two app windows alone until it finishes.", flush=True)
    try:
        wait_for("UI ready", lambda: js(ui, "window.App && App.ready ? true : null"), timeout=60)
        url1 = f"{server.base_url}/game_boy_gbc/pokemongoldsilver/"
        js(ui, f"App.setUrl({json.dumps(url1)}); App.setDest({json.dumps(DEST)}); App.setOption('thumbs', true); true")

        # ---- 1. Inventory, through the fake human check ----
        js(ui, "App.inventory(); true")
        wait_for("challenge state", lambda: ui_state().get("session") == "challenge", timeout=40)
        pass_fake_check()
        st = wait_for("catalog screen", lambda: (lambda s: s if s.get("screen") == "catalog" else None)(ui_state()), timeout=60)
        check(st["itemCount"] == 128, f"expected 128 items, got {st['itemCount']}")
        check(st["sectionCount"] == 5, f"expected 5 sections, got {st['sectionCount']}")
        check(st["selectedCount"] == 128, "everything should start selected")
        check(not st["warnings"], f"unexpected warnings {st['warnings']}")
        wait_for("thumbnails", lambda: ui_state().get("thumbCount") == 128, timeout=120)
        print("inventory OK: 128 items, 5 sections, 128 thumbnails")

        # ---- 2. Filter + selection shortcuts ----
        js(ui, "App.setFilter('route'); true")
        st = ui_state()
        check(st["shownCount"] == 46, f"filter 'route' should show the 46 route backgrounds, got {st['shownCount']}")
        js(ui, "App.deselectAll(); document.getElementById('btn-select-shown').click(); true")
        check(ui_state()["selectedCount"] == 46, "select shown should select 46")
        js(ui, "App.setFilter(''); App.deselectAll(); true")
        check(ui_state()["selectedCount"] == 0, "deselect all")
        picks = ["9100", "9077", "106321"]  # zip, big multi-chunk file, normal
        for pid in picks:
            js(ui, f"App.toggleItem('{pid}'); true")
        check(ui_state()["selectedCount"] == 3, "three picked")
        print("selection OK")

        # ---- 3. Download selected ----
        js(ui, "App.setDest(" + json.dumps(DEST) + "); App.downloadSelected(); true")
        wait_for("download summary", lambda: ui_state().get("summary"), timeout=180)
        st = ui_state()
        s = st["summary"]
        check(s["ok"] == 3 and s["failed"] == 0, f"summary {s}")
        game_dir = os.path.join(DEST, "Game Boy - GBC", "Pokémon Gold - Silver")
        check(os.path.isdir(game_dir), f"missing game dir {game_dir}: {os.listdir(DEST) if os.path.isdir(DEST) else 'no dest'}")
        big = os.path.join(game_dir, "Characters", "Characters (Overworld).png")
        check(os.path.isfile(big), f"missing {big}")
        check(sha(big) == asset_sha("9077"), "big file bytes differ (chunking bug)")
        check(os.path.getsize(big) > 9 * 1024 * 1024, "big file should exceed 9 MiB")
        normal = os.path.join(game_dir, "Miscellaneous", "Maps (World Map & Flight).png")
        check(os.path.isfile(normal) and sha(normal) == asset_sha("106321"), f"normal file wrong: {normal}")
        zdir = os.path.join(game_dir, "Characters", "Characters (Battle)")
        check(os.path.isfile(os.path.join(zdir, "Sheet A.png")), "zip not extracted (Sheet A)")
        check(os.path.isfile(os.path.join(zdir, "sub folder", "Sheet B.png")), "zip nested folder missing")
        check(not os.path.exists(os.path.join(game_dir, "Characters", "evil.png")), "zip slip! evil.png escaped")
        check(not os.path.exists(os.path.join(zdir + ".zip")), "zip file should not remain when extracted")
        manifest = os.path.join(game_dir, "catalog.json")
        check(os.path.isfile(manifest), "catalog.json missing")
        with open(manifest, encoding="utf-8") as f:
            man = json.load(f)
        check(len(man["downloads"]) == 3 and man["last_run"]["ok"] == 3, "manifest content")
        print("download selected OK (zip extracted safely, 9 MiB file byte-exact, manifest written)")

        # ---- 4. Skip existing on a re-run ----
        js(ui, "document.getElementById('btn-to-catalog').click(); App.setDest(" + json.dumps(DEST) + "); App.downloadSelected(); true")
        wait_for("second summary", lambda: (ui_state().get("summary") or {}).get("skipped") == 3, timeout=60)
        print("skip existing OK")

        # ---- 5. Download everything for another game, with the check expiring mid-run ----
        url2 = f"{server.base_url}/nes/1942/"
        js(ui, f"App.startOver(); App.setUrl({json.dumps(url2)}); App.setOption('thumbs', false); App.setDest(" + json.dumps(DEST) + "); App.downloadAll(); true")
        wait_for("progress past item 3", lambda: (ui_state().get("progress") or {}).get("index", -1) >= 3, timeout=60)
        server.revoke()
        wait_for("re-challenge during download", lambda: ui_state().get("session") == "challenge", timeout=60)
        pass_fake_check()
        wait_for("1942 summary", lambda: ui_state().get("summary"), timeout=180)
        s = ui_state()["summary"]
        check(s["total"] == 20 and s["ok"] == 19 and s["failed"] == 1, f"1942 summary {s}")
        d1942 = os.path.join(DEST, "NES", "1942")
        files = [os.path.join(dp, f) for dp, _, fs in os.walk(d1942) for f in fs if f.endswith(".png")]
        check(len(files) == 19, f"expected 19 png files, got {len(files)}")
        print("download everything OK (19 ok, 1 simulated server failure, resumed after re-check)")

        # ---- 6. Cancel ----
        js(ui, f"App.startOver(); App.setUrl({json.dumps(url1)}); App.setOption('skip', false); App.setDest(" + json.dumps(DEST) + "); App.downloadAll(); true")
        wait_for("progress started", lambda: (ui_state().get("progress") or {}).get("index", -1) >= 2, timeout=60)
        js(ui, "document.getElementById('btn-cancel').click(); true")
        s = wait_for("cancel summary", lambda: ui_state().get("summary"), timeout=60)
        check(s["cancelled"] is True and s["ok"] < 128, f"cancel summary {s}")
        print("cancel OK")

        RESULT["ok"] = True
    except Exception:
        RESULT["error"] = traceback.format_exc()
    finally:
        api.allow_site_close = True
        try:
            site.destroy()
        except Exception:
            pass
        try:
            ui.destroy()
        except Exception:
            pass


webview.start(driver, private_mode=False, storage_path=config.profile_dir())
server.stop()
if RESULT["ok"]:
    print("E2E PASS")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(0)
print("E2E FAIL\n" + (RESULT["error"] or ""))
print("artifacts left in", TMP)
sys.exit(1)
