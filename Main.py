import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox


# ---- dependency bootstrap --------------------------------------------------

import importlib

REQUIRED = {
    "librosa": "librosa", "numpy": "numpy", "pandas": "pandas",
    "matplotlib": "matplotlib", "pygame": "pygame", "mutagen": "mutagen",
    "PIL": "pillow", "winsdk": "winsdk",
}


def _missing_packages():
    out = []
    for mod, pip_name in REQUIRED.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            out.append(pip_name)
    return out


def ensure_dependencies():
    missing = _missing_packages()
    if not missing:
        return
    print(f"Installing missing packages: {', '.join(missing)}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
    if _missing_packages():
        print("Install failed.")
        sys.exit(1)


ensure_dependencies()

# ---- real imports ----------------------------------------------------------

import SongDataBase
import SongPicker
import SongPlayer
from AppSettings import load_settings, save_settings


# ---- folder resolution -----------------------------------------------------

def ask_for_folder():
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
    folder = settings.get("folder", "")
    if folder and os.path.isdir(folder):
        return folder
    if folder:
        print(f"Saved folder no longer exists: {folder}")
    folder = ask_for_folder()
    if not folder:
        return None
    settings["folder"] = os.path.abspath(folder)
    save_settings(settings)
    return settings["folder"]


# ---- modes -----------------------------------------------------------------

GUI_FLAG = "--gui"


def run_setup_then_relaunch():
    """Visible-console phase: resolve folder, analyze library, then hand off
    to a detached GUI process and exit so the terminal can close."""
    settings = load_settings()
    folder = resolve_folder(settings)
    if not folder:
        print("No music folder selected. Exiting.")
        sys.exit(1)

    print(f"Music folder: {folder}")
    print("Analyzing library (this can take several minutes on first run)...")
    db = SongDataBase.SongDataBase(folder)
    db.analyze_folder()
    print(f"\nLibrary ready. {len(db.df)} songs indexed.")

    # Relaunch ourselves in GUI mode, detached, so the terminal can close.
    print("Starting player...")
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable  # fallback; a console will linger

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(
        [pythonw, os.path.abspath(__file__), GUI_FLAG],
        creationflags=creationflags,
    )
    # Python exits here → the .bat's `python Main.py` returns → terminal closes.


def run_gui():
    """GUI-only phase: no console output matters, just open the player."""
    settings = load_settings()
    folder = settings.get("folder", "")
    if not folder or not os.path.isdir(folder):
        print("Music folder not set. Run the setup again.")
        sys.exit(1)

    db = SongDataBase.SongDataBase(folder)
    picker = SongPicker.SongPicker(db, seed=None)
    SongPlayer.SongPlayer(db, picker)


if __name__ == "__main__":
    if GUI_FLAG in sys.argv:
        run_gui()
    else:
        run_setup_then_relaunch()