"""And let me say this plainly, my friends: what we are testing here is the
beating heart of bdp_dock_pump_test -- its Standard MIDI File reader and the
scheduler that turns ticks into honest, wall-clock seconds. We are not here to
test the blinking lights of a Tk window; we are here to hold the parser
accountable. And so we examine the variable-length quantity decoder, that
humble workhorse, byte by byte. We examine parse_midi as it walks a real MThd
and MTrk, as it honors running status, as it merges many tracks into one stable,
tick-sorted stream. We examine midi_to_wire_schedule as it answers the only
question a metronome ever asks -- when? -- through tempo change after tempo
change. And let me be technically honest with you: the module imports tkinter
at module scope, because it is, at the end of the day, a GUI tool. On a headless
build server that import can fail, and a failed import helps no one. So before we
ask the parser to do its job, we install a lightweight, subclassable tkinter stub
-- it touches nothing in the MIDI logic, and that is a promise we keep. The
integration case reaches for jeopardy.mid as ground truth, and when that file is
not present, it does not fail and it does not pretend -- it skips, gracefully,
and we move on. That is the test suite. That is the work.
"""

import os
import sys
import types
import unittest


# ---------------------------------------------------------------------------
# Import the module under test. It builds a Tk GUI at import time, so we install
# a lightweight, subclassable tkinter stub family BEFORE importing. The stub
# yields a real (subclassable) dummy class for every attribute access, so a
# class definition like `class BDPTool(tk.Tk)` succeeds. None of this touches
# the MIDI parsing/scheduling logic actually under test. If real tkinter is
# already importable, we leave it alone.
# ---------------------------------------------------------------------------
class _Dummy:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return _Dummy()

    def __call__(self, *a, **k):
        return _Dummy()


class _FakeTkModule(types.ModuleType):
    def __getattr__(self, name):
        return _Dummy


def _ensure_tkinter():
    try:
        import tkinter  # noqa: F401
        if hasattr(sys.modules["tkinter"], "Tk"):
            return
    except Exception:
        pass
    for _name in (
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "tkinter.font",
        "tkinter.scrolledtext",
    ):
        sys.modules.setdefault(_name, _FakeTkModule(_name))
    _tk = sys.modules["tkinter"]
    _tk.ttk = sys.modules["tkinter.ttk"]
    _tk.messagebox = sys.modules["tkinter.messagebox"]
    _tk.filedialog = sys.modules["tkinter.filedialog"]


_ensure_tkinter()

sys.path.insert(0, "/Users/Jacob/Code/John")
import bdp_dock_pump_test as bdp  # noqa: E402


# ---------------------------------------------------------------------------
# De-duplicated, self-contained Standard MIDI File assembly helpers.
# ---------------------------------------------------------------------------
JEOPARDY_MID = "/Users/Jacob/Code/John/jeopardy.mid"


def _mthd(fmt, num_tracks, division):
    """Build a 14-byte MThd header chunk: 'MThd' + len(6) + fmt + ntrks + division."""
    body = (
        int(fmt).to_bytes(2, "big")
        + int(num_tracks).to_bytes(2, "big")
        + (int(division) & 0xFFFF).to_bytes(2, "big")
    )
    return b"MThd" + len(body).to_bytes(4, "big") + body


def _mtrk(track_data):
    """Wrap raw track-data bytes in an MTrk chunk (length = len of track data)."""
    track_data = bytes(track_data)
    return b"MTrk" + len(track_data).to_bytes(4, "big") + track_data


def _smf(tracks, division=480, fmt=0):
    """Assemble a complete SMF.

    `tracks` may be a single raw-track byte string or a list of them. The
    header's track count is derived from the number of tracks supplied.
    """
    if isinstance(tracks, (bytes, bytearray)):
        tracks = [tracks]
    out = _mthd(fmt, len(tracks), division)
    for td in tracks:
        out += _mtrk(td)
    return out


def _tempo_meta(us_per_qn):
    """Build a Set-Tempo meta event body: FF 51 03 <3 bytes us/qn>."""
    return bytes([0xFF, 0x51, 0x03]) + int(us_per_qn).to_bytes(3, "big")


