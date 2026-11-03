"""Build the standalone app and the double-click Setup.exe.

Run with the project's venv:  .venv\\Scripts\\python.exe build_installer.py

Outputs
  dist\\Spriters Resource Downloader\\            the app folder (portable; run the .exe inside)
  dist\\Spriters Resource Downloader Setup.exe   the installer
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "Spriters Resource Downloader"
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")


def build_env() -> dict:
    """Conda-based Pythons keep runtime DLLs (ffi, bz2, lzma, ssl...) in
    Library\\bin; put it on PATH so PyInstaller's dependency scan finds them."""
    env = dict(os.environ)
    extra = [os.path.join(sys.base_prefix, "Library", "bin"), os.path.join(sys.base_prefix, "DLLs"), sys.base_prefix]
    extra = [p for p in extra if os.path.isdir(p)]
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


def run(args: list[str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True, env=build_env())


def main() -> None:
    py = sys.executable
    shutil.rmtree(BUILD, ignore_errors=True)
    os.makedirs(DIST, exist_ok=True)
    app_dist = os.path.join(BUILD, "app_dist")

    # 1. The app itself (one folder, windowed). Built under build\ so a dist
    #    folder that is open in Explorer cannot block the rebuild.
    run([py, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
         "--distpath", app_dist,
         "--name", APP_NAME,
         "--add-data", f"{os.path.join('srd_gui', 'ui')}{os.pathsep}{os.path.join('srd_gui', 'ui')}",
         "--collect-all", "webview",
         "--collect-all", "clr_loader",
         "--collect-all", "pythonnet",
         "--hidden-import", "bs4",
         "--hidden-import", "srd_gui.app",
         "run_app.py"])
    app_dir = os.path.join(app_dist, APP_NAME)
    exe = os.path.join(app_dir, APP_NAME + ".exe")
    if not os.path.isfile(exe):
        raise SystemExit(f"app build failed, missing {exe}")

    # Portable copy next to the installer (best effort if dist is locked).
    portable = os.path.join(DIST, APP_NAME)
    try:
        shutil.rmtree(portable, ignore_errors=True)
        shutil.copytree(app_dir, portable, dirs_exist_ok=True)
    except OSError as exc:
        print("note: could not refresh the portable copy in dist:", exc)

    # 2. The installer, carrying the app folder as its payload.
    run([py, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
         "--distpath", DIST,
         "--name", APP_NAME + " Setup",
         "--add-data", f"{app_dir}{os.pathsep}app",
         os.path.join("installer", "setup_app.py")])
    setup = os.path.join(DIST, APP_NAME + " Setup.exe")
    if not os.path.isfile(setup):
        raise SystemExit("installer build failed")
    print("\nBuilt:")
    print("  ", exe)
    print("  ", setup, f"({os.path.getsize(setup) // (1024 * 1024)} MB)")


if __name__ == "__main__":
    main()
