import json
import logging
import os
import sys
import threading
from collections import Counter, deque
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
import tkinter.scrolledtext as st
from pathlib import Path
from tkinter import ttk

try:
    from .base_app import LyricsBaseApp, _year_sort
    from .catalog import (
        Catalog,
        CATALOG_PATH,
        FONT_NAME,
        SEPARATOR,
        SONGS_CATEGORY,
        _artist_matches,
        _extract_name,
        _read_mp3_info,
        _release_year,
        _unpack_track,
        get_resource_path,
    )
except ImportError:
    # Allow running as a script: python lyrix/browser.py
    import pathlib

    sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))
    from base_app import LyricsBaseApp, _year_sort  # type: ignore
    from catalog import (  # type: ignore
        Catalog,
        CATALOG_PATH,
        FONT_NAME,
        SEPARATOR,
        SONGS_CATEGORY,
        _artist_matches,
        _extract_name,
        _read_mp3_info,
        _release_year,
        _unpack_track,
        get_resource_path,
    )


def _year_from_folder(name: str) -> str:
    """Extract a 4-digit year from folder names like '1998 - Album Name'."""
    if len(name) >= 4 and name[:4].isdigit():
        rest = name[4:]
        if not rest or rest[0] in (" ", "-", "_", "."):
            return name[:4]
    return ""


def _parse_folder_artist(dir_path: Path) -> str:
    """Extract artist from parent folder name, stripping common suffixes."""
    parent = dir_path.parent.name.strip()
    suffixes = [" - Discography", " - Disc", " - Collection"]
    for suffix in suffixes:
        if parent.lower().endswith(suffix.lower()):
            parent = parent[: -len(suffix)].strip()
            break
    return parent or dir_path.parent.name


def _parse_folder_album(dir_path: Path) -> str:
    """Extract album from folder name, stripping leading year."""
    name = dir_path.name.strip()
    if len(name) >= 4 and name[:4].isdigit():
        rest = name[4:].lstrip(" -_.")
        if rest:
            return rest
    return name


def _folder_album_info(
    dir_path: Path, mp3s: list, tag_cache: dict
) -> tuple[str, str] | None:
    """Derive (artist, album) from the folder name + MP3 artist tags as a
    fallback when tag-based album detection fails (e.g. tags absent or
    inconsistent).  Returns None when the folder name is not informative."""
    artists = [tag_cache[p][0] for p in mp3s if tag_cache[p][0]]
    if not artists:
        return None
    artist = Counter(artists).most_common(1)[0][0]
    name = dir_path.name.strip()
    # Strip a leading year like "2001 - " or "2001_"
    if len(name) >= 4 and name[:4].isdigit():
        rest = name[4:].lstrip(" -_.")
        if rest:
            name = rest
    # "Artist - Album" style → take the trailing segment as the album title
    if " - " in name:
        parts = name.split(" - ", 1)
        if len(parts[1].strip()) >= 2:
            name = parts[1].strip()
    return (artist, name) if name else None


def _build_track_entries(
    tracks, artist_name: str, album_name: str, album_year: str
) -> list[dict]:
    """Build catalog entry dicts from a Genius album tracks list."""
    entries = []
    for item in tracks:
        num, track = _unpack_track(item)
        track_num = num if isinstance(num, int) else (getattr(track, "number", 0) or 0)
        entries.append(
            {
                "artist": artist_name,
                "title": track.title,
                "album": album_name,
                "year": album_year,
                "lyrics": track.to_text(),
                "track": track_num,
            }
        )
    return entries