# Common single-track building blocks. Each event is: VLQ delta + event bytes.
_NOTE_ON = bytes([0x00, 0x90, 0x3C, 0x40])   # dt=0, note-on  ch0 C4 vel64
_NOTE_OFF = bytes([0x00, 0x80, 0x3C, 0x40])  # dt=0, note-off ch0 C4 vel64
_EOT = bytes([0x00, 0xFF, 0x2F, 0x00])       # dt=0, End-of-Track meta


# ===========================================================================
# THEME: Variable-Length Quantity decoding (_read_vlq)
# ===========================================================================
class ReadVlqTests(unittest.TestCase):
    def test_single_byte_zero(self):
        # 0x00 -> value 0, next offset advances by one byte.
        value, offset = bdp._read_vlq(b"\x00", 0)
        self.assertEqual(value, 0)
        self.assertEqual(offset, 1)

    def test_single_byte_nonzero(self):
        # 0x40 has high bit clear -> final byte, value 64.
        value, offset = bdp._read_vlq(b"\x40", 0)
        self.assertEqual(value, 64)
        self.assertEqual(offset, 1)

    def test_single_byte_max(self):
        # 0x7F is the largest single-byte VLQ (127), high bit still clear.
        value, offset = bdp._read_vlq(b"\x7F", 0)
        self.assertEqual(value, 127)
        self.assertEqual(offset, 1)

    def test_two_byte_continuation(self):
        # 0x81 (continuation) then 0x00 -> (1 << 7) | 0 == 128, consumes two bytes.
        value, offset = bdp._read_vlq(b"\x81\x00", 0)
        self.assertEqual(value, 128)
        self.assertEqual(offset, 2)

    def test_two_byte_continuation_with_payload(self):
        # 0xC0 0x00 -> (0x40 << 7) | 0 == 8192.
        value, offset = bdp._read_vlq(b"\xC0\x00", 0)
        self.assertEqual(value, 8192)
        self.assertEqual(offset, 2)

    def test_large_four_byte_value(self):
        # 0xFF 0xFF 0xFF 0x7F is the canonical max 4-byte VLQ: 0x0FFFFFFF.
        value, offset = bdp._read_vlq(b"\xFF\xFF\xFF\x7F", 0)
        self.assertEqual(value, 0x0FFFFFFF)
        self.assertEqual(value, 268435455)
        self.assertEqual(offset, 4)

    def test_respects_nonzero_offset(self):
        # Reading should begin at the given offset, leaving leading bytes untouched.
        data = b"\xAA" + b"\x81\x00" + b"\x7F"
        value, offset = bdp._read_vlq(data, 1)
        self.assertEqual(value, 128)
        self.assertEqual(offset, 3)

    def test_returns_tuple_of_two_ints(self):
        result = bdp._read_vlq(b"\x00", 0)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], int)
        self.assertIsInstance(result[1], int)


# ===========================================================================
# THEME: parse_midi on a single track (event extraction + running status)
# ===========================================================================
# A single track:
#   tick   0: note-on  ch0 note 60 vel 100  (explicit status 0x90)
#   tick  96: note-on  ch0 note 62 vel 100  (RUNNING STATUS: 0x90 omitted)
#   tick 192: note-off ch0 note 60 vel 64   (explicit status 0x80)
#   tick 192: End of Track meta
_PARSE_TRACK = bytes([
    0x00, 0x90, 0x3C, 0x64,   # delta 0,   note-on  60,100
    0x60, 0x3E, 0x64,         # delta 96,  running-status note-on 62,100
    0x60, 0x80, 0x3C, 0x40,   # delta 96,  note-off 60,64
    0x00, 0xFF, 0x2F, 0x00,   # delta 0,   end-of-track
])


