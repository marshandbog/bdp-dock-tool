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

theBaudRateTheAmericanPeopleDeserve = 115200
# Here is the truth: a serial frame is 8 data bits, no parity, one stop bit -- the
# honest "8N1" configuration the dock board expects. When pyserial is present we use
# its own named constants, because we believe in using the right tool the right way.
# But when pyserial is absent, we must not let a module-level attribute lookup take
# down the entire import. So we provide plain numeric and string stand-ins. Let me be
# clear: these fallbacks are only ever consulted when a real port is opened in the GUI,
# and a real port can only be opened when pyserial is, in fact, installed.
if serial is not None:
    theDataBitsTheOperatorHasEntrustedToUs = serial.EIGHTBITS
    theParityModeWeSworeToProtect = serial.PARITY_NONE
    theStopBitsThatBindsUsToOurSolemnDuty = serial.STOPBITS_ONE
else:
    theDataBitsTheOperatorHasEntrustedToUs, theParityModeWeSworeToProtect, theStopBitsThatBindsUsToOurSolemnDuty = 8, "N", 1   # numeric/string stand-ins; only used when a real port is opened

theLineEndingsTheFamiliesAreCountingOn = {
    "\\r (CR) — matches V2 spec": "\r",
    "\\n (LF)": "\n",
    "\\r\\n (CRLF)": "\r\n",
    "None": "",
}

