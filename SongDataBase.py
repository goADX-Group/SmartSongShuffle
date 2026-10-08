from SongAnalyzer import analyze_song
import os
import time
import pandas as pd
import glob
import matplotlib.pyplot as plt
from typing import Any
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np


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
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path, index_col=0)
            missing = [c for c in self.Z_COLS
                       if c not in df.columns and c not in self.DERIVED]
            if missing:
                print(f"Schema changed — {len(missing)} new columns needed: "
                      f"{missing[:5]}{'...' if len(missing) > 5 else ''}. "
                      f"Re-analyzing library from scratch.")
                self.df = pd.DataFrame()
            else:
                self.df = df
                print(f"Loaded {len(self.df)} existing songs from {csv_path}")
        else:
            self.df = pd.DataFrame()
            print("No existing data, starting fresh")

    def analyze_folder(self, workers=None):
        files = sorted(glob.glob(os.path.join(self.folder, "*.mp3")))
        todo = [f for f in files if os.path.basename(f) not in self.df.index]
        skipped = len(files) - len(todo)
        total = len(todo)
        print(f"Found {len(files)} mp3 files, {skipped} already analyzed, {total} to do")

        done = failed = 0
        start = time.perf_counter()

        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(analyze_song, f): f for f in todo}

            for future in as_completed(futures):
                name = os.path.basename(futures[future])
                try:
                    row = future.result()
                except Exception as e:
                    failed += 1
                    print(f"FAILED: {name} -> {e}")
                    continue

                self.df.loc[name, list(row.keys())] = list(row.values())
                self.df.to_csv(self.csv_path)
                done += 1

                elapsed = time.perf_counter() - start
                remaining = (total - done - failed) * (elapsed / (done + failed))
                print(f"[{done + failed}/{total}] Done: {name} | ETA ~{remaining / 60:.1f} min")

        total_time = time.perf_counter() - start
        print(f"\nFinished in {total_time / 60:.1f} min: "
              f"{done} analyzed, {skipped} skipped, {failed} failed")
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