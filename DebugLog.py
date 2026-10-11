"""Central logging setup for SmartSongShuffle.

Set SMARTSONG_DEBUG=1 (or true/yes/on) to enable DEBUG-level logging.
Run_debug.bat sets this automatically.

Design notes:
  * Only the MAIN process installs a stdout handler.  Spawned worker
    processes (from ProcessPoolExecutor on Windows) re-import __main__,
    so if we configured logging at import time we'd get the same banner
    once per worker and megabytes of duplicate output.  Workers get a
    silent NullHandler at CRITICAL level instead.
  * Noisy third-party loggers (numba, matplotlib, PIL, fontTools, …) are
    pinned at WARNING so DEBUG mode isn't drowned in byteflow dumps.
  * get_logger() does NOT call setup_logging() — callers get a logger
    object and it starts working once setup_logging() is called.
"""
import logging
import os
import sys
import traceback

# Silence pygame's import banner before anything has a chance to import it.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

DEBUG = os.environ.get("SMARTSONG_DEBUG", "").lower() in ("1", "true", "yes", "on")

_configured = False

# Our own loggers, and the level we want them at.
_OUR_LOGGERS = (
    "Main", "SongDataBase", "SongPicker", "SongPlayer",
    "AppSettings", "SongAnalyzer", "uncaught",
)

# Third-party loggers that are only useful at WARNING or above.
_NOISY_LOGGERS = (
    "numba",
    "numba.core",
    "numba.core.byteflow",
    "numba.core.ssa",
    "numba.core.interpreter",
    "matplotlib",
    "matplotlib.font_manager",
    "matplotlib.pyplot",
    "matplotlib.backends",
    "fontTools",
    "PIL",
    "PIL.Image",
    "urllib3",
    "asyncio",
    "librosa",
    "audioread",
    "soundfile",
)


def _is_main_process():
    """True when running in the primary Python process (not a spawn worker)."""
    try:
        import multiprocessing
        return multiprocessing.current_process().name == "MainProcess"
    except Exception:
        return True


def setup_logging():
    """Install a stdout handler on the root logger.

    In worker processes this installs a NullHandler instead, so nothing
    from a worker leaks into the terminal.
    """
    global _configured
    if _configured:
        return
    _configured = True

    # Pin third-party loggers at WARNING regardless of DEBUG mode.
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    # --- worker process: stay silent ------------------------------------
    if not _is_main_process():
        root.setLevel(logging.CRITICAL)
        root.addHandler(logging.NullHandler())
        for name in _OUR_LOGGERS:
            logging.getLogger(name).setLevel(logging.CRITICAL)
        return

    # --- main process ----------------------------------------------------
    # Root stays at WARNING by default so any library we didn't list above
    # is quiet.  Our own loggers get their own level below.
    root.setLevel(logging.WARNING)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(handler)

    our_level = logging.DEBUG if DEBUG else logging.INFO
    for name in _OUR_LOGGERS:
        logging.getLogger(name).setLevel(our_level)

    root.info(
        "Logging initialised (DEBUG=%s, pid=%d)",
        DEBUG, os.getpid(),
    )


def get_logger(name):
    """Return a logger.  Does not configure logging — call setup_logging()
    once from the main entry point before relying on output."""
    return logging.getLogger(name)


def install_excepthook():
    """Print uncaught exceptions with full tracebacks, even in a windowed
    process where the console would otherwise close."""

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("uncaught").critical(
            "Uncaught exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )

    sys.excepthook = _hook