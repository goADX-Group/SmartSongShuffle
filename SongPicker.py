import random
from collections import deque

import numpy as np
import datetime
from SongDataBase import SongDataBase


class SongPicker:
    def __init__(self, db, seed=None, closest=3, cooldown=10):
        self.db = db
        if seed is None:
            self.seed = int(datetime.datetime.now().strftime("%Y%m%d"))  # daily seed
        else:
            self.seed = seed
        self.rng = random.Random(seed)          # used only when seed is None
        self.closest = closest
        self.recent = deque(maxlen=cooldown)
        self._pick_counter = 0 

    def distance(self, a, b):
        # ---- harmony: circle distance, down-weighted by key uncertainty ----
        dh = abs(a["harmony_position"] - b["harmony_position"])
        dh = min(dh, 12 - dh) / 6.0
        harmony = min(a["key_confidence"], b["key_confidence"]) * dh

        # ---- everything else: cosine on the z-scored block ----
        # (bpm_log lives inside Z_COLS, so it participates here too)
        vec_a = np.fromiter((a[c] for c in SongDataBase.Z_COLS), dtype=float,
                            count=len(SongDataBase.Z_COLS))
        vec_b = np.fromiter((b[c] for c in SongDataBase.Z_COLS), dtype=float,
                            count=len(SongDataBase.Z_COLS))
        denom = np.linalg.norm(vec_a) * np.linalg.norm(vec_b)
        timbre = 0.0 if denom < 1e-9 else (1.0 - float(np.dot(vec_a, vec_b) / denom)) / 2.0 # type: ignore

        W_HARMONY = 0.3
        W_TIMBRE  = 1.0
        return float(np.sqrt(W_HARMONY * harmony**2 + W_TIMBRE * timbre**2))

    def _rng_for(self, song):
        """Return an RNG whose state depends on the current song.
        With a fixed seed, the same song always produces the same choice
        from the same candidate list — but different songs give different
        choices. With seed=None, fall back to a fresh system-seeded RNG."""
        if self.seed is None:
            return random.Random()
        self._pick_counter += 1
        return random.Random(f"{self.seed}:{song}:{self._pick_counter}")

    def unforget(self):
        """Undo the effect of the last pick on the cooldown.
        Call this when the player steps back to the previous song, so the
        song that was just left becomes a valid candidate again."""
        if self.recent:
            self.recent.pop()

    def pick(self, previous):
        prev_stats = self.db.get_song_stats(previous)
        if prev_stats is None:
            return None

        stats = self.db.get_song_stats()
        if previous not in self.recent:
            self.recent.append(previous)

        candidates = [n for n in stats.index if n != previous and n not in self.recent]
        if not candidates:
            print("Not enough songs outside the cooldown, ignoring it for this pick")
            candidates = [n for n in stats.index if n != previous]
        if not candidates:
            return None

        candidates.sort(key=lambda n: self.distance(prev_stats, stats.loc[n]))

        rng = self._rng_for(previous)
        chosen = rng.choice(candidates[: self.closest])

        self.recent.append(chosen)
        return chosen
    
    def pick_start(self):
        """Seed-deterministic starting song. With a fixed seed, the same
        library always starts on the same song; different seeds give
        different starts. Picks from the analyzed DB, not the folder."""
        stats = self.db.get_song_stats()
        if stats is None or stats.empty:
            return None
        names = sorted(stats.index)
        rng = random.Random(f"{self.seed}:__start__")
        return rng.choice(names)