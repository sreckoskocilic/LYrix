"""Lyrics Browser app — catalog manager with tree view."""

import difflib
import json
import sys
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
import tkinter.scrolledtext as st
from collections import deque
from pathlib import Path
from tkinter import ttk

import ttkbootstrap as tb

try:
    from .base_app import (
        LyricsBaseApp,
        THEME_BG,
        THEME_FG,
        THEME_SELECTBG,
        _year_sort,
        BTN_BG,
        BTN_BG_ACTIVE,
        BTN_BG_DISABLED,
        BTN_FG,
        TREE_ARTIST_COLOR,
        TREE_ALBUM_COLOR,
        TREE_SONG_COLOR,
        TREE_MISSING_COLOR,
        FILTER_PLACEHOLDER_COLOR,
        TREE_ARTIST_FONT_SIZE,
        TREE_ALBUM_FONT_SIZE,
        TREE_SONG_FONT_SIZE,
    )
    from .browser_actions import BrowserActions
    from .browser_search import BrowserSearch
    from .catalog import (
        Catalog,
        CATALOG_PATH,
        FONT_NAME,
        SEPARATOR,
        SONGS_CATEGORY,
        _format_song_header,
        get_resource_path,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from base_app import (  # type: ignore
        LyricsBaseApp,
        THEME_BG,
        THEME_FG,
        THEME_SELECTBG,
        _year_sort,
        BTN_BG,
        BTN_BG_ACTIVE,
        BTN_BG_DISABLED,
        BTN_FG,
        TREE_ARTIST_COLOR,
        TREE_ALBUM_COLOR,
        TREE_SONG_COLOR,
        TREE_MISSING_COLOR,
        FILTER_PLACEHOLDER_COLOR,
        TREE_ARTIST_FONT_SIZE,
        TREE_ALBUM_FONT_SIZE,
        TREE_SONG_FONT_SIZE,
    )
    from browser_actions import BrowserActions  # type: ignore
    from browser_search import BrowserSearch  # type: ignore
    from catalog import (  # type: ignore
        Catalog,
        CATALOG_PATH,
        FONT_NAME,
        SEPARATOR,
        SONGS_CATEGORY,
        _format_song_header,
        get_resource_path,
    )


class LyricsBrowser(LyricsBaseApp, BrowserActions, BrowserSearch):
    def __init__(self, master):
        super().__init__(master)
        self.master.minsize(900, 540)
        self.catalog = Catalog(CATALOG_PATH)

        self._filter_after_id = None
        self._current_entry: dict | None = None
        self._undo_stack: deque[list[dict]] = deque(maxlen=20)
        self._editing = False
        self._filter_entry: ttk.Entry | None = None
        self._album_iid_name: dict[str, str] = {}  # treeview iid → raw album name

        # Load color settings before building UI
        settings = self._read_settings()
        self._lyrics_fg = settings.get("lyrics_fg", THEME_FG)
        self._tree_song_color = settings.get("tree_song_color", TREE_SONG_COLOR)
        self._tree_album_color = settings.get("tree_album_color", TREE_ALBUM_COLOR)
        self._tree_missing_color = settings.get(
            "tree_missing_color", TREE_MISSING_COLOR
        )
        self._current_theme: str = settings.get("theme", "darkly")
        if self._current_theme not in self.VALID_THEMES:
            self._current_theme = "darkly"
        self._expanded_artists: set[str] = set(settings.get("expanded_artists", []))
        self._pending_restore: dict | None = settings.get("last_selected") or None
        self._filter_trace_id: str | None = None
        self._theme_after_id: str | None = None

        self._set_app_icon()
        self._load_custom_font()

        self._restore_font_size(settings)
        self._build_ui()
        # Apply saved lyrics font color after UI is built
        self.lyrics_window.configure(
            fg=self._lyrics_fg,
            selectforeground=self._lyrics_fg,
            insertbackground=self._lyrics_fg,
        )
        self._bind_font_size_keys()
        self._restore_geometry(default="1000x680", settings=settings)
        sash = settings.get("sash", {}).get(type(self).__name__)
        self._sash_target = sash if sash is not None else 420
        self._sash_applied = False
        self._paned.bind("<Configure>", self._on_paned_configure)

        self.genius = self._create_genius_client(warn=False)
        if self.genius is None:
            for btn in self._gated_buttons:
                btn.pack_forget()  # Hide search buttons when no token
            for entry in (self._artist_entry, self._song_entry, self._album_entry):
                entry.configure(state="disabled")

        mod = "Command" if sys.platform == "darwin" else "Control"
        self.master.bind_all(f"<{mod}-z>", lambda e: self._undo_remove())
        self.master.bind_all(f"<{mod}-f>", lambda e: self._focus_filter())
        self.master.bind_all(f"<{mod}-e>", lambda e: self._export_catalog())
        self.master.bind_all(f"<{mod}-m>", lambda e: self._fetch_missing())
        self.master.bind_all(f"<{mod}-i>", lambda e: self._show_stats())
        self.master.bind_all(
            "<Escape>",
            lambda e: self._cancel_edit() if self._editing else self._clear_filter(),
        )
        self.master.bind_all("<question>", lambda e: self._show_shortcuts())

        self._artist_entry.bind("<Return>", lambda e: self._search_song_lyrics())
        self._song_entry.bind("<Return>", lambda e: self._search_song_lyrics())

        self._refresh_tree()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Apply theme BEFORE creating any widgets to avoid TclError
        self.master.style.theme_use(self._current_theme)

        outer = ttk.Frame(self.master, padding=10)
        outer.pack(fill="both", expand=True)

        self._paned = ttk.PanedWindow(outer, orient="horizontal")
        self._paned.pack(fill="both", expand=True)

        self._paned.add(self._build_catalog_panel(self._paned), weight=1)
        self._paned.add(self._build_viewer_panel(self._paned), weight=3)

    def _build_catalog_panel(self, parent):
        frame = ttk.Frame(parent, padding=(0, 0, 8, 0))

        header_frame = ttk.Frame(frame)
        header_frame.pack(fill="x", pady=(0, 2))
        ttk.Label(header_frame, text="Catalog", font=(FONT_NAME, 11, "bold")).pack(
            side="left"
        )
        self.catalog_count_var = tk.StringVar()
        ttk.Label(
            header_frame, textvariable=self.catalog_count_var, font=(FONT_NAME, 9)
        ).pack(side="left", padx=(8, 0))

        # Theme switcher
        self._theme_var = tk.StringVar(value=self._current_theme)
        self._theme_combo = ttk.Combobox(
            header_frame,
            textvariable=self._theme_var,
            values=self.VALID_THEMES,
            state="readonly",
            width=10,
        )
        self._theme_combo.pack(side="right")
        self._theme_combo.bind("<<ComboboxSelected>>", self._on_theme_change)

        self.filter_var = tk.StringVar()
        search_frame = ttk.Frame(frame)
        search_frame.pack(fill="x", pady=(0, 8))
        search_frame.columnconfigure(1, weight=1)

        ttk.Label(search_frame, text="Artist:").grid(
            row=0, column=0, sticky="e", padx=(0, 6), pady=3
        )
        self._artist_entry = ttk.Entry(search_frame, font=(FONT_NAME, 11))
        self._artist_entry.grid(row=0, column=1, sticky="ew", pady=3)

        ttk.Label(search_frame, text="Song:").grid(
            row=1, column=0, sticky="e", padx=(0, 6), pady=3
        )
        self._song_entry = ttk.Entry(search_frame, font=(FONT_NAME, 11))
        self._song_entry.grid(row=1, column=1, sticky="ew", pady=3)

        ttk.Label(search_frame, text="Album:").grid(
            row=2, column=0, sticky="e", padx=(0, 6), pady=3
        )
        self._album_entry = ttk.Entry(search_frame, font=(FONT_NAME, 11))
        self._album_entry.grid(row=2, column=1, sticky="ew", pady=3)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=(0, 8))
        self._search_song_btn = ttk.Button(
            btn_frame, text="Song Lyrics", command=self._search_song_lyrics
        )
        self._search_song_btn.pack(side="left", padx=(0, 6), expand=True, fill="x")
        self._search_album_btn = ttk.Button(
            btn_frame, text="Album Lyrics", command=self._search_album_lyrics
        )
        self._search_album_btn.pack(side="left", padx=(0, 6), expand=True, fill="x")
        self._search_artist_btn = ttk.Button(
            btn_frame, text="Artist", command=self._search_artist_songs
        )
        self._search_artist_btn.pack(side="left", expand=True, fill="x")

        self._filter_entry = ttk.Entry(
            frame, textvariable=self.filter_var, font=(FONT_NAME, 10)
        )
        self._filter_entry.pack(fill="x", pady=(0, 4))
        self._filter_entry.bind(
            "<FocusIn>", lambda e: self._filter_focus_in(self._filter_entry)
        )
        self._filter_entry.bind(
            "<FocusOut>", lambda e: self._filter_focus_out(self._filter_entry)
        )
        self._filter_placeholder = True
        self._filter_entry.insert(0, "Filter…")
        self._filter_entry.configure(foreground=FILTER_PLACEHOLDER_COLOR)

        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Return>", self._on_tree_select)
        self.tree.bind("<Up>", self._on_tree_arrow)
        self.tree.bind("<Down>", self._on_tree_arrow)
        btn = "<Button-2>" if sys.platform == "darwin" else "<Button-3>"
        self.tree.bind(btn, self._on_tree_right_click)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewClose>>", self._on_tree_close)
        self._filter_trace_id = self.filter_var.trace_add(
            "write", self._on_filter_change
        )

        self.tree.tag_configure(
            "artist",
            font=(FONT_NAME, TREE_ARTIST_FONT_SIZE, "bold"),
            foreground=TREE_ARTIST_COLOR,
        )
        self.tree.tag_configure(
            "album",
            font=(FONT_NAME, TREE_ALBUM_FONT_SIZE),
            foreground=self._tree_album_color,
        )
        self.tree.tag_configure(
            "song",
            font=(FONT_NAME, TREE_SONG_FONT_SIZE),
            foreground=self._tree_song_color,
        )
        self.tree.tag_configure(
            "missing",
            font=(FONT_NAME, TREE_SONG_FONT_SIZE),
            foreground=self._tree_missing_color,
        )

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", pady=(4, 2))
        ttk.Button(btn_row, text="Remove", command=self._remove_selected).pack(
            side="left", padx=(0, 4), expand=True, fill="x"
        )
        self._update_btn = ttk.Button(
            btn_row, text="Update", command=self._update_selected
        )
        self._update_btn.pack(side="left", padx=(0, 4), expand=True, fill="x")
        self._save_btn = ttk.Button(btn_row, text="Save", command=self._save_lyrics)
        self._save_btn.pack(side="left", expand=True, fill="x")

        self._gated_buttons = [
            self._search_song_btn,
            self._search_album_btn,
            self._search_artist_btn,
            self._update_btn,
        ]

        self._progress = ttk.Progressbar(frame, mode="indeterminate")
        self._progress.pack(fill="x", pady=(4, 0))
        ttk.Label(frame, textvariable=self.status_var, font=(FONT_NAME, 9)).pack(
            anchor="w", pady=(2, 0)
        )

        return frame

    def _build_viewer_panel(self, parent):
        frame = ttk.Frame(parent, padding=(8, 0, 0, 0))

        self.lyrics_window = st.ScrolledText(
            frame,
            font=(FONT_NAME, self._font_size),
            fg=self._lyrics_fg,
            bg=THEME_BG,
            selectbackground=THEME_SELECTBG,
            selectforeground=self._lyrics_fg,
            insertbackground=self._lyrics_fg,
            borderwidth=0,
            relief="flat",
            padx=8,
            pady=8,
            state="disabled",
        )
        self.lyrics_window.pack(fill="both", expand=True)
        ctrl_frame = ttk.Frame(frame)
        ctrl_frame.pack(fill="x", pady=(4, 0))
        self._edit_btn = ttk.Button(
            ctrl_frame,
            text="Edit",
            width=10,
            command=self._toggle_edit,
            state="disabled",
        )
        self._edit_btn.pack(side="right")
        self._copy_btn = ttk.Button(
            ctrl_frame,
            text="Copy",
            width=10,
            command=self._copy_lyrics,
            state="disabled",
        )
        self._copy_btn.pack(side="right", padx=(0, 4))
        ttk.Button(
            ctrl_frame, text="Text Color", width=10, command=self._pick_fg_color
        ).pack(side="left")
        ttk.Button(
            ctrl_frame, text="Tree Color", width=10, command=self._pick_tree_color
        ).pack(side="left", padx=(4, 0))
        return frame

    def _pick_fg_color(self):
        from tkinter.colorchooser import askcolor

        color = askcolor(color=self._lyrics_fg, title="Choose text color")[1]
        if color:
            self._lyrics_fg = color
            self.lyrics_window.configure(
                fg=color, selectforeground=color, insertbackground=color
            )
            data = self._read_settings()
            data["lyrics_fg"] = color
            self._write_settings(data)

    def _pick_tree_color(self):
        from tkinter.colorchooser import askcolor

        color = askcolor(color=self._tree_song_color, title="Choose tree song color")[1]
        if color:
            self._tree_song_color = color
            self.tree.tag_configure("song", foreground=color)
            data = self._read_settings()
            data["tree_song_color"] = color
            self._write_settings(data)

    # ── Filter placeholder ────────────────────────────────────────────────────

    def _on_filter_change(self, *_):
        if self._filter_placeholder:
            return
        if self._filter_after_id is not None:
            self.master.after_cancel(self._filter_after_id)
        self._filter_after_id = self.master.after(300, self._refresh_tree)

    def _clear_filter(self):
        if self._filter_entry is None or self._filter_placeholder:
            return
        self._filter_placeholder = True
        self._filter_entry.delete(0, tk.END)
        self._filter_entry.insert(0, "Filter…")
        self._filter_entry.configure(foreground=FILTER_PLACEHOLDER_COLOR)
        if self._filter_after_id is not None:
            self.master.after_cancel(self._filter_after_id)
            self._filter_after_id = None
        self._refresh_tree()

    def _filter_focus_in(self, entry):
        if self._filter_placeholder:
            entry.delete(0, tk.END)
            entry.configure(foreground=THEME_FG)
            self._filter_placeholder = False

    def _filter_focus_out(self, entry):
        if not self.filter_var.get():
            self._filter_placeholder = True
            entry.insert(0, "Filter…")
            entry.configure(foreground=FILTER_PLACEHOLDER_COLOR)

    # ── Catalog browser ───────────────────────────────────────────────────────

    def _fuzzy_match(self, query: str, text: str, threshold: float = 0.6) -> bool:
        """Return True if query fuzzy-matches text above threshold."""
        return difflib.SequenceMatcher(None, query, text).ratio() >= threshold

    def _refresh_tree(self):
        if self._filter_after_id is not None:
            self.master.after_cancel(self._filter_after_id)
            self._filter_after_id = None
        self.catalog.reload()
        raw_filter = (
            "" if self._filter_placeholder else self.filter_var.get().strip().lower()
        )

        raw_entries = self.catalog.all_entries()
        total_count = len(raw_entries)

        keyed4 = [
            (e, e["artist"].lower(), e["title"].lower(), (e.get("album") or "").lower())
            for e in raw_entries
        ]

        artist_count = len({t[1] for t in keyed4})

        if raw_filter:
            use_fuzzy = len(raw_filter) >= 3
            keyed4 = [
                t
                for t in keyed4
                if raw_filter in t[1]
                or raw_filter in t[2]
                or raw_filter in t[3]
                or (
                    use_fuzzy
                    and (
                        self._fuzzy_match(raw_filter, t[1])
                        or self._fuzzy_match(raw_filter, t[2])
                        or self._fuzzy_match(raw_filter, t[3])
                    )
                )
            ]

        canon_year: dict[tuple, str] = {}
        keyed = []
        for e, al, tl, alb in keyed4:
            bk = (al, alb)
            if not canon_year.get(bk):
                canon_year[bk] = e.get("year", "")
            keyed.append((e, al, tl, alb, bk))

        entries = sorted(
            keyed,
            key=lambda x: (
                x[1],  # artist_lower
                _year_sort(canon_year.get(x[4], "")),
                x[3],  # album_lower
                x[0].get("track") or 9999,
                x[2],  # title_lower
            ),
        )

        self.tree.delete(*self.tree.get_children())
        self._album_iid_name.clear()
        artist_nodes: dict[str, str] = {}
        album_nodes: dict[tuple, str] = {}

        for entry, al, _tl, alb, bk in entries:
            artist = entry["artist"] or "Unknown Artist"
            album = entry.get("album") or SONGS_CATEGORY
            year = canon_year.get(bk, "")

            if al not in artist_nodes:
                artist_nodes[al] = self.tree.insert(
                    "",
                    "end",
                    text=artist,
                    open=(artist in self._expanded_artists),
                    tags=("artist",),
                )
            if bk not in album_nodes:
                year_part = f" ({year})" if year else ""
                album_label = f"{album}{year_part}"
                album_iid = self.tree.insert(
                    artist_nodes[al],
                    "end",
                    text=album_label,
                    open=bool(raw_filter),
                    tags=("album",),
                )
                album_nodes[bk] = album_iid
                self._album_iid_name[album_iid] = album
            track_num = entry.get("track", 0)
            song_label = (
                f"{track_num}. {entry['title']}" if track_num else entry["title"]
            )
            has_lyrics = bool(entry.get("lyrics", "").strip())
            self.tree.insert(
                album_nodes[bk],
                "end",
                text=song_label,
                values=(entry["artist"], entry["title"], entry.get("album", "")),
                tags=("song" if has_lyrics else "missing",),
            )

        if raw_filter:
            self.catalog_count_var.set(
                f"{len(keyed)} of {total_count} song{'s' if total_count != 1 else ''}"
            )
        else:
            self.catalog_count_var.set(
                f"{artist_count} artist{'s' if artist_count != 1 else ''}"
                f" · {total_count} song{'s' if total_count != 1 else ''}"
            )

        # Prune stale artist names from the cache (e.g. after removing an artist)
        self._expanded_artists &= {
            self.tree.item(iid, "text") for iid in artist_nodes.values()
        }

        # Restore the last-viewed song if no filter is active.
        # _pending_restore carries the saved selection from settings and is consumed once
        # on startup; after that _current_entry is the authoritative source.
        if not raw_filter:
            if self._pending_restore is not None:
                restore = self._pending_restore
                self._pending_restore = None
            elif self._current_entry:
                restore = {
                    "artist": self._current_entry["artist"],
                    "title": self._current_entry["title"],
                    "album": self._current_entry.get("album", ""),
                }
            else:
                restore = None
            if restore:
                entry = self.catalog.get(
                    restore.get("artist", ""),
                    restore.get("title", ""),
                    restore.get("album", ""),
                )
                if entry:
                    self._current_entry = entry
                    self._edit_btn.configure(state="normal")
                    self._copy_btn.configure(state="normal")
                    self._show_entry(entry)

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        if not values:
            return
        artist, title = values[0], values[1]
        album = values[2]
        entry = self.catalog.get(artist, title, album)
        if not entry:
            return
        if self._editing:
            self._cancel_edit()
        self._current_entry = entry
        self._edit_btn.configure(state="normal")
        self._copy_btn.configure(state="normal")
        self._show_entry(entry)
        self.master.title(f"{entry['title']} — Lyrics Browser")

    def _on_tree_open(self, _event=None):
        item = self.tree.focus()
        if item and "artist" in self.tree.item(item, "tags"):
            self._expanded_artists.add(self.tree.item(item, "text"))

    def _on_tree_close(self, _event=None):
        item = self.tree.focus()
        if item and "artist" in self.tree.item(item, "tags"):
            self._expanded_artists.discard(self.tree.item(item, "text"))

    def _on_tree_arrow(self, _event=None):
        """Handle arrow key navigation in tree view."""
        if self.tree.selection():
            # <<TreeviewSelect>> already fired via the key event — don't double-call
            return
        # Nothing selected: pick the first song in the tree
        for artist_node in self.tree.get_children():
            for album_node in self.tree.get_children(artist_node):
                song_items = self.tree.get_children(album_node)
                if song_items:
                    self.tree.selection_set(song_items[0])
                    self._on_tree_select()
                    return

    def _remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        tags = self.tree.item(item, "tags")
        values = self.tree.item(item, "values")

        if ("song" in tags or "missing" in tags) and values:
            artist, title = values[0], values[1]
            album = values[2]
            if mb.askyesno("Remove", f'Remove "{title}" from catalog?'):
                entry = self.catalog.get(artist, title, album)
                if entry:
                    self._push_undo([entry])
                self.catalog.remove(artist, title, album)
                self._refresh_tree()
                self._set_status(f"Removed: {title}", duration_ms=4000)

        elif "album" in tags:
            children_values = [
                vals
                for c in self.tree.get_children(item)
                if (vals := self.tree.item(c, "values"))
            ]
            if children_values and mb.askyesno(
                "Remove Album",
                f"Remove all {len(children_values)} song(s) in this album from the catalog?",
            ):
                songs = [(vals[0], vals[1], vals[2]) for vals in children_values]
                entries = [
                    e
                    for vals in children_values
                    if (e := self.catalog.get(vals[0], vals[1], vals[2]))
                ]
                if entries:
                    self._push_undo(entries)
                self.catalog.remove_album_entries(songs)
                self._refresh_tree()
                self._set_status(f"Removed {len(songs)} songs", duration_ms=4000)

        elif "artist" in tags:
            artist_name = self.tree.item(item, "text")
            entries = self.catalog.find_by_artist(artist_name)
            count = len(entries)
            if count and mb.askyesno(
                "Remove Artist",
                f'Remove all {count} song(s) by "{artist_name}" from the catalog?',
            ):
                self._push_undo(entries)
                removed = self.catalog.remove_artist(artist_name)
                self._refresh_tree()
                self._set_status(f"Removed {removed} songs", duration_ms=4000)

    def _show_entry(self, entry: dict):
        album_str = entry.get("album") or "Unknown album"
        year_str = entry.get("year", "")
        self._set_output(
            _format_song_header(entry["artist"], entry["title"], album_str, year_str)
            + entry["lyrics"]
        )

    # ── Settings persistence ──────────────────────────────────────────────────

    def _on_close(self):
        if self._filter_trace_id is not None:
            try:
                self.filter_var.trace_remove("write", self._filter_trace_id)
            except Exception:
                pass
        if self._theme_after_id is not None:
            try:
                self.master.after_cancel(self._theme_after_id)
            except Exception:
                pass
        # Force-close the theme dropdown before destroy; an open popdown causes
        # a TclError when the window is destroyed while the dropdown is visible.
        try:
            self._theme_combo.event_generate("<Escape>")
        except Exception:
            pass
        super()._on_close()

    def _on_paned_configure(self, event):
        if self._sash_applied:
            return
        if self._paned.winfo_width() < 10:
            return
        self._paned.sashpos(0, self._sash_target)
        self._sash_applied = True

    def _collect_settings(self, data: dict) -> dict:
        data = super()._collect_settings(data)
        data.setdefault("sash", {})[type(self).__name__] = self._paned.sashpos(0)
        data["expanded_artists"] = list(self._expanded_artists)
        # Save last selected song
        if self._current_entry:
            data["last_selected"] = {
                "artist": self._current_entry["artist"],
                "title": self._current_entry["title"],
                "album": self._current_entry.get("album", ""),
            }
        return data

    # ── Busy state ────────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.master.configure(cursor="watch" if busy else "")
        if busy:
            if self._editing:
                self._cancel_edit()
            self._progress.start(15)
            state = "disabled"
        else:
            self._progress.stop()
            if self.genius is not None:
                state = "normal"
            else:
                return
        for btn in self._gated_buttons:
            btn.configure(state=state)

    # ── Right-click context menu ──────────────────────────────────────────────

    def _on_tree_right_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            menu = tk.Menu(self.master, tearoff=0)
            menu.add_command(label="Export Catalog…", command=self._export_catalog)
            menu.add_command(label="Fetch Missing Lyrics", command=self._fetch_missing)
            menu.add_command(label="Show Stats", command=self._show_stats)
            menu.tk_popup(event.x_root, event.y_root)
            return
        self.tree.selection_set(item)
        tags = self.tree.item(item, "tags")

        menu = tk.Menu(self.master, tearoff=0)
        genius_available = self.genius is not None and not self._busy
        if "song" in tags or "missing" in tags:
            menu.add_command(label="Remove Song", command=self._remove_selected)
        elif "album" in tags:
            menu.add_command(label="Remove Album", command=self._remove_selected)
        elif "artist" in tags:
            if genius_available:
                menu.add_command(
                    label="Import All Releases", command=self._import_artist_releases
                )
                menu.add_separator()
            menu.add_command(label="Remove Artist", command=self._remove_selected)

        if menu.index("end") is not None:
            menu.add_separator()
            menu.add_command(label="Export Catalog…", command=self._export_catalog)
            if genius_available:
                menu.add_command(
                    label="Fetch Missing Lyrics", command=self._fetch_missing
                )
            menu.add_command(label="Show Stats", command=self._show_stats)
            menu.tk_popup(event.x_root, event.y_root)

    # ── Export catalog ────────────────────────────────────────────────────────

    def _export_catalog(self):
        path_str = fd.asksaveasfilename(
            title="Export catalog",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="lyrics_catalog.json",
        )
        if not path_str:
            return
        entries = self.catalog.all_entries()
        content = json.dumps(
            {
                "version": 1,
                "entries": {
                    Catalog._key(e["artist"], e["title"], e.get("album", "")): e
                    for e in entries
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        try:
            Path(path_str).write_text(content, encoding="utf-8")
            self._set_status(
                f"Exported {len(entries)} songs to {Path(path_str).name}",
                duration_ms=5000,
            )
        except OSError as exc:
            mb.showerror("Export Error", f"Could not write file:\n{exc}")

    # ── Filter focus ──────────────────────────────────────────────────────────

    def _focus_filter(self):
        if self._filter_entry is None:
            return
        self._filter_focus_in(self._filter_entry)
        self._filter_entry.focus_set()

    # ── Copy to clipboard ─────────────────────────────────────────────────────

    def _copy_lyrics(self):
        content = self.lyrics_window.get("1.0", "end-1c")
        if content.strip():
            self.master.clipboard_clear()
            self.master.clipboard_append(content)
            self._set_status("Copied to clipboard.", duration_ms=3000)

    # ── Save lyrics ───────────────────────────────────────────────────────────

    def _save_lyrics(self):
        lyrics = self.lyrics_window.get("1.0", "end-1c").strip()
        if not lyrics:
            mb.showinfo("Information", "There are no lyrics to save.")
            return

        path = fd.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile="lyrics",
        )
        if not path:
            return

        try:
            Path(path).write_text(lyrics, encoding="utf-8")
            self._set_status(f"Saved: {path}")
        except OSError as exc:
            mb.showerror("Save Error", f"Could not write file:\n{exc}")

    # ── Undo ──────────────────────────────────────────────────────────────────

    def _push_undo(self, entries: list[dict]):
        self._undo_stack.append(entries)

    def _undo_remove(self):
        if not self._undo_stack:
            self._set_status("Nothing to undo.", duration_ms=3000)
            return
        entries = self._undo_stack.pop()
        self.catalog.add_many(entries)
        self._refresh_tree()
        first = self.catalog.get(
            entries[0]["artist"], entries[0]["title"], entries[0].get("album", "")
        )
        if first:
            self._current_entry = first
            self._edit_btn.configure(state="normal")
            self._copy_btn.configure(state="normal")
            self._show_entry(first)
        else:
            self._current_entry = None
            self._edit_btn.configure(state="disabled")
            self._copy_btn.configure(state="disabled")
            self._set_output("")
        self._set_status(
            f"Restored {len(entries)} song{'s' if len(entries) != 1 else ''}",
            duration_ms=4000,
        )

    # ── Edit lyrics ───────────────────────────────────────────────────────────

    def _toggle_edit(self):
        if not self._editing:
            if not self._current_entry:
                return
            self._editing = True
            self._edit_btn.configure(text="Save")
            self.lyrics_window.configure(state="normal")
            self.lyrics_window.focus_set()
        else:
            self._save_edit()

    def _cancel_edit(self):
        self._editing = False
        self._edit_btn.configure(text="Edit")
        self.lyrics_window.configure(state="disabled")
        if self._current_entry:
            self._show_entry(self._current_entry)

    def _save_edit(self):
        if not self._current_entry:
            self._cancel_edit()
            return
        full_text = self.lyrics_window.get("1.0", "end-1c")
        if full_text.count(SEPARATOR) < 2:
            mb.showwarning("Save", "Header was modified — cannot save.")
            self._cancel_edit()
            return
        new_lyrics = self._extract_lyrics_from_display(full_text)
        e = self._current_entry
        try:
            self.catalog.add(
                e["artist"],
                e["title"],
                e.get("album", ""),
                e.get("year", ""),
                new_lyrics,
                track=e.get("track", 0),
            )
        except Exception as exc:
            mb.showerror("Save Error", f"Could not save lyrics to catalog:\n{exc}")
            return
        self._current_entry = self.catalog.get(
            e["artist"], e["title"], e.get("album", "")
        )
        self._editing = False
        self._edit_btn.configure(text="Edit")
        self.lyrics_window.configure(state="disabled")
        self._refresh_tree()
        self._set_status(f"Saved: {e['title']}", duration_ms=4000)

    def _extract_lyrics_from_display(self, text: str) -> str:
        """Strip the header block to return only the lyrics portion."""
        sep = SEPARATOR
        first = text.find(sep)
        if first == -1:
            return text
        second = text.find(sep, first + len(sep))
        if second == -1:
            return text
        return text[second + len(sep) :].lstrip("\n")

    # ── Stats ──────────────────────────────────────────────────────────────────

    def _show_stats(self):
        """Show catalog statistics in a message box."""
        entries = self.catalog.all_entries()
        artists = {e["artist"] for e in entries}
        albums = {(e["artist"], e.get("album", "")) for e in entries}
        with_lyrics = sum(1 for e in entries if e.get("lyrics", "").strip())
        without_lyrics = len(entries) - with_lyrics
        duplicates = self.catalog.find_duplicates()

        msg = (
            f"Artists: {len(artists)}\n"
            f"Albums: {len(albums)}\n"
            f"Songs: {len(entries)}\n"
            f"With lyrics: {with_lyrics}\n"
            f"Without lyrics: {without_lyrics}\n"
            f"Duplicate entries: {len(duplicates)}"
        )
        mb.showinfo("Catalog Stats", msg)

    # ── Theme switcher ─────────────────────────────────────────────────────────

    VALID_THEMES = ["darkly", "superhero", "solar", "cyborg", "vapor"]

    def _on_theme_change(self, _event=None):
        theme = self._theme_var.get()
        if theme not in self.VALID_THEMES:
            return
        # Close combobox dropdown before theme change to avoid TclError;
        # cancel any pending theme switch so rapid clicks don't stack.
        if self._theme_after_id is not None:
            self.master.after_cancel(self._theme_after_id)
        self._theme_combo.event_generate("<Escape>")
        self._theme_after_id = self.master.after(50, self._apply_theme, theme)

    def _apply_theme(self, theme: str):
        self._current_theme = theme
        try:
            self.master.style.theme_use(theme)
        except tk.TclError:
            # ttkbootstrap tries to style the combobox popdown widget which may
            # not exist yet (dropdown never opened) or may already be destroyed;
            # the theme itself is applied regardless, so this is safe to ignore.
            pass
        # Update ScrolledText colors to match new theme
        colors = tb.style.STANDARD_THEMES.get(theme, {}).get("colors", {})
        bg = colors.get("bg", THEME_BG)
        selectbg = colors.get("selectbg", THEME_SELECTBG)
        self.lyrics_window.configure(
            bg=bg,
            fg=self._lyrics_fg,
            selectbackground=selectbg,
            selectforeground=self._lyrics_fg,
            insertbackground=self._lyrics_fg,
        )
        # Save theme preference
        data = self._read_settings()
        data["theme"] = theme
        self._write_settings(data)

    # ── Keyboard shortcuts reference ───────────────────────────────────────────

    def _show_shortcuts(self):
        """Show keyboard shortcut reference."""
        mod = "Cmd" if sys.platform == "darwin" else "Ctrl"
        msg = (
            f"{mod}+F — Focus filter\n"
            f"{mod}+Z — Undo remove\n"
            f"{mod}+E — Export catalog\n"
            f"{mod}+M — Fetch missing lyrics\n"
            f"{mod}+I — Show stats\n"
            f"{mod}+± — Font size\n"
            f"Esc — Clear filter / Cancel edit\n"
            f"? — This help"
        )
        mb.showinfo("Keyboard Shortcuts", msg)


def main():
    import dotenv

    dotenv.load_dotenv(get_resource_path(".env"), override=True)
    # iconphoto=None prevents ttkbootstrap from setting window icon
    # This avoids dock icon issues on macOS
    root = tb.Window(themename="darkly", iconphoto=None)
    root.title("Lyrics Browser")
    root.style.configure("TButton", background=BTN_BG, foreground=BTN_FG)
    root.style.map(
        "TButton", background=[("active", BTN_BG_ACTIVE), ("disabled", BTN_BG_DISABLED)]
    )

    LyricsBrowser(root)
    root.mainloop()


if __name__ == "__main__":
    main()