class ParseMidiTests(unittest.TestCase):
    def setUp(self):
        self.division = 480
        self.smf = _smf(_PARSE_TRACK, division=self.division)
        self.parsed_division, self.events = bdp.parse_midi(self.smf)

    def test_division_matches_header(self):
        self.assertEqual(self.parsed_division, 480)

    def test_event_count_is_three_wire_events(self):
        # Three channel-voice messages; the End-of-Track meta emits no event.
        self.assertEqual(len(self.events), 3)

    def test_all_events_are_wire_kind(self):
        kinds = [kind for (_tick, kind, _payload) in self.events]
        self.assertEqual(kinds, ["wire", "wire", "wire"])

    def test_abs_ticks_are_tick_sorted(self):
        ticks = [tick for (tick, _kind, _payload) in self.events]
        self.assertEqual(ticks, [0, 96, 192])

    def test_note_on_payload_bytes(self):
        tick, kind, payload = self.events[0]
        self.assertEqual(tick, 0)
        self.assertEqual(kind, "wire")
        self.assertEqual(payload, b"\x90\x3c\x64")
        self.assertIsInstance(payload, bytes)

    def test_note_off_payload_bytes(self):
        tick, kind, payload = self.events[2]
        self.assertEqual(tick, 192)
        self.assertEqual(kind, "wire")
        self.assertEqual(payload, b"\x80\x3c\x40")

    def test_running_status_reuses_previous_status_byte(self):
        # The second event omitted its status byte in the SMF; parse_midi must
        # reconstruct the full wire message using the running status 0x90.
        tick, kind, payload = self.events[1]
        self.assertEqual(tick, 96)
        self.assertEqual(kind, "wire")
        self.assertEqual(payload[0], 0x90)
        self.assertEqual(payload, b"\x90\x3e\x64")
        self.assertEqual(len(payload), 3)

    def test_running_status_note_distinct_from_first(self):
        # Sanity: the running-status note (62) differs from the first note (60),
        # proving the data bytes were read fresh while the status was reused.
        self.assertEqual(self.events[0][2][1], 0x3C)
        self.assertEqual(self.events[1][2][1], 0x3E)

    def test_payloads_are_immutable_bytes(self):
        for _tick, _kind, payload in self.events:
            self.assertIsInstance(payload, bytes)


