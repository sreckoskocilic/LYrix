# -*- mode: python ; coding: utf-8 -*-
# Build on Windows:
#   pip install pyinstaller lyricsgenius mutagen python-dotenv pyglet
#   pyinstaller LyricsBrowser.spec

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

font_file = "Roboto Mono for Powerline.ttf"
datas = [(font_file, ".")] if os.path.exists(font_file) else []
if os.path.exists(".env"):
    datas.append((".env", "."))

# collect_all picks up datas, binaries, and hidden imports for dynamic packages
lg_d, lg_b, lg_h = collect_all("lyricsgenius")
pyg_d, pyg_b, pyg_h = collect_all("pyglet")
mut_d, mut_b, mut_h = collect_all("mutagen")
dot_d, dot_b, dot_h = collect_all("dotenv")

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=lg_b + pyg_b + mut_b + dot_b,
    datas=datas + lg_d + pyg_d + mut_d + dot_d,
    hiddenimports=(
        lg_h
        + pyg_h
        + mut_h
        + dot_h
        + collect_submodules("lyricsgenius")
        + collect_submodules("mutagen")
        + [
            # tkinter extras sometimes missed on Windows
            "tkinter",
            "tkinter.ttk",
            "tkinter.scrolledtext",
            "tkinter.filedialog",
            "tkinter.messagebox",
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Linux/macOS display backends — not present on Windows
        "pyglet.window.xlib",
        "pyglet.window.wayland",
        "pyglet.canvas.xlib",
        "pyglet.canvas.wayland",
        "pyglet.libs.x11",
        "pyglet.input.x11_xinput",
        "pyglet.app.xlib",
        # macOS backends
        "pyglet.window.cocoa",
        "pyglet.canvas.cocoa",
        "pyglet.app.cocoa",
        "pyglet.input.darwin_hid",
        # optional mypyc-compiled speedup — pure Python fallback is used when absent
        "charset_normalizer.md__mypyc",
        # Wacom tablet input — not needed, suppresses wintab32.dll ctypes warning
        "pyglet.input.wintab",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LyricsBrowser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
