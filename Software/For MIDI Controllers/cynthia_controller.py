"""
VS1053B MIDI Device Controller
USB CDC Serial Interface

Requirements: pyserial  (`pip install pyserial`)
Run:          python midi_controller.py
"""

import tkinter as tk
from tkinter import ttk
import serial
import serial.tools.list_ports
import threading
import time

# ─────────────────────────────────────────────────────────────────────────────
#  General MIDI instrument list  (VS1053B GM bank, program numbers 0–127)
# ─────────────────────────────────────────────────────────────────────────────
GM_INSTRUMENTS = [
    # Piano
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2",
    "Harpsichord", "Clavinet",
    # Chromatic Perc
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    # Organ
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    # Guitar
    "Nylon String Guitar", "Steel String Guitar", "Jazz Guitar",
    "Clean Electric Guitar", "Muted Electric Guitar", "Overdriven Guitar",
    "Distortion Guitar", "Guitar Harmonics",
    # Bass
    "Acoustic Bass", "Fingered Electric Bass", "Picked Electric Bass",
    "Fretless Bass", "Slap Bass 1", "Slap Bass 2",
    "Synth Bass 1", "Synth Bass 2",
    # Strings
    "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    # Ensemble
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2",
    "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    # Brass
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    # Reed
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet",
    # Pipe
    "Piccolo", "Flute", "Recorder", "Pan Flute",
    "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    # Synth Lead
    "Square Wave Lead", "Sawtooth Lead", "Calliope Lead", "Chiff Lead",
    "Charang Lead", "Voice Lead", "Fifths Lead", "Bass+Lead",
    # Synth Pad
    "New Age Pad", "Warm Pad", "Polysynth Pad", "Choir Pad",
    "Bowed Pad", "Metallic Pad", "Halo Pad", "Sweep Pad",
    # Synth FX
    "Rain FX", "Soundtrack FX", "Crystal FX", "Atmosphere FX",
    "Brightness FX", "Goblins FX", "Echoes FX", "Sci-fi FX",
    # Ethnic
    "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bag Pipe", "Fiddle", "Shanai",
    # Percussive
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    # Sound Effects
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]

# ─────────────────────────────────────────────────────────────────────────────
#  Serial / MIDI helper
# ─────────────────────────────────────────────────────────────────────────────

class MIDISerial:
    """Thread-safe wrapper for sending raw MIDI bytes over a serial port."""

    def __init__(self):
        self._port: serial.Serial | None = None
        self._lock = threading.Lock()

    # ── connection ────────────────────────────────────────────────────────────

    def connect(self, port_name: str, baud: int = 31250) -> None:
        """Open (or re-open) a serial port. Raises serial.SerialException on failure."""
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

    # ── raw write ─────────────────────────────────────────────────────────────

    def send(self, data: bytes) -> None:
        with self._lock:
            if self._port and self._port.is_open:
                self._port.write(data)

    # ── MIDI messages ─────────────────────────────────────────────────────────

    def program_change(self, channel: int, program: int) -> None:
        """Cx pp  –  select instrument.  channel 0-15, program 0-127."""
        self.send(bytes([0xC0 | (channel & 0x0F), program & 0x7F]))

    def control_change(self, channel: int, control: int, value: int) -> None:
        """Bx cc vv  –  generic CC.  channel 0-15, control 0-127, value 0-127."""
        self.send(bytes([0xB0 | (channel & 0x0F), control & 0x7F, value & 0x7F]))

    def set_volume(self, channel: int, volume: int) -> None:
        """CC #7 = main channel volume (0-127)."""
        self.control_change(channel, 7, volume)

    def all_sound_off(self, channel: int) -> None:
        """CC #120 = All Sound Off."""
        self.control_change(channel, 120, 0)

    def reset_all_controllers(self, channel: int) -> None:
        """CC #121 = Reset All Controllers."""
        self.control_change(channel, 121, 0)


# ─────────────────────────────────────────────────────────────────────────────
#  Colour palette & shared fonts
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

FONT_MONO   = ("Courier New", 10)
FONT_MONO_S = ("Courier New", 8)
FONT_MONO_L = ("Courier New", 20, "bold")
FONT_MONO_XL= ("Courier New", 30, "bold")


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
                         bg=C_PANEL, fg=C_ACCENT2,
                         anchor="w", **kw)


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str):
        widget.bind("<Enter>", lambda _: self._show(widget))
        widget.bind("<Leave>", lambda _: self._hide())
        self._text = text
        self._win: tk.Toplevel | None = None

    def _show(self, widget: tk.Widget):
        x = widget.winfo_rootx() + 24
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        self._win = tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self._text, font=FONT_MONO_S,
                 bg=C_CARD, fg=C_MUTED,
                 bd=1, relief="flat",
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
        self._msg = tk.Label(self, text="Not connected",
                             font=FONT_MONO_S, bg=C_CARD, fg=C_MUTED,
                             anchor="w")
        self._msg.pack(side="left", fill="x", expand=True)

    def ok(self, text: str):
        self._dot.config(fg=C_OK)
        self._msg.config(text=text, fg=C_OK)

    def err(self, text: str):
        self._dot.config(fg=C_ERR)
        self._msg.config(text=text, fg=C_ERR)

    def info(self, text: str):
        self._dot.config(fg=C_MUTED)
        self._msg.config(text=text, fg=C_MUTED)


