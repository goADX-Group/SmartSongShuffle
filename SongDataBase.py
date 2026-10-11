from SongAnalyzer import analyze_song
import os
import time
import pandas as pd
import glob
from typing import Any
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
import numpy as np

from DebugLog import get_logger

log = get_logger("SongDataBase")

# librosa + numba + scipy are very heavy per process (~500 MB – 1 GB RSS
# each at import, more under JIT load).  Do NOT let ProcessPoolExecutor
# default to os.cpu_count() on a modern machine — that can easily demand
# 8–16 GB of RAM at once and Windows will kill the workers.
DEFAULT_MAX_WORKERS = 2
HARD_MAX_WORKERS = 4


class SongDataBase:
    # Columns that get robust-z-scored before distance computation.
    # 'bpm_log' is DERIVED (computed from 'bpm' in get_song_stats), so it
    # participates in z-scoring and distance but isn't stored in the CSV.
    Z_COLS = (
        ["bpm_log", "loudness_db", "onset_strength", "flatness", "zcr",
         "centroid_mean", "centroid_std", "rolloff_mean", "rolloff_std",
         "rms_p10", "rms_p50", "rms_p90"]
        + [f"mfcc_{i}_mean" for i in range(1, 13)]
        + [f"mfcc_{i}_std"  for i in range(1, 13)]
        + [f"contrast_{i}"  for i in range(7)]
    )

    DERIVED = {"bpm_log"}

    def __init__(self, folder, csv_path="songs.csv"):
        self.csv_path = csv_path
        self.folder = folder
        log.info("SongDataBase init: folder=%s csv=%s", folder, csv_path)

        if os.path.exists(csv_path):
            log.info("Loading existing CSV: %s", csv_path)
            try:
                df = pd.read_csv(csv_path, index_col=0)
                log.info("CSV loaded: %d rows, %d columns", len(df), len(df.columns))
            except Exception:
                log.exception("Failed to read CSV — starting fresh")
                df = pd.DataFrame()

            missing_cols = [c for c in self.Z_COLS
                            if c not in df.columns and c not in self.DERIVED]
            if missing_cols:
                log.warning(
                    "Schema changed — %d new columns needed: %s%s. "
                    "Re-analyzing library from scratch.",
                    len(missing_cols),
                    missing_cols[:5],
                    "…" if len(missing_cols) > 5 else "",
                )
                self.df = pd.DataFrame()
            else:
                self.df = df
                log.info("Loaded %d existing songs from %s", len(self.df), csv_path)
                self._drop_missing_files()
        else:
            self.df = pd.DataFrame()
            log.info("No existing data, starting fresh")

    def _drop_missing_files(self):
        """Remove CSV rows whose mp3 file no longer exists in the folder."""
        if self.df.empty:
            return
        try:
            present = {os.path.basename(p)
                       for p in glob.glob(os.path.join(self.folder, "*.mp3"))}
        except Exception:
            log.exception("Could not enumerate folder; skipping cleanup")
            return

        missing = [name for name in self.df.index if name not in present]
        if not missing:
            log.debug("No missing files in CSV")
            return

        log.info("Removing %d song(s) from CSV that are missing from the folder",
                 len(missing))
        for name in missing[:10]:
            log.debug("  missing: %s", name)
        if len(missing) > 10:
            log.debug("  … and %d more", len(missing) - 10)

        self.df = self.df.drop(missing)
        try:
            self.df.to_csv(self.csv_path)
            log.info("CSV rewritten after cleanup (%d songs remain)", len(self.df))
        except Exception:
            log.exception("Failed to write CSV after cleanup")

    def _resolve_worker_count(self, workers):
        """Choose a safe worker count for a librosa/numba pool."""
        cpu = os.cpu_count() or 2
        if workers is None:
            chosen = min(DEFAULT_MAX_WORKERS, max(1, cpu // 2))
        else:
            chosen = max(1, int(workers))
        if chosen > HARD_MAX_WORKERS:
            log.warning(
                "Requested %d workers; capping to %d because each worker "
                "imports librosa+numba and needs a lot of RAM.",
                chosen, HARD_MAX_WORKERS,
            )
            chosen = HARD_MAX_WORKERS
        log.info("CPU count=%d, using %d worker process(es)", cpu, chosen)
        return chosen

    # ------------------------------------------------------------------
    # Row persistence
    # ------------------------------------------------------------------

    def _store_row(self, name, row):
        """Write one analysed song into the DataFrame and flush to CSV.
        Returns True on success, False if writing failed."""
        try:
            self.df.loc[name, list(row.keys())] = list(row.values())
            self.df.to_csv(self.csv_path)
            return True
        except Exception:
            log.exception("Failed to save row for %s", name)
            return False

    # ------------------------------------------------------------------
    # Analysis backends
    # ------------------------------------------------------------------

    def _analyze_serial(self, files, start, total, done, failed):
        """In-process fallback (also used when workers == 1).

        Runs every file in the current process, so a native crash in
        librosa on one file will take the whole process down — but the
        CSV is flushed after each success, so a subsequent run resumes.
        """
        for f in files:
            name = os.path.basename(f)
            try:
                row = analyze_song(f)
            except Exception:
                failed += 1
                log.exception("FAILED (serial): %s", name)
                continue

            if not self._store_row(name, row):
                failed += 1
                continue

            done += 1
            completed = done + failed
            elapsed = time.perf_counter() - start
            remaining = (total - completed) * (elapsed / max(completed, 1))
            log.info("[%d/%d] Done: %s | ETA ~%.1f min",
                     completed, total, name, remaining / 60)

        return done, failed

    def _analyze_with_pool(self, files, n_workers, start, total, done, failed):
        """Run the pool over `files`.

        Returns (done, failed, pending_files, broken).  `pending_files`
        are the inputs that never produced a result — if the pool died
        mid-run, the caller retries these serially.
        """
        finished = set()
        broken = False

        ex = ProcessPoolExecutor(max_workers=n_workers)
        try:
            log.debug("Submitting %d tasks to ProcessPoolExecutor", len(files))
            futures = {ex.submit(analyze_song, f): f for f in files}

            try:
                for future in as_completed(futures):
                    f = futures[future]
                    name = os.path.basename(f)

                    try:
                        row = future.result()
                    except BrokenProcessPool as e:
                        # The pool is dead.  Every other pending future
                        # will raise the same thing; stop iterating and
                        # let the caller fall back to serial.
                        log.error("Worker pool died: %s", e)
                        broken = True
                        break
                    except Exception:
                        # A single bad song.  Not fatal.
                        failed += 1
                        log.exception("FAILED: %s", name)
                        finished.add(f)
                        continue

                    finished.add(f)

                    if not self._store_row(name, row):
                        failed += 1
                        continue

                    done += 1
                    completed = done + failed
                    elapsed = time.perf_counter() - start
                    remaining = (total - completed) * (elapsed / max(completed, 1))
                    log.info("[%d/%d] Done: %s | ETA ~%.1f min",
                             completed, total, name, remaining / 60)
            finally:
                # Don't let still-queued tasks keep churning after a break.
                try:
                    ex.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    # Python < 3.9 doesn't accept cancel_futures.
                    ex.shutdown(wait=False)
        except Exception:
            log.exception("Unexpected error while driving the worker pool")
            broken = True

        pending = [f for f in files if f not in finished]
        return done, failed, pending, broken

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def analyze_folder(self, workers=None):
        log.info("analyze_folder starting (workers=%s)", workers)

        try:
            files = sorted(glob.glob(os.path.join(self.folder, "*.mp3")))
        except Exception:
            log.exception("glob failed on folder %s", self.folder)
            raise

        log.info("Found %d mp3 file(s) in %s", len(files), self.folder)

        todo = [f for f in files if os.path.basename(f) not in self.df.index]
        skipped = len(files) - len(todo)
        total = len(todo)
        log.info("%d already analyzed, %d to do", skipped, total)

        if total == 0:
            log.info("Nothing to analyze")
            return self.df

        n_workers = self._resolve_worker_count(workers)
        start = time.perf_counter()
        done = failed = 0
        pending = list(todo)

        if n_workers > 1:
            done, failed, pending, broken = self._analyze_with_pool(
                pending, n_workers, start, total, done, failed,
            )
            if not broken:
                pending = []
            else:
                log.warning(
                    "Falling back to serial analysis for the remaining "
                    "%d file(s). Slower, but immune to worker crashes.",
                    len(pending),
                )

        if pending:
            done, failed = self._analyze_serial(
                pending, start, total, done, failed,
            )

        total_time = time.perf_counter() - start
        log.info("Finished in %.1f min: %d analyzed, %d skipped, %d failed",
                 total_time / 60, done, skipped, failed)
        return self.df

    # ------------------------------------------------------------------
    # Feature access
    # ------------------------------------------------------------------

    def get_song_stats(self, name=None):
        """Return z-scored features ready for distance.
        'bpm_log' is derived from 'bpm' on the fly; 'harmony_position' and
        'key_confidence' pass through un-z-scored (the picker handles them)."""
        if self.df.empty:
            return None if name is not None else pd.DataFrame()

        out = pd.DataFrame(index=self.df.index)
        out["harmony_position"] = self.df["harmony_position"]
        out["key_confidence"]   = self.df["key_confidence"]

        for col in self.Z_COLS:
            if col == "bpm_log":
                v = np.log2(self.df["bpm"].clip(lower=1e-3))
            else:
                v = self.df[col]
            med = v.median()
            iqr = v.quantile(0.75) - v.quantile(0.25)
            if not np.isfinite(iqr) or iqr == 0:
                out[col] = 0.0
            else:
                out[col] = ((v - med) / iqr).clip(-3, 3)

        if name is not None:
            if name not in out.index:
                return None
            return out.loc[name].to_dict()
        return out