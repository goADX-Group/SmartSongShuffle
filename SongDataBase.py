from SongAnalyzer import analyze_song
import os
import time
import pandas as pd
import glob
import matplotlib.pyplot as plt
from typing import Any
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np

from DebugLog import get_logger

log = get_logger("SongDataBase")

# librosa + numba + scipy are very heavy per process (~500 MB – 1 GB RSS
# each at import, more under JIT load).  Do NOT let ProcessPoolExecutor
# default to os.cpu_count() on a modern machine — that can easily demand
# 8–16 GB of RAM at once and Windows will kill the workers.
DEFAULT_MAX_WORKERS = 2
HARD_MAX_WORKERS = 4

# If this many tasks fail in a row, stop — the pool is almost certainly
# broken (OOM, import crash, dead worker) and continuing is pointless.
CONSECUTIVE_FAILURE_ABORT = 10


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

        done = failed = 0
        consecutive_failures = 0
        aborted = False
        start = time.perf_counter()

        try:
            with ProcessPoolExecutor(max_workers=n_workers) as ex:
                log.debug("Submitting %d tasks to ProcessPoolExecutor", total)
                futures = {ex.submit(analyze_song, f): f for f in todo}

                for future in as_completed(futures):
                    name = os.path.basename(futures[future])
                    try:
                        row = future.result()
                    except Exception:
                        failed += 1
                        consecutive_failures += 1
                        log.exception("FAILED: %s", name)

                        if consecutive_failures >= CONSECUTIVE_FAILURE_ABORT:
                            log.critical(
                                "%d tasks failed in a row — the worker pool "
                                "is almost certainly broken (OOM or an "
                                "import crash).  Aborting analysis.",
                                consecutive_failures,
                            )
                            aborted = True
                            # Cancel everything still queued, then break.
                            for f in futures:
                                f.cancel()
                            break
                        continue

                    # A successful task resets the failure streak.
                    consecutive_failures = 0

                    try:
                        self.df.loc[name, list(row.keys())] = list(row.values())
                        self.df.to_csv(self.csv_path)
                    except Exception:
                        failed += 1
                        log.exception("Failed to save row for %s", name)
                        continue

                    done += 1
                    elapsed = time.perf_counter() - start
                    completed = done + failed
                    remaining = (total - completed) * (elapsed / completed)
                    log.info("[%d/%d] Done: %s | ETA ~%.1f min",
                             completed, total, name, remaining / 60)
        except Exception:
            log.exception("analyze_folder: worker pool crashed")
            raise

        total_time = time.perf_counter() - start
        if aborted:
            log.error("ABORTED after %.1f min: %d analyzed, %d skipped, "
                      "%d failed.  See log above for the first failure.",
                      total_time / 60, done, skipped, failed)
        else:
            log.info("Finished in %.1f min: %d analyzed, %d skipped, %d failed",
                     total_time / 60, done, skipped, failed)
        return self.df

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