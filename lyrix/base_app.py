import json
import logging
import logging.handlers
import os
import sys
import tkinter as tk

try:
    from .catalog import ENV_ABS_PATH, FONT_NAME, _BASE_DIR, get_resource_path
except ImportError:
    from pathlib import Path as _Path
    import sys as _sys

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from catalog import ENV_ABS_PATH, FONT_NAME, _BASE_DIR, get_resource_path  # type: ignore

_SETTINGS_PATH = _BASE_DIR / "settings.json"
LOG_PATH = _BASE_DIR / "lyrix.log"

# ── Theme colors ──────────────────────────────────────────────────────────────
# Edit these values to customize the appearance of the app.

THEME_BG = "#222222"  # Main window / ScrolledText background
THEME_FG = "#bbbbbb"  # Default text color (Catppuccin-inspired)
THEME_SELECTBG = "#555555"  # Text selection background
THEME_INPUTBG = "#2f2f2f"  # Input field background

# Button colors
BTN_BG = "#2c437d"  # Normal button background
BTN_BG_ACTIVE = "#2e2e2e"  # Button background on hover/click
BTN_BG_DISABLED = "#2a2a2a"  # Disabled button background
BTN_FG = "#dddddd"  # Button text color

# Catalog tree font sizes
TREE_ARTIST_FONT_SIZE = 11  # Artist entries
TREE_ALBUM_FONT_SIZE = 11  # Album entries
TREE_SONG_FONT_SIZE = 11  # Song entries

# Catalog tree colors (defaults; user can override via color pickers)
TREE_ARTIST_COLOR = "#cfd424"  # Artist entries
TREE_ALBUM_COLOR = "#ff8099"  # Album entries
TREE_SONG_COLOR = "#5bc0de"  # Song entries
TREE_MISSING_COLOR = "#6c7086"  # Songs with missing lyrics

# Filter entry placeholder color
FILTER_PLACEHOLDER_COLOR = "#6c7086"


def _setup_logging():
    """Configure logging to file with rotation (max 256 KB, keep 1 backup)."""
    _BASE_DIR.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.ERROR,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                LOG_PATH, maxBytes=256 * 1024, backupCount=1, encoding="utf-8"
            ),
        ],
    )


def _year_sort(year_str: str) -> int:
    """Convert a year string to a sort key. Unknown/missing years sort last."""
    try:
        return int(year_str or 0) or 9999
    except (ValueError, TypeError):
        return 9999


class LyricsBaseApp:
    FONT_SIZE_DEFAULT = 11
    FONT_SIZE_MIN = 6
    FONT_SIZE_MAX = 32

    def __init__(self, master):
        _setup_logging()
        self.master = master
        self._busy = False
        self._closing = False
        self._status_after_id = None
        self.status_var = tk.StringVar()
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)
        self._font_size = self.FONT_SIZE_DEFAULT

    # ── Font ──────────────────────────────────────────────────────────────────

    def _set_app_icon(self):
        """No-op - dock icon handled by macOS bundle."""
        pass

    def _load_custom_font(self):
        font_path = get_resource_path("Roboto Mono for Powerline.ttf")
        if not font_path.is_file():
            return
        import pyglet

        if sys.platform == "win32":
            pyglet.options["win32_gdi_font"] = True
        pyglet.font.add_file(str(font_path))

    # ── Font size ─────────────────────────────────────────────────────────────

    def _bind_font_size_keys(self):
        mod = "Command" if sys.platform == "darwin" else "Control"
        # Use bind_all so the shortcut fires even when a child widget (e.g. ScrolledText) has focus.
        # Bind both <equal> and <plus>/<Shift-equal> to cover Cmd+= and Cmd++ (Shift+=).
        for seq in (f"<{mod}-equal>", f"<{mod}-plus>", f"<{mod}-Shift-equal>"):
            self.master.bind_all(seq, lambda e: self._change_font_size(1))
        self.master.bind_all(f"<{mod}-minus>", lambda e: self._change_font_size(-1))

    def _change_font_size(self, delta: int):
        new_size = max(
            self.FONT_SIZE_MIN, min(self.FONT_SIZE_MAX, self._font_size + delta)
        )
        if new_size == self._font_size:
            return
        self._font_size = new_size
        self.lyrics_window.configure(font=(FONT_NAME, new_size))

    def _restore_font_size(self, settings: dict | None = None):
        if settings is None:
            settings = self._read_settings()
        self._font_size = settings.get("font_size", self.FONT_SIZE_DEFAULT)

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

    def _require_genius_client(self) -> bool:
        if self.genius is None:
            import tkinter.messagebox as mb

            mb.showerror(
                "Error", "GENIUS_TOKEN is missing, so Genius searches are disabled."
            )
            return False
        return True

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
                duration_ms,
                lambda: self.status_var.set("") if not self._closing else None,
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
        content = json.dumps(data, indent=2)
        tmp = _SETTINGS_PATH.with_suffix(".tmp")
        try:
            _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(_SETTINGS_PATH)
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass

    def _restore_geometry(self, default: str = "", settings: dict | None = None):
        if settings is None:
            settings = self._read_settings()
        geom = settings.get("geometry", {}).get(type(self).__name__, "")
        self.master.geometry(geom if geom else default)

    def _collect_settings(self, data: dict) -> dict:
        """Populate data with this app's persistent state. Override to add more."""
        data.setdefault("geometry", {})[type(self).__name__] = self.master.geometry()
        data["font_size"] = self._font_size
        return data

    # ── Close ─────────────────────────────────────────────────────────────────

    def _on_close(self):
        self._closing = True
        data = self._collect_settings(self._read_settings())
        self._write_settings(data)
        if self._status_after_id is not None:
            self.master.after_cancel(self._status_after_id)
        self.master.destroy()