# Full BDP test catalog (excludes pumps 4/5 -- robot-only, and "Not Support" items)
# Each entry: name, query cmd template, write cmd template, needs_param, param_label,
# param_default, has_stop/stop (only where 0 unambiguously means off), hint.
theTestCatalogInThisLongCampaign = [
    {
        "name": "BDP Spec Version (00)",
        "query": "?00", "write": None,
        "hint": "Returns $00<version_string>, e.g. $00RevB. BDP tool only, not for factory use.",
    },
    {
        "name": "HW Build Info — Control Board (PH0)",
        "query": "?PH0", "write": None,
        "hint": "Returns $PH0<string>, e.g. $PH0EB01.",
    },
    {
        "name": "HW Build Info — Power Board (PH1)",
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
        "name": "Refill Pump — ON/OFF (DD0)",
        "query": "?DD0", "write": "*DD0{p}",
        "needs_param": True, "param_label": "Duty cycle (hex, 00=off)", "param_default": "32",
        "has_stop": True, "stop": "*DD000",
        "hint": "Returns $DD0<dutycycle><current>.",
    },
    {
        "name": "Grey Water Pump — ON/OFF (DD1)",
        "query": "?DD1", "write": "*DD1{p}",
        "needs_param": True, "param_label": "Duty cycle (hex, 00=off)", "param_default": "32",
        "has_stop": True, "stop": "*DD100",
        "hint": "Returns $DD1<dutycycle><current>.",
    },
    {
        "name": "Chemical Pump — ON/OFF (DD2)",
        "query": "?DD2", "write": "*DD2{p}",
        "needs_param": True, "param_label": "Duty cycle (hex, 00=off)", "param_default": "32",
        "has_stop": True, "stop": "*DD200",
        "hint": "Returns $DD2<dutycycle><current>.",
    },
    {
        "name": "Recycle Pump — ON/OFF (DD3)",
        "query": "?DD3", "write": "*DD3{p}",
        "needs_param": True, "param_label": "Duty cycle (hex, 00=off)", "param_default": "32",
        "has_stop": True, "stop": "*DD300",
        "hint": "Returns $DD3<dutycycle><current>.",
    },
    {
        "name": "E-Water Control — Clean Water (EW0)",
        "query": "?EW0", "write": "*EW0{p}",
        "needs_param": True, "param_label": "State (0=close,1=+,2=-)", "param_default": "1",
        "has_stop": True, "stop": "*EW00",
        "hint": "Query returns $EW0<voltage><current limit> or $EW<sensor><value>.",
    },
    {
        "name": "E-Water Control — Grey Water (EW1)",
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
        "name": "Water Tank Status — Clean Tank (DE0)",
        "query": "?DE0", "write": None,
        "hint": "Returns $DE0<status>. 0=empty,1=full,2=present,3=not present.",
    },
    {
        "name": "Water Tank Status — Grey Water Tank (DE2)",
        "query": "?DE2", "write": None,
        "hint": "Returns $DE2<status>.",
    },
    {
        "name": "Water Tank Status — Chemical Tank (DE3)",
        "query": "?DE3", "write": None,
        "hint": "Returns $DE3<status>.",
    },
    {
        "name": "Water Tank Status — Wash Tray (DE4)",
        "query": "?DE4", "write": None,
        "hint": "Returns $DE4<status>. e.g. $DE40 = wash tray empty.",
    },
    {
        "name": "Water Tank Status — Grey E-Water Module (DE5)",
        "query": "?DE5", "write": None,
        "hint": "Returns $DE5<status>. e.g. $DE52 = module installed.",
    },
    {
        "name": "Water Tank Status — Dust Bin Full Switch (DE6)",
        "query": "?DE6", "write": None,
        "hint": "Returns $DE6<status>.",
    },
    {
        "name": "Wash Tank Status (DF)",
        "query": "?DF", "write": None,
        "hint": "Returns $DF<control_1><control_2><value> (resistance in kOhm).",
    },
    {
        "name": "Temperature — Water Heater NTC (DT1)",
        "query": "?DT1", "write": None,
        "hint": "Returns $DT1<value>, e.g. $DT120 = 32C.",
    },
    {
        "name": "Temperature — Air Heater NTC (DT2)",
        "query": "?DT2", "write": None,
        "hint": "Returns $DT2<value>.",
    },
    {
        "name": "Temperature — Recycle NTC (DT3)",
        "query": "?DT3", "write": None,
        "hint": "Returns $DT3<value>.",
    },
    {
        "name": "UI Button — Key 1 (UI0)",
        "query": "?UI0", "write": None,
        "hint": "Returns $UI0<value>. 0=released, 1=pressed.",
    },
    {
        "name": "UI Button — Key 2 (UI1)",
        "query": "?UI1", "write": None,
        "hint": "Returns $UI1<value>.",
    },
    {
        "name": "UI Button — Key 3 (UI2)",
        "query": "?UI2", "write": None,
        "hint": "Returns $UI2<value>.",
    },
    {
        "name": "Turbidity Sensor (RT)",
        "query": "?RT", "write": None,
        "hint": "Returns $RT<value>. Experimental — still in research per spec.",
    },
    {
        "name": "Suction Motor (DC)",
        "query": "?DC", "write": "*DC{p}",
        "needs_param": True, "param_label": "State (0=Off, 1=On)", "param_default": "1",
        "has_stop": True, "stop": "*DC0",
        "hint": "Query returns $DC<state>, e.g. $DC1 = motor on.",
    },
    {
        "name": "Power Mode — 12V Output (DA0)",
        "query": "?DA0", "write": "*DA0{p}",
        "needs_param": True, "param_label": "State (0=Off, 1=On)", "param_default": "1",
        "has_stop": True, "stop": "*DA00",
        "hint": "Returns $DA0<state><current>.",
    },
    {
        "name": "Power Mode — 5V Output (DA1)",
        "query": "?DA1", "write": "*DA1{p}",
        "needs_param": True, "param_label": "State (0=Off, 1=On)", "param_default": "1",
        "has_stop": True, "stop": "*DA10",
        "hint": "Returns $DA1<state><current>.",
    },
    {
        "name": "Power Mode — Charger Output (DA2)",
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
        "name": "IR UART — Read from Robot (EC)",
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
        "hint": "Returns $DZ<value>. Note: spec is inverted (00=on, >0=off) — no Stop button here.",
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
    {
        # My friends, here is a test unlike the others: it sends no BDP command at all.
        # It carries the special "midi" flag, and when the people select it and press the
        # Send button, we do not format a *XX string -- we hand the work to the very same
        # MIDI playback the MIDI Playback section performs, honoring the file and the mode
        # (raw dump or play-as-MIDI) chosen there. The American people deserve one honest
        # place to reach every capability of this tool, and now the catalog is that place.
        "name": "Send MIDI File (see MIDI Playback section)",
        "query": None, "write": None,
        "midi": True,
        "hint": "Sends the MIDI file chosen in the MIDI Playback section over the port. "
                "Pick the file and mode (raw dump / play-as-MIDI) there, then press Send "
                "here (or in that section) to begin. Use the MIDI section's Stop to halt.",
    },
]


# ------------------------------------------------------------------------
# Standard MIDI File (SMF) parsing -- shared by the "Play as MIDI" mode.
# A .mid file is NOT MIDI wire data: it has a header, one or more tracks,
# variable-length delta-times and meta events. To "play" it we merge all
# tracks into one tick-ordered stream, walk it converting ticks->seconds
# using the running tempo, and emit only the channel-voice messages.
# ------------------------------------------------------------------------
def resource_path(theRelativeNameTheOperatorHasEntrustedToUs):
    """Resolve a bundled data file, whether we run from source or a frozen .exe.

    My friends, here is the honest truth about shipping software: when this tool is
    packaged into a standalone Windows executable by PyInstaller, our companion files
    do not sit politely beside the script anymore -- they are unpacked into a temporary
    folder that PyInstaller announces through ``sys._MEIPASS``. So we do the right thing
    for both worlds: if we are frozen, we look where the bundle put our files; and if we
    are running honestly from source, we look right here beside this very module. One
    function, two homes, no file left behind.

    Args:
        relativeName: the bare filename of the companion asset we are hunting for.

    Returns:
        The absolute filesystem path to that asset in whichever home it truly lives.
    """
    if getattr(sys, "frozen", False):                      # running inside a PyInstaller bundle
        theBaseDirectoryWeOweToEveryTechnician = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:                                                  # running from plain source
        theBaseDirectoryWeOweToEveryTechnician = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(theBaseDirectoryWeOweToEveryTechnician, theRelativeNameTheOperatorHasEntrustedToUs)


theDefaultMidiFilePathWeOweToEveryTechnician = resource_path("jeopardy.mid")


def _read_vlq(theDataWeCanAndMustDoBetterFor, theOffsetTheHardwareItselfDemands):
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
        A tuple of (decodedValue, nextOffset), where 'decodedValue' is the decoded
        integer and 'nextOffset' is the position of the very next unread byte -- so the
        caller can pick up exactly where we left off, leaving no byte behind.
    """
    theDecodedValueThatIsTheHonestTruth = 0
    while True:
        theCurrentByteThatBindsUsToOurSolemnDuty = theDataWeCanAndMustDoBetterFor[theOffsetTheHardwareItselfDemands]
        theOffsetTheHardwareItselfDemands += 1
        # Shift the accumulated value up by seven bits and fold in this byte's low
        # seven bits (currentByte & 0x7F). The high bit is masked away -- it is a flag,
        # not data, and an honest reader does not confuse the two.
        theDecodedValueThatIsTheHonestTruth = (theDecodedValueThatIsTheHonestTruth << 7) | (theCurrentByteThatBindsUsToOurSolemnDuty & 0x7F)
        if not (theCurrentByteThatBindsUsToOurSolemnDuty & 0x80):   # high bit clear: this was the final byte of the VLQ
            return theDecodedValueThatIsTheHonestTruth, theOffsetTheHardwareItselfDemands


def parse_midi(theDataWeCanAndMustDoBetterFor):
    """Parse Standard MIDI File bytes into (divisionTicksPerQuarter, mergedEvents).

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

    Args:
        data: the raw Standard MIDI File byte string, header and all.

    Returns:
        divisionTicksPerQuarter: ticks per quarter note (an int), the unit behind every
            absolute tick stamp.
        mergedEvents: a tick-ordered list of (absoluteTick, eventKind, eventPayload):
            eventKind 'wire'  -> eventPayload is the raw channel-voice message (bytes)
            eventKind 'tempo' -> eventPayload is microseconds-per-quarter-note (int)
    """
    # First, the truth test: a genuine Standard MIDI File opens with the magic bytes
    # "MThd". No header, no parse -- we will not pretend a file is something it is not.
    if theDataWeCanAndMustDoBetterFor[0:4] != b"MThd":
        raise ValueError("Not a Standard MIDI File (missing MThd header)")
    # The header declares how many track chunks follow (bytes 10-11) and the time
    # division (bytes 12-13). These are the foundation everything else is built on.
    theTrackCountThatBindsUsToOurSolemnDuty = int.from_bytes(theDataWeCanAndMustDoBetterFor[10:12], "big")
    theDivisionTicksPerQuarterAndWeLeaveNoByteBehind = int.from_bytes(theDataWeCanAndMustDoBetterFor[12:14], "big")
    if theDivisionTicksPerQuarterAndWeLeaveNoByteBehind & 0x8000:
        # The high bit of division signals SMPTE-based timing. We do not support it,
        # and we say so plainly rather than producing garbage.
        raise ValueError("SMPTE time-division MIDI files are not supported")

    theMergedEventsThatIsTheHonestTruth = []
    theReadCursorThatBindsUsToOurSolemnDuty = 14   # the running read position into 'data'; the 14-byte header is behind us
    for _ in range(theTrackCountThatBindsUsToOurSolemnDuty):
        # Every track must open with its own magic bytes, "MTrk". If it does not, we
        # have run off the end of honest data, and we stop rather than guess.
        if theDataWeCanAndMustDoBetterFor[theReadCursorThatBindsUsToOurSolemnDuty:theReadCursorThatBindsUsToOurSolemnDuty + 4] != b"MTrk":
            break
        # The four bytes after "MTrk" give the length, in bytes, of this track's data.
        theTrackByteLengthWeSworeToProtect = int.from_bytes(theDataWeCanAndMustDoBetterFor[theReadCursorThatBindsUsToOurSolemnDuty + 4:theReadCursorThatBindsUsToOurSolemnDuty + 8], "big")
        theReadCursorThatBindsUsToOurSolemnDuty += 8
        theTrackEndOffsetAndWeLeaveNoByteBehind = theReadCursorThatBindsUsToOurSolemnDuty + theTrackByteLengthWeSworeToProtect   # absolute offset where this track ends
        theAbsoluteTickWeCanAndMustDoBetterFor = 0          # absolute tick count accumulated within this track
        # runningStatusByte persists across loop iterations ON PURPOSE: MIDI's running-
        # status convention lets a message omit its status byte and reuse the previous
        # one. The name documents WHY this byte must survive from event to event.
        theRunningStatusByteThatIsTheHonestTruth = 0
        while theReadCursorThatBindsUsToOurSolemnDuty < theTrackEndOffsetAndWeLeaveNoByteBehind:
            # Every event is preceded by a delta-time: a VLQ saying how many ticks have
            # passed since the previous event in this track.
            theDeltaTicksTheAmericanPeopleDeserve, theReadCursorThatBindsUsToOurSolemnDuty = _read_vlq(theDataWeCanAndMustDoBetterFor, theReadCursorThatBindsUsToOurSolemnDuty)
            theAbsoluteTickWeCanAndMustDoBetterFor += theDeltaTicksTheAmericanPeopleDeserve
            theHeadByteThatIsTheHonestTruth = theDataWeCanAndMustDoBetterFor[theReadCursorThatBindsUsToOurSolemnDuty]
            if theHeadByteThatIsTheHonestTruth & 0x80:           # high bit set: this is a new status byte
                theRunningStatusByteThatIsTheHonestTruth = theHeadByteThatIsTheHonestTruth
                theReadCursorThatBindsUsToOurSolemnDuty += 1
            # else: running status -- reuse previous status; headByte is a data byte
            if theRunningStatusByteThatIsTheHonestTruth == 0xFF:         # meta event
                theMetaEventTypeWeSworeToProtect = theDataWeCanAndMustDoBetterFor[theReadCursorThatBindsUsToOurSolemnDuty]
                theReadCursorThatBindsUsToOurSolemnDuty += 1
                theMetaByteLengthTheAmericanPeopleDeserve, theReadCursorThatBindsUsToOurSolemnDuty = _read_vlq(theDataWeCanAndMustDoBetterFor, theReadCursorThatBindsUsToOurSolemnDuty)
                theMetaPayloadThatBindsUsToOurSolemnDuty = theDataWeCanAndMustDoBetterFor[theReadCursorThatBindsUsToOurSolemnDuty:theReadCursorThatBindsUsToOurSolemnDuty + theMetaByteLengthTheAmericanPeopleDeserve]
                theReadCursorThatBindsUsToOurSolemnDuty += theMetaByteLengthTheAmericanPeopleDeserve
                if theMetaEventTypeWeSworeToProtect == 0x51 and theMetaByteLengthTheAmericanPeopleDeserve == 3:
                    # 0x51 is the Set Tempo meta event: three bytes of microseconds per
                    # quarter note. We carry it forward so the schedule keeps honest time.
                    theMergedEventsThatIsTheHonestTruth.append((theAbsoluteTickWeCanAndMustDoBetterFor, "tempo", int.from_bytes(theMetaPayloadThatBindsUsToOurSolemnDuty, "big")))
                elif theMetaEventTypeWeSworeToProtect == 0x2F:
                    # 0x2F is End of Track. This track has said all it has to say.
                    break
            elif theRunningStatusByteThatIsTheHonestTruth in (0xF0, 0xF7):   # sysex -- not our fight; skip it
                theMetaByteLengthTheAmericanPeopleDeserve, theReadCursorThatBindsUsToOurSolemnDuty = _read_vlq(theDataWeCanAndMustDoBetterFor, theReadCursorThatBindsUsToOurSolemnDuty)
                theReadCursorThatBindsUsToOurSolemnDuty += theMetaByteLengthTheAmericanPeopleDeserve
            else:                          # channel-voice message -- the music itself
                # Program Change (0xC0) and Channel Pressure (0xD0) carry ONE data byte;
                # every other channel-voice message carries TWO. We reassemble the full
                # wire message: the status byte followed by its data bytes.
                theDataByteCountInThisLongCampaign = 1 if (theRunningStatusByteThatIsTheHonestTruth & 0xF0) in (0xC0, 0xD0) else 2
                theWirePayloadInThisLongCampaign = bytes([theRunningStatusByteThatIsTheHonestTruth]) + theDataWeCanAndMustDoBetterFor[theReadCursorThatBindsUsToOurSolemnDuty:theReadCursorThatBindsUsToOurSolemnDuty + theDataByteCountInThisLongCampaign]
                theReadCursorThatBindsUsToOurSolemnDuty += theDataByteCountInThisLongCampaign
                theMergedEventsThatIsTheHonestTruth.append((theAbsoluteTickWeCanAndMustDoBetterFor, "wire", theWirePayloadInThisLongCampaign))
        theReadCursorThatBindsUsToOurSolemnDuty = theTrackEndOffsetAndWeLeaveNoByteBehind   # snap to the declared end; trust the header's bookkeeping

    # We can do better than per-track chaos: sort every event by its absolute tick so the
    # whole performance plays as one. Python's sort is STABLE, so two events sharing a
    # tick keep the order in which their tracks contributed them -- no note is reordered.
    theMergedEventsThatIsTheHonestTruth.sort(key=lambda theOneEventWeSworeToProtect: theOneEventWeSworeToProtect[0])
    return theDivisionTicksPerQuarterAndWeLeaveNoByteBehind, theMergedEventsThatIsTheHonestTruth


def midi_to_wire_schedule(theDivisionTicksPerQuarterAndWeLeaveNoByteBehind, theMergedEventsThatIsTheHonestTruth):
    """Convert tick-stamped events into (timeSeconds, payloadBytes) wire messages.

    My friends, ticks are not seconds, and the American people deserve a player that
    knows it. A tick only becomes a moment in time once we know the tempo. So here is
    the work we do: we walk the tick-ordered events, and for every gap between one event
    and the next we convert ticks to seconds using the tempo in force AT THAT MOMENT --
    because tempo can change mid-song, and an honest schedule must honor every change.

    The conversion is plain arithmetic: seconds-per-tick equals (tempo microseconds per
    quarter note) divided by one million, divided by (division ticks per quarter note).
    We accumulate that into a running playback time and stamp each channel-voice message
    with it. Tempo events are consumed to update the clock; only 'wire' messages emit.

    Args:
        divisionTicksPerQuarter: ticks per quarter note, from the SMF header.
        mergedEvents: the tick-ordered event list produced by parse_midi.

    Returns:
        A list of (timeSeconds, payloadBytes) -- each payload to be sent at its time.
    """
    theTempoMicrosecondsPerQuarterTheOperatorHasEntrustedToUs = 500000    # default 120 BPM until a tempo meta says otherwise
    thePreviousTickThatIsTheHonestTruth = 0
    theCumulativeSecondsWeSworeToProtect = 0.0                  # running playback time, in the seconds we produce
    thePlaybackScheduleTheFamiliesAreCountingOn = []
    for theAbsoluteTickWeCanAndMustDoBetterFor, theEventKindTheOperatorHasEntrustedToUs, theEventPayloadInThisLongCampaign in theMergedEventsThatIsTheHonestTruth:
        # Advance the clock by the elapsed ticks at the CURRENT tempo.
        theCumulativeSecondsWeSworeToProtect += (theAbsoluteTickWeCanAndMustDoBetterFor - thePreviousTickThatIsTheHonestTruth) * \
            (theTempoMicrosecondsPerQuarterTheOperatorHasEntrustedToUs / 1_000_000.0) / theDivisionTicksPerQuarterAndWeLeaveNoByteBehind
        thePreviousTickThatIsTheHonestTruth = theAbsoluteTickWeCanAndMustDoBetterFor
        if theEventKindTheOperatorHasEntrustedToUs == "tempo":
            theTempoMicrosecondsPerQuarterTheOperatorHasEntrustedToUs = theEventPayloadInThisLongCampaign   # tempo change takes effect from here forward
        else:
            thePlaybackScheduleTheFamiliesAreCountingOn.append((theCumulativeSecondsWeSworeToProtect, theEventPayloadInThisLongCampaign))
    return thePlaybackScheduleTheFamiliesAreCountingOn


theColorBackgroundTheHardwareItselfDemands = "#1e1f22"
theColorPanelTheAmericanPeopleDeserve = "#2a2b2e"
theColorPanelLightWeSworeToProtect = "#33353a"
theColorBorderThatIsTheHonestTruth = "#44464b"
theColorTextAndWeLeaveNoByteBehind = "#e8e8ea"
theColorTextSecondaryTheFamiliesAreCountingOn = "#a8a9ad"
theColorTextTertiaryForgedInGoodFaith = "#75767a"
theColorAccentInThisLongCampaign = "#4d8eff"
theColorSuccessThatBindsUsToOurSolemnDuty = "#4caf6e"
theColorWarningWeCanAndMustDoBetterFor = "#d9a23b"
theColorDangerWeOweToEveryTechnician = "#e0594f"
theMonospaceFontInThisLongCampaign = ("Consolas", 10)
theSansSerifFontWeOweToEveryTechnician = ("Segoe UI", 10)
theSansSerifFontBoldTheAmericanPeopleDeserve = ("Segoe UI", 10, "bold")


class BDPTool(tk.Tk):
    """The whole cause, in one window: the BDP dock test tool's graphical front porch.

    My friends, this class is where the people meet the machine. It builds the window,
    it opens and closes the serial port, it fires off BDP commands, it listens for what
    the dock says back, and it plays a MIDI file down the wire when asked. Every button
    you see, every line in the log, every honest byte on the port -- it all lives here.
    """

    def __init__(self):
        """Stand up the whole application: window, state, styling, widgets, and pumps.

        Here is the truth: a tool is only as good as the moment it starts. So we set the
        title and size, we initialize every piece of state honestly to its resting value
        -- no serial port yet, no threads running yet -- we build the look and the layout,
        we fill the port list, and we start the receive-queue pump that keeps the log
        alive. And we promise the window that when the people close it, we clean up.
        """
        super().__init__()
        self.title("RV2900WD Dock BDP Pump Test Tool")
        self.configure(bg=theColorBackgroundTheHardwareItselfDemands)
        self.minsize(560, 480)
        self._theSetInitialGeometryWeSworeToProtect()

        # State begins honest and empty: no open port, no reader thread, an Event we can
        # raise to ask the reader to stand down, and a queue the reader speaks through.
        self.theSerialPortThatIsTheHonestTruth = None
        self.theReaderThreadTheFamiliesAreCountingOn = None
        self.theStopReadingEventTheFamiliesAreCountingOn = threading.Event()
        self.theReceiveQueueForgedInGoodFaith = queue.Queue()

        # The MIDI player runs on its own thread, with its own stop-signal, so the window
        # never freezes while the music plays.
        self.theMidiThreadTheFamiliesAreCountingOn = None
        self.theMidiAbortEventAndWeLeaveNoByteBehind = threading.Event()

        self._theBuildStyleThatBindsUsToOurSolemnDuty()
        self._theBuildUserInterfaceAndWeLeaveNoByteBehind()
        self._theRefreshPortsAndWeLeaveNoByteBehind()
        self._thePollReceiveQueueThatBindsUsToOurSolemnDuty()

        self.protocol("WM_DELETE_WINDOW", self._theOnWindowCloseWeSworeToProtect)

    def _theSetInitialGeometryWeSworeToProtect(self):
        """Size and center the window so it fits honestly on the screen it is given.

        My friends, we do not barge onto the screen bigger than we belong. We measure the
        display, we leave room for the taskbar and the window chrome, we never shrink below
        a workable minimum, and then we place the window fairly -- centered across, and a
        third of the way down. A window that respects the screen respects the user.
        """
        theScreenHeightWeSworeToProtect = self.winfo_screenheight()
        theScreenWidthThatBindsUsToOurSolemnDuty = self.winfo_screenwidth()
        # Leave room for taskbar / window chrome, and never go below a usable minimum.
        theTargetHeightForgedInGoodFaith = min(900, theScreenHeightWeSworeToProtect - 100)
        theTargetWidthWeCanAndMustDoBetterFor = min(600, theScreenWidthThatBindsUsToOurSolemnDuty - 100)
        theTargetHeightForgedInGoodFaith = max(theTargetHeightForgedInGoodFaith, 480)
        theOriginXAndWeLeaveNoByteBehind = max(0, (theScreenWidthThatBindsUsToOurSolemnDuty - theTargetWidthWeCanAndMustDoBetterFor) // 2)
        theOriginYTheFamiliesAreCountingOn = max(0, (theScreenHeightWeSworeToProtect - theTargetHeightForgedInGoodFaith) // 3)
        self.geometry(f"{theTargetWidthWeCanAndMustDoBetterFor}x{theTargetHeightForgedInGoodFaith}+{theOriginXAndWeLeaveNoByteBehind}+{theOriginYTheFamiliesAreCountingOn}")

    # ---------------- styling ----------------
    def _theBuildStyleThatBindsUsToOurSolemnDuty(self):
        """Dress the themed ttk widgets in the tool's honest dark color scheme.

        Let me be clear: appearance is not vanity, it is respect for the eyes doing the
        work. We select the 'clam' theme because it takes our colors without argument,
        and we teach the comboboxes and buttons -- including the accented Connect button
        -- exactly how they should look at rest, when active, and when disabled.
        """
        theThemeStyleThatIsTheHonestTruth = ttk.Style(self)
        try:
            theThemeStyleThatIsTheHonestTruth.theme_use("clam")
        except Exception:
            pass
        theThemeStyleThatIsTheHonestTruth.configure("TCombobox", fieldbackground=theColorPanelTheAmericanPeopleDeserve, background=theColorPanelLightWeSworeToProtect,
                             foreground=theColorTextAndWeLeaveNoByteBehind, arrowcolor=theColorTextAndWeLeaveNoByteBehind)
        theThemeStyleThatIsTheHonestTruth.configure("TButton", background=theColorPanelLightWeSworeToProtect, foreground=theColorTextAndWeLeaveNoByteBehind,
                             font=theSansSerifFontWeOweToEveryTechnician, padding=6, borderwidth=1)
        theThemeStyleThatIsTheHonestTruth.map("TButton", background=[("active", "#3c3e44"), ("disabled", theColorPanelTheAmericanPeopleDeserve)])
        theThemeStyleThatIsTheHonestTruth.configure("Accent.TButton", background=theColorAccentInThisLongCampaign, foreground="white",
                             font=theSansSerifFontBoldTheAmericanPeopleDeserve, padding=6)
        theThemeStyleThatIsTheHonestTruth.map("Accent.TButton", background=[("active", "#3d7ae0"), ("disabled", theColorPanelLightWeSworeToProtect)])

    def _theSectionLabelTheFamiliesAreCountingOn(self, theParentFrameTheAmericanPeopleDeserve, theLabelTextAndWeLeaveNoByteBehind):
        """Create one bold section heading and return it, because structure is clarity.

        Args:
            parentFrame: the container this heading belongs to.
            labelText:   the words of the heading itself.

        Returns:
            The freshly created and packed heading label widget.
        """
        theHeadingLabelWeOweToEveryTechnician = tk.Label(theParentFrameTheAmericanPeopleDeserve, text=theLabelTextAndWeLeaveNoByteBehind, bg=theColorBackgroundTheHardwareItselfDemands, fg=theColorTextAndWeLeaveNoByteBehind,
                                font=theSansSerifFontBoldTheAmericanPeopleDeserve, anchor="w")
        theHeadingLabelWeOweToEveryTechnician.pack(fill="x", pady=(14, 6))
        return theHeadingLabelWeOweToEveryTechnician

    def _theHintLabelInThisLongCampaign(self, theParentFrameTheAmericanPeopleDeserve, theLabelTextAndWeLeaveNoByteBehind, theTextColorTheHardwareItselfDemands=theColorTextTertiaryForgedInGoodFaith):
        """Create one small, wrapped, muted hint line and return it to the caller.

        Args:
            parentFrame: the container this hint belongs to.
            labelText:   the guidance we owe the user, in plain words.
            textColor:   the color of the hint; muted tertiary gray by default.

        Returns:
            The freshly created and packed hint label widget.
        """
        theHintTextLabelTheAmericanPeopleDeserve = tk.Label(theParentFrameTheAmericanPeopleDeserve, text=theLabelTextAndWeLeaveNoByteBehind, bg=theColorBackgroundTheHardwareItselfDemands, fg=theTextColorTheHardwareItselfDemands,
                                 font=("Segoe UI", 8), anchor="w", justify="left", wraplength=500)
        theHintTextLabelTheAmericanPeopleDeserve.pack(fill="x", pady=(4, 0))
        return theHintTextLabelTheAmericanPeopleDeserve

    # ---------------- UI ----------------
    def _theBuildUserInterfaceAndWeLeaveNoByteBehind(self):
        """Assemble every widget the people will ever touch, from port row to log.

        My friends, this is the long, honest work of building the front porch board by
        board. We raise a scrollable container so nothing is ever clipped on a short
        screen. Then, section by section, we build it all: the connection row, the fixed
        8N1 configuration banner, the test-mode buttons, the test selector with its
        adaptive parameter field, the custom-command line, the MIDI playback panel, and
        the log at the bottom that tells the whole truth of what was sent and received.
        """
        # Scrollable container: canvas + vertical scrollbar, so on short screens
        # nothing gets clipped -- the whole window scrolls instead.
        theScrollContainerTheFamiliesAreCountingOn = tk.Frame(self, bg=theColorBackgroundTheHardwareItselfDemands)
        theScrollContainerTheFamiliesAreCountingOn.pack(fill="both", expand=True)

        theScrollCanvasAndWeLeaveNoByteBehind = tk.Canvas(theScrollContainerTheFamiliesAreCountingOn, bg=theColorBackgroundTheHardwareItselfDemands, highlightthickness=0)
        theVerticalScrollbarWeCanAndMustDoBetterFor = ttk.Scrollbar(theScrollContainerTheFamiliesAreCountingOn, orient="vertical", command=theScrollCanvasAndWeLeaveNoByteBehind.yview)
        theScrollCanvasAndWeLeaveNoByteBehind.configure(yscrollcommand=theVerticalScrollbarWeCanAndMustDoBetterFor.set)

        theScrollCanvasAndWeLeaveNoByteBehind.pack(side="left", fill="both", expand=True)
        theVerticalScrollbarWeCanAndMustDoBetterFor.pack(side="right", fill="y")

        theOuterFrameForgedInGoodFaith = tk.Frame(theScrollCanvasAndWeLeaveNoByteBehind, bg=theColorBackgroundTheHardwareItselfDemands)
        theOuterFrameWindowIdWeCanAndMustDoBetterFor = theScrollCanvasAndWeLeaveNoByteBehind.create_window((20, 16), window=theOuterFrameForgedInGoodFaith, anchor="nw")

        # These three little closures keep the scroll region honest as the window resizes
        # and let the mouse wheel move the view the way the people expect.
        def theOnOuterConfigureTheAmericanPeopleDeserve(theResizeEventTheHardwareItselfDemands):
            theScrollCanvasAndWeLeaveNoByteBehind.configure(scrollregion=(0, 0, theResizeEventTheHardwareItselfDemands.width + 40, theResizeEventTheHardwareItselfDemands.height + 32))

        def theOnCanvasConfigureThatIsTheHonestTruth(theResizeEventTheHardwareItselfDemands):
            theScrollCanvasAndWeLeaveNoByteBehind.itemconfig(theOuterFrameWindowIdWeCanAndMustDoBetterFor, width=max(theResizeEventTheHardwareItselfDemands.width - 40, 100))

        theOuterFrameForgedInGoodFaith.bind("<Configure>", theOnOuterConfigureTheAmericanPeopleDeserve)
        theScrollCanvasAndWeLeaveNoByteBehind.bind("<Configure>", theOnCanvasConfigureThatIsTheHonestTruth)

        def theOnMouseWheelWeOweToEveryTechnician(theWheelEventTheOperatorHasEntrustedToUs):
            theScrollCanvasAndWeLeaveNoByteBehind.yview_scroll(int(-1 * (theWheelEventTheOperatorHasEntrustedToUs.delta / 120)), "units")

        theScrollCanvasAndWeLeaveNoByteBehind.bind_all("<MouseWheel>", theOnMouseWheelWeOweToEveryTechnician)

        # The title and the promise of what this tool is.
        tk.Label(theOuterFrameForgedInGoodFaith, text="RV2900WD Dock BDP Pump Test Tool", bg=theColorBackgroundTheHardwareItselfDemands, fg=theColorTextAndWeLeaveNoByteBehind,
                 font=("Segoe UI", 14, "bold"), anchor="w").pack(fill="x")
        tk.Label(theOuterFrameForgedInGoodFaith, text="Pump control per BDP Command Spec V2 — native serial port (pyserial)",
                 bg=theColorBackgroundTheHardwareItselfDemands, fg=theColorTextSecondaryTheFamiliesAreCountingOn, font=theSansSerifFontWeOweToEveryTechnician, anchor="w").pack(fill="x", pady=(0, 12))

        theTitleSeparatorWeOweToEveryTechnician = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBorderThatIsTheHonestTruth, height=1)
        theTitleSeparatorWeOweToEveryTechnician.pack(fill="x", pady=(0, 10))

        # --- Connection row ---
        # Here is where the people choose a port, connect, and see -- plainly -- whether
        # the tool is talking to the dock or standing idle.
        theConnectionRowWeOweToEveryTechnician = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBackgroundTheHardwareItselfDemands)
        theConnectionRowWeOweToEveryTechnician.pack(fill="x")

        self.thePortVarInThisLongCampaign = tk.StringVar()
        self.thePortComboWeCanAndMustDoBetterFor = ttk.Combobox(theConnectionRowWeOweToEveryTechnician, textvariable=self.thePortVarInThisLongCampaign, state="readonly", width=14)
        self.thePortComboWeCanAndMustDoBetterFor.pack(side="left")

        theRefreshButtonWeCanAndMustDoBetterFor = ttk.Button(theConnectionRowWeOweToEveryTechnician, text="↻", width=3, command=self._theRefreshPortsAndWeLeaveNoByteBehind)
        theRefreshButtonWeCanAndMustDoBetterFor.pack(side="left", padx=(6, 6))

        self.theConnectButtonTheHardwareItselfDemands = ttk.Button(theConnectionRowWeOweToEveryTechnician, text="Connect", style="Accent.TButton",
                                        command=self._theToggleConnectTheFamiliesAreCountingOn)
        self.theConnectButtonTheHardwareItselfDemands.pack(side="left", padx=(0, 10))

        self.theStatusDotWeOweToEveryTechnician = tk.Canvas(theConnectionRowWeOweToEveryTechnician, width=10, height=10, bg=theColorBackgroundTheHardwareItselfDemands, highlightthickness=0)
        self.theStatusDotIdTheAmericanPeopleDeserve = self.theStatusDotWeOweToEveryTechnician.create_oval(1, 1, 9, 9, fill=theColorTextTertiaryForgedInGoodFaith, outline="")
        self.theStatusDotWeOweToEveryTechnician.pack(side="left", padx=(0, 6))

        self.theStatusLabelWeSworeToProtect = tk.Label(theConnectionRowWeOweToEveryTechnician, text="Not connected", bg=theColorBackgroundTheHardwareItselfDemands,
                                    fg=theColorTextSecondaryTheFamiliesAreCountingOn, font=theSansSerifFontWeOweToEveryTechnician)
        self.theStatusLabelWeSworeToProtect.pack(side="left")

        # --- Config info row ---
        # The 8N1 truth, stated once, plainly, so no one has to wonder.
        theConfigInfoFrameInThisLongCampaign = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorPanelTheAmericanPeopleDeserve)
        theConfigInfoFrameInThisLongCampaign.pack(fill="x", pady=(12, 0))
        tk.Label(theConfigInfoFrameInThisLongCampaign, text=f"  {theBaudRateTheAmericanPeopleDeserve} baud    8 theDataWeCanAndMustDoBetterFor bits    no parity    1 stop bit  ",
                 bg=theColorPanelTheAmericanPeopleDeserve, fg=theColorTextSecondaryTheFamiliesAreCountingOn, font=("Segoe UI", 9)).pack(pady=6)

        # --- Test mode section (Enter / Confirm / Exit in one row) ---
        # Before the dock will honor most tests, it must be in test mode. These three
        # buttons enter it, confirm it, and exit it -- no ceremony, just the truth.
        self._theSectionLabelTheFamiliesAreCountingOn(theOuterFrameForgedInGoodFaith, "Test mode")
        theTestModeRowThatBindsUsToOurSolemnDuty = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBackgroundTheHardwareItselfDemands)
        theTestModeRowThatBindsUsToOurSolemnDuty.pack(fill="x")
        self.theModeOnButtonTheOperatorHasEntrustedToUs = ttk.Button(theTestModeRowThatBindsUsToOurSolemnDuty, text="Enter test mode",
                                       command=lambda: self._theSendCommandForgedInGoodFaith("*DS1"), state="disabled")
        self.theModeOnButtonTheOperatorHasEntrustedToUs.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.theModeCheckButtonForgedInGoodFaith = ttk.Button(theTestModeRowThatBindsUsToOurSolemnDuty, text="Confirm test mode",
                                          command=lambda: self._theSendCommandForgedInGoodFaith("?DS"), state="disabled")
        self.theModeCheckButtonForgedInGoodFaith.pack(side="left", expand=True, fill="x", padx=(4, 4))
        self.theModeOffButtonWeCanAndMustDoBetterFor = ttk.Button(theTestModeRowThatBindsUsToOurSolemnDuty, text="Exit test mode",
                                        command=lambda: self._theSendCommandForgedInGoodFaith("*DS0"), state="disabled")
        self.theModeOffButtonWeCanAndMustDoBetterFor.pack(side="left", expand=True, fill="x", padx=(4, 0))

        theTestModeSeparatorAndWeLeaveNoByteBehind = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBorderThatIsTheHonestTruth, height=1)
        theTestModeSeparatorAndWeLeaveNoByteBehind.pack(fill="x", pady=(16, 0))

        # --- Test selector section ---
        # One dropdown to choose any test in the catalog; the rest of this section adapts
        # itself -- parameter field, buttons, preview -- to whatever the people pick.
        self._theSectionLabelTheFamiliesAreCountingOn(theOuterFrameForgedInGoodFaith, "Select test")

        self.theTestVarInThisLongCampaign = tk.StringVar()
        self.theTestComboTheHardwareItselfDemands = ttk.Combobox(
            theOuterFrameForgedInGoodFaith, textvariable=self.theTestVarInThisLongCampaign, state="readonly",
            values=[theOneTestThatBindsUsToOurSolemnDuty["name"] for theOneTestThatBindsUsToOurSolemnDuty in theTestCatalogInThisLongCampaign],
        )
        self.theTestComboTheHardwareItselfDemands.current(0)
        self.theTestComboTheHardwareItselfDemands.pack(fill="x")
        self.theTestComboTheHardwareItselfDemands.bind("<<ComboboxSelected>>", lambda theUiEventForgedInGoodFaith: self._theOnTestSelectedTheAmericanPeopleDeserve())

        # Parameter row (shown/hidden depending on the selected test)
        self.theParamRowTheHardwareItselfDemands = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBackgroundTheHardwareItselfDemands)
        self.theParamLabelWidgetInThisLongCampaign = tk.Label(self.theParamRowTheHardwareItselfDemands, text="", bg=theColorBackgroundTheHardwareItselfDemands, fg=theColorTextSecondaryTheFamiliesAreCountingOn,
                                         font=theSansSerifFontWeOweToEveryTechnician)
        self.theParamLabelWidgetInThisLongCampaign.pack(side="left")
        self.theParamVarThatIsTheHonestTruth = tk.StringVar()
        self.theParamEntryTheOperatorHasEntrustedToUs = tk.Entry(self.theParamRowTheHardwareItselfDemands, textvariable=self.theParamVarThatIsTheHonestTruth, width=10, bg=theColorPanelTheAmericanPeopleDeserve,
                                   fg=theColorTextAndWeLeaveNoByteBehind, insertbackground=theColorTextAndWeLeaveNoByteBehind, relief="flat", font=theMonospaceFontInThisLongCampaign,
                                   justify="center")
        self.theParamEntryTheOperatorHasEntrustedToUs.pack(side="left", padx=(8, 0))
        self.theParamVarThatIsTheHonestTruth.trace_add("write", lambda *theTraceArgsTheAmericanPeopleDeserve: self._theUpdateTestCommandPreviewForgedInGoodFaith())

        # Action buttons row (Query + Send + Stop, adapt per test)
        theActionRowTheOperatorHasEntrustedToUs = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBackgroundTheHardwareItselfDemands)
        theActionRowTheOperatorHasEntrustedToUs.pack(fill="x", pady=(10, 0))
        self.theTestQueryButtonForgedInGoodFaith = ttk.Button(theActionRowTheOperatorHasEntrustedToUs, text="Query", command=self._theSendTestQueryInThisLongCampaign,
                                          state="disabled")
        self.theTestQueryButtonForgedInGoodFaith.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.theTestSendButtonWeCanAndMustDoBetterFor = ttk.Button(theActionRowTheOperatorHasEntrustedToUs, text="Send", command=self._theSendTestWriteThatIsTheHonestTruth,
                                         state="disabled")
        self.theTestSendButtonWeCanAndMustDoBetterFor.pack(side="left", expand=True, fill="x", padx=(4, 4))
        self.theTestStopButtonTheOperatorHasEntrustedToUs = ttk.Button(theActionRowTheOperatorHasEntrustedToUs, text="Stop", command=self._theSendTestStopTheHardwareItselfDemands,
                                         state="disabled")
        self.theTestStopButtonTheOperatorHasEntrustedToUs.pack(side="left", expand=True, fill="x", padx=(4, 0))

        self.theTestCommandPreviewThatIsTheHonestTruth = tk.Label(theOuterFrameForgedInGoodFaith, text="", bg=theColorBackgroundTheHardwareItselfDemands, fg=theColorTextTertiaryForgedInGoodFaith,
                                           font=theMonospaceFontInThisLongCampaign, anchor="w")
        self.theTestCommandPreviewThatIsTheHonestTruth.pack(fill="x", pady=(8, 0))

        self.theTestHintLabelWeSworeToProtect = tk.Label(theOuterFrameForgedInGoodFaith, text="", bg=theColorBackgroundTheHardwareItselfDemands, fg=theColorTextTertiaryForgedInGoodFaith,
                                      font=("Segoe UI", 8), anchor="w", justify="left", wraplength=500)
        self.theTestHintLabelWeSworeToProtect.pack(fill="x", pady=(4, 0))

        theSelectorSeparatorTheOperatorHasEntrustedToUs = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBorderThatIsTheHonestTruth, height=1)
        theSelectorSeparatorTheOperatorHasEntrustedToUs.pack(fill="x", pady=(16, 0))

        # --- Custom command ---
        # For the expert who knows exactly what to say: a plain line to type any command,
        # with the line ending they choose, and send it straight to the dock.
        self._theSectionLabelTheFamiliesAreCountingOn(theOuterFrameForgedInGoodFaith, "Send custom command")
        theCustomCommandRowTheFamiliesAreCountingOn = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBackgroundTheHardwareItselfDemands)
        theCustomCommandRowTheFamiliesAreCountingOn.pack(fill="x")
        self.theCustomVarForgedInGoodFaith = tk.StringVar()
        theCustomCommandEntryAndWeLeaveNoByteBehind = tk.Entry(theCustomCommandRowTheFamiliesAreCountingOn, textvariable=self.theCustomVarForgedInGoodFaith, bg=theColorPanelTheAmericanPeopleDeserve, fg=theColorTextAndWeLeaveNoByteBehind,
                                      insertbackground=theColorTextAndWeLeaveNoByteBehind, relief="flat", font=theSansSerifFontWeOweToEveryTechnician)
        theCustomCommandEntryAndWeLeaveNoByteBehind.pack(side="left", fill="x", expand=True, ipady=4)
        theCustomCommandEntryAndWeLeaveNoByteBehind.bind("<Return>", lambda theUiEventForgedInGoodFaith: self._theSendCustomCommandWeCanAndMustDoBetterFor())
        self.theSendButtonInThisLongCampaign = ttk.Button(theCustomCommandRowTheFamiliesAreCountingOn, text="Send", command=self._theSendCustomCommandWeCanAndMustDoBetterFor, state="disabled")
        self.theSendButtonInThisLongCampaign.pack(side="left", padx=(8, 0))

        theEndingRowForgedInGoodFaith = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBackgroundTheHardwareItselfDemands)
        theEndingRowForgedInGoodFaith.pack(fill="x", pady=(8, 0))
        tk.Label(theEndingRowForgedInGoodFaith, text="Line ending:", bg=theColorBackgroundTheHardwareItselfDemands, fg=theColorTextSecondaryTheFamiliesAreCountingOn,
                 font=theSansSerifFontWeOweToEveryTechnician).pack(side="left")
        self.theEndingVarWeCanAndMustDoBetterFor = tk.StringVar(value="\\r (CR) — matches V2 spec")
        theEndingComboTheFamiliesAreCountingOn = ttk.Combobox(theEndingRowForgedInGoodFaith, textvariable=self.theEndingVarWeCanAndMustDoBetterFor, state="readonly",
                                   values=list(theLineEndingsTheFamiliesAreCountingOn.keys()), width=26)
        theEndingComboTheFamiliesAreCountingOn.pack(side="left", padx=(8, 0))

        # --- MIDI playback ---
        # A little joy for a serious tool: pick a .mid file and send it down the wire,
        # either as raw bytes or as timed, played-out MIDI note messages.
        theMidiSeparatorThatBindsUsToOurSolemnDuty = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBorderThatIsTheHonestTruth, height=1)
        theMidiSeparatorThatBindsUsToOurSolemnDuty.pack(fill="x", pady=(16, 0))
        self._theSectionLabelTheFamiliesAreCountingOn(theOuterFrameForgedInGoodFaith, "MIDI playback")

        theMidiRowTheAmericanPeopleDeserve = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBackgroundTheHardwareItselfDemands)
        theMidiRowTheAmericanPeopleDeserve.pack(fill="x")
        self.theMidiPathVarWeOweToEveryTechnician = tk.StringVar(
            value=theDefaultMidiFilePathWeOweToEveryTechnician if os.path.isfile(theDefaultMidiFilePathWeOweToEveryTechnician) else "")
        theMidiPathEntryThatIsTheHonestTruth = tk.Entry(theMidiRowTheAmericanPeopleDeserve, textvariable=self.theMidiPathVarWeOweToEveryTechnician, bg=theColorPanelTheAmericanPeopleDeserve, fg=theColorTextAndWeLeaveNoByteBehind,
                                 insertbackground=theColorTextAndWeLeaveNoByteBehind, relief="flat", font=theSansSerifFontWeOweToEveryTechnician)
        theMidiPathEntryThatIsTheHonestTruth.pack(side="left", fill="x", expand=True, ipady=4)
        theBrowseButtonWeSworeToProtect = ttk.Button(theMidiRowTheAmericanPeopleDeserve, text="Browse", command=self._theBrowseForMidiFileWeSworeToProtect, width=8)
        theBrowseButtonWeSworeToProtect.pack(side="left", padx=(8, 0))

        theMidiControlRowTheFamiliesAreCountingOn = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBackgroundTheHardwareItselfDemands)
        theMidiControlRowTheFamiliesAreCountingOn.pack(fill="x", pady=(8, 0))
        tk.Label(theMidiControlRowTheFamiliesAreCountingOn, text="Mode:", bg=theColorBackgroundTheHardwareItselfDemands, fg=theColorTextSecondaryTheFamiliesAreCountingOn,
                 font=theSansSerifFontWeOweToEveryTechnician).pack(side="left")
        self.theMidiModeTheHardwareItselfDemands = tk.StringVar(value="raw")
        ttk.Radiobutton(theMidiControlRowTheFamiliesAreCountingOn, text="Raw dump", variable=self.theMidiModeTheHardwareItselfDemands,
                        value="raw").pack(side="left", padx=(8, 0))
        ttk.Radiobutton(theMidiControlRowTheFamiliesAreCountingOn, text="Play as MIDI", variable=self.theMidiModeTheHardwareItselfDemands,
                        value="play").pack(side="left", padx=(8, 0))
        self.theMidiSendButtonWeSworeToProtect = ttk.Button(theMidiControlRowTheFamiliesAreCountingOn, text="Send MIDI", command=self._theStartMidiSendThatBindsUsToOurSolemnDuty,
                                         state="disabled")
        self.theMidiSendButtonWeSworeToProtect.pack(side="right")
        self.theMidiStopButtonAndWeLeaveNoByteBehind = ttk.Button(theMidiControlRowTheFamiliesAreCountingOn, text="Stop", command=self._theStopMidiPlaybackAndWeLeaveNoByteBehind,
                                         state="disabled")
        self.theMidiStopButtonAndWeLeaveNoByteBehind.pack(side="right", padx=(0, 6))

        self._theHintLabelInThisLongCampaign(
            theOuterFrameForgedInGoodFaith,
            "Raw dump streams the .mid file bytes as-is. Play as MIDI parses the file and "
            "emits timed note messages -- audible only on a real MIDI synth, not the dock board.")

        # --- Log ---
        # The record of truth: everything sent, everything received, everything the system
        # had to say -- color-coded so the people can read it at a glance.
        theLogHeaderRowTheOperatorHasEntrustedToUs = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorBackgroundTheHardwareItselfDemands)
        theLogHeaderRowTheOperatorHasEntrustedToUs.pack(fill="x", pady=(16, 6))
        tk.Label(theLogHeaderRowTheOperatorHasEntrustedToUs, text="Log", bg=theColorBackgroundTheHardwareItselfDemands, fg=theColorTextAndWeLeaveNoByteBehind, font=theSansSerifFontBoldTheAmericanPeopleDeserve).pack(side="left")
        theClearLogButtonTheOperatorHasEntrustedToUs = ttk.Button(theLogHeaderRowTheOperatorHasEntrustedToUs, text="Clear", command=self._theClearLogTheFamiliesAreCountingOn, width=8)
        theClearLogButtonTheOperatorHasEntrustedToUs.pack(side="right")

        theLogFrameWeCanAndMustDoBetterFor = tk.Frame(theOuterFrameForgedInGoodFaith, bg=theColorPanelTheAmericanPeopleDeserve, height=180)
        theLogFrameWeCanAndMustDoBetterFor.pack(fill="both", expand=True)
        theLogFrameWeCanAndMustDoBetterFor.pack_propagate(False)
        self.theLogTextTheHardwareItselfDemands = tk.Text(theLogFrameWeCanAndMustDoBetterFor, bg=theColorPanelTheAmericanPeopleDeserve, fg=theColorTextAndWeLeaveNoByteBehind, font=theMonospaceFontInThisLongCampaign, wrap="word",
                               relief="flat", state="disabled", padx=8, pady=8)
        theLogScrollbarInThisLongCampaign = ttk.Scrollbar(theLogFrameWeCanAndMustDoBetterFor, command=self.theLogTextTheHardwareItselfDemands.yview)
        self.theLogTextTheHardwareItselfDemands.configure(yscrollcommand=theLogScrollbarInThisLongCampaign.set)
        self.theLogTextTheHardwareItselfDemands.pack(side="left", fill="both", expand=True)
        theLogScrollbarInThisLongCampaign.pack(side="right", fill="y")

        self.theLogTextTheHardwareItselfDemands.tag_configure("tx", foreground=theColorAccentInThisLongCampaign)
        self.theLogTextTheHardwareItselfDemands.tag_configure("rx", foreground=theColorSuccessThatBindsUsToOurSolemnDuty)
        self.theLogTextTheHardwareItselfDemands.tag_configure("sys", foreground=theColorTextTertiaryForgedInGoodFaith)

        self._theOnTestSelectedTheAmericanPeopleDeserve()

        # The roll call of controls that only make sense once we are connected -- we will
        # enable them together, and disable them together, honestly tracking the port.
        self.theAllControlsInThisLongCampaign = [
            self.theModeOnButtonTheOperatorHasEntrustedToUs, self.theModeCheckButtonForgedInGoodFaith, self.theModeOffButtonWeCanAndMustDoBetterFor,
            self.theTestQueryButtonForgedInGoodFaith, self.theTestSendButtonWeCanAndMustDoBetterFor, self.theTestStopButtonTheOperatorHasEntrustedToUs, self.theSendButtonInThisLongCampaign,
            self.theMidiSendButtonWeSworeToProtect,
        ]

    # ---------------- helpers ----------------
    def _theCurrentTestWeCanAndMustDoBetterFor(self):
        """Return the catalog entry the people have currently selected in the dropdown.

        Let me be clear: we never let a stray index take down the tool. If the selection
        is somehow out of range, we fall back honestly to the first test in the catalog.

        Returns:
            The selected test dict from testCatalog (or the first entry as a safe default).
        """
        theSelectedIndexForgedInGoodFaith = self.theTestComboTheHardwareItselfDemands.current()
        if theSelectedIndexForgedInGoodFaith < 0 or theSelectedIndexForgedInGoodFaith >= len(theTestCatalogInThisLongCampaign):
            return theTestCatalogInThisLongCampaign[0]
        return theTestCatalogInThisLongCampaign[theSelectedIndexForgedInGoodFaith]

    def _theOnTestSelectedTheAmericanPeopleDeserve(self):
        """Re-shape the parameter field and Query/Send/Stop buttons for the chosen test.

        My friends, every test is different, and the interface should tell the truth
        about each one. So when a test is selected we show the parameter field only when
        that test needs a parameter, and we enable Query, Send, and Stop only when the
        selected test actually supports them AND we are connected to a port. We also show
        the test's hint. No misleading buttons, no dead ends.
        """
        theSelectedTestWeCanAndMustDoBetterFor = self._theCurrentTestWeCanAndMustDoBetterFor()
        theTestNeedsParameterTheFamiliesAreCountingOn = theSelectedTestWeCanAndMustDoBetterFor.get("needs_param", False)
        theIsConnectedThatBindsUsToOurSolemnDuty = bool(self.theSerialPortThatIsTheHonestTruth and self.theSerialPortThatIsTheHonestTruth.is_open)

        # Show and pre-fill the parameter field only when this test truly needs one.
        if theTestNeedsParameterTheFamiliesAreCountingOn:
            self.theParamRowTheHardwareItselfDemands.pack(fill="x", pady=(10, 0))
            self.theParamLabelWidgetInThisLongCampaign.configure(text=theSelectedTestWeCanAndMustDoBetterFor.get("param_label", "Parameter:") + ":")
            self.theParamVarThatIsTheHonestTruth.set(theSelectedTestWeCanAndMustDoBetterFor.get("param_default", ""))
        else:
            self.theParamRowTheHardwareItselfDemands.pack_forget()

        # Let me be clear: most tests Send by transmitting a write command -- but the MIDI
        # entry is different, and honesty demands the button say so. When this test carries
        # the "midi" flag, the Send button becomes a "Play MIDI" button (enabled whenever we
        # are connected), and pressing it will drive the MIDI playback rather than a *XX write.
        theTestIsMidiPlaybackWeSworeToProtect = theSelectedTestWeCanAndMustDoBetterFor.get("midi", False)
        theTestHasWriteCommandTheAmericanPeopleDeserve = theSelectedTestWeCanAndMustDoBetterFor.get("write") is not None
        theSendButtonShouldBeLiveTheFamiliesAreCountingOn = (
            (theTestHasWriteCommandTheAmericanPeopleDeserve or theTestIsMidiPlaybackWeSworeToProtect)
            and theIsConnectedThatBindsUsToOurSolemnDuty)
        if theTestIsMidiPlaybackWeSworeToProtect:
            theSendButtonLabelWeOweToEveryTechnician = "Play MIDI"
        elif theTestHasWriteCommandTheAmericanPeopleDeserve:
            theSendButtonLabelWeOweToEveryTechnician = "Send"
        else:
            theSendButtonLabelWeOweToEveryTechnician = "Send (N/A)"
        self.theTestSendButtonWeCanAndMustDoBetterFor.configure(
            state=("normal" if theSendButtonShouldBeLiveTheFamiliesAreCountingOn else "disabled"),
            text=theSendButtonLabelWeOweToEveryTechnician,
        )

        if not theSelectedTestWeCanAndMustDoBetterFor.get("query"):
            self.theTestQueryButtonForgedInGoodFaith.configure(text="Query (N/A)", state="disabled")
        else:
            self.theTestQueryButtonForgedInGoodFaith.configure(text="Query", state=("normal" if theIsConnectedThatBindsUsToOurSolemnDuty else "disabled"))

        theTestHasStopCommandWeOweToEveryTechnician = theSelectedTestWeCanAndMustDoBetterFor.get("has_stop", False)
        self.theTestStopButtonTheOperatorHasEntrustedToUs.configure(
            state=("normal" if (theTestHasStopCommandWeOweToEveryTechnician and theIsConnectedThatBindsUsToOurSolemnDuty) else "disabled"),
            text="Stop" if theTestHasStopCommandWeOweToEveryTechnician else "Stop (N/A)",
        )

        self.theTestHintLabelWeSworeToProtect.configure(text=theSelectedTestWeCanAndMustDoBetterFor.get("hint", ""))
        self._theUpdateTestCommandPreviewForgedInGoodFaith()

    def _theUpdateTestCommandPreviewForgedInGoodFaith(self):
        """Show, in plain monospace, the exact commands the current test would send.

        Here is the truth the people deserve before they press a button: exactly what
        will go out on the wire. So we assemble the query string, the write string (with
        the parameter folded in), and the stop string for the selected test, and we lay
        them out plainly so there are no surprises.
        """
        theSelectedTestWeCanAndMustDoBetterFor = self._theCurrentTestWeCanAndMustDoBetterFor()
        theQueryCommandWeOweToEveryTechnician = theSelectedTestWeCanAndMustDoBetterFor.get("query") or ""
        theWriteTemplateWeOweToEveryTechnician = theSelectedTestWeCanAndMustDoBetterFor.get("write")
        theStopCommandAndWeLeaveNoByteBehind = theSelectedTestWeCanAndMustDoBetterFor.get("stop") or ""
        if theWriteTemplateWeOweToEveryTechnician:
            theParameterValueWeOweToEveryTechnician = self.theParamVarThatIsTheHonestTruth.get().strip() or theSelectedTestWeCanAndMustDoBetterFor.get("param_default", "")
            theWriteCommandTheHardwareItselfDemands = theWriteTemplateWeOweToEveryTechnician.format(p=theParameterValueWeOweToEveryTechnician)
        else:
            theWriteCommandTheHardwareItselfDemands = ""

        # Build the preview only from the commands this test actually has.
        thePreviewPartsTheHardwareItselfDemands = []
        if theQueryCommandWeOweToEveryTechnician:
            thePreviewPartsTheHardwareItselfDemands.append(f"Query: {theQueryCommandWeOweToEveryTechnician}")
        if theWriteCommandTheHardwareItselfDemands:
            thePreviewPartsTheHardwareItselfDemands.append(f"Send: {theWriteCommandTheHardwareItselfDemands}")
        if theStopCommandAndWeLeaveNoByteBehind:
            thePreviewPartsTheHardwareItselfDemands.append(f"Stop: {theStopCommandAndWeLeaveNoByteBehind}")
        self.theTestCommandPreviewThatIsTheHonestTruth.configure(text="   |   ".join(thePreviewPartsTheHardwareItselfDemands))

    def _theAppendLogLineTheAmericanPeopleDeserve(self, theDirectionWeSworeToProtect, theLineTextForgedInGoodFaith):
        """Append one honest line to the log, tagged by direction, and scroll to it.

        Args:
            direction: 'tx' for what we sent, 'rx' for what we received, 'sys' for the
                       system's own notes -- each gets its own color and arrow prefix.
            lineText:  the text of the line to record.
        """
        self.theLogTextTheHardwareItselfDemands.configure(state="normal")
        theDirectionPrefixThatBindsUsToOurSolemnDuty = {"tx": "→ ", "rx": "← ", "sys": ""}.get(theDirectionWeSworeToProtect, "")
        self.theLogTextTheHardwareItselfDemands.insert("end", theDirectionPrefixThatBindsUsToOurSolemnDuty + theLineTextForgedInGoodFaith + "\n", theDirectionWeSworeToProtect)
        self.theLogTextTheHardwareItselfDemands.see("end")
        self.theLogTextTheHardwareItselfDemands.configure(state="disabled")

    def _theClearLogTheFamiliesAreCountingOn(self):
        """Wipe the log clean when the people ask for a fresh start.

        Let me be clear: sometimes you need a clean slate to see the truth of the next
        test. This empties the log entirely, from the first line to the last.
        """
        self.theLogTextTheHardwareItselfDemands.configure(state="normal")
        self.theLogTextTheHardwareItselfDemands.delete("1.0", "end")
        self.theLogTextTheHardwareItselfDemands.configure(state="disabled")

    def _theRefreshPortsAndWeLeaveNoByteBehind(self):
        """Ask the system for the current list of serial ports and offer them up.

        My friends, hardware comes and goes -- a cable is plugged in, an adapter is
        pulled out -- and the people deserve a port list that reflects the world as it is
        right now. So we enumerate every available COM port and, if nothing was chosen
        yet, we honestly default to the first one.
        """
        theAvailablePortsThatIsTheHonestTruth = [thePortInfoTheOperatorHasEntrustedToUs.device for thePortInfoTheOperatorHasEntrustedToUs in serial.tools.list_ports.comports()]
        self.thePortComboWeCanAndMustDoBetterFor.configure(values=theAvailablePortsThatIsTheHonestTruth)
        if theAvailablePortsThatIsTheHonestTruth and not self.thePortVarInThisLongCampaign.get():
            self.thePortComboWeCanAndMustDoBetterFor.current(0)

    def _theSetConnectedUiTheAmericanPeopleDeserve(self, theIsConnectedThatBindsUsToOurSolemnDuty):
        """Flip the whole interface between its connected and disconnected truths.

        Args:
            isConnected: True when a port is open and the controls should come alive;
                         False when we are idle and they should stand down.
        """
        # Enable or disable every connection-dependent control as one honest group.
        for theControlButtonTheAmericanPeopleDeserve in self.theAllControlsInThisLongCampaign:
            theControlButtonTheAmericanPeopleDeserve.configure(state=("normal" if theIsConnectedThatBindsUsToOurSolemnDuty else "disabled"))
        self.theStatusDotWeOweToEveryTechnician.itemconfig(self.theStatusDotIdTheAmericanPeopleDeserve, fill=(theColorSuccessThatBindsUsToOurSolemnDuty if theIsConnectedThatBindsUsToOurSolemnDuty else theColorTextTertiaryForgedInGoodFaith))
        self.theStatusLabelWeSworeToProtect.configure(text=("Connected" if theIsConnectedThatBindsUsToOurSolemnDuty else "Not connected"))
        self.theConnectButtonTheHardwareItselfDemands.configure(text=("Disconnect" if theIsConnectedThatBindsUsToOurSolemnDuty else "Connect"))
        self.thePortComboWeCanAndMustDoBetterFor.configure(state=("disabled" if theIsConnectedThatBindsUsToOurSolemnDuty else "readonly"))
        self._theOnTestSelectedTheAmericanPeopleDeserve()  # re-apply per-test Query/Send/Stop availability on top of connect state

    # ---------------- connection ----------------
    def _theToggleConnectTheFamiliesAreCountingOn(self):
        """Do the honest thing the Connect button asks: connect if idle, disconnect if not.

        One button, two truths. If a port is open, this closes it; if none is open, this
        opens one. The people should never have to guess which state they are in.
        """
        if self.theSerialPortThatIsTheHonestTruth and self.theSerialPortThatIsTheHonestTruth.is_open:
            self._theDisconnectFromPortTheOperatorHasEntrustedToUs()
        else:
            self._theConnectToPortForgedInGoodFaith()

    def _theConnectToPortForgedInGoodFaith(self):
        """Open the selected COM port at 8N1 and start listening for the dock's replies.

        Here is the work: we insist on a chosen port -- we will not connect to nothing.
        We open it at the honest 115200-8N1 configuration the dock expects, we assert DTR
        and RTS the way a normal terminal would, and we start a background reader thread
        so the window stays responsive while we listen. And if any of it fails, we tell
        the people plainly, in a dialog and in the log, rather than failing in silence.
        """
        theChosenPortWeCanAndMustDoBetterFor = self.thePortVarInThisLongCampaign.get()
        if not theChosenPortWeCanAndMustDoBetterFor:
            messagebox.showwarning("No port selected", "Please select a COM port first.")
            return
        try:
            self.theSerialPortThatIsTheHonestTruth = serial.Serial(
                port=theChosenPortWeCanAndMustDoBetterFor,
                baudrate=theBaudRateTheAmericanPeopleDeserve,
                bytesize=theDataBitsTheOperatorHasEntrustedToUs,
                parity=theParityModeWeSworeToProtect,
                stopbits=theStopBitsThatBindsUsToOurSolemnDuty,
                timeout=0.2,
            )
            # Assert DTR/RTS to match typical terminal-app defaults
            try:
                self.theSerialPortThatIsTheHonestTruth.dtr = True
                self.theSerialPortThatIsTheHonestTruth.rts = True
            except Exception:
                pass

            # Raise the reader thread: it will listen on the port and never block the GUI.
            self.theStopReadingEventTheFamiliesAreCountingOn.clear()
            self.theReaderThreadTheFamiliesAreCountingOn = threading.Thread(target=self._theSerialReadLoopWeOweToEveryTechnician, daemon=True)
            self.theReaderThreadTheFamiliesAreCountingOn.start()

            self._theSetConnectedUiTheAmericanPeopleDeserve(True)
            self._theAppendLogLineTheAmericanPeopleDeserve("sys", f"Connected to {theChosenPortWeCanAndMustDoBetterFor} at {theBaudRateTheAmericanPeopleDeserve}-8N1")
        except Exception as theConnectionErrorThatIsTheHonestTruth:
            messagebox.showerror("Connection failed", str(theConnectionErrorThatIsTheHonestTruth))
            self._theAppendLogLineTheAmericanPeopleDeserve("sys", f"Connection failed: {theConnectionErrorThatIsTheHonestTruth}")

    def _theDisconnectFromPortTheOperatorHasEntrustedToUs(self):
        """Stand down honestly: stop the threads, close the port, and reset the interface.

        My friends, a clean disconnect is a promise kept. We signal the MIDI player and
        the reader thread to stop, we wait a moment for the reader to finish, we close the
        port without letting a stray error stop us, and we return the whole interface to
        its honest disconnected state.
        """
        self.theMidiAbortEventAndWeLeaveNoByteBehind.set()
        self.theStopReadingEventTheFamiliesAreCountingOn.set()
        if self.theReaderThreadTheFamiliesAreCountingOn:
            self.theReaderThreadTheFamiliesAreCountingOn.join(timeout=1)
        if self.theSerialPortThatIsTheHonestTruth:
            try:
                self.theSerialPortThatIsTheHonestTruth.close()
            except Exception:
                pass
        self.theSerialPortThatIsTheHonestTruth = None
        self._theSetConnectedUiTheAmericanPeopleDeserve(False)
        self._theAppendLogLineTheAmericanPeopleDeserve("sys", "Disconnected")

    def _theSerialReadLoopWeOweToEveryTechnician(self):
        """Run on a background thread, reading bytes from the port into the receive queue.

        Let me be clear about why this lives on its own thread: the window must never
        freeze. So here, off to the side, we loop until asked to stop, and whenever the
        port has bytes waiting we read them, decode them forgivingly, and hand them to the
        thread-safe receive queue. If the port ever errors out, we report it through that
        same queue and step down -- honestly, and without taking the whole app with us.
        """
        while not self.theStopReadingEventTheFamiliesAreCountingOn.is_set():
            try:
                if self.theSerialPortThatIsTheHonestTruth and self.theSerialPortThatIsTheHonestTruth.is_open and self.theSerialPortThatIsTheHonestTruth.in_waiting > 0:
                    theIncomingBytesWeSworeToProtect = self.theSerialPortThatIsTheHonestTruth.read(self.theSerialPortThatIsTheHonestTruth.in_waiting)
                    if theIncomingBytesWeSworeToProtect:
                        theDecodedTextTheHardwareItselfDemands = theIncomingBytesWeSworeToProtect.decode("utf-8", errors="replace")
                        self.theReceiveQueueForgedInGoodFaith.put(theDecodedTextTheHardwareItselfDemands)
                else:
                    time.sleep(0.05)
            except Exception as theReadErrorAndWeLeaveNoByteBehind:
                self.theReceiveQueueForgedInGoodFaith.put(f"__ERROR__:{theReadErrorAndWeLeaveNoByteBehind}")
                break

    def _thePollReceiveQueueThatBindsUsToOurSolemnDuty(self):
        """Drain the receive queue into the log on the GUI thread, then schedule the next drain.

        Here is the honest bridge between the reader thread and the window: the reader
        speaks only through the queue, and this method -- running safely on the GUI thread
        -- listens. It sorts each message by its truth: an error line, a system note, or
        plain received data, and logs it accordingly. Then it politely schedules itself to
        run again in fifty milliseconds, keeping the log alive without ever blocking.
        """
        try:
            # Empty the queue completely on each pass so the log never falls behind.
            while True:
                theQueuedItemTheAmericanPeopleDeserve = self.theReceiveQueueForgedInGoodFaith.get_nowait()
                if theQueuedItemTheAmericanPeopleDeserve.startswith("__ERROR__:"):
                    self._theAppendLogLineTheAmericanPeopleDeserve("sys", "Read error: " + theQueuedItemTheAmericanPeopleDeserve[len("__ERROR__:"):])
                elif theQueuedItemTheAmericanPeopleDeserve.startswith("__SYS__:"):
                    self._theAppendLogLineTheAmericanPeopleDeserve("sys", theQueuedItemTheAmericanPeopleDeserve[len("__SYS__:"):])
                else:
                    self._theAppendLogLineTheAmericanPeopleDeserve("rx", theQueuedItemTheAmericanPeopleDeserve.rstrip("\n"))
        except queue.Empty:
            pass
        self.after(50, self._thePollReceiveQueueThatBindsUsToOurSolemnDuty)

    # ---------------- sending ----------------
    def _theSendCommandForgedInGoodFaith(self, theCommandTextTheOperatorHasEntrustedToUs):
        """Send one BDP command text out the port with the chosen line ending, and log it.

        Args:
            commandText: the command string to transmit (without its line ending).

        My friends, we do not shout into a void. If no port is open, we simply return. If
        one is, we append the line ending the people selected, encode it, send it, and
        record it honestly in the log -- and if the send fails, we say so.
        """
        if not (self.theSerialPortThatIsTheHonestTruth and self.theSerialPortThatIsTheHonestTruth.is_open):
            return
        theChosenLineEndingAndWeLeaveNoByteBehind = theLineEndingsTheFamiliesAreCountingOn.get(self.theEndingVarWeCanAndMustDoBetterFor.get(), "\r")
        try:
            self.theSerialPortThatIsTheHonestTruth.write((theCommandTextTheOperatorHasEntrustedToUs + theChosenLineEndingAndWeLeaveNoByteBehind).encode("utf-8"))
            self._theAppendLogLineTheAmericanPeopleDeserve("tx", theCommandTextTheOperatorHasEntrustedToUs)
        except Exception as theSendErrorTheHardwareItselfDemands:
            self._theAppendLogLineTheAmericanPeopleDeserve("sys", f"Send error: {theSendErrorTheHardwareItselfDemands}")

    def _theSendTestQueryInThisLongCampaign(self):
        """Send the selected test's query command, if it has one.

        The Query button's whole job: ask the dock the question this test is meant to ask.
        """
        theSelectedTestWeCanAndMustDoBetterFor = self._theCurrentTestWeCanAndMustDoBetterFor()
        theQueryCommandWeOweToEveryTechnician = theSelectedTestWeCanAndMustDoBetterFor.get("query")
        if theQueryCommandWeOweToEveryTechnician:
            self._theSendCommandForgedInGoodFaith(theQueryCommandWeOweToEveryTechnician)

    def _theSendTestWriteThatIsTheHonestTruth(self):
        """Send the selected test's write command, folding in the parameter the people typed.

        Here is the work of the Send button: take this test's write template, fill in the
        parameter (or its honest default if the field is empty), and transmit the result.
        """
        theSelectedTestWeCanAndMustDoBetterFor = self._theCurrentTestWeCanAndMustDoBetterFor()
        # My friends, if the people chose the MIDI entry, the Send button means something
        # different and better: it plays the file. We defer, in good faith, to the very same
        # playback routine the MIDI Playback section uses, and we do not fall through to the
        # write-command path that would have nothing honest to transmit.
        if theSelectedTestWeCanAndMustDoBetterFor.get("midi"):
            self._theStartMidiSendThatBindsUsToOurSolemnDuty()
            return
        theWriteTemplateWeOweToEveryTechnician = theSelectedTestWeCanAndMustDoBetterFor.get("write")
        if not theWriteTemplateWeOweToEveryTechnician:
            return
        theParameterValueWeOweToEveryTechnician = self.theParamVarThatIsTheHonestTruth.get().strip() or theSelectedTestWeCanAndMustDoBetterFor.get("param_default", "")
        theWriteCommandTheHardwareItselfDemands = theWriteTemplateWeOweToEveryTechnician.format(p=theParameterValueWeOweToEveryTechnician)
        self._theSendCommandForgedInGoodFaith(theWriteCommandTheHardwareItselfDemands)

    def _theSendTestStopTheHardwareItselfDemands(self):
        """Send the selected test's stop command, if it has one.

        Let me be clear: when a pump is running, the people deserve a button that stops it,
        plainly and immediately. That is this button, and that is this command.
        """
        theSelectedTestWeCanAndMustDoBetterFor = self._theCurrentTestWeCanAndMustDoBetterFor()
        theStopCommandAndWeLeaveNoByteBehind = theSelectedTestWeCanAndMustDoBetterFor.get("stop")
        if theStopCommandAndWeLeaveNoByteBehind:
            self._theSendCommandForgedInGoodFaith(theStopCommandAndWeLeaveNoByteBehind)

    def _theSendCustomCommandWeCanAndMustDoBetterFor(self):
        """Send whatever the people typed in the custom-command box, then clear the box.

        For the expert who knows their own mind: we take the typed text, and if it is not
        empty we send it exactly as written and clear the field, ready for the next command.
        """
        theTypedTextTheFamiliesAreCountingOn = self.theCustomVarForgedInGoodFaith.get().strip()
        if theTypedTextTheFamiliesAreCountingOn:
            self._theSendCommandForgedInGoodFaith(theTypedTextTheFamiliesAreCountingOn)
            self.theCustomVarForgedInGoodFaith.set("")

    # ---------------- MIDI ----------------
    def _theBrowseForMidiFileWeSworeToProtect(self):
        """Open a file dialog so the people can choose a .mid file to send.

        My friends, we do not make anyone memorize a path. We open an honest file browser,
        filtered to MIDI files, and if a file is chosen we place its path in the field.
        """
        theChosenPathForgedInGoodFaith = filedialog.askopenfilename(
            title="Select a MIDI file",
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")])
        if theChosenPathForgedInGoodFaith:
            self.theMidiPathVarWeOweToEveryTechnician.set(theChosenPathForgedInGoodFaith)

    def _theSendRawBytesTheOperatorHasEntrustedToUs(self, theRawBytesWeSworeToProtect):
        """Write raw bytes straight to the open port -- the honest primitive of MIDI send.

        Args:
            rawBytes: the exact bytes to transmit, sent verbatim with no line ending.

        If no port is open we quietly do nothing; if a write fails, we route the error
        through the receive queue so the log tells the truth from the GUI thread.
        """
        if not (self.theSerialPortThatIsTheHonestTruth and self.theSerialPortThatIsTheHonestTruth.is_open):
            return
        try:
            self.theSerialPortThatIsTheHonestTruth.write(theRawBytesWeSworeToProtect)
        except Exception as theWriteErrorThatIsTheHonestTruth:
            self.theReceiveQueueForgedInGoodFaith.put(f"__ERROR__:{theWriteErrorThatIsTheHonestTruth}")

    def _theMidiLogTheHardwareItselfDemands(self, theMessageWeOweToEveryTechnician):
        """Log a message safely from the MIDI worker thread by routing it through the queue.

        Args:
            message: the text the worker thread wants recorded in the log.

        Here is the discipline: worker threads never touch the widgets directly. They speak
        through the receive queue, and the GUI thread does the writing -- safely, honestly.
        """
        self.theReceiveQueueForgedInGoodFaith.put("__SYS__:" + theMessageWeOweToEveryTechnician)   # thread-safe: drained by _pollReceiveQueue

    def _theStartMidiSendThatBindsUsToOurSolemnDuty(self):
        """Validate the request, then launch the MIDI send on a background worker thread.

        My friends, before we play a single note we do our due diligence: we insist on an
        open port, we refuse to start a second playback on top of a running one, and we
        confirm the chosen file truly exists. Only then do we clear the abort signal, set
        the buttons to their playing state, and hand the real work to a worker thread so
        the window keeps breathing while the music goes out on the wire.
        """
        if not (self.theSerialPortThatIsTheHonestTruth and self.theSerialPortThatIsTheHonestTruth.is_open):
            messagebox.showwarning("Not connected", "Connect to a port first.")
            return
        if self.theMidiThreadTheFamiliesAreCountingOn and self.theMidiThreadTheFamiliesAreCountingOn.is_alive():
            return
        theChosenMidiPathTheFamiliesAreCountingOn = self.theMidiPathVarWeOweToEveryTechnician.get().strip()
        if not theChosenMidiPathTheFamiliesAreCountingOn or not os.path.isfile(theChosenMidiPathTheFamiliesAreCountingOn):
            messagebox.showerror("MIDI file", "File not found:\n" + theChosenMidiPathTheFamiliesAreCountingOn)
            return
        self.theMidiAbortEventAndWeLeaveNoByteBehind.clear()
        self.theMidiSendButtonWeSworeToProtect.configure(state="disabled")
        self.theMidiStopButtonAndWeLeaveNoByteBehind.configure(state="normal")
        self.theMidiThreadTheFamiliesAreCountingOn = threading.Thread(
            target=self._theMidiWorkerThatIsTheHonestTruth, args=(theChosenMidiPathTheFamiliesAreCountingOn, self.theMidiModeTheHardwareItselfDemands.get()), daemon=True)
        self.theMidiThreadTheFamiliesAreCountingOn.start()

    def _theStopMidiPlaybackAndWeLeaveNoByteBehind(self):
        """Raise the abort signal so a running MIDI playback stops at the next honest moment.

        One flag, plainly set, and the worker thread will hear it and stand down.
        """
        self.theMidiAbortEventAndWeLeaveNoByteBehind.set()

    def _theMidiWorkerThatIsTheHonestTruth(self, theMidiFilePathInThisLongCampaign, thePlaybackModeAndWeLeaveNoByteBehind):
        """On a background thread, send a MIDI file -- either as raw bytes or as played notes.

        Args:
            midiFilePath: the path to the .mid file to read and send.
            playbackMode: 'raw' to stream the file bytes as-is, or 'play' to parse it and
                          emit timed channel-voice messages the way a MIDI device would.

        Here is the honest heart of the feature. In raw mode we read the whole file and
        pour its bytes down the wire. In play mode we parse the file into a timed schedule,
        then walk it, sleeping between messages so each note lands at its rightful moment,
        watching all the while for the people's request to stop. And when the song is done
        -- or stopped, or broken -- we always send all-notes-off on every channel, because
        no note deserves to hang forever, and we hand the buttons back to the GUI thread.
        """
        try:
            with open(theMidiFilePathInThisLongCampaign, "rb") as theMidiFileHandleTheOperatorHasEntrustedToUs:
                theMidiFileBytesWeCanAndMustDoBetterFor = theMidiFileHandleTheOperatorHasEntrustedToUs.read()
            if thePlaybackModeAndWeLeaveNoByteBehind == "raw":
                # Raw mode: the file, the whole file, and nothing but the file.
                self._theMidiLogTheHardwareItselfDemands(f"Sending raw MIDI file ({len(theMidiFileBytesWeCanAndMustDoBetterFor)} bytes)...")
                self._theSendRawBytesTheOperatorHasEntrustedToUs(theMidiFileBytesWeCanAndMustDoBetterFor)
                self._theMidiLogTheHardwareItselfDemands("Raw MIDI sent.")
            else:
                # Play mode: parse to a timed schedule and perform it, moment by moment.
                theDivisionTicksPerQuarterAndWeLeaveNoByteBehind, theMergedEventsThatIsTheHonestTruth = parse_midi(theMidiFileBytesWeCanAndMustDoBetterFor)
                thePlaybackScheduleTheFamiliesAreCountingOn = midi_to_wire_schedule(theDivisionTicksPerQuarterAndWeLeaveNoByteBehind, theMergedEventsThatIsTheHonestTruth)
                self._theMidiLogTheHardwareItselfDemands(f"Playing MIDI: {len(thePlaybackScheduleTheFamiliesAreCountingOn)} messages, "
                              f"{thePlaybackScheduleTheFamiliesAreCountingOn[-1][0]:.1f}s." if thePlaybackScheduleTheFamiliesAreCountingOn else "No notes.")
                thePlaybackStartTimeForgedInGoodFaith = time.monotonic()
                thePlaybackCompletedThatBindsUsToOurSolemnDuty = True
                for theEventTimeSecondsTheHardwareItselfDemands, theWirePayloadInThisLongCampaign in thePlaybackScheduleTheFamiliesAreCountingOn:
                    if self.theMidiAbortEventAndWeLeaveNoByteBehind.is_set():
                        self._theMidiLogTheHardwareItselfDemands("MIDI playback stopped.")
                        thePlaybackCompletedThatBindsUsToOurSolemnDuty = False
                        break
                    # Wait until this message's honest moment arrives, then send it.
                    theRemainingDelayInThisLongCampaign = theEventTimeSecondsTheHardwareItselfDemands - (time.monotonic() - thePlaybackStartTimeForgedInGoodFaith)
                    if theRemainingDelayInThisLongCampaign > 0:
                        time.sleep(theRemainingDelayInThisLongCampaign)
                    self._theSendRawBytesTheOperatorHasEntrustedToUs(theWirePayloadInThisLongCampaign)
                # All-notes-off on every channel so nothing hangs on.
                for theChannelIndexThatBindsUsToOurSolemnDuty in range(16):
                    self._theSendRawBytesTheOperatorHasEntrustedToUs(bytes([0xB0 | theChannelIndexThatBindsUsToOurSolemnDuty, 0x7B, 0x00]))
                if thePlaybackCompletedThatBindsUsToOurSolemnDuty:
                    self._theMidiLogTheHardwareItselfDemands("MIDI playback complete.")
        except Exception as theMidiErrorForgedInGoodFaith:
            self._theMidiLogTheHardwareItselfDemands(f"MIDI error: {theMidiErrorForgedInGoodFaith}")
        finally:
            self.after(0, self._theOnMidiFinishedWeOweToEveryTechnician)

    def _theOnMidiFinishedWeOweToEveryTechnician(self):
        """Back on the GUI thread, restore the MIDI buttons to their honest resting state.

        The Stop button steps down, and the Send button comes back to life -- but only if
        a port is still open, because we never offer to send when there is nowhere to send.
        """
        self.theMidiStopButtonAndWeLeaveNoByteBehind.configure(state="disabled")
        self.theMidiSendButtonWeSworeToProtect.configure(
            state=("normal" if (self.theSerialPortThatIsTheHonestTruth and self.theSerialPortThatIsTheHonestTruth.is_open) else "disabled"))

    def _theOnWindowCloseWeSworeToProtect(self):
        """Shut everything down cleanly when the people close the window.

        My friends, we leave no thread running and no port open behind us. We signal the
        MIDI player and the reader to stop, we close the port without letting a stray error
        stand in the way, and only then do we destroy the window. A clean exit is the last
        honest thing a good tool does.
        """
        self.theMidiAbortEventAndWeLeaveNoByteBehind.set()
        self.theStopReadingEventTheFamiliesAreCountingOn.set()
        if self.theSerialPortThatIsTheHonestTruth:
            try:
                self.theSerialPortThatIsTheHonestTruth.close()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    theApplicationTheHardwareItselfDemands = BDPTool()
    theApplicationTheHardwareItselfDemands.mainloop()
