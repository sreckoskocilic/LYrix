# -*- mode: python ; coding: utf-8 -*-
# Build on macOS:
#   pip install pyinstaller lyricsgenius mutagen python-dotenv pyglet
#   pyinstaller LyricsBrowser-macOS.spec
#
# To code-sign (optional, required for Gatekeeper without quarantine):
#   Set CODESIGN_IDENTITY to your "Developer ID Application: ..." identity,
#   or leave None to skip signing (app will need to be approved via System Settings
#   > Privacy & Security the first time it's opened).
#
# Produces:  dist/LyricsBrowser.app

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

CODESIGN_IDENTITY = None  # e.g. "Developer ID Application: Jane Doe (XXXXXXXXXX)"

font_file = "Roboto Mono for Powerline.ttf"
datas = [(font_file, ".")] if os.path.exists(font_file) else []
if os.path.exists(".env"):
    datas.append((".env", "."))

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
        # Windows/Linux display backends — not present on macOS
        "pyglet.window.xlib",
        "pyglet.window.wayland",
        "pyglet.canvas.xlib",
        "pyglet.canvas.wayland",
        "pyglet.libs.x11",
        "pyglet.input.x11_xinput",
        "pyglet.app.xlib",
        "pyglet.input.wintab",
        # optional mypyc-compiled speedup — pure Python fallback is used when absent
        "charset_normalizer.md__mypyc",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LyricsBrowser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX is unreliable on macOS arm64/universal2
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,  # set True only if you need file-open-with support via Apple Events
    target_arch=None,  # None = native arch; use "universal2" for fat binary
    codesign_identity=CODESIGN_IDENTITY,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LyricsBrowser",
)

app = BUNDLE(
    coll,
    name="LyricsBrowser.app",
    icon="LyricsBrowser.icns",
    bundle_identifier="com.lyrix.lyricsbrowser",
    info_plist={
        "CFBundleName": "LyricsBrowser",
        "CFBundleDisplayName": "Lyrics Browser",
        "CFBundleShortVersionString": "1.2.3",
        "CFBundleVersion": "1",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,  # allows dark mode
        "NSAppleEventsUsageDescription": "Lyrics Browser uses Apple Events for file dialogs.",
    },
)
