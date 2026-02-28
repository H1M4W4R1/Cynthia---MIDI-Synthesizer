"""
VS1053B MIDI Player
Reads .mid files and streams them as raw MIDI bytes over a USB CDC serial port.

Requirements:
    pip install pyserial mido

Run:
    python midi_player.py
"""

import tkinter as tk
from tkinter import ttk, filedialog
import serial
import serial.tools.list_ports
import threading
import time
import os
import queue as _queue

# ── optional mido import (auto-install) ──────────────────────────────────────
try:
    import mido
except ImportError:
    import subprocess, sys
    print("[info] mido not found – installing…")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "mido"])
    import mido

try:
    import serial
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyserial"])
    import serial
    import serial.tools.list_ports


# ─────────────────────────────────────────────────────────────────────────────
#  MIDI serial sender
# ─────────────────────────────────────────────────────────────────────────────

class MIDISerial:
    def __init__(self):
        self._port: serial.Serial | None = None
        self._lock = threading.Lock()

    def connect(self, port_name: str, baud: int = 31250) -> None:
        with self._lock:
            if self._port and self._port.is_open:
                self._port.close()
            self._port = serial.Serial(port_name, baud, timeout=1)

    def disconnect(self) -> None:
        with self._lock:
            if self._port and self._port.is_open:
                self._port.close()
            self._port = None

    @property
    def connected(self) -> bool:
        return self._port is not None and self._port.is_open

    def send(self, data: bytes) -> None:
        with self._lock:
            if self._port and self._port.is_open:
                try:
                    self._port.write(data)
                except serial.SerialException:
                    pass

    def all_sound_off(self) -> None:
        for ch in range(16):
            self.send(bytes([0xB0 | ch, 120, 0]))


# ─────────────────────────────────────────────────────────────────────────────
#  MIDI playback engine  (runs in a background thread)
# ─────────────────────────────────────────────────────────────────────────────

class PlayerState:
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED  = "paused"


