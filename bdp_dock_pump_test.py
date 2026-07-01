"""
RV2900WD Dock BDP Pump Test Tool
---------------------------------
Standalone serial terminal for sending BDP commands to the dock board
over a standard Windows COM port (via pyserial), per BDP Command Spec V2.

Requirements:
    pip install pyserial

Run:
    python bdp_dock_pump_test_3.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
# My friends, let me be clear: this tool was built to talk to real hardware over a
# real serial port, and for that it leans on pyserial. But here is the truth -- the
# pure MIDI parsing logic in this module owes nothing to a COM port. The families who
# want to run our test suite, who want to import this module on a build server where
# pyserial was never installed, deserve a module that does not slam the door on import.
# So we do the hard work: we TRY to bring pyserial in, and when it is absent we leave
# 'serial' bound to None rather than undefined, so runtime code can honestly ask
# "if serial is None" and degrade with dignity instead of crashing.
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None
import threading
import queue
import time
import os
import sys

BAUD_RATE = 115200
# Here is the truth: a serial frame is 8 data bits, no parity, one stop bit -- the
# honest "8N1" configuration the dock board expects. When pyserial is present we use
# its own named constants, because we believe in using the right tool the right way.
# But when pyserial is absent, we must not let a module-level attribute lookup take
# down the entire import. So we provide plain numeric and string stand-ins. Let me be
# clear: these fallbacks are only ever consulted when a real port is opened in the GUI,
# and a real port can only be opened when pyserial is, in fact, installed.
if serial is not None:
    DATA_BITS = serial.EIGHTBITS
    PARITY = serial.PARITY_NONE
    STOP_BITS = serial.STOPBITS_ONE
else:
    DATA_BITS, PARITY, STOP_BITS = 8, "N", 1   # numeric/string stand-ins; only used when a real port is opened

LINE_ENDINGS = {
    "\\r (CR) \u2014 matches V2 spec": "\r",
    "\\n (LF)": "\n",
    "\\r\\n (CRLF)": "\r\n",
    "None": "",
}

# Full BDP test catalog (excludes pumps 4/5 -- robot-only, and "Not Support" items)
# Each entry: name, query cmd template, write cmd template, needs_param, param_label,
# param_default, has_stop/stop (only where 0 unambiguously means off), hint.
TESTS = [
    {
        "name": "BDP Spec Version (00)",
        "query": "?00", "write": None,
        "hint": "Returns $00<version_string>, e.g. $00RevB. BDP tool only, not for factory use.",
    },
    {
        "name": "HW Build Info \u2014 Control Board (PH0)",
        "query": "?PH0", "write": None,
        "hint": "Returns $PH0<string>, e.g. $PH0EB01.",
    },
    {
        "name": "HW Build Info \u2014 Power Board (PH1)",
        "query": "?PH1", "write": None,
        "hint": "Returns $PH1<string>, e.g. $PH1PWUS (US power board).",
    },
    {
        "name": "Solenoid Valve (DV)",
        "query": "?DV", "write": "*DV{p}",
        "needs_param": True, "param_label": "State (0=Off, 1=On)", "param_default": "1",
        "has_stop": True, "stop": "*DV0",
        "hint": "Query returns $DV<state><current>. e.g. $DV1093 = on, 147mA.",
    },
    {
        "name": "Refill Pump \u2014 ON/OFF (DD0)",
        "query": "?DD0", "write": "*DD0{p}",
        "needs_param": True, "param_label": "Duty cycle (hex, 00=off)", "param_default": "32",
        "has_stop": True, "stop": "*DD000",
        "hint": "Returns $DD0<dutycycle><current>.",
    },
    {
        "name": "Grey Water Pump \u2014 ON/OFF (DD1)",
        "query": "?DD1", "write": "*DD1{p}",
        "needs_param": True, "param_label": "Duty cycle (hex, 00=off)", "param_default": "32",
        "has_stop": True, "stop": "*DD100",
        "hint": "Returns $DD1<dutycycle><current>.",
    },
    {
        "name": "Chemical Pump \u2014 ON/OFF (DD2)",
        "query": "?DD2", "write": "*DD2{p}",
        "needs_param": True, "param_label": "Duty cycle (hex, 00=off)", "param_default": "32",
        "has_stop": True, "stop": "*DD200",
        "hint": "Returns $DD2<dutycycle><current>.",
    },
    {
        "name": "Recycle Pump \u2014 ON/OFF (DD3)",
        "query": "?DD3", "write": "*DD3{p}",
        "needs_param": True, "param_label": "Duty cycle (hex, 00=off)", "param_default": "32",
        "has_stop": True, "stop": "*DD300",
        "hint": "Returns $DD3<dutycycle><current>.",
    },
    {
        "name": "E-Water Control \u2014 Clean Water (EW0)",
        "query": "?EW0", "write": "*EW0{p}",
        "needs_param": True, "param_label": "State (0=close,1=+,2=-)", "param_default": "1",
        "has_stop": True, "stop": "*EW00",
        "hint": "Query returns $EW0<voltage><current limit> or $EW<sensor><value>.",
    },
    {
        "name": "E-Water Control \u2014 Grey Water (EW1)",
        "query": "?EW1", "write": "*EW1{p}",
        "needs_param": True, "param_label": "State (0=close,1=+,2=-)", "param_default": "1",
        "has_stop": True, "stop": "*EW10",
        "hint": "Query returns $EW1<voltage><current limit> or $EW<sensor><value>.",
    },
    {
        "name": "Water Heater (WH)",
        "query": "?WH", "write": "*WH{p}",
        "needs_param": True, "param_label": "Duty cycle (hex, 00=off)", "param_default": "32",
        "has_stop": True, "stop": "*WH00",
        "hint": "Returns $WH<dutycycle><actual temperature>.",
    },
    {
        "name": "Hot Air Heater (AH)",
        "query": "?AH", "write": "*AH{p}",
        "needs_param": True, "param_label": "Duty cycle (hex, 00=off)", "param_default": "64",
        "has_stop": True, "stop": "*AH00",
        "hint": "Returns $AH<dutycycle><actual temperature>.",
    },
    {
        "name": "Water Tank Status \u2014 Clean Tank (DE0)",
        "query": "?DE0", "write": None,
        "hint": "Returns $DE0<status>. 0=empty,1=full,2=present,3=not present.",
    },
    {
        "name": "Water Tank Status \u2014 Grey Water Tank (DE2)",
        "query": "?DE2", "write": None,
        "hint": "Returns $DE2<status>.",
    },
    {
        "name": "Water Tank Status \u2014 Chemical Tank (DE3)",
        "query": "?DE3", "write": None,
        "hint": "Returns $DE3<status>.",
    },
    {
        "name": "Water Tank Status \u2014 Wash Tray (DE4)",
        "query": "?DE4", "write": None,
        "hint": "Returns $DE4<status>. e.g. $DE40 = wash tray empty.",
    },
    {
        "name": "Water Tank Status \u2014 Grey E-Water Module (DE5)",
        "query": "?DE5", "write": None,
        "hint": "Returns $DE5<status>. e.g. $DE52 = module installed.",
    },
    {
        "name": "Water Tank Status \u2014 Dust Bin Full Switch (DE6)",
        "query": "?DE6", "write": None,
        "hint": "Returns $DE6<status>.",
    },
    {
        "name": "Wash Tank Status (DF)",
        "query": "?DF", "write": None,
        "hint": "Returns $DF<control_1><control_2><value> (resistance in kOhm).",
    },
    {
        "name": "Temperature \u2014 Water Heater NTC (DT1)",
        "query": "?DT1", "write": None,
        "hint": "Returns $DT1<value>, e.g. $DT120 = 32C.",
    },
    {
        "name": "Temperature \u2014 Air Heater NTC (DT2)",
        "query": "?DT2", "write": None,
        "hint": "Returns $DT2<value>.",
    },
    {
        "name": "Temperature \u2014 Recycle NTC (DT3)",
        "query": "?DT3", "write": None,
        "hint": "Returns $DT3<value>.",
    },
    {
        "name": "UI Button \u2014 Key 1 (UI0)",
        "query": "?UI0", "write": None,
        "hint": "Returns $UI0<value>. 0=released, 1=pressed.",
    },
    {
        "name": "UI Button \u2014 Key 2 (UI1)",
        "query": "?UI1", "write": None,
        "hint": "Returns $UI1<value>.",
    },
    {
        "name": "UI Button \u2014 Key 3 (UI2)",
        "query": "?UI2", "write": None,
        "hint": "Returns $UI2<value>.",
    },
    {
        "name": "Turbidity Sensor (RT)",
        "query": "?RT", "write": None,
        "hint": "Returns $RT<value>. Experimental \u2014 still in research per spec.",
    },
    {
        "name": "Suction Motor (DC)",
        "query": "?DC", "write": "*DC{p}",
        "needs_param": True, "param_label": "State (0=Off, 1=On)", "param_default": "1",
        "has_stop": True, "stop": "*DC0",
        "hint": "Query returns $DC<state>, e.g. $DC1 = motor on.",
    },
    {
        "name": "Power Mode \u2014 12V Output (DA0)",
        "query": "?DA0", "write": "*DA0{p}",
        "needs_param": True, "param_label": "State (0=Off, 1=On)", "param_default": "1",
        "has_stop": True, "stop": "*DA00",
        "hint": "Returns $DA0<state><current>.",
    },
    {
        "name": "Power Mode \u2014 5V Output (DA1)",
        "query": "?DA1", "write": "*DA1{p}",
        "needs_param": True, "param_label": "State (0=Off, 1=On)", "param_default": "1",
        "has_stop": True, "stop": "*DA10",
        "hint": "Returns $DA1<state><current>.",
    },
    {
        "name": "Power Mode \u2014 Charger Output (DA2)",
        "query": "?DA2", "write": "*DA2{p}",
        "needs_param": True, "param_label": "State (0=Off, 1=On)", "param_default": "1",
        "has_stop": True, "stop": "*DA20",
        "hint": "Returns $DA2<state><current>. e.g. $DA21021 = charger on, 33mA.",
    },
    {
        "name": "Dry Fan Control (DL)",
        "query": "?DL", "write": "*DL{p}",
        "needs_param": True, "param_label": "State (0=Off, 1=On)", "param_default": "1",
        "has_stop": True, "stop": "*DL0",
        "hint": "Returns $DL<state><current>. Note: requires charging limit switch triggered per spec.",
    },
    {
        "name": "Shuttle Motor Go Home (DN)",
        "query": "?DN", "write": "*DN{p}",
        "needs_param": True, "param_label": "Duty cycle (hex)", "param_default": "32",
        "hint": "Query returns $DN<home_switch>. 1=closed (shuttle home).",
    },
    {
        "name": "IR UART \u2014 Read from Robot (EC)",
        "query": "?EC", "write": None,
        "hint": "Returns $EC<string> (16 chars) received from robot.",
    },
    {
        "name": "Enter/Exit Debug Mode (DS)",
        "query": "?DS", "write": "*DS{p}",
        "needs_param": True, "param_label": "State (0=off, 1=on)", "param_default": "1",
        "has_stop": True, "stop": "*DS0",
        "hint": "Returns $DS<state>. This is the main Enter/Exit Test Mode command.",
    },
    {
        "name": "Z Signal LED (DZ)",
        "query": "?DZ", "write": "*DZ{p}",
        "needs_param": True, "param_label": "Z signal (hex 00-FF)", "param_default": "9B",
        "hint": "Returns $DZ<value>. Note: spec is inverted (00=on, >0=off) \u2014 no Stop button here.",
    },
    {
        "name": "UI LED (LG)",
        "query": "?LG", "write": "*LG{p}",
        "needs_param": True, "param_label": "Mode (0=off,1=on,2=flash,B/G/R)", "param_default": "1",
        "hint": "Returns $LG<mode>.",
    },
    {
        "name": "QR Code Information (WX)",
        "query": "?WX", "write": None,
        "hint": "Returns $WX<model><serial_number>.",
    },
    {
        "name": "Software Version (WZ)",
        "query": "?WZ", "write": None,
        "hint": "Returns $WZ<AA><BB><CC><build date>.",
    },
]


# ------------------------------------------------------------------------
# Standard MIDI File (SMF) parsing -- shared by the "Play as MIDI" mode.
# A .mid file is NOT MIDI wire data: it has a header, one or more tracks,
# variable-length delta-times and meta events. To "play" it we merge all
# tracks into one tick-ordered stream, walk it converting ticks->seconds
# using the running tempo, and emit only the channel-voice messages.
# ------------------------------------------------------------------------
def resource_path(relative_name):
    """Resolve a bundled data file, whether we run from source or a frozen .exe.

    My friends, here is the honest truth about shipping software: when this tool is
    packaged into a standalone Windows executable by PyInstaller, our companion files
    do not sit politely beside the script anymore -- they are unpacked into a temporary
    folder that PyInstaller announces through ``sys._MEIPASS``. So we do the right thing
    for both worlds: if we are frozen, we look where the bundle put our files; and if we
    are running honestly from source, we look right here beside this very module. One
    function, two homes, no file left behind.
    """
    if getattr(sys, "frozen", False):                      # running inside a PyInstaller bundle
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:                                                  # running from plain source
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, relative_name)


DEFAULT_MIDI_PATH = resource_path("jeopardy.mid")


def _read_vlq(data, offset):
    """Read a MIDI variable-length quantity (VLQ) from the SMF byte buffer.

    My friends, let me be clear about the variable-length quantity, because it is the
    quiet workhorse of the Standard MIDI File. A VLQ is how the format encodes an
    integer of unknown size -- a delta-time, a chunk length -- using as few bytes as it
    honestly needs. Here is the truth of the encoding: each byte carries SEVEN bits of
    real value in its low bits, and the HIGH bit (0x80) is a continuation flag. When the
    high bit is set, the number is not finished -- there is more to come, and we keep
    fighting. When the high bit is clear, this is the last byte, and we are done.

    Args:
        data:   the raw Standard MIDI File byte string we are reading from.
        offset: the byte offset into 'data' at which this quantity begins.

    Returns:
        A tuple of (value, offset), where 'value' is the decoded integer and 'offset'
        is the position of the very next unread byte -- so the caller can pick up
        exactly where we left off, leaving no byte behind.
    """
    value = 0
    while True:
        byte = data[offset]
        offset += 1
        # Shift the accumulated value up by seven bits and fold in this byte's low
        # seven bits (byte & 0x7F). The high bit is masked away -- it is a flag, not data.
        value = (value << 7) | (byte & 0x7F)
        if not (byte & 0x80):   # high bit clear: this was the final byte of the VLQ
            return value, offset


def parse_midi(data):
    """Parse Standard MIDI File bytes into (division_ticks_per_quarter, merged_events).

    My friends, let me be clear: a MIDI *file* is not -- and has never been -- the same
    thing as the MIDI *wire* protocol. A .mid file is a structured container: a header
    chunk (MThd), followed by one or more track chunks (MTrk), each a stream of
    variable-length delta-times paired with events. The families who plug in this dock
    deserve a parser that knows the difference, that does the hard work, that leaves no
    delta-time behind.

    Here is what we do, and here is why. We read the header to learn two things: how
    many tracks there are, and the 'division' -- the number of ticks per quarter note
    that gives every timestamp its meaning. We refuse, honestly and up front, the SMPTE
    style of time division, because this tool does not support it and the American
    people deserve an error message, not silent nonsense. Then we walk every track,
    honoring MIDI 'running status' (the thrifty convention that lets a status byte be
    omitted when it repeats), and we merge all tracks into one tick-ordered stream.

    Returns:
        division: ticks per quarter note (an int), the unit behind every abs_tick.
        merged_events: a tick-ordered list of (abs_tick, kind, payload):
            kind 'wire'  -> payload is the raw channel-voice message (bytes)
            kind 'tempo' -> payload is microseconds-per-quarter-note (int)
    """
    # First, the truth test: a genuine Standard MIDI File opens with the magic bytes
    # "MThd". No header, no parse -- we will not pretend a file is something it is not.
    if data[0:4] != b"MThd":
        raise ValueError("Not a Standard MIDI File (missing MThd header)")
    # The header declares how many track chunks follow (bytes 10-11) and the time
    # division (bytes 12-13). These are the foundation everything else is built on.
    num_tracks = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        # The high bit of division signals SMPTE-based timing. We do not support it,
        # and we say so plainly rather than producing garbage.
        raise ValueError("SMPTE time-division MIDI files are not supported")

    events = []
    cursor = 14   # the running read position into 'data'; the 14-byte header is behind us
    for _ in range(num_tracks):
        # Every track must open with its own magic bytes, "MTrk". If it does not, we
        # have run off the end of honest data, and we stop rather than guess.
        if data[cursor:cursor + 4] != b"MTrk":
            break
        # The four bytes after "MTrk" give the length, in bytes, of this track's data.
        track_len = int.from_bytes(data[cursor + 4:cursor + 8], "big")
        cursor += 8
        track_end = cursor + track_len   # absolute offset where this track's data ends
        abs_tick = 0          # absolute tick count accumulated within this track
        # running_status persists across loop iterations ON PURPOSE: MIDI's running-
        # status convention lets a message omit its status byte and reuse the previous
        # one. The name documents WHY this byte must survive from event to event.
        running_status = 0
        while cursor < track_end:
            # Every event is preceded by a delta-time: a VLQ saying how many ticks have
            # passed since the previous event in this track.
            delta_ticks, cursor = _read_vlq(data, cursor)
            abs_tick += delta_ticks
            head_byte = data[cursor]
            if head_byte & 0x80:           # high bit set: this is a new status byte
                running_status = head_byte
                cursor += 1
            # else: running status -- reuse previous status; head_byte is a data byte
            if running_status == 0xFF:         # meta event
                meta_type = data[cursor]
                cursor += 1
                meta_len, cursor = _read_vlq(data, cursor)
                meta_payload = data[cursor:cursor + meta_len]
                cursor += meta_len
                if meta_type == 0x51 and meta_len == 3:
                    # 0x51 is the Set Tempo meta event: three bytes of microseconds per
                    # quarter note. We carry it forward so the schedule can keep honest time.
                    events.append((abs_tick, "tempo", int.from_bytes(meta_payload, "big")))
                elif meta_type == 0x2F:
                    # 0x2F is End of Track. This track has said all it has to say.
                    break
            elif running_status in (0xF0, 0xF7):   # sysex -- not our fight; skip it
                meta_len, cursor = _read_vlq(data, cursor)
                cursor += meta_len
            else:                          # channel-voice message -- the music itself
                # Program Change (0xC0) and Channel Pressure (0xD0) carry ONE data byte;
                # every other channel-voice message carries TWO. We reassemble the full
                # wire message: the status byte followed by its data bytes.
                num_data_bytes = 1 if (running_status & 0xF0) in (0xC0, 0xD0) else 2
                payload = bytes([running_status]) + data[cursor:cursor + num_data_bytes]
                cursor += num_data_bytes
                events.append((abs_tick, "wire", payload))
        cursor = track_end   # snap to the declared end; trust the header's bookkeeping

    # We can do better than per-track chaos: sort every event by its absolute tick so the
    # whole performance plays as one. Python's sort is STABLE, so two events sharing a
    # tick keep the order in which their tracks contributed them -- no note is reordered.
    events.sort(key=lambda ev: ev[0])
    return division, events


def midi_to_wire_schedule(division, events):
    """Convert tick-stamped events into (time_seconds, payload_bytes) wire messages.

    My friends, ticks are not seconds, and the American people deserve a player that
    knows it. A tick only becomes a moment in time once we know the tempo. So here is
    the work we do: we walk the tick-ordered events, and for every gap between one event
    and the next we convert ticks to seconds using the tempo in force AT THAT MOMENT --
    because tempo can change mid-song, and an honest schedule must honor every change.

    The conversion is plain arithmetic: seconds-per-tick equals (tempo microseconds per
    quarter note) divided by one million, divided by (division ticks per quarter note).
    We accumulate that into a running playback time and stamp each channel-voice message
    with it. Tempo events are consumed to update the clock; only 'wire' messages are emitted.

    Returns:
        A list of (time_seconds, payload_bytes) -- each payload to be sent at its time.
    """
    tempo = 500000          # default 120 BPM (us/quarter) until a tempo meta says otherwise
    last_tick = 0
    cur_seconds = 0.0       # running playback time; '_seconds' names the unit we produce
    schedule = []
    for abs_tick, kind, payload in events:
        # Advance the clock by the elapsed ticks at the CURRENT tempo.
        cur_seconds += (abs_tick - last_tick) * (tempo / 1_000_000.0) / division
        last_tick = abs_tick
        if kind == "tempo":
            tempo = payload   # tempo change takes effect from here forward
        else:
            schedule.append((cur_seconds, payload))
    return schedule


BG = "#1e1f22"
PANEL = "#2a2b2e"
PANEL_LIGHT = "#33353a"
BORDER = "#44464b"
TEXT = "#e8e8ea"
TEXT_SECONDARY = "#a8a9ad"
TEXT_TERTIARY = "#75767a"
ACCENT = "#4d8eff"
SUCCESS = "#4caf6e"
WARNING = "#d9a23b"
DANGER = "#e0594f"
MONO_FONT = ("Consolas", 10)
SANS_FONT = ("Segoe UI", 10)
SANS_FONT_BOLD = ("Segoe UI", 10, "bold")


class BDPTool(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RV2900WD Dock BDP Pump Test Tool")
        self.configure(bg=BG)
        self.minsize(560, 480)
        self._set_initial_geometry()

        self.ser = None
        self.read_thread = None
        self.stop_reading = threading.Event()
        self.rx_queue = queue.Queue()

        self.midi_thread = None
        self.midi_abort = threading.Event()

        self._build_style()
        self._build_ui()
        self._refresh_ports()
        self._poll_rx_queue()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_initial_geometry(self):
        screen_h = self.winfo_screenheight()
        screen_w = self.winfo_screenwidth()
        # Leave room for taskbar / window chrome
        target_h = min(900, screen_h - 100)
        target_w = min(600, screen_w - 100)
        target_h = max(target_h, 480)
        x = max(0, (screen_w - target_w) // 2)
        y = max(0, (screen_h - target_h) // 3)
        self.geometry(f"{target_w}x{target_h}+{x}+{y}")

    # ---------------- styling ----------------
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL_LIGHT,
                         foreground=TEXT, arrowcolor=TEXT)
        style.configure("TButton", background=PANEL_LIGHT, foreground=TEXT,
                         font=SANS_FONT, padding=6, borderwidth=1)
        style.map("TButton", background=[("active", "#3c3e44"), ("disabled", PANEL)])
        style.configure("Accent.TButton", background=ACCENT, foreground="white",
                         font=SANS_FONT_BOLD, padding=6)
        style.map("Accent.TButton", background=[("active", "#3d7ae0"), ("disabled", PANEL_LIGHT)])

    def _section_label(self, parent, text):
        lbl = tk.Label(parent, text=text, bg=BG, fg=TEXT, font=SANS_FONT_BOLD, anchor="w")
        lbl.pack(fill="x", pady=(14, 6))
        return lbl

    def _hint_label(self, parent, text, color=TEXT_TERTIARY):
        lbl = tk.Label(parent, text=text, bg=BG, fg=color, font=("Segoe UI", 8),
                        anchor="w", justify="left", wraplength=500)
        lbl.pack(fill="x", pady=(4, 0))
        return lbl

    # ---------------- UI ----------------
    def _build_ui(self):
        # Scrollable container: canvas + vertical scrollbar, so on short screens
        # nothing gets clipped -- the whole window scrolls instead.
        container = tk.Frame(self, bg=BG)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, bg=BG, highlightthickness=0)
        v_scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=v_scroll.set)

        canvas.pack(side="left", fill="both", expand=True)
        v_scroll.pack(side="right", fill="y")

        outer = tk.Frame(canvas, bg=BG)
        outer_id = canvas.create_window((20, 16), window=outer, anchor="nw")

        def _on_outer_configure(event):
            canvas.configure(scrollregion=(0, 0, event.width + 40, event.height + 32))

        def _on_canvas_configure(event):
            canvas.itemconfig(outer_id, width=max(event.width - 40, 100))

        outer.bind("<Configure>", _on_outer_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        tk.Label(outer, text="RV2900WD Dock BDP Pump Test Tool", bg=BG, fg=TEXT,
                 font=("Segoe UI", 14, "bold"), anchor="w").pack(fill="x")
        tk.Label(outer, text="Pump control per BDP Command Spec V2 \u2014 native serial port (pyserial)",
                 bg=BG, fg=TEXT_SECONDARY, font=SANS_FONT, anchor="w").pack(fill="x", pady=(0, 12))

        sep = tk.Frame(outer, bg=BORDER, height=1)
        sep.pack(fill="x", pady=(0, 10))

        # --- Connection row ---
        conn_row = tk.Frame(outer, bg=BG)
        conn_row.pack(fill="x")

        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(conn_row, textvariable=self.port_var, state="readonly", width=14)
        self.port_combo.pack(side="left")

        refresh_btn = ttk.Button(conn_row, text="\u21bb", width=3, command=self._refresh_ports)
        refresh_btn.pack(side="left", padx=(6, 6))

        self.connect_btn = ttk.Button(conn_row, text="Connect", style="Accent.TButton",
                                       command=self._toggle_connect)
        self.connect_btn.pack(side="left", padx=(0, 10))

        self.status_dot = tk.Canvas(conn_row, width=10, height=10, bg=BG, highlightthickness=0)
        self.status_dot_id = self.status_dot.create_oval(1, 1, 9, 9, fill=TEXT_TERTIARY, outline="")
        self.status_dot.pack(side="left", padx=(0, 6))

        self.status_label = tk.Label(conn_row, text="Not connected", bg=BG, fg=TEXT_SECONDARY, font=SANS_FONT)
        self.status_label.pack(side="left")

        # --- Config info row ---
        config_frame = tk.Frame(outer, bg=PANEL)
        config_frame.pack(fill="x", pady=(12, 0))
        tk.Label(config_frame, text=f"  {BAUD_RATE} baud    8 data bits    no parity    1 stop bit  ",
                 bg=PANEL, fg=TEXT_SECONDARY, font=("Segoe UI", 9)).pack(pady=6)

        # --- Test mode section (Enter / Confirm / Exit in one row) ---
        self._section_label(outer, "Test mode")
        mode_row = tk.Frame(outer, bg=BG)
        mode_row.pack(fill="x")
        self.mode_on_btn = ttk.Button(mode_row, text="Enter test mode",
                                       command=lambda: self._send("*DS1"), state="disabled")
        self.mode_on_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.mode_check_btn = ttk.Button(mode_row, text="Confirm test mode",
                                          command=lambda: self._send("?DS"), state="disabled")
        self.mode_check_btn.pack(side="left", expand=True, fill="x", padx=(4, 4))
        self.mode_off_btn = ttk.Button(mode_row, text="Exit test mode",
                                        command=lambda: self._send("*DS0"), state="disabled")
        self.mode_off_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        sep_mode = tk.Frame(outer, bg=BORDER, height=1)
        sep_mode.pack(fill="x", pady=(16, 0))

        # --- Test selector section ---
        self._section_label(outer, "Select test")

        self.test_var = tk.StringVar()
        self.test_combo = ttk.Combobox(
            outer, textvariable=self.test_var, state="readonly",
            values=[t["name"] for t in TESTS],
        )
        self.test_combo.current(0)
        self.test_combo.pack(fill="x")
        self.test_combo.bind("<<ComboboxSelected>>", lambda e: self._on_test_selected())

        # Parameter row (shown/hidden depending on the selected test)
        self.param_row = tk.Frame(outer, bg=BG)
        self.param_label_widget = tk.Label(self.param_row, text="", bg=BG, fg=TEXT_SECONDARY,
                                            font=SANS_FONT)
        self.param_label_widget.pack(side="left")
        self.param_var = tk.StringVar()
        self.param_entry = tk.Entry(self.param_row, textvariable=self.param_var, width=10, bg=PANEL,
                                     fg=TEXT, insertbackground=TEXT, relief="flat", font=MONO_FONT,
                                     justify="center")
        self.param_entry.pack(side="left", padx=(8, 0))
        self.param_var.trace_add("write", lambda *a: self._update_test_tags())

        # Action buttons row (Query + Send + Stop, adapt per test)
        action_row = tk.Frame(outer, bg=BG)
        action_row.pack(fill="x", pady=(10, 0))
        self.test_query_btn = ttk.Button(action_row, text="Query", command=self._send_test_query,
                                          state="disabled")
        self.test_query_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.test_send_btn = ttk.Button(action_row, text="Send", command=self._send_test_write,
                                         state="disabled")
        self.test_send_btn.pack(side="left", expand=True, fill="x", padx=(4, 4))
        self.test_stop_btn = ttk.Button(action_row, text="Stop", command=self._send_test_stop,
                                         state="disabled")
        self.test_stop_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        self.test_cmd_preview = tk.Label(outer, text="", bg=BG, fg=TEXT_TERTIARY, font=MONO_FONT,
                                          anchor="w")
        self.test_cmd_preview.pack(fill="x", pady=(8, 0))

        self.test_hint_label = tk.Label(outer, text="", bg=BG, fg=TEXT_TERTIARY, font=("Segoe UI", 8),
                                         anchor="w", justify="left", wraplength=500)
        self.test_hint_label.pack(fill="x", pady=(4, 0))

        sep2 = tk.Frame(outer, bg=BORDER, height=1)
        sep2.pack(fill="x", pady=(16, 0))

        # --- Custom command ---
        self._section_label(outer, "Send custom command")
        custom_row = tk.Frame(outer, bg=BG)
        custom_row.pack(fill="x")
        self.custom_var = tk.StringVar()
        custom_entry = tk.Entry(custom_row, textvariable=self.custom_var, bg=PANEL, fg=TEXT,
                                 insertbackground=TEXT, relief="flat", font=SANS_FONT)
        custom_entry.pack(side="left", fill="x", expand=True, ipady=4)
        custom_entry.bind("<Return>", lambda e: self._send_custom())
        self.send_btn = ttk.Button(custom_row, text="Send", command=self._send_custom, state="disabled")
        self.send_btn.pack(side="left", padx=(8, 0))

        ending_row = tk.Frame(outer, bg=BG)
        ending_row.pack(fill="x", pady=(8, 0))
        tk.Label(ending_row, text="Line ending:", bg=BG, fg=TEXT_SECONDARY, font=SANS_FONT).pack(side="left")
        self.ending_var = tk.StringVar(value="\\r (CR) \u2014 matches V2 spec")
        ending_combo = ttk.Combobox(ending_row, textvariable=self.ending_var, state="readonly",
                                     values=list(LINE_ENDINGS.keys()), width=26)
        ending_combo.pack(side="left", padx=(8, 0))

        # --- MIDI playback ---
        sep_midi = tk.Frame(outer, bg=BORDER, height=1)
        sep_midi.pack(fill="x", pady=(16, 0))
        self._section_label(outer, "MIDI playback")

        midi_row = tk.Frame(outer, bg=BG)
        midi_row.pack(fill="x")
        self.midi_path_var = tk.StringVar(
            value=DEFAULT_MIDI_PATH if os.path.isfile(DEFAULT_MIDI_PATH) else "")
        midi_entry = tk.Entry(midi_row, textvariable=self.midi_path_var, bg=PANEL, fg=TEXT,
                              insertbackground=TEXT, relief="flat", font=SANS_FONT)
        midi_entry.pack(side="left", fill="x", expand=True, ipady=4)
        browse_btn = ttk.Button(midi_row, text="Browse", command=self._browse_midi, width=8)
        browse_btn.pack(side="left", padx=(8, 0))

        midi_ctrl = tk.Frame(outer, bg=BG)
        midi_ctrl.pack(fill="x", pady=(8, 0))
        tk.Label(midi_ctrl, text="Mode:", bg=BG, fg=TEXT_SECONDARY, font=SANS_FONT).pack(side="left")
        self.midi_mode = tk.StringVar(value="raw")
        ttk.Radiobutton(midi_ctrl, text="Raw dump", variable=self.midi_mode,
                        value="raw").pack(side="left", padx=(8, 0))
        ttk.Radiobutton(midi_ctrl, text="Play as MIDI", variable=self.midi_mode,
                        value="play").pack(side="left", padx=(8, 0))
        self.midi_send_btn = ttk.Button(midi_ctrl, text="Send MIDI", command=self._send_midi,
                                        state="disabled")
        self.midi_send_btn.pack(side="right")
        self.midi_stop_btn = ttk.Button(midi_ctrl, text="Stop", command=self._stop_midi,
                                        state="disabled")
        self.midi_stop_btn.pack(side="right", padx=(0, 6))

        self._hint_label(
            outer,
            "Raw dump streams the .mid file bytes as-is. Play as MIDI parses the file and "
            "emits timed note messages -- audible only on a real MIDI synth, not the dock board.")

        # --- Log ---
        log_header = tk.Frame(outer, bg=BG)
        log_header.pack(fill="x", pady=(16, 6))
        tk.Label(log_header, text="Log", bg=BG, fg=TEXT, font=SANS_FONT_BOLD).pack(side="left")
        clear_btn = ttk.Button(log_header, text="Clear", command=self._clear_log, width=8)
        clear_btn.pack(side="right")

        log_frame = tk.Frame(outer, bg=PANEL, height=180)
        log_frame.pack(fill="both", expand=True)
        log_frame.pack_propagate(False)
        self.log_text = tk.Text(log_frame, bg=PANEL, fg=TEXT, font=MONO_FONT, wrap="word",
                                 relief="flat", state="disabled", padx=8, pady=8)
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self.log_text.tag_configure("tx", foreground=ACCENT)
        self.log_text.tag_configure("rx", foreground=SUCCESS)
        self.log_text.tag_configure("sys", foreground=TEXT_TERTIARY)

        self._on_test_selected()

        self.all_controls = [
            self.mode_on_btn, self.mode_check_btn, self.mode_off_btn,
            self.test_query_btn, self.test_send_btn, self.test_stop_btn, self.send_btn,
            self.midi_send_btn,
        ]

    # ---------------- helpers ----------------
    def _current_test(self):
        idx = self.test_combo.current()
        if idx < 0 or idx >= len(TESTS):
            return TESTS[0]
        return TESTS[idx]

    def _on_test_selected(self):
        test = self._current_test()
        needs_param = test.get("needs_param", False)
        connected = bool(self.ser and self.ser.is_open)

        if needs_param:
            self.param_row.pack(fill="x", pady=(10, 0))
            self.param_label_widget.configure(text=test.get("param_label", "Parameter:") + ":")
            self.param_var.set(test.get("param_default", ""))
        else:
            self.param_row.pack_forget()

        has_write = test.get("write") is not None
        self.test_send_btn.configure(
            state=("normal" if (has_write and connected) else "disabled"),
            text="Send" if has_write else "Send (N/A)",
        )

        if not test.get("query"):
            self.test_query_btn.configure(text="Query (N/A)", state="disabled")
        else:
            self.test_query_btn.configure(text="Query", state=("normal" if connected else "disabled"))

        has_stop = test.get("has_stop", False)
        self.test_stop_btn.configure(
            state=("normal" if (has_stop and connected) else "disabled"),
            text="Stop" if has_stop else "Stop (N/A)",
        )

        self.test_hint_label.configure(text=test.get("hint", ""))
        self._update_test_tags()

    def _update_test_tags(self):
        test = self._current_test()
        query_cmd = test.get("query") or ""
        write_tpl = test.get("write")
        stop_cmd = test.get("stop") or ""
        if write_tpl:
            param = self.param_var.get().strip() or test.get("param_default", "")
            write_cmd = write_tpl.format(p=param)
        else:
            write_cmd = ""

        preview_parts = []
        if query_cmd:
            preview_parts.append(f"Query: {query_cmd}")
        if write_cmd:
            preview_parts.append(f"Send: {write_cmd}")
        if stop_cmd:
            preview_parts.append(f"Stop: {stop_cmd}")
        self.test_cmd_preview.configure(text="   |   ".join(preview_parts))

    def _log(self, direction, text):
        self.log_text.configure(state="normal")
        prefix = {"tx": "\u2192 ", "rx": "\u2190 ", "sys": ""}.get(direction, "")
        self.log_text.insert("end", prefix + text + "\n", direction)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo.configure(values=ports)
        if ports and not self.port_var.get():
            self.port_combo.current(0)

    def _set_connected_ui(self, connected):
        for ctrl in self.all_controls:
            ctrl.configure(state=("normal" if connected else "disabled"))
        self.status_dot.itemconfig(self.status_dot_id, fill=(SUCCESS if connected else TEXT_TERTIARY))
        self.status_label.configure(text=("Connected" if connected else "Not connected"))
        self.connect_btn.configure(text=("Disconnect" if connected else "Connect"))
        self.port_combo.configure(state=("disabled" if connected else "readonly"))
        self._on_test_selected()  # re-apply per-test Query/Send/Stop availability on top of connect state

    # ---------------- connection ----------------
    def _toggle_connect(self):
        if self.ser and self.ser.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self.port_var.get()
        if not port:
            messagebox.showwarning("No port selected", "Please select a COM port first.")
            return
        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=BAUD_RATE,
                bytesize=DATA_BITS,
                parity=PARITY,
                stopbits=STOP_BITS,
                timeout=0.2,
            )
            # Assert DTR/RTS to match typical terminal-app defaults
            try:
                self.ser.dtr = True
                self.ser.rts = True
            except Exception:
                pass

            self.stop_reading.clear()
            self.read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self.read_thread.start()

            self._set_connected_ui(True)
            self._log("sys", f"Connected to {port} at {BAUD_RATE}-8N1")
        except Exception as e:
            messagebox.showerror("Connection failed", str(e))
            self._log("sys", f"Connection failed: {e}")

    def _disconnect(self):
        self.midi_abort.set()
        self.stop_reading.set()
        if self.read_thread:
            self.read_thread.join(timeout=1)
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self._set_connected_ui(False)
        self._log("sys", "Disconnected")

    def _read_loop(self):
        while not self.stop_reading.is_set():
            try:
                if self.ser and self.ser.is_open and self.ser.in_waiting > 0:
                    data = self.ser.read(self.ser.in_waiting)
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        self.rx_queue.put(text)
                else:
                    time.sleep(0.05)
            except Exception as e:
                self.rx_queue.put(f"__ERROR__:{e}")
                break

    def _poll_rx_queue(self):
        try:
            while True:
                item = self.rx_queue.get_nowait()
                if item.startswith("__ERROR__:"):
                    self._log("sys", "Read error: " + item[len("__ERROR__:"):])
                elif item.startswith("__SYS__:"):
                    self._log("sys", item[len("__SYS__:"):])
                else:
                    self._log("rx", item.rstrip("\n"))
        except queue.Empty:
            pass
        self.after(50, self._poll_rx_queue)

    # ---------------- sending ----------------
    def _send(self, text):
        if not (self.ser and self.ser.is_open):
            return
        ending = LINE_ENDINGS.get(self.ending_var.get(), "\r")
        try:
            self.ser.write((text + ending).encode("utf-8"))
            self._log("tx", text)
        except Exception as e:
            self._log("sys", f"Send error: {e}")

    def _send_test_query(self):
        test = self._current_test()
        cmd = test.get("query")
        if cmd:
            self._send(cmd)

    def _send_test_write(self):
        test = self._current_test()
        write_tpl = test.get("write")
        if not write_tpl:
            return
        param = self.param_var.get().strip() or test.get("param_default", "")
        cmd = write_tpl.format(p=param)
        self._send(cmd)

    def _send_test_stop(self):
        test = self._current_test()
        cmd = test.get("stop")
        if cmd:
            self._send(cmd)

    def _send_custom(self):
        text = self.custom_var.get().strip()
        if text:
            self._send(text)
            self.custom_var.set("")

    # ---------------- MIDI ----------------
    def _browse_midi(self):
        path = filedialog.askopenfilename(
            title="Select a MIDI file",
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")])
        if path:
            self.midi_path_var.set(path)

    def _send_bytes(self, data):
        if not (self.ser and self.ser.is_open):
            return
        try:
            self.ser.write(data)
        except Exception as e:
            self.rx_queue.put(f"__ERROR__:{e}")

    def _midi_log(self, msg):
        self.rx_queue.put("__SYS__:" + msg)   # thread-safe: drained by _poll_rx_queue

    def _send_midi(self):
        if not (self.ser and self.ser.is_open):
            messagebox.showwarning("Not connected", "Connect to a port first.")
            return
        if self.midi_thread and self.midi_thread.is_alive():
            return
        path = self.midi_path_var.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showerror("MIDI file", "File not found:\n" + path)
            return
        self.midi_abort.clear()
        self.midi_send_btn.configure(state="disabled")
        self.midi_stop_btn.configure(state="normal")
        self.midi_thread = threading.Thread(
            target=self._midi_worker, args=(path, self.midi_mode.get()), daemon=True)
        self.midi_thread.start()

    def _stop_midi(self):
        self.midi_abort.set()

    def _midi_worker(self, path, mode):
        try:
            with open(path, "rb") as f:
                data = f.read()
            if mode == "raw":
                self._midi_log(f"Sending raw MIDI file ({len(data)} bytes)...")
                self._send_bytes(data)
                self._midi_log("Raw MIDI sent.")
            else:
                division, events = parse_midi(data)
                schedule = midi_to_wire_schedule(division, events)
                self._midi_log(f"Playing MIDI: {len(schedule)} messages, "
                               f"{schedule[-1][0]:.1f}s." if schedule else "No notes.")
                start = time.monotonic()
                completed = True
                for t, payload in schedule:
                    if self.midi_abort.is_set():
                        self._midi_log("MIDI playback stopped.")
                        completed = False
                        break
                    delay = t - (time.monotonic() - start)
                    if delay > 0:
                        time.sleep(delay)
                    self._send_bytes(payload)
                # All-notes-off on every channel so nothing hangs on.
                for ch in range(16):
                    self._send_bytes(bytes([0xB0 | ch, 0x7B, 0x00]))
                if completed:
                    self._midi_log("MIDI playback complete.")
        except Exception as e:
            self._midi_log(f"MIDI error: {e}")
        finally:
            self.after(0, self._midi_done)

    def _midi_done(self):
        self.midi_stop_btn.configure(state="disabled")
        self.midi_send_btn.configure(
            state=("normal" if (self.ser and self.ser.is_open) else "disabled"))

    def _on_close(self):
        self.midi_abort.set()
        self.stop_reading.set()
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    app = BDPTool()
    app.mainloop()