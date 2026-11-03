"""Double-click installer for Spriters Resource Downloader.

Packaged by build_installer.py as a single Setup.exe that carries the built
app folder inside it. It copies the app to a per-user Programs folder, makes
Desktop / Start Menu shortcuts, registers an uninstall entry (Settings > Apps)
and writes Uninstall.bat. Run with /S for a silent install with defaults
(optionally /D=<folder>).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import winreg

APP_NAME = "Spriters Resource Downloader"
EXE_NAME = APP_NAME + ".exe"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SpritersResourceDownloader"
MARKER = ".srd-install"
VERSION = "2.0.0"
NO_WINDOW = 0x08000000


def payload_dir() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "app")


def default_target() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, "Programs", APP_NAME)


def special_folder(name: str) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"[Environment]::GetFolderPath('{name}')"],
        capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    return out.stdout.strip()


def make_shortcut(lnk: str, target: str, workdir: str) -> None:
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk}'); $s.TargetPath = '{target}'; "
        f"$s.WorkingDirectory = '{workdir}'; $s.IconLocation = '{target},0'; "
        f"$s.Description = 'SOG Interactive {APP_NAME}'; $s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, creationflags=NO_WINDOW)


def register(target: str, uninstall_bat: str) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
        for name, value in {
            "DisplayName": APP_NAME,
            "DisplayVersion": VERSION,
            "Publisher": "SOG Interactive (Stroke of Genius Interactive)",
            "InstallLocation": target,
            "DisplayIcon": os.path.join(target, EXE_NAME),
            "UninstallString": f'"{uninstall_bat}"',
        }.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def write_uninstaller(target: str, shortcuts: list[str]) -> str:
    path = os.path.join(target, "Uninstall.bat")
    lines = ["@echo off", f"title Uninstall {APP_NAME}", f"echo Removing {APP_NAME}..."]
    lines.append(f'reg delete "HKCU\\{REG_KEY}" /f >nul 2>&1')
    for s in shortcuts:
        lines.append(f'del /q "{s}" >nul 2>&1')
    # The script lives inside the folder, so delete the folder after it exits.
    lines.append(f'start "" /min cmd /c "timeout /t 1 /nobreak >nul & rmdir /s /q "{target}""')
    lines.append("echo Done.")
    with open(path, "w", encoding="ascii", errors="replace") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return path


def install(target: str, desktop: bool, start_menu: bool, log=print) -> str:
    src = payload_dir()
    if not os.path.isdir(src):
        raise RuntimeError("Installer payload is missing; rebuild Setup.exe.")
    target = os.path.abspath(target)
    if os.path.isdir(target):
        if os.listdir(target) and not os.path.isfile(os.path.join(target, MARKER)):
            raise RuntimeError(f"{target} already exists and is not a previous install. Choose another folder.")
        log("Removing previous version...")
        shutil.rmtree(target)
    log("Copying files...")
    shutil.copytree(src, target)
    with open(os.path.join(target, MARKER), "w") as f:
        f.write(VERSION)
    exe = os.path.join(target, EXE_NAME)
    shortcuts = []
    if desktop:
        log("Creating Desktop shortcut...")
        lnk = os.path.join(special_folder("Desktop"), APP_NAME + ".lnk")
        make_shortcut(lnk, exe, target)
        shortcuts.append(lnk)
    if start_menu:
        log("Creating Start Menu shortcut...")
        lnk = os.path.join(special_folder("Programs"), APP_NAME + ".lnk")
        make_shortcut(lnk, exe, target)
        shortcuts.append(lnk)
    unins = write_uninstaller(target, shortcuts)
    register(target, unins)
    log("Done.")
    return exe


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(f"SOG Interactive - {APP_NAME} Setup")
    root.resizable(False, False)
    frm = ttk.Frame(root, padding=16)
    frm.grid()
    ttk.Label(frm, text="SOG Interactive (Stroke of Genius Interactive)", foreground="#3a6fd6", font=("Segoe UI", 9, "bold")).grid(column=0, row=0, columnspan=3, sticky="w")
    ttk.Label(frm, text=f"Install {APP_NAME} {VERSION}", font=("Segoe UI", 13, "bold")).grid(column=0, row=1, columnspan=3, sticky="w", pady=(2, 0))
    ttk.Label(frm, text="A point-and-click downloader for sprite sheets from spriters-resource.com.").grid(column=0, row=2, columnspan=3, sticky="w")
    ttk.Label(frm, text="Based on Spriters-Resource-Downloader by torresflo (GPL v3) - github.com/torresflo/Spriters-Resource-Downloader",
              foreground="#3a6fd6").grid(column=0, row=9, columnspan=3, sticky="w", pady=(14, 0))
    ttk.Label(frm, text="").grid(column=0, row=10)

    ttk.Label(frm, text="Install to:").grid(column=0, row=3, sticky="w")
    path_var = tk.StringVar(value=default_target())
    ttk.Entry(frm, textvariable=path_var, width=60).grid(column=1, row=3, sticky="we", padx=6)
    ttk.Button(frm, text="Browse...", command=lambda: path_var.set(filedialog.askdirectory(initialdir=path_var.get()) or path_var.get())).grid(column=2, row=3)

    desk = tk.BooleanVar(value=True)
    menu = tk.BooleanVar(value=True)
    launch = tk.BooleanVar(value=True)
    ttk.Checkbutton(frm, text="Create a Desktop shortcut", variable=desk).grid(column=0, row=4, columnspan=3, sticky="w", pady=(10, 0))
    ttk.Checkbutton(frm, text="Add to the Start Menu", variable=menu).grid(column=0, row=5, columnspan=3, sticky="w")
    ttk.Checkbutton(frm, text="Launch when finished", variable=launch).grid(column=0, row=6, columnspan=3, sticky="w")

    status = tk.StringVar(value="")
    ttk.Label(frm, textvariable=status).grid(column=0, row=7, columnspan=3, sticky="w", pady=(12, 0))
    btn = ttk.Button(frm, text="Install")
    btn.grid(column=2, row=8, sticky="e", pady=(12, 0))

    def go():
        btn.state(["disabled"])

        def log(msg):
            status.set(msg)
            root.update_idletasks()

        try:
            exe = install(path_var.get(), desk.get(), menu.get(), log)
        except Exception as exc:
            btn.state(["!disabled"])
            messagebox.showerror("Setup failed", str(exc))
            return
        messagebox.showinfo("Setup", f"{APP_NAME} was installed to:\n{path_var.get()}")
        if launch.get():
            subprocess.Popen([exe], cwd=os.path.dirname(exe))
        root.destroy()

    btn.configure(command=go)
    root.mainloop()


def main() -> None:
    if any(a.upper() == "/S" for a in sys.argv[1:]):
        target = default_target()
        for a in sys.argv[1:]:
            if a.upper().startswith("/D="):
                target = a[3:]
        install(target, desktop=True, start_menu=True, log=lambda m: None)
        return
    run_gui()


if __name__ == "__main__":
    main()
