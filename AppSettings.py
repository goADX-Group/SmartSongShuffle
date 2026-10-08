import json
import os

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULT_SETTINGS = {
    "volume":   0.5,
    "closest":  3,
    "cooldown": 10,
    "folder":   "",      # music folder; empty means "not set yet"
}

SETTINGS_BOUNDS = {
    "volume":   (0.0, 1.0),
    "closest":  (1,   50),
    "cooldown": (0,   500),
}


def clamp_settings(s):
    out = {}
    for k, default in DEFAULT_SETTINGS.items():
        v = s.get(k, default)
        if k in SETTINGS_BOUNDS:
            lo, hi = SETTINGS_BOUNDS[k]
            try:
                v = int(v) if isinstance(default, int) else float(v)
                v = max(lo, min(hi, v))
            except (TypeError, ValueError):
                v = default
        out[k] = v
    return out


def load_settings(path=SETTINGS_PATH):
    s = dict(DEFAULT_SETTINGS)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for k, v in loaded.items():
                if k in DEFAULT_SETTINGS:
                    s[k] = v
        except Exception as e:
            print(f"Failed to load settings ({e}); using defaults")
    return clamp_settings(s)


def save_settings(settings, path=SETTINGS_PATH):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(clamp_settings(settings), f, indent=2, sort_keys=True)
    except Exception as e:
        print(f"Failed to save settings: {e}")