class LyricsBrowser(LyricsBaseApp):
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

        self._load_custom_font()
        self._apply_styles()

        # Read settings once for font size, geometry, and sash position
        settings = self._read_settings()
        self._restore_font_size(settings)
        self._build_ui()
        self._bind_font_size_keys()
        self._restore_geometry(default="1000x680", settings=settings)
        sash = settings.get("sash", {}).get(type(self).__name__)
        self._sash_target = sash if sash is not None else 420
        self._sash_applied = False
        self._paned.bind("<Configure>", self._on_paned_configure)

        # Genius is optional — only needed for Scan / Update
        self.genius = self._create_genius_client(warn=False)
        if self.genius is None:
            for btn in self._gated_buttons:
                btn.configure(state="disabled")

        mod = "Command" if sys.platform == "darwin" else "Control"
        self.master.bind_all(f"<{mod}-z>", lambda e: self._undo_remove())
        self.master.bind_all(f"<{mod}-f>", lambda e: self._focus_filter())
        self.master.bind_all(
            "<Escape>",
            lambda e: self._cancel_edit() if self._editing else self._clear_filter(),
        )

        self._artist_entry.bind("<Return>", lambda e: self._search_song_lyrics())
        self._song_entry.bind("<Return>", lambda e: self._search_song_lyrics())

        self._refresh_tree()

    # ── Styles ────────────────────────────────────────────────────────────────

    def _apply_styles(self):
        self._apply_base_styles()
        s = ttk.Style(self.master)
        s.configure(
            "Treeview",
            background=self.ENTRY_BG,
            foreground=self.FG,
            fieldbackground=self.ENTRY_BG,
            borderwidth=0,
            font=(FONT_NAME, 9),
            rowheight=17,
        )
        s.configure(
            "Treeview.Heading",
            background=self.BTN_BG,
            foreground=self.FG,
            font=(FONT_NAME, 9),
        )
        s.map(
            "Treeview",
            background=[("selected", self.BTN_BG)],
            foreground=[("selected", self.ACCENT)],
        )

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = ttk.Frame(self.master, padding=10)
        outer.pack(fill="both", expand=True)

        self._paned = ttk.PanedWindow(outer, orient="horizontal")
        self._paned.pack(fill="both", expand=True)

        self._paned.add(self._build_catalog_panel(self._paned), weight=1)
        self._paned.add(self._build_viewer_panel(self._paned), weight=3)

    def _build_catalog_panel(self, parent):
        frame = ttk.Frame(parent, padding=(0, 0, 8, 0))

        # Header
        header_frame = ttk.Frame(frame)
        header_frame.pack(fill="x", pady=(0, 2))
        ttk.Label(header_frame, text="Catalog", font=(FONT_NAME, 11, "bold")).pack(
            side="left"
        )
        self.catalog_count_var = tk.StringVar()
        ttk.Label(
            header_frame, textvariable=self.catalog_count_var, font=(FONT_NAME, 9)
        ).pack(side="left", padx=(8, 0))

        # Search fields - vertical layout like lyrics.py
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

        # Search buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=(0, 8))
        self._search_song_btn = ttk.Button(
            btn_frame, text="Song Lyrics", width=15, command=self._search_song_lyrics
        )
        self._search_song_btn.pack(side="left", padx=(0, 6))
        self._search_album_btn = ttk.Button(
            btn_frame, text="Album Lyrics", width=15, command=self._search_album_lyrics
        )
        self._search_album_btn.pack(side="left", padx=(0, 6))
        self._search_artist_btn = ttk.Button(
            btn_frame, text="Artist", width=15, command=self._search_artist_songs
        )
        self._search_artist_btn.pack(side="left", padx=(0, 6))
        ttk.Button(btn_frame, text="Save", width=15, command=self._save_lyrics).pack(
            side="left"
        )

        # Filter entry — sits just above the browser tree
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
        self._filter_entry.configure(foreground="#6c7086")

        # Treeview
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Return>", self._on_tree_select)
        btn = "<Button-2>" if sys.platform == "darwin" else "<Button-3>"
        self.tree.bind(btn, self._on_tree_right_click)
        self.filter_var.trace_add("write", self._on_filter_change)
        self.tree.tag_configure("artist", font=(FONT_NAME, 9, "bold"))
        self.tree.tag_configure("album", foreground=self.FG)
        self.tree.tag_configure("song", foreground=self.ACCENT)
        self.tree.tag_configure("missing", foreground="#6c7086")

        # Buttons
        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", pady=(4, 2))
        ttk.Button(
            btn_row, text="Remove", width=15, command=self._remove_selected
        ).pack(side="left", padx=(0, 4))
        self._update_btn = ttk.Button(
            btn_row, text="Update", width=15, command=self._update_selected
        )
        self._update_btn.pack(side="left")

        # Collect genius-gated buttons for bulk enable/disable
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
            fg=self.ACCENT,
            bg="black",
            selectbackground=self.BTN_BG,
            selectforeground=self.ACCENT,
            insertbackground=self.ACCENT,
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
        return frame

    # ── Filter placeholder ────────────────────────────────────────────────────

    def _on_filter_change(self, *_):
        if self._filter_placeholder:
            return
        if self._filter_after_id is not None:
            self.master.after_cancel(self._filter_after_id)
        self._filter_after_id = self.master.after(200, self._refresh_tree)

    def _clear_filter(self):
        if self._filter_entry is None or self._filter_placeholder:
            return
        self._filter_placeholder = True
        self._filter_entry.delete(0, tk.END)
        self._filter_entry.insert(0, "Filter…")
        self._filter_entry.configure(foreground="#6c7086")
        if self._filter_after_id is not None:
            self.master.after_cancel(self._filter_after_id)
            self._filter_after_id = None
        self._refresh_tree()

    def _filter_focus_in(self, entry):
        if self._filter_placeholder:
            entry.delete(0, tk.END)
            entry.configure(foreground=self.FG)
            self._filter_placeholder = False

    def _filter_focus_out(self, entry):
        if not self.filter_var.get():
            self._filter_placeholder = True
            entry.insert(0, "Filter…")
            entry.configure(foreground="#6c7086")

    # ── Catalog browser ───────────────────────────────────────────────────────

    def _refresh_tree(self):
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
            keyed4 = [
                t
                for t in keyed4
                if raw_filter in t[1] or raw_filter in t[2] or raw_filter in t[3]
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
                x[2],  # title_lower — pre-computed, no extra .lower() call
            ),
        )

        self.tree.delete(*self.tree.get_children())
        self._album_iid_name.clear()
        artist_nodes: dict[str, str] = {}
        album_nodes: dict[tuple, str] = {}

        for entry, al, _tl, alb, bk in entries:
            artist = entry["artist"] or "Unknown Artist"
            # Use SONGS_CATEGORY for songs not belonging to an album
            album = entry.get("album") or SONGS_CATEGORY
            year = canon_year.get(bk, "")

            if al not in artist_nodes:
                artist_nodes[al] = self.tree.insert(
                    "", "end", text=artist, open=True, tags=("artist",)
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

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        if not values:
            return  # artist or album node
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
                v
                for c in self.tree.get_children(item)
                if (v := self.tree.item(c, "values"))
            ]
            if children_values and mb.askyesno(
                "Remove Album",
                f"Remove all {len(children_values)} song(s) in this album from the catalog?",
            ):
                songs = [(v[0], v[1], v[2]) for v in children_values]
                entries = [
                    e
                    for v in children_values
                    if (e := self.catalog.get(v[0], v[1], v[2]))
                ]
                if entries:
                    self._push_undo(entries)
                self.catalog.remove_album_entries(songs)
                self._refresh_tree()
                self._set_status(f"Removed {len(songs)} songs", duration_ms=4000)

        elif "artist" in tags:
            artist_name = self.tree.item(item, "text")
            artist_lower = artist_name.lower().strip()
            entries = [
                e
                for e in self.catalog.all_entries()
                if e["artist"].lower().strip() == artist_lower
            ]
            count = len(entries)
            if count and mb.askyesno(
                "Remove Artist",
                f'Remove all {count} song(s) by "{artist_name}" from the catalog?',
            ):
                self._push_undo(entries)
                removed = self.catalog.remove_artist(artist_name)
                self._refresh_tree()
                self._set_status(f"Removed {removed} songs", duration_ms=4000)

    # ── Update selected ───────────────────────────────────────────────────────

    def _update_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        tags = self.tree.item(item, "tags")
        values = self.tree.item(item, "values")

        if ("song" in tags or "missing" in tags) and values:
            artist, title = values[0], values[1]
            album = values[2]
            self._set_busy(True)
            self._set_status(f"Updating: {title}…")
            threading.Thread(
                target=self._run_update_song, args=(artist, title, album), daemon=True
            ).start()

        elif "album" in tags:
            songs = [
                (v[0], v[1])
                for c in self.tree.get_children(item)
                if (v := self.tree.item(c, "values"))
            ]
            if not songs:
                return
            album_name = self._album_iid_name.get(item, "")
            artist_name = songs[0][0]
            self._set_busy(True)
            self._set_status(f"Updating album: {album_name}…")
            threading.Thread(
                target=self._run_update_album,
                args=(artist_name, album_name),
                daemon=True,
            ).start()

        elif "artist" in tags:
            artist_name = self.tree.item(item, "text")
            # Build album_map from the full catalog (not the filtered tree) so that
            # an active filter doesn't silently skip hidden songs.
            artist_lower = artist_name.lower().strip()
            album_map: dict[str, list] = {}
            for e in self.catalog.all_entries():
                if e["artist"].lower().strip() == artist_lower:
                    alb = e.get("album") or ""
                    album_map.setdefault(alb, []).append((e["artist"], e["title"]))
            if not album_map:
                return
            total = sum(len(s) for s in album_map.values())
            self._set_busy(True)
            self._set_status(f"Updating {total} songs…")
            threading.Thread(
                target=self._run_update_artist,
                args=(artist_name, album_map),
                daemon=True,
            ).start()

    # song-level update
    def _run_update_song(self, artist: str, title: str, album: str = ""):
        try:
            ss = self.genius.search_song(title, artist)
        except Exception as e:
            self._ui(self._finish_update_song, None, artist, title, album, str(e))
            return
        self._ui(self._finish_update_song, ss, artist, title, album, "")

    def _finish_update_song(self, ss, artist: str, title: str, album: str, error: str):
        self._set_busy(False)
        if error:
            mb.showerror("Error", f"Could not fetch lyrics:\n{error}")
            return
        if not ss:
            self._set_status(f"Not found: {title}")
            return
        existing = self.catalog.get(artist, title, album)
        ss_album = getattr(ss, "album", {}) or {}
        album_name = ss_album.get("name") or (existing or {}).get("album", "") or album
        if not album_name:
            album_name = SONGS_CATEGORY
        year = _release_year(ss_album) or (existing or {}).get("year", "")
        track = (existing or {}).get("track", 0)
        if ss.title != title:
            self.catalog.remove(artist, title, album)
        self.catalog.add(artist, ss.title, album_name, year, ss.to_text(), track=track)
        self._refresh_tree()
        self._set_status(f"Updated: {ss.title}", duration_ms=4000)
        entry = self.catalog.get(artist, ss.title, album_name)
        if entry:
            self._current_entry = entry
            self._edit_btn.configure(state="normal")
            self._copy_btn.configure(state="normal")
            self._show_entry(entry)

    # album-level update
    def _run_update_album(self, artist: str, album: str):
        try:
            ss = self.genius.search_album(album, artist)
        except Exception as e:
            self._ui(self._set_busy, False)
            self._ui(mb.showerror, "Error", f"Could not fetch album:\n{e}")
            return
        if not ss or not ss.tracks:
            self._ui(self._set_busy, False)
            self._ui(self._set_status, f"Album not found: {album}")
            return
        artist_name = _extract_name(getattr(ss, "artist", None), artist)
        album_name = getattr(ss, "name", "").strip() or album
        album_year = _release_year(ss)
        try:
            entries = _build_track_entries(
                ss.tracks, artist_name, album_name, album_year
            )
        except Exception as e:
            self._ui(self._set_busy, False)
            self._ui(mb.showerror, "Error", f"Could not fetch album lyrics:\n{e}")
            return
        self.catalog.add_many(entries)
        self._ui(self._set_busy, False)
        self._ui(self._refresh_tree)
        self._ui(
            self._set_status,
            f"Updated album: {album_name} ({len(ss.tracks)} tracks)",
            4000,
        )

    # artist-level update
    def _run_update_artist(self, artist: str, album_map: dict):
        updated = failed = 0
        for album_name, songs in album_map.items():
            if self._closing:
                break
            self._ui(
                self._set_status, f"Updating: {artist} — {album_name or 'singles'}…"
            )
            if album_name and album_name.lower() != "unknown album":
                try:
                    ss = self.genius.search_album(album_name, artist)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "Album fetch failed for %s / %s: %s", artist, album_name, exc
                    )
                    ss = None
                if ss and ss.tracks:
                    a_name = _extract_name(getattr(ss, "artist", None), artist)
                    alb_name = getattr(ss, "name", "").strip() or album_name
                    alb_year = _release_year(ss)
                    try:
                        entries = _build_track_entries(
                            ss.tracks, a_name, alb_name, alb_year
                        )
                        self.catalog.add_many(entries)
                    except Exception as exc:
                        logging.getLogger(__name__).warning(
                            "Album update failed for %s / %s: %s",
                            artist,
                            album_name,
                            exc,
                        )
                        failed += len(ss.tracks)
                        continue
                    updated += len(ss.tracks)
                    continue
            # Fallback: update songs individually
            for a, t in songs:
                if self._closing:
                    break
                existing = self.catalog.get(a, t, album_name)
                try:
                    ss = self.genius.search_song(t, a)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "Song fetch failed for %s / %s: %s", a, t, exc
                    )
                    failed += 1
                    continue
                if ss:
                    ss_album = getattr(ss, "album", {}) or {}
                    alb = (
                        ss_album.get("name")
                        or (existing or {}).get("album", "")
                        or album_name
                    )
                    yr = _release_year(ss_album) or (existing or {}).get("year", "")
                    trk = (existing or {}).get("track", 0)
                    if ss.title != t:
                        self.catalog.remove(a, t, album_name)
                    self.catalog.add(a, ss.title, alb, yr, ss.to_text(), track=trk)
                    updated += 1
                else:
                    failed += 1
        self._ui(self._set_busy, False)
        self._ui(self._refresh_tree)
        msg = f"Updated {updated} songs" + (f", {failed} failed" if failed else "")
        self._ui(self._set_status, msg, 6000)

    def _show_entry(self, entry: dict):
        album_str = entry.get("album") or "Unknown album"
        year_str = entry.get("year", "")
        header = (
            f"{SEPARATOR}\n"
            f"Artist: {entry['artist']}\n"
            f"Song: {entry['title']}\n"
            f"Album: {album_str}{' (' + year_str + ')' if year_str else ''}\n"
            f"{SEPARATOR}\n\n"
        )
        self._set_output(header + entry["lyrics"])

    # ── Folder scan ───────────────────────────────────────────────────────────

    def scan_folder(self):
        folder = fd.askdirectory(title="Select folder to scan for MP3s")
        if not folder:
            return
        self._set_busy(True)
        self._set_status("Reading folder…")
        threading.Thread(
            target=self._run_scan_prepare, args=(Path(folder),), daemon=True
        ).start()

    def _run_scan_prepare(self, folder: Path):
        mp3s = [p for p in folder.rglob("*") if p.suffix.lower() == ".mp3"]
        if not mp3s:
            self._ui(self._set_busy, False)
            self._ui(mb.showinfo, "Scan", "No MP3 files found in the selected folder.")
            return
        self._ui(self._set_status, f"Reading tags for {len(mp3s)} file(s)…")
        tag_cache = {p: _read_mp3_info(p) for p in mp3s}

        # One catalog snapshot avoids N separate lock acquisitions in the loop below.
        catalogued_with_lyrics = {
            (e["artist"].lower().strip(), e["title"].lower().strip())
            for e in self.catalog.all_entries()
            if e.get("lyrics", "").strip()
        }
        need = [
            p
            for p in mp3s
            if (a := tag_cache[p][0])
            and (t := tag_cache[p][1])
            and (a.lower().strip(), t.lower().strip()) not in catalogued_with_lyrics
        ]
        if not need:
            self._ui(self._set_busy, False)
            self._ui(mb.showinfo, "Scan", f"All {len(mp3s)} MP3s already have lyrics.")
            return

        by_dir: dict[Path, list[Path]] = {}
        for p in need:
            by_dir.setdefault(p.parent, []).append(p)

        total = len(need)
        self._ui(self._set_status, f"Scanning 0/{total}…")
        added = skipped = failed = done = 0

        for dir_path, dir_mp3s in by_dir.items():
            if self._closing:
                break
            artist = _parse_folder_artist(dir_path)
            album = _parse_folder_album(dir_path)
            if not artist or not album:
                skipped += len(dir_mp3s)
                continue
            folder_year = _year_from_folder(dir_path.name)

            try:
                ss = self.genius.search_album(album, artist)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Album fetch failed for %s / %s: %s", artist, album, exc
                )
                self._ui(self._set_status, f"Album not found: {artist} - {album}")
                failed += len(dir_mp3s)
                continue

            if not ss or not ss.tracks:
                self._ui(self._set_status, f"Album not found: {artist} - {album}")
                failed += len(dir_mp3s)
                continue

            artist_name = _extract_name(getattr(ss, "artist", None), artist)
            if not _artist_matches(artist, artist_name):
                self._ui(
                    self._set_status, f"Artist mismatch: {artist} vs {artist_name}"
                )
                failed += len(dir_mp3s)
                continue

            album_name = getattr(ss, "name", "").strip() or album
            album_year = _release_year(ss) or folder_year

            entries_to_add = []
            for item in ss.tracks:
                num, track = _unpack_track(item)
                track_title = track.title.strip()
                track_num = (
                    num
                    if isinstance(num, int)
                    else (getattr(track, "number", None) or 0)
                )
                entries_to_add.append(
                    {
                        "artist": artist_name,
                        "title": track_title,
                        "album": album_name,
                        "year": album_year,
                        "lyrics": track.to_text(),
                        "track": track_num,
                    }
                )

            self.catalog.add_many(entries_to_add)
            added += len(entries_to_add)
            done += len(dir_mp3s)
            self._ui(
                self._set_status,
                f"Scanning {done}/{total} — album: {album_name} ({len(entries_to_add)} tracks)…",
            )

        self._ui(self._on_scan_done, added, skipped, failed, len(mp3s))

    def _scan_album_dir(
        self,
        artist: str,
        album: str,
        mp3s: list,
        folder_year: str = "",
    ) -> tuple[int, int, int, set[str]]:
        """Import all tracks from a Genius album - no matching with local MP3s."""
        try:
            ss = self.genius.search_album(album, artist)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Album scan fetch failed for %s / %s: %s", artist, album, exc
            )
            return 0, 0, len(mp3s), set()
        if not ss or not ss.tracks:
            return 0, 0, len(mp3s), set()

        artist_name = _extract_name(getattr(ss, "artist", None), artist)
        if not _artist_matches(artist, artist_name):
            return 0, 0, len(mp3s), set()

        album_name = getattr(ss, "name", "").strip() or album
        album_year = _release_year(ss) or folder_year

        entries_to_add = []
        matched_titles = set()
        for item in ss.tracks:
            num, track = _unpack_track(item)
            track_title = track.title.strip()
            track_num = (
                num if isinstance(num, int) else (getattr(track, "number", None) or 0)
            )
            entries_to_add.append(
                {
                    "artist": artist_name,
                    "title": track_title,
                    "album": album_name,
                    "year": album_year,
                    "lyrics": track.to_text(),
                    "track": track_num if isinstance(track_num, int) else 0,
                }
            )
            matched_titles.add(track_title.lower())

        self.catalog.add_many(entries_to_add)
        return len(entries_to_add), 0, 0, matched_titles

    def _on_scan_done(self, added, skipped, failed, total):
        self._set_busy(False)
        self._refresh_tree()
        msg = f"Scan complete — {added} added, {skipped} skipped, {failed} not found (of {total})"
        self._set_status(msg, duration_ms=8000)

    # ── Scan single file ─────────────────────────────────────────────────────

    def scan_file(self):
        path_str = fd.askopenfilename(
            title="Select an MP3 file",
            filetypes=[("MP3 files", "*.mp3 *.MP3"), ("All files", "*.*")],
        )
        if not path_str:
            return
        path = Path(path_str)
        artist, title, album, track_num = _read_mp3_info(path)
        if not artist or not title:
            self._set_status(f"Could not read tags from: {path.name}")
            return
        if not album:
            album = _parse_folder_album(path.parent)
        if self.catalog.find(artist, title):
            self._set_status(f"Already in catalog: {artist} - {title}")
            return
        self._set_busy(True)
        self._set_status(f"Scanning: {artist} - {title}…")
        threading.Thread(
            target=self._run_scan_file,
            args=(artist, title, album, track_num),
            daemon=True,
        ).start()

    def _run_scan_file(self, artist: str, title: str, album: str, track_num: int = 0):
        ss = None
        exc = None
        try:
            ss = self.genius.search_song(title, artist)
        except Exception as e:
            exc = e

        if ss:
            self._ui(self._finish_scan_file, ss, artist, title, album, track_num)
            return

        if album:
            try:
                ss_album = self.genius.search_album(album, artist)
                if ss_album and ss_album.tracks:
                    title_lower = title.lower().strip()
                    for item in ss_album.tracks:
                        _, track = _unpack_track(item)
                        track_title = track.title.strip()
                        if track_title.lower() == title_lower:
                            ss = track
                            break
                        if (
                            title_lower in track_title.lower()
                            or track_title.lower() in title_lower
                        ):
                            ss = track
                            break
            except Exception as e:
                exc = e

        if ss:
            exc = None  # album fallback succeeded; discard any song-search error
        self._ui(self._finish_scan_file, ss, artist, title, album, track_num, exc)

    def _finish_scan_file(
        self,
        ss,
        artist: str,
        title: str,
        album: str,
        track_num: int = 0,
        exc: Exception = None,
    ):
        self._set_busy(False)
        if exc:
            self._set_status(f"Error: {exc}")
            return
        if not ss:
            self._set_status(f"Lyrics not found: {artist} - {title}")
            return
        if hasattr(ss, "to_text"):
            lyrics = ss.to_text()
        else:
            lyrics = str(ss)
        ss_album = getattr(ss, "album", {}) or {}
        album_name = album or ss_album.get("name", "")
        if not album_name:
            album_name = SONGS_CATEGORY
        year = _release_year(ss_album) if ss_album else ""
        title_used = getattr(ss, "title", title)
        self.catalog.add(artist, title_used, album_name, year, lyrics, track=track_num)
        self._refresh_tree()
        self._set_status(f"Scanned: {artist} - {title_used}", duration_ms=4000)
        entry = self.catalog.get(artist, title_used, album_name)
        if entry:
            self._current_entry = entry
            self._edit_btn.configure(state="normal")
            self._copy_btn.configure(state="normal")
            self._show_entry(entry)

    def _finish_import_file(
        self, ss, artist: str, title: str, album: str, track_num: int = 0
    ):
        self._set_busy(False)
        if not ss:
            self._set_status(f"Not found: {title}")
            return
        ss_album = getattr(ss, "album", {}) or {}
        album_name = album or ss_album.get("name", "")
        year = _release_year(ss_album)
        self.catalog.add(
            artist, ss.title, album_name, year, ss.to_text(), track=track_num
        )
        self._refresh_tree()
        self._set_status(f"Imported: {ss.title}", duration_ms=4000)
        entry = self.catalog.get(artist, ss.title, album_name)
        if entry:
            self._current_entry = entry
            self._edit_btn.configure(state="normal")
            self._copy_btn.configure(state="normal")
            self._show_entry(entry)

    def _on_paned_configure(self, event):
        if self._sash_applied:
            return
        # Wait until the paned window has a real width before applying the saved position.
        if self._paned.winfo_width() < 10:
            return
        self._paned.sashpos(0, self._sash_target)
        self._sash_applied = True

    # ── Settings persistence ──────────────────────────────────────────────────

    def _collect_settings(self, data: dict) -> dict:
        data = super()._collect_settings(data)
        data.setdefault("sash", {})[type(self).__name__] = self._paned.sashpos(0)
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

    # ── Import Artist All Releases ───────────────────────────────────────────

    def _import_artist_releases(self):
        """Import all releases (albums, EPs, singles) for a selected artist."""
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        tags = self.tree.item(item, "tags")
        if "artist" not in tags:
            return
        artist_name = self.tree.item(item, "text")
        self._set_busy(True)
        self._set_status(f"Fetching releases for: {artist_name}…")
        threading.Thread(
            target=self._run_import_all_albums,
            args=(artist_name,),
            daemon=True,
        ).start()

    # ── Search Lyrics ───────────────────────────────────────────────────────────

    def _search_song_lyrics(self):
        """Search for song lyrics with partial matching support."""
        if not self._require_genius_client():
            return
        artist = self._artist_entry.get().strip()
        song = self._song_entry.get().strip()
        if not artist:
            mb.showerror("Error", "Artist is required!")
            self._artist_entry.focus_set()
            return
        if not song:
            mb.showerror("Error", "Song is required!")
            self._song_entry.focus_set()
            return

        self._set_busy(True)
        self._set_status(f"Searching: {artist} — {song}…")
        threading.Thread(
            target=self._run_search_song_lyrics,
            args=(artist, song),
            daemon=True,
        ).start()

    def _run_search_song_lyrics(self, artist: str, song: str):
        """Search for song with partial matching."""
        import requests

        token = os.getenv("GENIUS_TOKEN", "").strip()
        headers = {"Authorization": "Bearer " + token}

        try:
            # Search with partial matching
            query = f"{artist} {song}"
            r = requests.get(
                "https://api.genius.com/search",
                params={"q": query},
                headers=headers,
                timeout=30,
            )
            hits = r.json().get("response", {}).get("hits", [])

            # Find best matching song
            best_match = None
            for hit in hits:
                result = hit["result"]
                hit_artist = result.get("primary_artist", {}).get("name", "")
                hit_title = result.get("title", "")
                # Check if artist matches (case insensitive)
                if (
                    artist.lower() in hit_artist.lower()
                    or hit_artist.lower() in artist.lower()
                ):
                    # Check if song title matches (partial)
                    if (
                        song.lower() in hit_title.lower()
                        or hit_title.lower() in song.lower()
                    ):
                        best_match = result
                        break

            if not best_match:
                self._ui(self._set_busy, False)
                self._ui(self._set_status, f"Song not found: {song}", 4000)
                return

            # Fetch full song details
            song_id = best_match["id"]
            r = requests.get(
                f"https://api.genius.com/songs/{song_id}", headers=headers, timeout=30
            )
            song_data = r.json().get("response", {}).get("song", {})

            # Get lyrics using genius client
            ss = self.genius.search_song(song_data.get("title", song), artist)
            track_num = song_data.get("track_position") or 0

        except Exception as e:
            self._ui(self._set_busy, False)
            self._ui(mb.showerror, "Error", f"Search failed:\n{e}")
            return

        self._ui(self._set_busy, False)
        if not ss:
            self._ui(self._set_status, f"Not found: {song}", 4000)
            return

        self._ui(self._finish_search_song, ss, artist, track_num)

    def _finish_search_song(self, ss, artist: str, track_num: int = 0):
        """Display song lyrics and add to catalog."""
        title = ss.title.strip()
        ss_album = getattr(ss, "album", {}) or {}
        album_name = ss_album.get("name") or SONGS_CATEGORY
        release_year = _release_year(ss_album)
        year_suffix = f" ({release_year})" if release_year else ""

        lyrics_text = ss.to_text()

        self.catalog.add(
            ss.artist,
            title,
            album_name,
            release_year,
            lyrics_text,
            track=track_num,
        )

        header = (
            f"{SEPARATOR}\nArtist: {ss.artist}\nSong: {title}\n"
            f"Album: {album_name}{year_suffix}\n{SEPARATOR}\n\n"
        )
        self._set_output(header + lyrics_text)
        self._set_status(f"Found and imported: {title}")
        self._refresh_tree()

    def _search_album_lyrics(self):
        """Search for album lyrics with partial matching support."""
        if not self._require_genius_client():
            return
        self._song_entry.delete(0, tk.END)
        artist = self._artist_entry.get().strip()
        album = self._album_entry.get().strip()
        if not artist:
            mb.showerror("Error", "Artist is required!")
            self._artist_entry.focus_set()
            return
        if not album:
            mb.showerror("Error", "Album is required!")
            self._album_entry.focus_set()
            return

        self._set_busy(True)
        self._set_status(f"Searching: {artist} — {album}…")
        threading.Thread(
            target=self._run_search_album_lyrics,
            args=(artist, album),
            daemon=True,
        ).start()

    def _run_search_album_lyrics(self, artist: str, album: str):
        """Search for album with partial matching."""
        import requests

        token = os.getenv("GENIUS_TOKEN", "").strip()
        headers = {"Authorization": "Bearer " + token}

        try:
            # Try direct search first
            ss = self.genius.search_album(album, artist)
        except Exception:
            ss = None

        # If direct search failed, try partial matching via API
        if not ss or not ss.tracks:
            try:
                # Search for artist first
                r = requests.get(
                    "https://api.genius.com/search",
                    params={"q": artist},
                    headers=headers,
                    timeout=30,
                )
                hits = r.json().get("response", {}).get("hits", [])
                if not hits:
                    self._ui(self._set_busy, False)
                    self._ui(self._set_status, f"Artist not found: {artist}", 4000)
                    return

                artist_id = hits[0]["result"]["primary_artist"]["id"]
                artist_name = hits[0]["result"]["primary_artist"]["name"]

                # Get artist's songs and extract albums
                # Note: The /artists/{id}/albums endpoint often returns empty
                albums = []
                seen_ids = set()
                page = 1
                while len(albums) < 50:  # Limit to prevent too many requests
                    r = requests.get(
                        f"https://api.genius.com/artists/{artist_id}/songs",
                        params={"per_page": 50, "page": page},
                        headers=headers,
                        timeout=30,
                    )
                    songs = r.json().get("response", {}).get("songs", [])
                    if not songs:
                        break
                    for song in songs:
                        alb = song.get("album", {})
                        if alb:
                            alb_name = alb.get("name", "").strip()
                            alb_id = alb.get("id", 0)
                            key = alb_id if alb_id else alb_name.lower()
                            if alb_name and key and key not in seen_ids:
                                albums.append(alb)
                                seen_ids.add(key)
                    if len(songs) < 50:
                        break
                    page += 1

                # Find best matching album (partial match)
                best_album = None
                album_lower = album.lower()
                for alb in albums:
                    alb_name = alb.get("name", "")
                    if (
                        album_lower in alb_name.lower()
                        or alb_name.lower() in album_lower
                    ):
                        best_album = alb
                        break

                if not best_album:
                    self._ui(self._set_busy, False)
                    self._ui(self._set_status, f"Album not found: {album}", 4000)
                    return

                # Fetch the album using the matched name
                ss = self.genius.search_album(best_album["name"], artist_name)

            except Exception as e:
                self._ui(self._set_busy, False)
                self._ui(mb.showerror, "Error", f"Search failed:\n{e}")
                return

        self._ui(self._set_busy, False)
        if not ss or not ss.tracks:
            self._ui(self._set_status, f"Album not found: {album}", 4000)
            return

        self._ui(self._finish_search_album, ss)

    def _finish_search_album(self, ss):
        """Display album lyrics and add all tracks to catalog."""
        artist_name = _extract_name(getattr(ss, "artist", None), "Unknown artist")
        album_name = getattr(ss, "name", "").strip() or "Unknown album"
        album_year = _release_year(ss)

        tracks_text_parts = []
        entries_to_add = []

        for item in ss.tracks:
            num, track = _unpack_track(item)
            track_num = num if isinstance(num, int) else 0
            lyrics = track.to_text()

            prefix = f"{num}. " if num is not None else ""
            tracks_text_parts.append(
                f"{SEPARATOR}\n{prefix}{track.title}\n{SEPARATOR}\n{lyrics}\n\n\n"
            )

            entries_to_add.append(
                {
                    "artist": artist_name,
                    "title": track.title,
                    "album": album_name,
                    "year": album_year,
                    "lyrics": lyrics,
                    "track": track_num,
                }
            )

        self.catalog.add_many(entries_to_add)

        header = (
            f"{SEPARATOR}\nArtist: {artist_name}\nAlbum: {album_name}"
            f"{' (' + album_year + ')' if album_year else ''}\n{SEPARATOR}\n\n"
        )
        self._set_output(header + "".join(tracks_text_parts))
        self._set_status(f"Found and imported: {album_name} ({len(ss.tracks)} tracks)")
        self._refresh_tree()

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

    def _require_genius_client(self):
        if self.genius is None:
            mb.showerror(
                "Error", "GENIUS_TOKEN is missing, so Genius searches are disabled."
            )
            return False
        return True

    def _search_artist_songs(self):
        """Search for artist and import all releases with lyrics."""
        if not self._require_genius_client():
            return
        self._song_entry.delete(0, tk.END)
        self._album_entry.delete(0, tk.END)
        artist = self._artist_entry.get().strip()
        if not artist:
            mb.showerror("Error", "Artist is required!")
            self._artist_entry.focus_set()
            return

        self._set_busy(True)
        self._set_status(f"Searching releases for: {artist}…")
        threading.Thread(
            target=self._run_import_all_albums,
            args=(artist,),
            daemon=True,
        ).start()

    def _run_import_all_albums(self, artist: str):
        """Shared: discover all albums via search_artist then import each via search_album."""
        self._ui(self._set_status, f"Fetching songs for: {artist}…")
        try:
            artist_obj = self.genius.search_artist(artist, per_page=50)
        except Exception as e:
            self._ui(self._set_busy, False)
            self._ui(mb.showerror, "Error", f"Could not search artist:\n{e}")
            return
        if not artist_obj:
            self._ui(self._set_busy, False)
            self._ui(self._set_status, f"Artist not found: {artist}", 4000)
            return

        artist_name = artist_obj.name

        # Extract unique albums from songs (each song carries an album dict with id)
        seen_ids: set = set()
        albums: list[dict] = []
        for song in artist_obj.songs:
            album = getattr(song, "album", None)
            if not album or not isinstance(album, dict):
                continue
            aid = album.get("id")
            if aid and aid not in seen_ids:
                seen_ids.add(aid)
                albums.append(album)

        if not albums:
            self._ui(self._set_busy, False)
            self._ui(self._set_status, f"No albums found for: {artist_name}", 4000)
            return

        existing = {
            (e["artist"].lower().strip(), (e.get("album") or "").lower().strip())
            for e in self.catalog.all_entries()
        }

        added = skipped = failed = 0
        for i, album in enumerate(albums, 1):
            if self._closing:
                break
            album_name = (album.get("name") or "").strip()
            if not album_name:
                continue
            key = (artist_name.lower().strip(), album_name.lower().strip())
            if key in existing:
                skipped += 1
                continue
            self._ui(self._set_status, f"[{i}/{len(albums)}] Importing: {album_name}…")
            try:
                ss = self.genius.search_album(album_name, artist_name)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Album fetch failed for %s / %s: %s", artist_name, album_name, exc
                )
                failed += 1
                continue
            if not ss or not ss.tracks:
                failed += 1
                continue
            year = _release_year(ss) or ""
            entries = []
            for item in ss.tracks:
                num, track = _unpack_track(item)
                track_num = (
                    num if isinstance(num, int) else (getattr(track, "number", 0) or 0)
                )
                entries.append(
                    {
                        "artist": artist_name,
                        "title": track.title.strip(),
                        "album": album_name,
                        "year": year,
                        "lyrics": track.to_text(),
                        "track": track_num,
                    }
                )
            if entries:
                self.catalog.add_many(entries)
                added += len(entries)
                existing.add(key)

        self._ui(self._set_busy, False)
        self._ui(self._refresh_tree)
        msg = f"Imported {added} songs"
        if skipped:
            msg += f", {skipped} albums skipped"
        if failed:
            msg += f", {failed} failed"
        self._ui(self._set_status, msg, 6000)

    # ── Fetch Missing ─────────────────────────────────────────────────────────

    def _fetch_missing(self):
        missing = [
            e for e in self.catalog.all_entries() if not e.get("lyrics", "").strip()
        ]
        if not missing:
            self._set_status("No songs with missing lyrics.", duration_ms=4000)
            return
        self._set_busy(True)
        self._set_status(f"Fetching lyrics for {len(missing)} song(s)…")
        threading.Thread(
            target=self._run_fetch_missing, args=(missing,), daemon=True
        ).start()

    def _run_fetch_missing(self, entries: list):
        added = failed = 0
        for i, e in enumerate(entries, 1):
            if self._closing:
                break
            self._ui(
                self._set_status,
                f"Fetching {i}/{len(entries)}: {e['artist']} – {e['title']}…",
            )
            try:
                ss = self.genius.search_song(e["title"], e["artist"])
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Fetch missing failed for %s / %s: %s", e["artist"], e["title"], exc
                )
                failed += 1
                continue
            if ss:
                ss_album = getattr(ss, "album", {}) or {}
                if ss.title != e["title"]:
                    self.catalog.remove(e["artist"], e["title"], e.get("album", ""))
                self.catalog.add(
                    e["artist"],
                    ss.title,
                    e.get("album") or ss_album.get("name", ""),
                    e.get("year") or _release_year(ss_album),
                    ss.to_text(),
                    track=e.get("track", 0),
                )
                added += 1
            else:
                failed += 1
        self._ui(self._set_busy, False)
        self._ui(self._refresh_tree)
        msg = f"Fetched {added} lyrics" + (f", {failed} not found" if failed else "")
        self._ui(self._set_status, msg, 6000)

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
        # Show the first restored entry in the viewer
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
        self.catalog.add(
            e["artist"],
            e["title"],
            e.get("album", ""),
            e.get("year", ""),
            new_lyrics,
            track=e.get("track", 0),
        )
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


def main():
    import dotenv

    dotenv.load_dotenv(get_resource_path(".env"), override=True)
    root = tk.Tk()
    root.title("Lyrics Browser")
    LyricsBrowser(root)
    root.mainloop()


if __name__ == "__main__":
    main()