class MIDIPlayer:
    """
    Reads a mido MidiFile and streams MIDI bytes with correct timing.
    Supports: play, pause/resume, rewind, seek (silent), skip, volume.

    Design: a single background thread owns all playback.
    Any operation that changes position (seek/rewind/load) stops the current
    thread synchronously via _stop_thread(), then restarts from the new
    position.  This avoids every race condition that arises from trying to
    redirect a running thread mid-flight.
    """

    def __init__(self, midi_serial: MIDISerial):
        self._ser  = midi_serial

        # Protected by _lock
        self._state    = PlayerState.STOPPED
        self._messages : list[tuple[float, bytes]] = []
        self._duration : float = 0.0
        self._pos      : float = 0.0          # start position for next play()
        self._volume   : float = 1.0
        self._lock     = threading.Lock()

        # Thread control
        self._thread   : threading.Thread | None = None
        self._stop_evt = threading.Event()    # set → thread must exit ASAP
        self._pause_evt= threading.Event()
        self._pause_evt.set()                 # clear = paused, set = running

        # Callbacks (called from bg thread; must be set before play())
        self.on_position: callable | None = None   # (pos_s, dur_s) → None
        self.on_finished: callable | None = None   # () → None

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def position(self) -> float:
        return self._pos

    def load(self, path: str) -> float:
        """Stop playback, load a new file, reset position. Returns duration."""
        self._stop_thread()
        mid      = mido.MidiFile(path)
        messages = self._flatten(mid)
        with self._lock:
            self._messages = messages
            self._duration = messages[-1][0] if messages else 0.0
            self._pos      = 0.0
            self._state    = PlayerState.STOPPED
        return self._duration

    def play(self) -> None:
        """Start or resume playback."""
        with self._lock:
            if self._state == PlayerState.PLAYING:
                return
            if self._state == PlayerState.PAUSED:
                self._state = PlayerState.PLAYING
                self._pause_evt.set()
                return
            # STOPPED → launch thread from current _pos
            self._state = PlayerState.PLAYING
        self._launch_thread()

    def pause(self) -> None:
        with self._lock:
            if self._state != PlayerState.PLAYING:
                return
            self._state = PlayerState.PAUSED
        self._pause_evt.clear()
        self._ser.all_sound_off()

    def stop(self) -> None:
        """Stop and reset position to 0."""
        self._stop_thread()
        with self._lock:
            self._pos   = 0.0
            self._state = PlayerState.STOPPED
        self._ser.all_sound_off()

    def seek(self, seconds: float) -> None:
        """
        Jump to position silently:
        1. Stop current thread.
        2. Update _pos.
        3. Restart thread if we were playing, otherwise stay stopped.
        """
        with self._lock:
            was_playing = (self._state == PlayerState.PLAYING)
            was_paused  = (self._state == PlayerState.PAUSED)
        self._stop_thread()
        with self._lock:
            self._pos = max(0.0, min(seconds, self._duration))
        self._ser.all_sound_off()
        if was_playing or was_paused:
            with self._lock:
                self._state = PlayerState.PLAYING
            self._launch_thread()

    def rewind(self) -> None:
        self.seek(0.0)

    def set_volume(self, v: float) -> None:
        with self._lock:
            self._volume = max(0.0, min(1.0, v))

    def skip(self) -> None:
        """Stop current track; caller decides what to play next."""
        self._stop_thread()
        with self._lock:
            self._pos   = 0.0
            self._state = PlayerState.STOPPED
        self._ser.all_sound_off()

    # ── thread management ─────────────────────────────────────────────────────

    def _stop_thread(self) -> None:
        """Signal the bg thread to stop and block until it exits."""
        self._stop_evt.set()
        self._pause_evt.set()   # unblock if sleeping in pause
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread   = None
        self._stop_evt.clear()

    def _launch_thread(self) -> None:
        self._stop_evt.clear()
        self._pause_evt.set()   # ensure not paused on start
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ── playback loop ─────────────────────────────────────────────────────────

    def _run(self) -> None:
        with self._lock:
            messages = self._messages[:]
            start_pos = self._pos
            duration  = self._duration

        # Find the first message at or after start_pos (silent seek already done)
        idx = self._bisect(messages, start_pos)
        t0  = time.perf_counter() - start_pos   # wall-clock anchor

        i = idx
        while i < len(messages):
            if self._stop_evt.is_set():
                break

            msg_time, raw = messages[i]

            # ── wait loop: sleep in small increments, honouring pause/stop ──
            while True:
                if self._stop_evt.is_set():
                    break

                # Pause: block here without burning CPU
                if not self._pause_evt.is_set():
                    # Record position before blocking
                    with self._lock:
                        self._pos = msg_time
                    self._pause_evt.wait()
                    if self._stop_evt.is_set():
                        break
                    # Re-anchor wall clock after unpause
                    t0 = time.perf_counter() - msg_time

                elapsed = time.perf_counter() - t0
                delta   = msg_time - elapsed
                if delta <= 0:
                    break
                time.sleep(min(delta, 0.002))   # 2 ms granularity

            if self._stop_evt.is_set():
                with self._lock:
                    self._pos = msg_time
                break

            # ── send ──────────────────────────────────────────────────────
            self._ser.send(self._apply_volume(raw))

            # ── update reported position ───────────────────────────────────
            with self._lock:
                self._pos = msg_time
            if self.on_position:
                self.on_position(msg_time, duration)

            i += 1

        # ── thread exiting ────────────────────────────────────────────────
        if not self._stop_evt.is_set():
            # Reached end of track naturally
            with self._lock:
                self._state = PlayerState.STOPPED
                self._pos   = 0.0
            self._ser.all_sound_off()
            if self.on_finished:
                self.on_finished()

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _bisect(messages: list, pos: float) -> int:
        """Return index of first message with time >= pos."""
        lo, hi = 0, len(messages)
        while lo < hi:
            mid = (lo + hi) // 2
            if messages[mid][0] < pos:
                lo = mid + 1
            else:
                hi = mid
        return lo

    @staticmethod
    def _flatten(mid: mido.MidiFile) -> list[tuple[float, bytes]]:
        """
        Merge all tracks → sorted (absolute_seconds, raw_bytes) list.

        Steps:
          1. Accumulate each track's delta-ticks into absolute ticks.
          2. Merge & sort by absolute tick.
          3. Walk in tick order, tracking tempo changes to convert ticks→seconds.
        """
        ticks_per_beat = mid.ticks_per_beat

        # Step 1 & 2: absolute-tick merge
        merged: list[tuple[int, object]] = []
        for track in mid.tracks:
            abs_tick = 0
            for msg in track:
                abs_tick += msg.time        # delta ticks
                merged.append((abs_tick, msg))
        merged.sort(key=lambda x: x[0])

        # Step 3: ticks → seconds with live tempo map
        tempo      = 500_000    # µs/beat  (120 BPM default)
        last_tick  = 0
        elapsed_us = 0.0
        result     = []

        for abs_tick, msg in merged:
            delta_tick  = abs_tick - last_tick
            elapsed_us += delta_tick * (tempo / ticks_per_beat)
            last_tick   = abs_tick

            if msg.is_meta:
                if msg.type == "set_tempo":
                    tempo = msg.tempo
                continue

            raw = bytes(msg.bytes())
            if raw:
                result.append((elapsed_us / 1_000_000.0, raw))

        return result

    def _apply_volume(self, raw: bytes) -> bytes:
        """Scale CC#7 (main volume) by the current volume factor."""
        if len(raw) >= 3 and (raw[0] & 0xF0) == 0xB0 and raw[1] == 7:
            with self._lock:
                vol = self._volume
            scaled = max(0, min(127, int(raw[2] * vol)))
            return bytes([raw[0], raw[1], scaled])
        return raw