# ===========================================================================
# THEME: tempo handling + midi_to_wire_schedule timing
# ===========================================================================
# VLQ(240) == 0x81 0x70 ; VLQ(480) == 0x83 0x60 (verified against bdp._read_vlq).
class TempoSchedulingTests(unittest.TestCase):
    def test_tempo_meta_changes_ticks_to_seconds(self):
        # division=480, default tempo 500000 (120 BPM) until tick 480, then a
        # tempo meta sets 1000000 us/qn (60 BPM).
        #   tick 0   wire note-on  -> 0.0 s
        #   tick 480 tempo 1000000 -> clock advances 480 ticks @ 500000 = 0.5 s
        #   tick 960 wire note-off -> +480 ticks @ 1000000 = +1.0 s -> 1.5 s
        track = (
            bytes([0x00, 0x90, 0x3C, 0x40])               # dt 0,   note-on
            + bytes([0x83, 0x60]) + _tempo_meta(1000000)  # dt 480, set tempo
            + bytes([0x83, 0x60, 0x80, 0x3C, 0x40])       # dt 480, note-off
            + _EOT
        )
        division, events = bdp.parse_midi(_smf(track, division=480))
        schedule = bdp.midi_to_wire_schedule(division, events)

        self.assertEqual(len(schedule), 2)
        times = [t for t, _ in schedule]
        self.assertAlmostEqual(times[0], 0.0, places=9)
        self.assertAlmostEqual(times[1], 1.5, places=9)
        # Only the two channel-voice messages were emitted, in order.
        self.assertEqual(schedule[0][1], b"\x90\x3C\x40")
        self.assertEqual(schedule[1][1], b"\x80\x3C\x40")

    def test_tempo_event_emits_no_wire_message(self):
        # A track whose only events are wire-note + tempo + wire-note must yield
        # exactly two scheduled wire messages; the tempo event is consumed.
        track = (
            bytes([0x00, 0x90, 0x3C, 0x40])
            + bytes([0x83, 0x60]) + _tempo_meta(1000000)
            + bytes([0x83, 0x60, 0x80, 0x3C, 0x40])
            + _EOT
        )
        division, events = bdp.parse_midi(_smf(track, division=480))

        # parse_midi surfaces the tempo as its own ('tempo', us/qn) event...
        kinds = [kind for _tick, kind, _payload in events]
        self.assertIn("tempo", kinds)
        self.assertEqual(kinds.count("tempo"), 1)
        self.assertEqual(kinds.count("wire"), 2)
        tempo_payload = next(p for _t, k, p in events if k == "tempo")
        self.assertEqual(tempo_payload, 1000000)

        # ...but the schedule drops it: only wire messages survive.
        schedule = bdp.midi_to_wire_schedule(division, events)
        self.assertEqual(len(schedule), 2)
        for _time, payload in schedule:
            self.assertIsInstance(payload, (bytes, bytearray))

    def test_default_tempo_cumulative_times(self):
        # No tempo meta at all: default 500000 us/qn, division 480.
        # seconds-per-tick = 0.5/480. tick 240 -> 0.25 s, tick 480 -> 0.5 s.
        track = (
            bytes([0x00, 0x90, 0x3C, 0x40])               # tick 0
            + bytes([0x81, 0x70, 0x90, 0x3E, 0x40])       # +240 -> tick 240
            + bytes([0x81, 0x70, 0x80, 0x3C, 0x40])       # +240 -> tick 480
            + _EOT
        )
        division, events = bdp.parse_midi(_smf(track, division=480))
        schedule = bdp.midi_to_wire_schedule(division, events)

        times = [t for t, _ in schedule]
        self.assertEqual(len(times), 3)
        self.assertAlmostEqual(times[0], 0.0, places=9)
        self.assertAlmostEqual(times[1], 0.25, places=9)
        self.assertAlmostEqual(times[2], 0.5, places=9)

    def test_multiple_tempo_changes_cumulative_times(self):
        # Two tempo changes; verify the clock honors the tempo in force at each
        # gap. division=480.
        #   tick 0   note-on            -> 0.0 s   (default 500000)
        #   tick 480 tempo 1000000      -> +480 @ 500000  = 0.5 s
        #   tick 480 note-on (same tick)-> +0             = 0.5 s
        #   tick 720 tempo 250000       -> +240 @ 1000000 = +0.5 -> 1.0 s
        #   tick 960 note-on            -> +240 @ 250000  = +0.125 -> 1.125 s
        track = (
            bytes([0x00, 0x90, 0x3C, 0x40])               # tick 0   note-on
            + bytes([0x83, 0x60]) + _tempo_meta(1000000)  # tick 480 tempo
            + bytes([0x00, 0x90, 0x3E, 0x40])             # tick 480 note-on
            + bytes([0x81, 0x70]) + _tempo_meta(250000)   # tick 720 tempo
            + bytes([0x81, 0x70, 0x90, 0x40, 0x40])       # tick 960 note-on
            + _EOT
        )
        division, events = bdp.parse_midi(_smf(track, division=480))
        schedule = bdp.midi_to_wire_schedule(division, events)

        times = [t for t, _ in schedule]
        self.assertEqual(len(times), 3)  # 3 wire msgs; 2 tempo events dropped
        self.assertAlmostEqual(times[0], 0.0, places=9)
        self.assertAlmostEqual(times[1], 0.5, places=9)
        self.assertAlmostEqual(times[2], 1.125, places=9)


