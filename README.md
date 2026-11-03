# Spriters Resource Downloader (GUI)

A **SOG Interactive (Stroke of Genius Interactive)** tool.

A point-and-click Windows app that downloads sprite sheets from
[The Spriters Resource](https://www.spriters-resource.com).

> **Credits:** based on [Spriters-Resource-Downloader](https://github.com/torresflo/Spriters-Resource-Downloader)
> by **torresflo**, the original command-line downloader this app grew from (GPL v3). The app shows
> this credit on its start screen and in its Credits panel.

Paste a game page address, pick a folder, and either **download everything** or
**inventory first**: the app lists every sheet on the page with its thumbnail,
grouped in the same sections as the website, with checkboxes on every section and
every sheet so you can choose exactly what to download.

## Quick start

**Option A - installer (no Python needed)**

1. Double-click **`Spriters Resource Downloader Setup.exe`** (in `dist\` after a build,
   or from a release).
2. Click **Install**. It installs to `%LOCALAPPDATA%\Programs\Spriters Resource Downloader`
   and puts a **Spriters Resource Downloader** shortcut on your Desktop and in the Start Menu.
3. Double-click the shortcut to launch. To remove it later, use Settings > Apps or run
   `Uninstall.bat` from the install folder.

**Option B - run from source**

1. Install [Python 3.9 or newer](https://www.python.org/downloads/windows/) if you
   do not have it (tick *Add python.exe to PATH* in the installer).
2. Double-click **`Start Spriters Downloader.bat`**.
   The first run creates a private `.venv` folder and installs two small libraries;
   later runs start straight away.

Either way, two windows open:

* **Spriters Resource Downloader** - the app.
* **Site browser** - a real browser tab on the website. Leave it open.

## Using it

**Start screen**

* *Game page address* - for example
  `https://www.spriters-resource.com/game_boy_gbc/pokemongoldsilver/`.
  A sheet address (`.../asset/12345/`) also works; its game page is used.
* *Download to* - any folder. Files are organised as
  `Console \ Game \ Section \ Sheet name.png` and a `catalog.json` is written next to
  them listing everything that was found and downloaded.
* Options: load thumbnails, extract `.zip` sheets into folders, skip files that
  already exist, include NSFW sheets (flips the site's own toggle).
* **Download everything** grabs every sheet on the page.
* **Inventory first** opens the catalog.

**Catalog screen**

* Every section has a checkbox (tick/untick the whole section) and an *Only this* button.
* Every sheet is a card with its thumbnail and a checkbox; click anywhere on the card to toggle it.
* Toolbar: *Select all*, *Deselect all*, *Invert*, a filter box (matches sheet or section
  names) with *Select shown* / *Deselect shown*, sort by page order or name, thumbnail size,
  *Only selected* view, *Expand all* / *Collapse all*.
* **Download selected** at the bottom.

**Progress screen** shows a progress bar, a log of every file, counts of done / skipped /
failed, a *Cancel* button and, when finished, *Open folder*.

## About the "Verify you are human" check

The website is protected by a bot check that only a real browser can pass. That is why
the app opens a real browser window (Microsoft Edge WebView2, built into Windows 10/11).
If that window shows a *Verify you are human* box, click it yourself once; the app waits,
then reads pages and files through that same window. Your clearance is kept in the app's
own browser profile (`%LOCALAPPDATA%\SpritersResourceDownloader`), so you rarely see the
check again. If it reappears in the middle of a download, the app pauses, asks you to click
it again, and then carries on where it left off.

The app never tries to bypass or auto-click that check.

## Developers

```
.venv\Scripts\python.exe -I tests\test_parser.py      # parser / naming unit tests (offline)
.venv\Scripts\python.exe tests\e2e_offline.py          # full app run against a local fixture server
.venv\Scripts\python.exe tests\fixture_server.py --challenge   # keep the fixture server up
set SRD_BASE_URL=http://127.0.0.1:PORT                   # point the app at it
.venv\Scripts\python.exe -m srd_gui
```

Building the installer (needs the `.venv` from Option B):

```
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe build_installer.py
```

This produces `dist\Spriters Resource Downloader Setup.exe` (the double-click installer) and a
portable copy of the app in `dist\Spriters Resource Downloader\`. The installer source is
`installer\setup_app.py`; `Setup.exe /S` installs silently with defaults.

Layout:

* `srd_gui/parser.py` - pure HTML parsing (game page sections/items, asset page download link).
* `srd_gui/session.py` - the browser session: readiness probe, in-page `fetch()` bridge.
* `srd_gui/downloader.py` - inventory, thumbnails, download job, zip extraction, manifest.
* `srd_gui/app.py` - windows, the JS-facing API, process lock.
* `srd_gui/ui/` - the interface (plain HTML/CSS/JS).
* `tests/fixtures/` - archived copies of real pages the parser is tested against.

The original command-line script (`spriters-resource-downloader.py`, `Downloader/`) predates
the site's bot check and no longer works unattended. It is kept for reference; its
dependencies are listed in `requirements-legacy-cli.txt`.

## License

Distributed under the GNU General Public License v3.0. See `LICENSE` for more information.