# ─────────────────────────────────────────────────────────────────────────────
#  Colour palette  (matches midi_controller.py)
# ─────────────────────────────────────────────────────────────────────────────

C_BG        = "#0d0f14"
C_PANEL     = "#13161f"
C_CARD      = "#1c202e"
C_BORDER    = "#262a38"
C_ACCENT    = "#00e5ff"
C_ACCENT2   = "#005f70"
C_TEXT      = "#dde3ec"
C_MUTED     = "#55607a"
C_OK        = "#00d68f"
C_ERR       = "#ff4060"
C_TROUGH    = "#1c202e"
C_HIGHLIGHT = "#1e3540"   # selected queue row
C_PLAYING   = "#002a35"   # currently-playing row tint

FONT_MONO   = ("Courier New", 10)
FONT_MONO_S = ("Courier New", 8)
FONT_MONO_M = ("Courier New", 12, "bold")
FONT_MONO_L = ("Courier New", 18, "bold")
FONT_MONO_XL= ("Courier New", 26, "bold")


# ─────────────────────────────────────────────────────────────────────────────
#  Reusable widgets
# ─────────────────────────────────────────────────────────────────────────────

class Divider(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C_BORDER, height=1, **kw)


class SectionLabel(tk.Label):
    def __init__(self, parent, text, **kw):
        super().__init__(parent, text=text.upper(),
                         font=("Courier New", 7, "bold"),
                         bg=C_PANEL, fg=C_ACCENT2, anchor="w", **kw)


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str):
        widget.bind("<Enter>", lambda _: self._show(widget))
        widget.bind("<Leave>", lambda _: self._hide())
        self._text = text
        self._win: tk.Toplevel | None = None

    def _show(self, widget):
        x = widget.winfo_rootx() + 24
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        self._win = tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self._text, font=FONT_MONO_S,
                 bg=C_CARD, fg=C_MUTED, bd=1, relief="flat",
                 highlightbackground=C_BORDER, highlightthickness=1,
                 padx=8, pady=4).pack()

    def _hide(self):
        if self._win:
            self._win.destroy()
            self._win = None


class StatusBar(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C_CARD, pady=6, **kw)
        self._dot = tk.Label(self, text="●", font=("Courier New", 11),
                             bg=C_CARD, fg=C_MUTED)
        self._dot.pack(side="left", padx=(12, 6))
        self._msg = tk.Label(self, text="No file loaded",
                             font=FONT_MONO_S, bg=C_CARD, fg=C_MUTED, anchor="w")
        self._msg.pack(side="left", fill="x", expand=True)

    def ok(self, text):
        self._dot.config(fg=C_OK);  self._msg.config(text=text, fg=C_OK)

    def err(self, text):
        self._dot.config(fg=C_ERR); self._msg.config(text=text, fg=C_ERR)

    def info(self, text):
        self._dot.config(fg=C_MUTED); self._msg.config(text=text, fg=C_MUTED)


