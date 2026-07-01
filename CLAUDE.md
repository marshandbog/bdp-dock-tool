# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Single-file Python/Tkinter desktop app: a serial terminal for sending **BDP Command Spec V2** commands to the RV2900WD dock board over a Windows COM port. The whole app is `bdp_dock_pump_test.py` — there is no package, build system, or test suite.

## Commands

```bash
pip install pyserial   # only dependency (tkinter ships with Python)
python bdp_dock_pump_test.py
```

Targets Windows COM ports and uses Windows fonts (Consolas/Segoe UI), but pyserial port enumeration works cross-platform.

## Architecture

One class, `BDPTool(tk.Tk)`, holds all state and UI. Two pieces are worth understanding before editing:

- **`TESTS` (module-level list of dicts)** is the source of truth for every device command. Each entry is a self-describing dict (`name`, `query`, `write` template with a `{p}` param placeholder, `stop`, `needs_param`, `param_label`, `param_default`, `hint`). The "Select test" UI, command preview, and Query/Send/Stop button behavior are all generated from these dicts — **to add or change a supported command, edit the `TESTS` list, not the UI code.** Protocol convention: `?XX` queries, `*XX<param>` writes, device replies `$XX<data>`.

- **Threaded serial I/O.** A background daemon thread (`_read_loop`) polls `serial.in_waiting` and pushes received text onto `self.rx_queue` (a `queue.Queue`). The Tk main thread drains that queue via `_poll_rx_queue`, which reschedules itself every 50ms with `self.after`. This is the standard pattern for not blocking the GUI — **never touch Tk widgets from the read thread; route everything through `rx_queue`.** `threading.Event` `stop_reading` signals the thread to exit on disconnect/close.

Serial config is fixed at 115200-8N1 (module constants). All outgoing commands go through `_send`, which appends the user-selected line ending (default `\r` per V2 spec). Button enable/disable is recomputed by `_set_connected_ui` (connection state) layered with `_on_test_selected` (per-test capability, e.g. query-only tests disable Send/Stop).