# ===========================================================================
# THEME: multi-track merge (format-1) + variable-length message widths
# ===========================================================================
class MultiTrackMergeTests(unittest.TestCase):
    # Two format-1 tracks whose events interleave in tick time. Track 1 and
    # track 2 each carry an event at the same tick (20) to exercise the stable
    # tie-break (track order preserved). Program Change (0xC0) and Channel
    # Pressure (0xD0) are exercised to confirm they consume only ONE data byte.

    def _two_track_file(self):
        # Track 1: tick 0 NoteOn(2 data), tick 20 ProgramChange(1 data), EOT
        t1 = bytes([0x00, 0x90, 0x3C, 0x40,
                    0x14, 0xC0, 0x05,
                    0x00, 0xFF, 0x2F, 0x00])
        # Track 2: tick 10 ChannelPressure(1 data), tick 20 NoteOn(2 data), EOT
        t2 = bytes([0x0A, 0xD0, 0x50,
                    0x0A, 0x91, 0x40, 0x40,
                    0x00, 0xFF, 0x2F, 0x00])
        return _smf([t1, t2], division=96, fmt=1)

    def test_division_and_event_count(self):
        division, events = bdp.parse_midi(self._two_track_file())
        self.assertEqual(division, 96)
        # Four wire events; tempo/EOT meta produce none here.
        self.assertEqual(len(events), 4)
        self.assertTrue(all(kind == "wire" for _, kind, _ in events))

    def test_events_tick_sorted_across_tracks(self):
        _, events = bdp.parse_midi(self._two_track_file())
        ticks = [t for t, _, _ in events]
        self.assertEqual(ticks, sorted(ticks))
        self.assertEqual(ticks, [0, 10, 20, 20])

    def test_stable_tie_keeps_track_order(self):
        # Both tracks emit at tick 20. Track 1 (ProgramChange 0xC0 0x05) was
        # appended before track 2 (NoteOn 0x91 ...), so a stable sort must keep
        # the track-1 event first among the tie.
        _, events = bdp.parse_midi(self._two_track_file())
        tied = [(k, p) for t, k, p in events if t == 20]
        self.assertEqual(tied,
                         [("wire", b"\xC0\x05"), ("wire", b"\x91\x40\x40")])

    def test_full_merged_stream(self):
        _, events = bdp.parse_midi(self._two_track_file())
        self.assertEqual(events, [
            (0, "wire", b"\x90\x3C\x40"),
            (10, "wire", b"\xD0\x50"),
            (20, "wire", b"\xC0\x05"),
            (20, "wire", b"\x91\x40\x40"),
        ])

    def test_program_change_one_data_byte(self):
        # 0xC0 consumes exactly one data byte: payload is status + 1 byte.
        _, events = bdp.parse_midi(self._two_track_file())
        pc = [p for _, _, p in events if p[0] == 0xC0]
        self.assertEqual(len(pc), 1)
        self.assertEqual(pc[0], b"\xC0\x05")
        self.assertEqual(len(pc[0]), 2)

    def test_channel_pressure_one_data_byte(self):
        # 0xD0 consumes exactly one data byte.
        _, events = bdp.parse_midi(self._two_track_file())
        cp = [p for _, _, p in events if p[0] == 0xD0]
        self.assertEqual(len(cp), 1)
        self.assertEqual(cp[0], b"\xD0\x50")
        self.assertEqual(len(cp[0]), 2)

    def test_one_byte_message_does_not_swallow_next(self):
        # If 0xC0/0xD0 wrongly consumed two data bytes, the byte alignment would
        # drift and the following events would be mis-parsed. Verify the two-data
        # NoteOn messages (0x90, 0x91) are intact, proving alignment held.
        _, events = bdp.parse_midi(self._two_track_file())
        note_ons = [p for _, _, p in events if p[0] & 0xF0 == 0x90]
        self.assertEqual(note_ons, [b"\x90\x3C\x40", b"\x91\x40\x40"])

    def test_schedule_preserves_merged_order_and_ties(self):
        # The playback schedule must keep the merged tick order, including the
        # tie at tick 20, and emit one entry per wire message. Default tempo
        # 500000 us/qn, division 96 -> 0.0052083.. s/tick.
        division, events = bdp.parse_midi(self._two_track_file())
        schedule = bdp.midi_to_wire_schedule(division, events)
        payloads = [p for _, p in schedule]
        self.assertEqual(payloads,
                         [b"\x90\x3C\x40", b"\xD0\x50",
                          b"\xC0\x05", b"\x91\x40\x40"])
        spt = (500000 / 1_000_000.0) / division
        self.assertAlmostEqual(schedule[0][0], 0.0)
        self.assertAlmostEqual(schedule[1][0], 10 * spt)
        self.assertAlmostEqual(schedule[2][0], 20 * spt)
        # Tied events at tick 20 share the exact same timestamp.
        self.assertAlmostEqual(schedule[2][0], schedule[3][0])


