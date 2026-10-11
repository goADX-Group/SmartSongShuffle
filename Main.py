import os

# Must be set before pygame is imported anywhere in the process tree.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
import importlib

import DebugLog

# Module-level logger — does NOT trigger setup_logging().
log = DebugLog.get_logger("Main")


# ---- dependency bootstrap --------------------------------------------------

REQUIRED = {
    "librosa": "librosa", "numpy": "numpy", "pandas": "pandas",
    "matplotlib": "matplotlib", "pygame": "pygame", "mutagen": "mutagen",
    "PIL": "pillow", "winsdk": "winsdk",
}

from importlib import metadata

# Newer numba/llvmlite/numpy crash librosa's beat_track with an access violation.
PINNED = {
    "numpy": "2.2.6",
    "scipy": "1.15.3",
    "numba": "0.61.2",
    "llvmlite": "0.44.0",
}

def _missing_packages():
    # Check pinned versions via metadata BEFORE importing anything, so we
    # never try to replace numpy/numba DLLs that are already loaded.
    wrong = []
    for name, ver in PINNED.items():
        try:
            if metadata.version(name) != ver:
                wrong.append(f"{name}=={ver}")
        except metadata.PackageNotFoundError:
            wrong.append(f"{name}=={ver}")
    if wrong:
        log.warning("Wrong or missing pinned versions: %s", wrong)
        return wrong

    out = []
    for mod, pip_name in REQUIRED.items():
        try:
            importlib.import_module(mod)
        except ImportError as e:
            log.debug("Missing module %r (%s): %s", mod, pip_name, e)
            out.append(pip_name)
    return out


def ensure_dependencies():
    log.info("Checking dependencies…")
    missing = _missing_packages()
    if not missing:
        log.info("All dependencies present")
        return
    log.warning("Installing missing packages: %s", ", ".join(missing))
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
    except Exception:
        log.exception("pip install failed")
        sys.exit(1)
    still = _missing_packages()
    if still:
        log.critical("Still missing after install: %s", still)
        sys.exit(1)
    log.info("Dependencies installed successfully")


# ---- folder resolution -----------------------------------------------------

def ask_for_folder():
    log.info("Prompting user for music folder")
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(
        "First-time setup",
        "Please select the folder containing your MP3 files.",
    )
    folder = filedialog.askdirectory(title="Select music folder")
    root.destroy()
    log.info("User selected folder: %r", folder)
    return folder or None


def resolve_folder(settings):
    folder = settings.get("folder", "")
    if folder and os.path.isdir(folder):
        log.info("Using saved music folder: %s", folder)
        return folder
    if folder:
        log.warning("Saved folder no longer exists: %s", folder)
    folder = ask_for_folder()
    if not folder:
        log.error("No music folder selected")
        return None
    settings["folder"] = os.path.abspath(folder)
    save_settings(settings)
    log.info("Saved new music folder: %s", settings["folder"])
    return settings["folder"]


# ---- modes -----------------------------------------------------------------

GUI_FLAG = "--gui"


def run_setup_then_relaunch():
    log.info("=== Setup phase starting ===")
    settings = load_settings()
    folder = resolve_folder(settings)
    if not folder:
        log.error("No music folder selected. Exiting.")
        sys.exit(1)

    log.info("Music folder: %s", folder)

    log.info("Creating SongDataBase…")
    try:
        db = SongDataBase.SongDataBase(folder)
    except Exception:
        log.exception("Failed to create SongDataBase")
        raise

    log.info("Analyzing library (first run can take several minutes)…")
    try:
        db.analyze_folder()
    except Exception:
        log.exception("Library analysis crashed")
        raise

    log.info("Library ready. %d songs indexed.", len(db.df))

    # In debug mode, stay attached so the terminal shows everything.
    if DebugLog.DEBUG:
        log.info("DEBUG mode: running GUI in this process (no detached relaunch)")
        run_gui()
        log.info("=== GUI closed, setup process exiting ===")
        return

    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw):
        log.warning("pythonw.exe not found; falling back to %s", sys.executable)
        pythonw = sys.executable

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    log.info("Launching detached GUI process: %s %s", pythonw, GUI_FLAG)
    subprocess.Popen(
        [pythonw, os.path.abspath(__file__), GUI_FLAG],
        creationflags=creationflags,
    )
    log.info("=== Setup done; exiting so the terminal can close ===")


def run_gui():
    log.info("=== GUI phase starting ===")
    try:
        import SongPlayer

        settings = load_settings()
        folder = settings.get("folder", "")
        log.info("Music folder from settings: %r", folder)
        if not folder or not os.path.isdir(folder):
            log.error("Music folder not set or missing on disk")
            sys.exit(1)

        log.info("Opening SongDataBase…")
        db = SongDataBase.SongDataBase(folder)
        log.info("Loaded %d songs", len(db.df))

        log.info("Creating SongPicker…")
        picker = SongPicker.SongPicker(db, seed=None)

        log.info("Starting SongPlayer GUI…")
        SongPlayer.SongPlayer(db, picker)
    except Exception:
        log.exception("GUI phase crashed")
        raise
    finally:
        log.info("=== GUI phase exited ===")


# ---- entry point -----------------------------------------------------------
#
# Everything below is guarded by __name__ == "__main__" so that when
# ProcessPoolExecutor spawns worker processes on Windows and those workers
# re-import this module, they do NOT re-run logging setup, the dependency
# check, or any heavy imports.

if __name__ == "__main__":
    DebugLog.setup_logging()
    DebugLog.install_excepthook()

    try:
        ensure_dependencies()

        # Heavy imports — only loaded in the real entry point.
        import SongDataBase
        import SongPicker
        from AppSettings import load_settings, save_settings

        if GUI_FLAG in sys.argv:
            run_gui()
        else:
            run_setup_then_relaunch()
    except SystemExit:
        raise
    except Exception:
        log.exception("Top-level crash")
        raise  # let the .bat's ERRORLEVEL reflect failure