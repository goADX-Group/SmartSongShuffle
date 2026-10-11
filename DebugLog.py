"""Central logging setup for SmartSongShuffle.

Set SMARTSONG_DEBUG=1 (or true/yes/on) to enable DEBUG-level logging.
Run_debug.bat sets this automatically.

Design notes:
  * Only the MAIN process installs handlers.  Spawned worker processes
    (ProcessPoolExecutor on Windows re-imports __main__) get a silent
    NullHandler at CRITICAL so they don't spam the console or the log.
  * Noisy third-party loggers are pinned at WARNING regardless of DEBUG.
  * A rotating file handler writes to smartsong.log next to this script,
    so crashes in the detached GUI process (which has no console) are
    still recoverable after the fact.
  * get_logger() does not configure anything — callers just get a logger.
"""
import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler

# Silence pygame's import banner before anything imports it.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

DEBUG = os.environ.get("SMARTSONG_DEBUG", "").lower() in ("1", "true", "yes", "on")

_configured = False

# Our own loggers — these get DEBUG/INFO depending on SMARTSONG_DEBUG.
_OUR_LOGGERS = (
    "Main", "SongDataBase", "SongPicker", "SongPlayer",
    "AppSettings", "SongAnalyzer", "uncaught",
)

# Third-party loggers to pin at WARNING (or higher).
_NOISY_LOGGERS = (
    "numba", "numba.core", "numba.core.byteflow",
    "numba.core.ssa", "numba.core.interpreter",
    "matplotlib", "matplotlib.font_manager", "matplotlib.pyplot",
    "matplotlib.backends",
    "fontTools", "PIL", "PIL.Image",
    "urllib3", "asyncio",
    "librosa", "audioread", "soundfile",
)

LOG_FILENAME = "smartsong.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 3


def _is_main_process():
    """True when running in the primary Python process (not a spawn worker)."""
    try:
        import multiprocessing
        return multiprocessing.current_process().name == "MainProcess"
    except Exception:
        return True


def setup_logging():
    global _configured
    if _configured:
        return
    _configured = True

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    # ---- worker process: silent -----------------------------------------
    if not _is_main_process():
        root.setLevel(logging.CRITICAL)
        root.addHandler(logging.NullHandler())
        for name in _OUR_LOGGERS:
            logging.getLogger(name).setLevel(logging.CRITICAL)
        return

    # ---- main process ----------------------------------------------------
    # Root stays at WARNING so unknown libraries stay quiet.  Our own
    # loggers get their own (lower) level below, which overrides this.
    root.setLevel(logging.WARNING)

    # Console formatter (short time).
    console_fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # File formatter (full timestamp + pid so you can tell processes apart).
    file_fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-7s] pid=%(process)d %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(console_fmt)
    root.addHandler(console)

    try:
        log_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), LOG_FILENAME
        )
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUPS,
            encoding="utf-8",
        )
        file_handler.setFormatter(file_fmt)
        root.addHandler(file_handler)
    except Exception as e:
        print(f"[logging] Could not open {LOG_FILENAME}: {e}", file=sys.stderr)

    our_level = logging.DEBUG if DEBUG else logging.INFO
    for name in _OUR_LOGGERS:
        logging.getLogger(name).setLevel(our_level)

    # Announce via a named logger — root itself is at WARNING.
    logging.getLogger("Main").info(
        "Logging initialised (DEBUG=%s, pid=%d)", DEBUG, os.getpid()
    )


def get_logger(name):
    """Return a logger without side-effects.  Call setup_logging() once from
    the main entry point."""
    return logging.getLogger(name)


def install_excepthook():
    """Log uncaught exceptions with full tracebacks so nothing is lost when
    the console is about to close."""

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("uncaught").critical(
            "Uncaught exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )

    sys.excepthook = _hook