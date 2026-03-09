import json
import os
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from .catalog import ENV_ABS_PATH, FONT_NAME, _FROZEN, get_resource_path

_WRITABLE_DIR = Path(sys.executable).parent if _FROZEN else ENV_ABS_PATH
_SETTINGS_PATH = _WRITABLE_DIR / "settings.json"


def _year_sort(year_str: str) -> int:
    """Convert a year string to a sort key. Unknown/missing years sort last."""
    try:
        return int(year_str or 0) or 9999
    except (ValueError, TypeError):
        return 9999


class LyricsBaseApp:
    BG = "#1e1e2e"
    FG = "#cdd6f4"
    ACCENT = "#2ff022"
    ENTRY_BG = "#313244"
    BTN_BG = "#45475a"
    BTN_ACTIVE = "#585b70"

    def __init__(self, master):
        self.master = master
        self.master.configure(bg=self.BG)
        self._busy = False
        self._closing = False
        self._status_after_id = None
        self.status_var = tk.StringVar()
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Font ──────────────────────────────────────────────────────────────────

    def _load_custom_font(self):
        font_path = get_resource_path("Roboto Mono for Powerline.ttf")
        if not font_path.is_file():
            return
        import pyglet

        if sys.platform == "win32":
            pyglet.options["win32_gdi_font"] = True
        pyglet.font.add_file(str(font_path))

    # ── Styles ────────────────────────────────────────────────────────────────

    def _apply_base_styles(self):
        s = ttk.Style(self.master)
        s.theme_use("clam")
        s.configure(
            ".", background=self.BG, foreground=self.FG, fieldbackground=self.ENTRY_BG
        )
        s.configure("TLabel", font=(FONT_NAME, 11), padding=(4, 2))
        s.configure("TEntry", padding=4)
        s.configure(
            "TButton",
            font=(FONT_NAME, 10),
            padding=(12, 4),
            background=self.BTN_BG,
            foreground=self.FG,
        )
        s.map("TButton", background=[("active", self.BTN_ACTIVE)])

    # ── Genius client ─────────────────────────────────────────────────────────

    def _create_genius_client(self, warn: bool = True):
        token = os.getenv("GENIUS_TOKEN", "").strip()
        if not token:
            if warn:
                import tkinter.messagebox as mb

                mb.showwarning(
                    "Missing Token",
                    f"GENIUS_TOKEN is not set.\nAdd it to:\n{ENV_ABS_PATH / '.env'}",
                )
            return None
        from lyricsgenius import Genius

        return Genius(token, verbose=False, timeout=10)

    # ── Output & status ───────────────────────────────────────────────────────

    def _set_output(self, text):
        self.lyrics_window.configure(state="normal")
        self.lyrics_window.delete("1.0", tk.END)
        if text:
            self.lyrics_window.insert(tk.END, text)
        self.lyrics_window.configure(state="disabled")
        self.lyrics_window.yview_moveto(0)

    def _set_status(self, message, duration_ms=0):
        if self._status_after_id is not None:
            self.master.after_cancel(self._status_after_id)
            self._status_after_id = None
        self.status_var.set(message)
        if duration_ms:
            self._status_after_id = self.master.after(
                duration_ms, lambda: self.status_var.set("")
            )

    # ── Thread → UI bridge ────────────────────────────────────────────────────

    def _ui(self, fn, *args):
        if not self._closing:
            self.master.after(0, fn, *args)

    # ── Settings persistence ──────────────────────────────────────────────────

    def _read_settings(self) -> dict:
        try:
            return (
                json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
                if _SETTINGS_PATH.exists()
                else {}
            )
        except Exception:
            return {}

    def _write_settings(self, data: dict):
        try:
            _SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _restore_geometry(self, default: str = ""):
        geom = self._read_settings().get("geometry", {}).get(type(self).__name__, "")
        self.master.geometry(geom if geom else default)

    def _save_geometry(self):
        data = self._read_settings()
        data.setdefault("geometry", {})[type(self).__name__] = self.master.geometry()
        self._write_settings(data)

    # ── Close ─────────────────────────────────────────────────────────────────

    def _on_close(self):
        self._closing = True
        self._save_geometry()
        if self._status_after_id is not None:
            self.master.after_cancel(self._status_after_id)
        self.master.destroy()
