# -*- mode: python ; coding: python -*-
#
# PyInstaller build spec for the RV2900WD Dock BDP Pump Test Tool.
#
# Builds a single-file, windowed (no console) Windows executable and bundles the
# jeopardy.mid asset alongside it so the "MIDI playback" feature works out of the
# box.  Build with:
#
#     pyinstaller --clean --noconfirm packaging/bdp_tool.spec
#
# The repo root is the working directory PyInstaller is invoked from (see the CI
# workflow), so source paths below are relative to the repo root.

block_cipher = None

a = Analysis(
    ['bdp_dock_pump_test.py'],
    pathex=[],
    binaries=[],
    # (source_on_disk, destination_dir_inside_bundle) -- '.' puts it at the bundle root,
    # which is exactly where resource_path()/sys._MEIPASS looks for it at runtime.
    datas=[('jeopardy.mid', '.')],
    hiddenimports=['serial', 'serial.tools.list_ports'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='BDP Dock Pump Test Tool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # windowed GUI app -- no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='packaging/bdp_tool.ico',   # optional: drop a .ico here to brand the exe
)