# ─────────────────────────────────────────────────────────────────────────────
#  Main application window
# ─────────────────────────────────────────────────────────────────────────────

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("VS1053B · MIDI Controller")
        self.configure(bg=C_BG)
        self.resizable(False, False)

        self.midi = MIDISerial()
        self._connected = False

        # ── tracked state ──────────────────────────────────────────────────
        self._port_var       = tk.StringVar()
        self._channel_var    = tk.IntVar(value=1)        # 1-16 (display)
        self._volume_var     = tk.IntVar(value=100)
        self._instrument_var = tk.StringVar(value=GM_INSTRUMENTS[0])

        self._apply_ttk_styles()
        self._build_ui()
        self._refresh_ports()
        self._start_port_watcher()

        # Watch for port combo changes while connected
        self._port_var.trace_add("write", self._on_port_changed)

    # ── TTK theming ───────────────────────────────────────────────────────────

    def _apply_ttk_styles(self):
        s = ttk.Style()
        s.theme_use("clam")

        s.configure("TCombobox",
                    fieldbackground=C_CARD, background=C_CARD,
                    foreground=C_TEXT, arrowcolor=C_ACCENT,
                    bordercolor=C_BORDER, lightcolor=C_BORDER,
                    darkcolor=C_BORDER, insertcolor=C_TEXT,
                    selectbackground=C_ACCENT2, selectforeground=C_BG)
        s.map("TCombobox",
              fieldbackground=[("readonly", C_CARD)],
              selectbackground=[("readonly", C_ACCENT2)])

        s.configure("Vol.Horizontal.TScale",
                    background=C_PANEL, troughcolor=C_TROUGH,
                    sliderthickness=16, sliderrelief="flat",
                    bordercolor=C_BORDER, lightcolor=C_ACCENT2,
                    darkcolor=C_ACCENT2)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Header ───────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=C_BG, padx=28, pady=18)
        hdr.pack(fill="x")
        tk.Label(hdr, text="VS1053B", font=("Courier New", 24, "bold"),
                 bg=C_BG, fg=C_ACCENT).pack(side="left")
        tk.Label(hdr, text="  ·  MIDI CONTROLLER",
                 font=("Courier New", 12),
                 bg=C_BG, fg=C_MUTED).pack(side="left", pady=(10, 0))
        tk.Frame(self, bg=C_ACCENT, height=1).pack(fill="x", padx=28)

        # ── Main card ─────────────────────────────────────────────────────────
        card = tk.Frame(self, bg=C_PANEL, padx=28, pady=22)
        card.pack(fill="both", padx=20, pady=14)

        row = 0

        # ── Serial port section ──────────────────────────────────────────────
        SectionLabel(card, "Serial Port").grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(0, 6))
        row += 1

        self._port_combo = ttk.Combobox(card, textvariable=self._port_var,
                                        state="readonly", width=22,
                                        font=FONT_MONO)
        self._port_combo.grid(row=row, column=0, columnspan=2, sticky="ew",
                              padx=(0, 6))

        refresh_btn = self._flat_btn(card, "⟳", self._refresh_ports,
                                     tip="Re-scan serial ports",
                                     fg=C_ACCENT2, hover_fg=C_ACCENT)
        refresh_btn.grid(row=row, column=2, padx=(0, 8))

        self._conn_btn = tk.Button(card, text="CONNECT",
                                   font=("Courier New", 9, "bold"),
                                   bg=C_ACCENT2, fg=C_BG, bd=0,
                                   activebackground=C_ACCENT,
                                   activeforeground=C_BG,
                                   padx=14, pady=6, cursor="hand2",
                                   command=self._toggle_connection)
        self._conn_btn.grid(row=row, column=3, sticky="e")
        row += 1

        Divider(card).grid(row=row, column=0, columnspan=4,
                            sticky="ew", pady=18)
        row += 1

        # ── MIDI Channel section ─────────────────────────────────────────────
        SectionLabel(card, "MIDI Channel").grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(0, 8))
        row += 1

        ch_frame = tk.Frame(card, bg=C_PANEL)
        ch_frame.grid(row=row, column=0, columnspan=4, sticky="w")

        self._ch_display = tk.Label(ch_frame, text="01",
                                    font=FONT_MONO_XL,
                                    bg=C_PANEL, fg=C_TEXT, width=3, anchor="w")
        self._ch_display.pack(side="left")

        arrow_frame = tk.Frame(ch_frame, bg=C_PANEL)
        arrow_frame.pack(side="left", padx=6)
        for txt, delta in (("▲", +1), ("▼", -1)):
            tk.Button(arrow_frame, text=txt, font=("Courier New", 9),
                      bg=C_CARD, fg=C_ACCENT, bd=0,
                      activebackground=C_BORDER, activeforeground=C_ACCENT,
                      padx=12, pady=1, cursor="hand2",
                      command=lambda d=delta: self._step_channel(d)
                      ).pack(fill="x", pady=1)

        tk.Label(ch_frame, text="channels  1 – 16",
                 font=FONT_MONO_S, bg=C_PANEL, fg=C_MUTED).pack(
            side="left", padx=12)
        row += 1

        Divider(card).grid(row=row, column=0, columnspan=4,
                            sticky="ew", pady=18)
        row += 1

        # ── Instrument section ───────────────────────────────────────────────
        SectionLabel(card, "Instrument  (Program Change)").grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(0, 6))
        row += 1

        self._instr_combo = ttk.Combobox(card, textvariable=self._instrument_var,
                                         values=GM_INSTRUMENTS,
                                         state="readonly", width=38,
                                         font=FONT_MONO)
        self._instr_combo.grid(row=row, column=0, columnspan=3,
                                sticky="ew", padx=(0, 8))
        self._instr_combo.bind("<<ComboboxSelected>>", self._on_instrument_change)

        self._prog_num = tk.Label(card, text="PC #000",
                                  font=FONT_MONO_S, bg=C_PANEL, fg=C_MUTED)
        self._prog_num.grid(row=row, column=3, sticky="w")
        row += 1

        Divider(card).grid(row=row, column=0, columnspan=4,
                            sticky="ew", pady=18)
        row += 1

        # ── Volume section ───────────────────────────────────────────────────
        SectionLabel(card, "Volume  (CC #7  ·  0 – 127)").grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(0, 8))
        row += 1

        vol_frame = tk.Frame(card, bg=C_PANEL)
        vol_frame.grid(row=row, column=0, columnspan=4, sticky="ew")

        self._vol_slider = ttk.Scale(vol_frame, from_=0, to=127,
                                     orient="horizontal", length=300,
                                     variable=self._volume_var,
                                     style="Vol.Horizontal.TScale",
                                     command=self._on_volume_slide)
        self._vol_slider.pack(side="left", padx=(0, 16))

        self._vol_display = tk.Label(vol_frame, text="100",
                                     font=FONT_MONO_L,
                                     bg=C_PANEL, fg=C_TEXT, width=4, anchor="w")
        self._vol_display.pack(side="left")
        row += 1

        Divider(card).grid(row=row, column=0, columnspan=4,
                            sticky="ew", pady=18)
        row += 1

        # ── Quick actions ────────────────────────────────────────────────────
        SectionLabel(card, "Quick Actions").grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(0, 8))
        row += 1

        qa = tk.Frame(card, bg=C_PANEL)
        qa.grid(row=row, column=0, columnspan=4, sticky="w")

        def qa_btn(text, tip, cmd):
            b = tk.Button(qa, text=text, font=("Courier New", 8, "bold"),
                          bg=C_CARD, fg=C_TEXT, bd=0,
                          activebackground=C_BORDER, activeforeground=C_ACCENT,
                          padx=14, pady=7, cursor="hand2", command=cmd)
            b.pack(side="left", padx=(0, 8))
            Tooltip(b, tip)

        qa_btn("SEND ALL",   "Re-send channel + instrument + volume",     self._send_all)
        qa_btn("SILENCE",    "CC #120 – All Sound Off on active channel",  self._send_silence)
        qa_btn("RESET CTRL", "CC #121 – Reset All Controllers",            self._send_reset)

        # column weights
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=0)
        card.columnconfigure(2, weight=0)
        card.columnconfigure(3, weight=0)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = StatusBar(self)
        self._status.pack(fill="x", padx=20, pady=(0, 14))

    # ── Small helper: flat button ─────────────────────────────────────────────

    def _flat_btn(self, parent, text, cmd, tip="", fg=C_TEXT, hover_fg=C_ACCENT):
        b = tk.Button(parent, text=text, font=("Courier New", 14),
                      bg=C_PANEL, fg=fg, bd=0,
                      activebackground=C_PANEL, activeforeground=hover_fg,
                      cursor="hand2", command=cmd)
        if tip:
            Tooltip(b, tip)
        return b

    # ── Port scanning ─────────────────────────────────────────────────────────

    def _refresh_ports(self, *_):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        old = self._port_combo["values"]
        if list(old) == ports:
            return
        self._port_combo["values"] = ports
        if ports:
            if self._port_var.get() not in ports:
                self._port_var.set(ports[0])
        else:
            self._port_var.set("")

    def _start_port_watcher(self):
        def _loop():
            while True:
                time.sleep(3)
                ports = [p.device for p in serial.tools.list_ports.comports()]
                if list(ports) != list(self._port_combo["values"]):
                    self.after(0, self._refresh_ports)
        threading.Thread(target=_loop, daemon=True).start()

    # ── Connection ────────────────────────────────────────────────────────────

    def _toggle_connection(self):
        if self._connected:
            self.midi.disconnect()
            self._connected = False
            self._conn_btn.config(text="CONNECT", bg=C_ACCENT2)
            self._status.info("Disconnected")
        else:
            self._connect_to(self._port_var.get())

    def _connect_to(self, port: str) -> bool:
        if not port:
            self._status.err("No port selected")
            return False
        try:
            self.midi.connect(port)
            self._connected = True
            self._conn_btn.config(text="DISCONNECT", bg=C_ERR)
            self._status.ok(f"Connected  →  {port}")
            self._send_all()
            return True
        except serial.SerialException as exc:
            self._connected = False
            self._conn_btn.config(text="CONNECT", bg=C_ACCENT2)
            self._status.err(f"Connection failed: {exc}")
            return False

    # ── COM port hot-swap ─────────────────────────────────────────────────────

    def _on_port_changed(self, *_):
        """When the user picks a different port while already connected,
        transparently re-connect and re-send all settings to the new port."""
        if self._connected:
            new_port = self._port_var.get()
            self._status.info(f"Switching to {new_port}…")
            try:
                self.midi.connect(new_port)   # re-opens on new port atomically
                self._send_all()
                self._status.ok(f"Switched to {new_port}  ·  all settings re-sent")
            except serial.SerialException as exc:
                self._connected = False
                self._conn_btn.config(text="CONNECT", bg=C_ACCENT2)
                self._status.err(f"Port switch failed: {exc}")

    # ── Controls ──────────────────────────────────────────────────────────────

    def _ch0(self) -> int:
        """Return 0-based channel index."""
        return self._channel_var.get() - 1

    def _step_channel(self, delta: int):
        new = max(1, min(16, self._channel_var.get() + delta))
        self._channel_var.set(new)
        self._ch_display.config(text=f"{new:02d}")
        if self._connected:
            # Channel changed: re-send instrument and volume on the new channel
            self._send_all()
            self._status.ok(f"Channel → {new}  ·  settings re-sent")

    def _on_instrument_change(self, _=None):
        idx = GM_INSTRUMENTS.index(self._instrument_var.get())
        self._prog_num.config(text=f"PC #{idx:03d}")
        if self._connected:
            self.midi.program_change(self._ch0(), idx)
            self._status.ok(
                f"Instrument → [{idx:03d}]  {GM_INSTRUMENTS[idx]}")

    def _on_volume_slide(self, val=None):
        v = int(float(val)) if val is not None else self._volume_var.get()
        self._vol_display.config(text=str(v))
        if self._connected:
            self.midi.set_volume(self._ch0(), v)

    # ── Bulk send ─────────────────────────────────────────────────────────────

    def _send_all(self):
        """Transmit current instrument, volume to the active channel."""
        if not self._connected:
            return
        ch  = self._ch0()
        idx = GM_INSTRUMENTS.index(self._instrument_var.get())
        vol = self._volume_var.get()
        self.midi.program_change(ch, idx)
        self.midi.set_volume(ch, vol)
        self._status.ok(
            f"All settings sent  ·  ch={ch+1}  pc={idx}  vol={vol}")

    def _send_silence(self):
        if not self._connected:
            self._status.err("Not connected"); return
        self.midi.all_sound_off(self._ch0())
        self._status.ok("All Sound Off sent (CC #120)")

    def _send_reset(self):
        if not self._connected:
            self._status.err("Not connected"); return
        self.midi.reset_all_controllers(self._ch0())
        self._status.ok("Reset All Controllers sent (CC #121)")


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_pyserial():
    try:
        import serial  # noqa: F401
    except ImportError:
        import subprocess, sys
        print("[info] pyserial not found – installing…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyserial"])
        print("[info] pyserial installed. Please re-run the script.")
        sys.exit(0)


if __name__ == "__main__":
    _ensure_pyserial()
    app = App()
    app.mainloop()