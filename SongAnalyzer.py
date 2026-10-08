import librosa
import numpy as np

MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def analyze_song(path, offset=None, duration=None):
    y, sr = librosa.load(path, sr=22050, offset=offset, duration=duration)  # type: ignore

    # ----- rhythm / key (kept) -----
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, _ = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)

    chroma_mean = librosa.feature.chroma_stft(y=y, sr=sr).mean(axis=1)
    best_score, best_key, best_mode = -2, 0, 1
    for i in range(12):
        for mode, profile in ((1, MAJOR_PROFILE), (0, MINOR_PROFILE)):
            score = np.corrcoef(chroma_mean, np.roll(profile, i))[0, 1]
            if score > best_score:
                best_score, best_key, best_mode = score, i, mode

    harmony_position = (best_key * 7) % 12 if best_mode == 1 else ((best_key + 3) * 7) % 12

    # ----- timbre: MFCCs are the single biggest similarity signal -----
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    mfcc_mean = mfcc[1:].mean(axis=1)   # drop coeff 0 (it's loudness)
    mfcc_std  = mfcc[1:].std(axis=1)    # variance = how much timbre moves

    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rolloff  = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
    contrast = librosa.feature.spectral_contrast(y=y, sr=sr).mean(axis=1)

    # ----- dynamics: percentiles, not just mean -----
    rms_db = librosa.amplitude_to_db(librosa.feature.rms(y=y)[0] + 1e-10)
    rms_p10, rms_p50, rms_p90 = np.percentile(rms_db, [10, 50, 90])

    row = {
        "bpm": float(np.atleast_1d(tempo)[0]),
        "harmony_position": harmony_position,
        "key_confidence": float(best_score),
        "loudness_db": float(np.mean(rms_db)),
        "onset_strength": float(np.mean(onset_env)),
        "flatness": float(np.mean(librosa.feature.spectral_flatness(y=y))),
        "zcr": float(np.mean(librosa.feature.zero_crossing_rate(y))),

        "centroid_mean": float(centroid.mean()),
        "centroid_std":  float(centroid.std()),
        "rolloff_mean":  float(rolloff.mean()),
        "rolloff_std":   float(rolloff.std()),
        "rms_p10": float(rms_p10),
        "rms_p50": float(rms_p50),
        "rms_p90": float(rms_p90),
    }
    for i in range(12):
        row[f"mfcc_{i+1}_mean"] = float(mfcc_mean[i])
        row[f"mfcc_{i+1}_std"]  = float(mfcc_std[i])
    for i in range(7):
        row[f"contrast_{i}"] = float(contrast[i])
    return row