# ===========================================================================
# THEME: error handling + sysex/meta skipping
# ===========================================================================
class ErrorHandlingTests(unittest.TestCase):

    # ---- Malformed / unsupported headers ---------------------------------
    def test_missing_mthd_raises_valueerror(self):
        bogus = b"XXXX" + b"\x00" * 20
        with self.assertRaises(ValueError):
            bdp.parse_midi(bogus)

    def test_smpte_division_raises_valueerror(self):
        # High bit of the division field signals SMPTE timing -> rejected.
        smpte = _smf([_NOTE_ON + _EOT], division=0x8000 | 0x0078, fmt=1)
        self.assertTrue(smpte[12] & 0x80, "division high byte should have high bit set")
        with self.assertRaises(ValueError):
            bdp.parse_midi(smpte)

    def test_smpte_division_high_bit_only(self):
        # Even a bare high bit (0x8000) must trip the SMPTE rejection path.
        with self.assertRaises(ValueError):
            bdp.parse_midi(_smf([_NOTE_ON + _EOT], division=0x8000, fmt=1))

    # ---- Skipping of sysex / unknown-meta events -------------------------
    def test_sysex_events_are_skipped(self):
        # F0-sysex (and F7-escape) carry a VLQ length then that many bytes; the
        # parser must consume and discard them, leaving only the two notes.
        sysex_f0 = bytes([0x00, 0xF0, 0x03, 0x7E, 0x7F, 0xF7])   # dt=0, F0, len=3
        sysex_f7 = bytes([0x00, 0xF7, 0x02, 0x12, 0x34])         # dt=0, F7, len=2
        track = _NOTE_ON + sysex_f0 + sysex_f7 + _NOTE_OFF + _EOT
        division, events = bdp.parse_midi(_smf([track], division=192, fmt=1))
        self.assertEqual(division, 192)
        wires = [e for e in events if e[1] == "wire"]
        self.assertEqual(len(wires), 2)
        self.assertEqual(wires[0][2], b"\x90\x3c\x40")
        self.assertEqual(wires[1][2], b"\x80\x3c\x40")
        # No sysex payload bytes (0xF0/0xF7) should leak into any wire message.
        for _, _, payload in wires:
            self.assertNotIn(0xF0, payload)
            self.assertNotIn(0xF7, payload)

    def test_unknown_meta_is_skipped(self):
        # An unrecognized meta (0x01 text, here "ABC") is neither tempo nor EOT,
        # so it is consumed and produces no event; the note survives.
        text_meta = bytes([0x00, 0xFF, 0x01, 0x03]) + b"ABC"   # dt=0, FF 01 len=3
        track = text_meta + _NOTE_ON + _EOT
        division, events = bdp.parse_midi(_smf([track], division=192, fmt=1))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1], "wire")
        self.assertEqual(events[0][2], b"\x90\x3c\x40")

    def test_unknown_meta_does_not_emit_tempo(self):
        # A non-0x51 meta with a 3-byte payload must NOT be mistaken for tempo.
        marker_meta = bytes([0x00, 0xFF, 0x7F, 0x03, 0x01, 0x02, 0x03])  # seq-specific
        track = marker_meta + _NOTE_ON + _EOT
        _, events = bdp.parse_midi(_smf([track], division=192, fmt=1))
        self.assertEqual([k for _, k, _ in events], ["wire"])


# ===========================================================================
# THEME: integration against real jeopardy.mid ground truth
# ===========================================================================
@unittest.skipUnless(
    os.path.isfile(JEOPARDY_MID),
    "jeopardy.mid not present at %s" % JEOPARDY_MID,
)
class JeopardyIntegrationTests(unittest.TestCase):
    def _parse_jeopardy(self):
        with open(JEOPARDY_MID, "rb") as f:
            data = f.read()
        return bdp.parse_midi(data)

    def test_jeopardy_division_and_event_count(self):
        division, events = self._parse_jeopardy()
        self.assertEqual(division, 192)
        self.assertEqual(len(events), 586)

    def test_jeopardy_note_on_count(self):
        _, events = self._parse_jeopardy()
        note_ons = sum(
            1 for _, kind, p in events
            if kind == "wire" and (p[0] & 0xF0) == 0x90 and p[2] != 0
        )
        self.assertEqual(note_ons, 283)

    def test_jeopardy_schedule_duration(self):
        division, events = self._parse_jeopardy()
        schedule = bdp.midi_to_wire_schedule(division, events)
        self.assertTrue(schedule)
        self.assertAlmostEqual(schedule[-1][0], 29.664166249999994, places=4)

    def test_jeopardy_first_wire_bytes(self):
        division, events = self._parse_jeopardy()
        schedule = bdp.midi_to_wire_schedule(division, events)
        blob = b"".join(payload for _, payload in schedule)
        self.assertEqual(blob[:8].hex(), "b6071fc678963b40")

    def test_jeopardy_events_tick_sorted(self):
        # The merged stream must be non-decreasing in absolute tick (stable sort).
        _, events = self._parse_jeopardy()
        ticks = [t for t, _, _ in events]
        self.assertEqual(ticks, sorted(ticks))


if __name__ == '__main__':
    unittest.main()