# ─────────────────────────────────────────────────────────────────────────────
#  Queue list widget
# ─────────────────────────────────────────────────────────────────────────────

class QueueList(tk.Frame):
    """Scrollable list of queued MIDI files."""

    ROW_H = 32

    def __init__(self, parent, on_double_click=None, **kw):
        super().__init__(parent, bg=C_CARD, **kw)
        self._on_dc   = on_double_click
        self._items   : list[str] = []   # full paths
        self._playing : int       = -1   # index currently playing
        self._selected: int       = -1

        self._canvas = tk.Canvas(self, bg=C_CARD, bd=0, highlightthickness=0,
                                 width=500)
        self._scroll = ttk.Scrollbar(self, orient="vertical",
                                     command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scroll.set)

        self._inner = tk.Frame(self._canvas, bg=C_CARD)
        self._window = self._canvas.create_window((0, 0), window=self._inner,
                                                   anchor="nw")

        self._canvas.pack(side="left", fill="both", expand=True)
        self._scroll.pack(side="right", fill="y")

        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<MouseWheel>",
                          lambda e: self._canvas.yview_scroll(-1*(e.delta//120), "units"))

    def _on_inner_configure(self, _=None):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, e):
        self._canvas.itemconfig(self._window, width=e.width)

    def add(self, path: str):
        idx = len(self._items)
        self._items.append(path)
        self._make_row(idx)

    def _make_row(self, idx: int):
        path  = self._items[idx]
        name  = os.path.basename(path)
        frame = tk.Frame(self._inner, bg=C_CARD, height=self.ROW_H,
                         cursor="hand2")
        frame.pack(fill="x", expand=True)
        frame.pack_propagate(False)

        # index label
        num = tk.Label(frame, text=f"{idx+1:02d}",
                       font=FONT_MONO_S, bg=C_CARD, fg=C_MUTED,
                       width=4, anchor="e")
        num.pack(side="left", padx=(10, 6))

        # file name
        name_lbl = tk.Label(frame, text=name, font=FONT_MONO,
                             bg=C_CARD, fg=C_TEXT, anchor="w")
        name_lbl.pack(side="left", fill="x", expand=True)

        # remove button
        rm = tk.Label(frame, text="✕", font=("Courier New", 9),
                      bg=C_CARD, fg=C_MUTED, cursor="hand2", padx=10)
        rm.pack(side="right")

        # separator
        sep = tk.Frame(self._inner, bg=C_BORDER, height=1)
        sep.pack(fill="x")

        widgets = (frame, num, name_lbl, rm, sep)

        # bindings
        for w in (frame, num, name_lbl, rm):
            w.bind("<Button-1>",   lambda e, i=idx: self._select(i))
            w.bind("<Double-1>",   lambda e, i=idx: self._double(i))
        rm.bind("<Button-1>",      lambda e, i=idx, ws=widgets: self._remove(i, ws))
        rm.bind("<Enter>",         lambda e, w=rm: w.config(fg=C_ERR))
        rm.bind("<Leave>",         lambda e, w=rm: w.config(fg=C_MUTED))

        self._apply_row_style(idx, frame, num, name_lbl)

        # store for later style updates
        if not hasattr(self, '_rows'):
            self._rows = []
        self._rows.append((frame, num, name_lbl, rm, sep))

    def _select(self, idx: int):
        self._selected = idx
        self._refresh_styles()

    def _double(self, idx: int):
        self._select(idx)
        if self._on_dc:
            self._on_dc(idx, self._items[idx])

    def _remove(self, idx: int, widgets):
        if idx < len(self._items):
            self._items.pop(idx)
        for w in widgets:
            w.destroy()
        if hasattr(self, '_rows') and idx < len(self._rows):
            self._rows.pop(idx)
        # re-number remaining
        self._renumber()

    def _renumber(self):
        if not hasattr(self, '_rows'):
            return
        for i, (frame, num, name_lbl, rm, sep) in enumerate(self._rows):
            try:
                num.config(text=f"{i+1:02d}")
            except tk.TclError:
                pass

    def _apply_row_style(self, idx, frame, num, name_lbl):
        if idx == self._playing:
            bg, fg = C_PLAYING, C_ACCENT
        elif idx == self._selected:
            bg, fg = C_HIGHLIGHT, C_TEXT
        else:
            bg, fg = C_CARD, C_TEXT
            num_fg = C_MUTED
            num.config(bg=bg, fg=num_fg)
        for w in (frame, name_lbl):
            w.config(bg=bg)
        name_lbl.config(fg=fg)

    def _refresh_styles(self):
        if not hasattr(self, '_rows'):
            return
        for i, (frame, num, name_lbl, rm, sep) in enumerate(self._rows):
            try:
                self._apply_row_style(i, frame, num, name_lbl)
            except tk.TclError:
                pass

    def set_playing(self, idx: int):
        self._playing = idx
        self._refresh_styles()

    def clear_playing(self):
        self._playing = -1
        self._refresh_styles()

    @property
    def items(self) -> list[str]:
        return self._items[:]

    @property
    def selected(self) -> int:
        return self._selected

    def __len__(self):
        return len(self._items)


# ─────────────────────────────────────────────────────────────────────────────
#  Main application
# ─────────────────────────────────────────────────────────────────────────────

def fmt_time(s: float) -> str:
    s = max(0.0, s)
    m = int(s) // 60
    return f"{m:02d}:{int(s) % 60:02d}"


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("VS1053B · MIDI Player")
        self.configure(bg=C_BG)
        self.resizable(False, False)

        self._ser    = MIDISerial()
        self._player = MIDIPlayer(self._ser)

        self._player.on_position = self._cb_position
        self._player.on_finished = self._cb_finished

        # playback queue
        self._queue_paths : list[str] = []
        self._current_idx : int       = -1

        # UI state
        self._scrubbing       = False   # user dragging the time slider
        self._pos_var         = tk.DoubleVar(value=0.0)
        self._vol_var         = tk.IntVar(value=100)
        self._duration        = 0.0
        self._port_var        = tk.StringVar()

        self._apply_ttk_styles()
        self._build_ui()
        self._refresh_ports()
        self._start_port_watcher()

        self._port_var.trace_add("write", self._on_port_changed)

        # periodic UI tick
        self._tick()

    # ── TTK styles ────────────────────────────────────────────────────────────

    def _apply_ttk_styles(self):
        s = ttk.Style()
        s.theme_use("clam")

        for name in ("Time.Horizontal.TScale", "Vol.Horizontal.TScale"):
            s.configure(name,
                        background=C_PANEL, troughcolor=C_TROUGH,
                        sliderthickness=14, sliderrelief="flat",
                        bordercolor=C_BORDER, lightcolor=C_ACCENT2,
                        darkcolor=C_ACCENT2)

        s.configure("TScrollbar",
                    background=C_CARD, troughcolor=C_CARD,
                    arrowcolor=C_MUTED, bordercolor=C_BORDER,
                    darkcolor=C_CARD, lightcolor=C_CARD)
        s.map("TScrollbar", background=[("active", C_BORDER)])

        s.configure("TCombobox",
                    fieldbackground=C_CARD, background=C_CARD,
                    foreground=C_TEXT, arrowcolor=C_ACCENT,
                    bordercolor=C_BORDER, lightcolor=C_BORDER,
                    darkcolor=C_BORDER, selectbackground=C_ACCENT2,
                    selectforeground=C_BG)
        s.map("TCombobox",
              fieldbackground=[("readonly", C_CARD)],
              selectbackground=[("readonly", C_ACCENT2)])

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Header ───────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=C_BG, padx=28, pady=16)
        hdr.pack(fill="x")
        tk.Label(hdr, text="VS1053B", font=("Courier New", 24, "bold"),
                 bg=C_BG, fg=C_ACCENT).pack(side="left")
        tk.Label(hdr, text="  ·  MIDI PLAYER",
                 font=("Courier New", 12), bg=C_BG, fg=C_MUTED).pack(
            side="left", pady=(9, 0))
        tk.Frame(self, bg=C_ACCENT, height=1).pack(fill="x", padx=28)

        # ── Main panel ────────────────────────────────────────────────────────
        panel = tk.Frame(self, bg=C_PANEL, padx=24, pady=20)
        panel.pack(fill="both", padx=18, pady=12)

        # ── Serial port row ───────────────────────────────────────────────────
        port_row = tk.Frame(panel, bg=C_PANEL)
        port_row.pack(fill="x", pady=(0, 14))

        SectionLabel(panel, "Serial Port").pack(anchor="w", pady=(0, 5))
        pr2 = tk.Frame(panel, bg=C_PANEL)
        pr2.pack(fill="x", pady=(0, 4))

        self._port_combo = ttk.Combobox(pr2, textvariable=self._port_var,
                                        state="readonly", width=22, font=FONT_MONO)
        self._port_combo.pack(side="left", padx=(0, 6))

        self._flat_btn(pr2, "⟳", self._refresh_ports, "Re-scan serial ports",
                       fg=C_ACCENT2, hover_fg=C_ACCENT).pack(side="left", padx=(0, 10))

        self._conn_btn = tk.Button(pr2, text="CONNECT",
                                   font=("Courier New", 9, "bold"),
                                   bg=C_ACCENT2, fg=C_BG, bd=0,
                                   activebackground=C_ACCENT, activeforeground=C_BG,
                                   padx=14, pady=6, cursor="hand2",
                                   command=self._toggle_connection)
        self._conn_btn.pack(side="left")

        Divider(panel).pack(fill="x", pady=14)

        # ── Now-playing info ──────────────────────────────────────────────────
        np_frame = tk.Frame(panel, bg=C_PANEL)
        np_frame.pack(fill="x", pady=(0, 12))

        self._track_lbl = tk.Label(np_frame, text="No track loaded",
                                   font=FONT_MONO_M, bg=C_PANEL, fg=C_TEXT,
                                   anchor="w")
        self._track_lbl.pack(side="left", fill="x", expand=True)

        self._time_lbl = tk.Label(np_frame, text="00:00 / 00:00",
                                  font=FONT_MONO_S, bg=C_PANEL, fg=C_MUTED,
                                  anchor="e")
        self._time_lbl.pack(side="right")

        # ── Time scrubber ─────────────────────────────────────────────────────
        self._time_slider = ttk.Scale(panel, from_=0, to=1,
                                      orient="horizontal",
                                      variable=self._pos_var,
                                      style="Time.Horizontal.TScale",
                                      command=self._on_time_slide)
        self._time_slider.pack(fill="x", pady=(0, 2))
        self._time_slider.bind("<ButtonPress-1>",   self._scrub_start)
        self._time_slider.bind("<ButtonRelease-1>", self._scrub_end)

        # ── Transport buttons ─────────────────────────────────────────────────
        Divider(panel).pack(fill="x", pady=14)

        transport = tk.Frame(panel, bg=C_PANEL)
        transport.pack(pady=(0, 4))

        btn_specs = [
            ("📂", "Open MIDI file(s)",            self._open_files,   C_ACCENT,  C_ACCENT),
            ("⏮",  "Rewind to beginning",           self._rewind,       C_TEXT,    C_ACCENT),
            ("▶",  "Play / Pause",                  self._play_pause,   C_ACCENT,  C_ACCENT),
            ("⏭",  "Skip to next track",            self._skip_next,    C_TEXT,    C_ACCENT),
        ]

        self._play_btn_ref = None
        for i, (icon, tip, cmd, fg, hfg) in enumerate(btn_specs):
            b = tk.Button(transport, text=icon,
                          font=("Courier New", 16),
                          bg=C_CARD, fg=fg, bd=0,
                          activebackground=C_BORDER,
                          activeforeground=hfg,
                          padx=18, pady=10, cursor="hand2",
                          command=cmd)
            b.grid(row=0, column=i, padx=5)
            Tooltip(b, tip)
            if icon == "▶":
                self._play_btn_ref = b

        Divider(panel).pack(fill="x", pady=14)

        # ── Volume row ────────────────────────────────────────────────────────
        vol_row = tk.Frame(panel, bg=C_PANEL)
        vol_row.pack(fill="x", pady=(0, 4))

        SectionLabel(vol_row, "Volume").pack(side="left", padx=(0, 12))

        self._vol_slider = ttk.Scale(vol_row, from_=0, to=127,
                                     orient="horizontal", length=340,
                                     variable=self._vol_var,
                                     style="Vol.Horizontal.TScale",
                                     command=self._on_vol_slide)
        self._vol_slider.pack(side="left", padx=(0, 12))

        self._vol_lbl = tk.Label(vol_row, text="100",
                                 font=FONT_MONO_L, bg=C_PANEL, fg=C_TEXT, width=4)
        self._vol_lbl.pack(side="left")

        Divider(panel).pack(fill="x", pady=14)

        # ── Queue ─────────────────────────────────────────────────────────────
        SectionLabel(panel, "Queue").pack(anchor="w", pady=(0, 6))

        self._queue_widget = QueueList(panel, on_double_click=self._queue_double_click,
                                       height=200)
        self._queue_widget.pack(fill="both", expand=True)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = StatusBar(self)
        self._status.pack(fill="x", padx=18, pady=(0, 12))

    # ── helper ───────────────────────────────────────────────────────────────

    def _flat_btn(self, parent, text, cmd, tip="",
                  fg=C_TEXT, hover_fg=C_ACCENT) -> tk.Button:
        b = tk.Button(parent, text=text, font=("Courier New", 14),
                      bg=C_PANEL, fg=fg, bd=0,
                      activebackground=C_PANEL, activeforeground=hover_fg,
                      cursor="hand2", command=cmd)
        if tip:
            Tooltip(b, tip)
        return b

    # ── port management ───────────────────────────────────────────────────────

    def _refresh_ports(self, *_):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if list(self._port_combo["values"]) == ports:
            return
        self._port_combo["values"] = ports
        if ports and self._port_var.get() not in ports:
            self._port_var.set(ports[0])
        elif not ports:
            self._port_var.set("")

    def _start_port_watcher(self):
        def _loop():
            while True:
                time.sleep(3)
                ports = [p.device for p in serial.tools.list_ports.comports()]
                if list(ports) != list(self._port_combo["values"]):
                    self.after(0, self._refresh_ports)
        threading.Thread(target=_loop, daemon=True).start()

    def _toggle_connection(self):
        if self._ser.connected:
            self._player.stop()
            self._ser.disconnect()
            self._conn_btn.config(text="CONNECT", bg=C_ACCENT2)
            self._status.info("Disconnected")
        else:
            port = self._port_var.get()
            if not port:
                self._status.err("No port selected"); return
            try:
                self._ser.connect(port)
                self._conn_btn.config(text="DISCONNECT", bg=C_ERR)
                self._status.ok(f"Connected  →  {port}")
            except serial.SerialException as e:
                self._status.err(f"Connection failed: {e}")

    def _on_port_changed(self, *_):
        if self._ser.connected:
            new = self._port_var.get()
            try:
                self._ser.connect(new)
                self._status.ok(f"Switched to {new}")
            except serial.SerialException as e:
                self._ser.disconnect()
                self._conn_btn.config(text="CONNECT", bg=C_ACCENT2)
                self._status.err(f"Port switch failed: {e}")

    # ── file / queue ──────────────────────────────────────────────────────────

    def _open_files(self):
        paths = filedialog.askopenfilenames(
            title="Open MIDI File(s)",
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")])
        for p in paths:
            self._queue_widget.add(p)
            self._queue_paths.append(p)
        if paths:
            self._status.info(f"Added {len(paths)} file(s) to queue")
            if self._current_idx == -1:
                self._status.info("Press ▶ to start playback")

    def _queue_double_click(self, idx: int, path: str):
        self._play_track(idx)

    # ── transport ─────────────────────────────────────────────────────────────

    def _play_pause(self):
        if self._player.state == PlayerState.PLAYING:
            self._player.pause()
            if self._play_btn_ref:
                self._play_btn_ref.config(text="▶")
            self._status.info("Paused")
        elif self._player.state == PlayerState.PAUSED:
            self._player.play()
            if self._play_btn_ref:
                self._play_btn_ref.config(text="⏸")
            self._status.ok("Resumed")
        else:
            # STOPPED – start from current or first track
            paths = self._queue_widget.items
            if not paths:
                self._status.err("Queue is empty – open a MIDI file first")
                return
            idx = max(0, self._current_idx) if self._current_idx >= 0 else 0
            self._play_track(idx)

    def _play_track(self, idx: int):
        paths = self._queue_widget.items
        if idx < 0 or idx >= len(paths):
            return
        path = paths[idx]
        # load() stops any running thread synchronously before loading
        try:
            dur = self._player.load(path)
        except Exception as e:
            self._status.err(f"Load error: {e}")
            return
        self._duration = dur
        self._current_idx = idx
        self._queue_widget.set_playing(idx)
        name = os.path.basename(path)
        self._track_lbl.config(text=name)
        self._time_slider.config(to=max(dur, 1.0))
        self._pos_var.set(0.0)
        self._time_lbl.config(text=f"00:00 / {fmt_time(dur)}")
        self._player.set_volume(self._vol_var.get() / 127)
        self._player.play()
        if self._play_btn_ref:
            self._play_btn_ref.config(text="⏸")
        self._status.ok(f"Playing  ·  {name}")

    def _rewind(self):
        self._player.rewind()
        self._pos_var.set(0.0)
        self._time_lbl.config(text=f"00:00 / {fmt_time(self._duration)}")

    def _skip_next(self):
        paths = self._queue_widget.items
        if not paths:
            return
        nxt = self._current_idx + 1
        if nxt < len(paths):
            self._play_track(nxt)
        else:
            # End of queue: stop cleanly without triggering on_finished again
            self._player.skip()
            self._current_idx = -1
            self._queue_widget.clear_playing()
            self._pos_var.set(0.0)
            if self._play_btn_ref:
                self._play_btn_ref.config(text="▶")
            self._status.info("End of queue")

    # ── time scrubber ─────────────────────────────────────────────────────────

    def _scrub_start(self, _=None):
        self._scrubbing = True

    def _scrub_end(self, _=None):
        if self._duration > 0:
            self._player.seek(self._pos_var.get())
            # Update play button if seek restarted playback
            if self._player.state == PlayerState.PLAYING and self._play_btn_ref:
                self._play_btn_ref.config(text="⏸")
        self._scrubbing = False

    def _on_time_slide(self, val=None):
        v = float(val) if val is not None else self._pos_var.get()
        self._time_lbl.config(text=f"{fmt_time(v)} / {fmt_time(self._duration)}")

    # ── volume ────────────────────────────────────────────────────────────────

    def _on_vol_slide(self, val=None):
        v = int(float(val)) if val is not None else self._vol_var.get()
        self._vol_lbl.config(text=str(v))
        self._player.set_volume(v / 127)

    # ── player callbacks (from background thread → schedule on main thread) ───

    def _cb_position(self, pos: float, dur: float):
        self.after(0, self._update_position, pos, dur)

    def _update_position(self, pos: float, dur: float):
        if not self._scrubbing:
            self._pos_var.set(pos)
        self._time_lbl.config(text=f"{fmt_time(pos)} / {fmt_time(dur)}")

    def _cb_finished(self):
        self.after(0, self._on_track_finished)

    def _on_track_finished(self):
        if self._play_btn_ref:
            self._play_btn_ref.config(text="▶")
        # auto-advance queue
        paths = self._queue_widget.items
        nxt   = self._current_idx + 1
        if nxt < len(paths):
            self._play_track(nxt)
        else:
            self._queue_widget.clear_playing()
            self._current_idx = -1
            self._status.info("Queue finished")

    # ── periodic tick ─────────────────────────────────────────────────────────

    def _tick(self):
        # keep time display live even without position callbacks
        if self._player.state == PlayerState.PLAYING and not self._scrubbing:
            pos = self._player.position
            self._pos_var.set(pos)
            self._time_lbl.config(
                text=f"{fmt_time(pos)} / {fmt_time(self._duration)}")
        self.after(200, self._tick)


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()