"""
SmartSongShuffle — entry point.

On first run, any missing third-party libraries are installed automatically
via pip before the app itself is imported. This is why the dependency check
sits above every other import in the file.
"""

import importlib
import subprocess
import sys


# ---- dependency bootstrap ---------------
REQUIRED = {
    "librosa":    "librosa",
    "numpy":      "numpy",
    "pandas":     "pandas",
    "matplotlib": "matplotlib",
    "pygame":     "pygame",
    "mutagen":    "mutagen",
    "PIL":        "pillow",
    "winsdk":     "winsdk",
}


def _missing_packages():
    missing = []
    for module_name, pip_name in REQUIRED.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(pip_name)
    return missing


def _pip_install(packages):
    """Install the given pip packages using the running interpreter's pip."""
    cmd = [sys.executable, "-m", "pip", "install", *packages]
    print(f"Installing missing packages: {' '.join(packages)}")
    print(f"  $ {' '.join(cmd)}")
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        print(f"\npip failed with exit code {e.returncode}.")
        print("Try running this manually:")
        print(f"  {' '.join(cmd)}")
        sys.exit(1)
    except FileNotFoundError:
        print("\nCould not run pip (python -m pip not found).")
        print("Install pip and try again.")
        sys.exit(1)


def ensure_dependencies():
    missing = _missing_packages()
    if not missing:
        return
    print(f"Missing {len(missing)} package(s): {', '.join(missing)}")
    _pip_install(missing)
    # Re-check after install; pip can silently succeed while a dependency
    # still fails to import (e.g. broken build). If so, bail with a clear msg.
    still_missing = _missing_packages()
    if still_missing:
        print(f"\nStill missing after install: {', '.join(still_missing)}")
        print("Something went wrong during installation.")
        sys.exit(1)


ensure_dependencies()

# ---- real imports (safe now that deps are installed) ------------------------

import os
import tkinter as tk
from tkinter import filedialog, messagebox

import SongDataBase
import SongPicker
import SongPlayer
from AppSettings import load_settings, save_settings


# ---- folder resolution ------------------------------------------------------

def ask_for_folder():
    """Show a folder picker. Returns the chosen path, or None if cancelled."""
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(
        "First-time setup",
        "Please select the folder containing your MP3 files.",
    )
    folder = filedialog.askdirectory(title="Select music folder")
    root.destroy()
    return folder or None


def resolve_folder(settings):
    """Return a valid music folder, prompting if needed.
    Saves the chosen path into settings. Returns None if the user cancels."""
    folder = settings.get("folder", "")
    if folder and os.path.isdir(folder):
        return folder

    if folder:
        print(f"Saved folder no longer exists, re-prompting: {folder}")

    folder = ask_for_folder()
    if not folder:
        return None

    settings["folder"] = os.path.abspath(folder)
    save_settings(settings)
    return settings["folder"]


# ---- entry point ------------------------------------------------------------

def main(folder=None):
    settings = load_settings()

    if folder is None:
        folder = resolve_folder(settings)
    else:
        folder = os.path.abspath(folder)
        settings["folder"] = folder
        save_settings(settings)

    if not folder:
        print("No music folder selected. Exiting.")
        return

    db = SongDataBase.SongDataBase(folder)
    db.analyze_folder()
    picker = SongPicker.SongPicker(db, seed=None)
    SongPlayer.SongPlayer(db, picker)


if __name__ == "__main__":
    main()