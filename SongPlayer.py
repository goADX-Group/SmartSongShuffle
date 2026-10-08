import glob
import io
import os
import queue
import tkinter as tk
from collections import deque
from tkinter import ttk

import pygame
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from PIL import Image, ImageTk
from winsdk.windows.media import (  # type: ignore
    MediaPlaybackStatus,
    MediaPlaybackType,
    SystemMediaTransportControlsButton,
)
from winsdk.windows.media.playback import MediaPlayer  # type: ignore

from SongDataBase import SongDataBase
from SongPicker import SongPicker
from AppSettings import (
    DEFAULT_SETTINGS,
    load_settings,
    save_settings,
    clamp_settings,
)

COVER_SIZE = 260
SWIPE_DISTANCE = 60


def format_time(seconds):
    seconds = int(max(seconds, 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


class SongPlayer:
    def __init__(self, db, picker, start=None):
        self.db = db
        self.picker = picker
        self.folder = db.folder
        self.settings = load_settings()

        # Push loaded settings into the picker straight away
        self.picker.closest = self.settings["closest"]
        self._resize_cooldown(self.settings["cooldown"])

        if start is None:
            start = picker.pick_start()
            if start is None:
                files = sorted(glob.glob(os.path.join(self.folder, "*.mp3")))
                if not files:
                    raise FileNotFoundError(f"No mp3 files in {self.folder}")
                start = os.path.basename(files[0])

        self.current = start
        self.history = []
        self.offset = 0.0
        self.length = 1.0
        self.paused = False
        self.dragging = False
        self.swipe_start = None
        self.cover_img = None
        self.commands = queue.Queue()
        self._volume_save_job = None

        pygame.mixer.init()
        pygame.mixer.music.set_volume(self.settings["volume"])
        self.setup_system_controls()

        root = self.root = tk.Tk()
        root.title("Song Player")
        root.geometry("460x540")
        root.resizable(False, False)

        container = ttk.Frame(root)
        container.pack(fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.player_frame = ttk.Frame(container)
        self.settings_frame = ttk.Frame(container)
        for f in (self.player_frame, self.settings_frame):
            f.grid(row=0, column=0, sticky="nsew")

        self._build_player_view(self.player_frame)
        self._build_settings_view(self.settings_frame)
        self.player_frame.tkraise()

        root.bind("<Left>",  lambda e: self.previous())
        root.bind("<Right>", lambda e: self.next())
        root.bind("<space>", lambda e: self.toggle_pause())

        root.protocol("WM_DELETE_WINDOW", self.close)
        self.play()
        self.update_loop()
        root.mainloop()

    # ---------- Views ----------

    def _build_player_view(self, parent):
        self.cover_label = tk.Label(parent, bd=0)
        self.cover_label.pack(pady=(14, 6))
        self.cover_label.bind("<ButtonPress-1>", self.on_swipe_press)
        self.cover_label.bind("<ButtonRelease-1>", self.on_swipe_release)

        self.title_label = ttk.Label(parent, text="", font=("Segoe UI", 11))
        self.title_label.pack(pady=(0, 6))

        self.slider = ttk.Scale(parent, from_=0, to=1, orient="horizontal",
                                length=400, takefocus=False)
        self.slider.pack()
        self.slider.bind("<ButtonPress-1>", self.on_press)
        self.slider.bind("<ButtonRelease-1>", self.on_release)

        self.time_label = ttk.Label(parent, text="0:00 / 0:00")
        self.time_label.pack(pady=4)

        buttons = ttk.Frame(parent)
        buttons.pack(pady=6)
        ttk.Button(buttons, text="⏮ Previous",
                   command=self.previous).grid(row=0, column=0, padx=5)
        self.pause_button = ttk.Button(buttons, text="⏸ Pause",
                                       command=self.toggle_pause)
        self.pause_button.grid(row=0, column=1, padx=5)
        ttk.Button(buttons, text="Next ⏭",
                   command=self.next).grid(row=0, column=2, padx=5)
        ttk.Button(buttons, text="⚙", width=3,
                   command=self.show_settings).grid(row=0, column=3, padx=5)

    def _build_settings_view(self, parent):
        body = ttk.Frame(parent, padding=20)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Settings", font=("Segoe UI", 14)).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))

        self.setting_vars = {
            "volume":   tk.DoubleVar(value=self.settings["volume"]),
            "closest":  tk.IntVar(value=self.settings["closest"]),
            "cooldown": tk.IntVar(value=self.settings["cooldown"]),
        }
        self.setting_value_labels = {}

        row = 1
        row = self._add_settings_slider(
            body, row, "Volume", "volume",
            from_=0.0, to=1.0,
            fmt=lambda v: f"{float(v):.0%}",
            note="playback volume, applied immediately",
            on_change=self._live_volume,
        )
        row = self._add_settings_spinner(
            body, row, "Closest", "closest",
            from_=1, to=50, increment=1,
            note="size of the candidate pool the picker samples from",
        )
        row = self._add_settings_spinner(
            body, row, "Cooldown", "cooldown",
            from_=0, to=500, increment=5,
            note="songs kept in the recent list and skipped as candidates",
        )

        # Read-only display of the music folder
        ttk.Label(body, text="Music folder").grid(
            row=row, column=0, sticky="w", pady=(10, 0))
        row += 1
        ttk.Label(body, text=self.settings.get("folder") or "(not set)",
                  foreground="#888", wraplength=320).grid(
            row=row, column=0, columnspan=2, sticky="w")
        row += 1

        btns = ttk.Frame(body)
        btns.grid(row=row, column=0, columnspan=2, pady=(24, 0), sticky="ew")
        ttk.Button(btns, text="Reset defaults",
                   command=self._reset_settings).pack(side="left")
        ttk.Button(btns, text="Back",
                   command=self.show_player).pack(side="right", padx=(4, 0))
        ttk.Button(btns, text="Apply & Save",
                   command=self._apply_settings).pack(side="right")

    def _add_settings_slider(self, parent, row, label, key, *,
                             from_, to, fmt, note=None, on_change=None):
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", pady=(10, 0))
        val_lbl = ttk.Label(parent, text=fmt(self.setting_vars[key].get()),
                            width=8, anchor="e")
        val_lbl.grid(row=row, column=1, sticky="e", pady=(10, 0))
        self.setting_value_labels[key] = (val_lbl, fmt)

        ttk.Scale(parent, from_=from_, to=to, orient="horizontal",
                  variable=self.setting_vars[key], length=320,
                  command=lambda _v, k=key, cb=on_change: self._on_setting_slider(k, cb),
                  ).grid(row=row + 1, column=0, columnspan=2, sticky="ew")

        if note:
            ttk.Label(parent, text=note, foreground="#888").grid(
                row=row + 2, column=0, columnspan=2, sticky="w")
            return row + 3
        return row + 2

    def _add_settings_spinner(self, parent, row, label, key, *,
                              from_, to, increment, note=None):
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", pady=(10, 0))
        ttk.Spinbox(parent, from_=from_, to=to, increment=increment, width=8,
                    textvariable=self.setting_vars[key]).grid(
            row=row, column=1, sticky="e", pady=(10, 0))
        if note:
            ttk.Label(parent, text=note, foreground="#888").grid(
                row=row + 1, column=0, columnspan=2, sticky="w")
            return row + 2
        return row + 1

    def _on_setting_slider(self, key, on_change=None):
        lbl, fmt = self.setting_value_labels[key]
        val = self.setting_vars[key].get()
        lbl.config(text=fmt(val))
        if on_change is not None:
            on_change(val)

    def _live_volume(self, value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        v = max(0.0, min(1.0, v))
        pygame.mixer.music.set_volume(v)
        self.settings["volume"] = v
        if self._volume_save_job is not None:
            try:
                self.root.after_cancel(self._volume_save_job)
            except Exception:
                pass
        self._volume_save_job = self.root.after(400, self._save_volume_deferred)

    def _save_volume_deferred(self):
        self._volume_save_job = None
        save_settings(self.settings)

    # ---------- View switching ----------

    def show_settings(self):
        self.settings_frame.tkraise()

    def show_player(self):
        self.player_frame.tkraise()

    # ---------- Settings apply / reset ----------

    def _apply_settings(self):
        try:
            new = {k: v.get() for k, v in self.setting_vars.items()}
        except tk.TclError:
            return

        merged = dict(self.settings)
        merged.update(new)
        self.settings = clamp_settings(merged)
        save_settings(self.settings)

        pygame.mixer.music.set_volume(self.settings["volume"])
        self.picker.closest = self.settings["closest"]
        self._resize_cooldown(self.settings["cooldown"])

        for k, v in self.settings.items():
            if k in self.setting_vars:
                self.setting_vars[k].set(v)
                if k in self.setting_value_labels:
                    self._on_setting_slider(k)

        print(f"Settings applied: {self.settings}")

    def _reset_settings(self):
        for k in self.setting_vars:
            v = DEFAULT_SETTINGS[k]
            self.setting_vars[k].set(v)
            if k in self.setting_value_labels:
                self._on_setting_slider(k)

    def _resize_cooldown(self, new_cooldown):
        if new_cooldown <= 0:
            keep = []
        else:
            keep = list(self.picker.recent)[-new_cooldown:]
        self.picker.recent = deque(keep, maxlen=new_cooldown)

    # ---------- Windows media controls ----------

    def setup_system_controls(self):
        self.media_player = MediaPlayer()
        self.media_player.command_manager.is_enabled = False
        self.smtc = self.media_player.system_media_transport_controls
        self.smtc.is_enabled = True
        self.smtc.is_play_enabled = True
        self.smtc.is_pause_enabled = True
        self.smtc.is_next_enabled = True
        self.smtc.is_previous_enabled = True
        self.smtc.add_button_pressed(self.on_system_button)

    def on_system_button(self, sender, args):
        buttons = SystemMediaTransportControlsButton
        mapping = {
            buttons.PLAY: "play",
            buttons.PAUSE: "pause",
            buttons.NEXT: "next",
            buttons.PREVIOUS: "previous",
        }
        command = mapping.get(args.button)
        if command:
            self.commands.put(command)

    def handle_command(self, command):
        if command == "next":
            self.next()
        elif command == "previous":
            self.previous()
        elif command == "play" and self.paused:
            self.toggle_pause()
        elif command == "pause" and not self.paused:
            self.toggle_pause()

    def update_system_info(self, path):
        updater = self.smtc.display_updater
        updater.type = MediaPlaybackType.MUSIC
        title = os.path.splitext(os.path.basename(path))[0]
        artist = ""
        try:
            tags = ID3(path)
            if "TIT2" in tags:
                title = str(tags["TIT2"])
            if "TPE1" in tags:
                artist = str(tags["TPE1"])
        except Exception:
            pass
        updater.music_properties.title = title
        updater.music_properties.artist = artist
        updater.update()

    def set_system_status(self):
        self.smtc.playback_status = (
            MediaPlaybackStatus.PAUSED if self.paused else MediaPlaybackStatus.PLAYING
        )

    # ---------- Player ----------

    def load_cover(self, path):
        try:
            tags = ID3(path)
            pictures = tags.getall("APIC")
            if pictures:
                img = Image.open(io.BytesIO(pictures[0].data)).convert("RGB")
                img = img.resize((COVER_SIZE, COVER_SIZE))
                return ImageTk.PhotoImage(img)
        except Exception:
            pass
        placeholder = Image.new("RGB", (COVER_SIZE, COVER_SIZE), "#2b2b2b")
        return ImageTk.PhotoImage(placeholder)

    def play(self, start=0.0):
        path = os.path.join(self.folder, self.current)
        self.length = max(MP3(path).info.length, 1.0)
        pygame.mixer.music.load(path)
        pygame.mixer.music.play(start=start)
        self.offset = start
        self.paused = False
        self.pause_button.config(text="⏸ Pause")
        self.slider.config(to=self.length)
        self.title_label.config(text=self.current)

        if start == 0.0:
            self.cover_img = self.load_cover(path)
            self.cover_label.config(image=self.cover_img)
            self.update_system_info(path)
        self.set_system_status()

    def position(self):
        return self.offset + pygame.mixer.music.get_pos() / 1000

    def next(self):
        chosen = self.picker.pick(self.current)
        if chosen is None:
            print("Picker found no song, replaying the current one")
            self.play()
            return
        self.history.append(self.current)
        self.current = chosen
        self.play()

    def previous(self):
        if self.position() > 3 or not self.history:
            self.play()
        else:
            self.current = self.history.pop()
            self.picker.unforget()
            self.play()

    def toggle_pause(self):
        if self.paused:
            pygame.mixer.music.unpause()
            self.pause_button.config(text="⏸ Pause")
        else:
            pygame.mixer.music.pause()
            self.pause_button.config(text="▶ Play")
        self.paused = not self.paused
        self.set_system_status()

    def on_swipe_press(self, event):
        self.swipe_start = (event.x_root, event.y_root)

    def on_swipe_release(self, event):
        if self.swipe_start is None:
            return
        dx = event.x_root - self.swipe_start[0]
        dy = event.y_root - self.swipe_start[1]
        self.swipe_start = None
        if abs(dx) >= SWIPE_DISTANCE and abs(dx) > 2 * abs(dy):
            if dx < 0:
                self.next()
            else:
                self.previous()

    def on_press(self, event):
        self.dragging = True

    def on_release(self, event):
        self.dragging = False
        target = float(self.slider.get())
        was_paused = self.paused
        self.play(start=target)
        if was_paused:
            self.toggle_pause()

    def update_loop(self):
        while not self.commands.empty():
            self.handle_command(self.commands.get())

        if not self.dragging:
            pos = min(self.position(), self.length)
            self.slider.set(pos)
            self.time_label.config(text=f"{format_time(pos)} / {format_time(self.length)}")
            if not self.paused and not pygame.mixer.music.get_busy():
                self.next()
        else:
            pos = float(self.slider.get())
            self.time_label.config(text=f"{format_time(pos)} / {format_time(self.length)}")
        self.root.after(200, self.update_loop)

    def close(self):
        if self._volume_save_job is not None:
            try:
                self.root.after_cancel(self._volume_save_job)
            except Exception:
                pass
            self._volume_save_job = None
        save_settings(self.settings)
        pygame.mixer.music.stop()
        self.smtc.is_enabled = False
        self.root.destroy()