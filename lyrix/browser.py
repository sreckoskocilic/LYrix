import json
import logging
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
        _artist_matches,
        _detect_album,
        _extract_name,
        _read_mp3_info,
        _read_mp3_tags,
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
        _artist_matches,
        _detect_album,
        _extract_name,
        _read_mp3_info,
        _read_mp3_tags,
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
        self.master.after(
            100, lambda: self._paned.sashpos(0, sash if sash is not None else 420)
        )

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

        # Filter entry
        self.filter_var = tk.StringVar()
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

        # Buttons — row 1: add content
        btn_row1 = ttk.Frame(frame)
        btn_row1.pack(fill="x", pady=(4, 2))
        self._scan_btn = ttk.Button(
            btn_row1, text="Scan Folder", width=13, command=self.scan_folder
        )
        self._scan_btn.pack(side="left", padx=(0, 4))
        self._import_btn = ttk.Button(
            btn_row1, text="Import File", width=13, command=self.import_file
        )
        self._import_btn.pack(side="left")

        # Buttons — row 2: enrich content
        btn_row2 = ttk.Frame(frame)
        btn_row2.pack(fill="x", pady=(0, 2))
        self._update_btn = ttk.Button(
            btn_row2, text="Update Lyrics", width=13, command=self._update_selected
        )
        self._update_btn.pack(side="left", padx=(0, 4))
        self._fill_years_btn = ttk.Button(
            btn_row2, text="Fill Years", width=13, command=self._fill_years
        )
        self._fill_years_btn.pack(side="left")

        # Buttons — row 3: remove / undo
        btn_row3 = ttk.Frame(frame)
        btn_row3.pack(fill="x")
        ttk.Button(
            btn_row3, text="Remove", width=13, command=self._remove_selected
        ).pack(side="left", padx=(0, 4))
        ttk.Button(btn_row3, text="Undo", width=13, command=self._undo_remove).pack(
            side="left", padx=(0, 4)
        )

        # Buttons — row 4: export / fetch missing
        btn_row4 = ttk.Frame(frame)
        btn_row4.pack(fill="x", pady=(2, 0))
        ttk.Button(
            btn_row4, text="Export…", width=13, command=self._export_catalog
        ).pack(side="left", padx=(0, 4))
        self._fetch_missing_btn = ttk.Button(
            btn_row4, text="Fetch Missing", width=13, command=self._fetch_missing
        )
        self._fetch_missing_btn.pack(side="left")

        # Collect genius-gated buttons for bulk enable/disable
        self._gated_buttons = [
            self._scan_btn,
            self._import_btn,
            self._update_btn,
            self._fill_years_btn,
            self._fetch_missing_btn,
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

        all_entries = self.catalog.all_entries()
        total_count = len(all_entries)
        artist_count = len({e["artist"] for e in all_entries})
        if raw_filter:
            search_lyrics = len(raw_filter) >= 3
            all_entries = [
                e
                for e in all_entries
                if raw_filter in e["artist"].lower()
                or raw_filter in e["title"].lower()
                or raw_filter in e.get("album", "").lower()
                or (search_lyrics and raw_filter in e.get("lyrics", "").lower())
            ]

        # Pre-compute normalized keys to avoid repeated .lower() calls during sort.
        # Canonical year per (artist_lower, album_lower): first non-empty year wins.
        canon_year: dict[tuple, str] = {}
        keyed = []
        for e in all_entries:
            al = e["artist"].lower()
            alb = (e.get("album") or "").lower()
            bk = (al, alb)
            if not canon_year.get(bk):
                canon_year[bk] = e.get("year", "")
            keyed.append((e, al, alb, bk))

        entries = sorted(
            keyed,
            key=lambda x: (
                x[1],  # artist_lower
                _year_sort(canon_year.get(x[3], "")),
                x[2],  # album_lower
                x[0].get("track") or 9999,
                x[0]["title"].lower(),
            ),
        )

        album_counts: dict[tuple, int] = Counter(bk for _, _, _, bk in keyed)

        self.tree.delete(*self.tree.get_children())
        self._album_iid_name.clear()
        artist_nodes: dict[str, str] = {}
        album_nodes: dict[tuple, str] = {}

        for entry, al, alb, bk in entries:
            artist = entry["artist"] or "Unknown Artist"
            album = entry.get("album") or "Unknown Album"
            year = canon_year.get(bk, "")

            if al not in artist_nodes:
                artist_nodes[al] = self.tree.insert(
                    "", "end", text=artist, open=True, tags=("artist",)
                )
            if bk not in album_nodes:
                n = album_counts[bk]
                year_part = f"  ({year})" if year else ""
                album_label = f"{album}{year_part}  ·  {n}"
                album_iid = self.tree.insert(
                    artist_nodes[al],
                    "end",
                    text=album_label,
                    open=False,
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
                values=(entry["artist"], entry["title"]),
                tags=("song" if has_lyrics else "missing",),
            )

        if raw_filter:
            self.catalog_count_var.set(
                f"{len(all_entries)} of {total_count} song{'s' if total_count != 1 else ''}"
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
        entry = self.catalog.get(artist, title)
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

        if "song" in tags and values:
            artist, title = values[0], values[1]
            if mb.askyesno("Remove", f'Remove "{title}" from catalog?'):
                entry = self.catalog.get(artist, title)
                if entry:
                    self._push_undo([entry])
                self.catalog.remove(artist, title)
                self._refresh_tree()
                self._set_status(f"Removed: {title}", duration_ms=4000)

        elif "album" in tags:
            songs = [
                (v[0], v[1])
                for c in self.tree.get_children(item)
                if (v := self.tree.item(c, "values"))
            ]
            if songs and mb.askyesno(
                "Remove Album",
                f"Remove all {len(songs)} song(s) in this album from the catalog?",
            ):
                entries = [e for a, t in songs if (e := self.catalog.get(a, t))]
                if entries:
                    self._push_undo(entries)
                self.catalog.remove_entries(songs)
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

        if "song" in tags and values:
            artist, title = values[0], values[1]
            self._set_busy(True)
            self._set_status(f"Updating: {title}…")
            threading.Thread(
                target=self._run_update_song, args=(artist, title), daemon=True
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
    def _run_update_song(self, artist: str, title: str):
        try:
            ss = self.genius.search_song(title, artist)
        except Exception as e:
            self._ui(self._finish_update_song, None, artist, title, str(e))
            return
        self._ui(self._finish_update_song, ss, artist, title, "")

    def _finish_update_song(self, ss, artist: str, title: str, error: str):
        self._set_busy(False)
        if error:
            mb.showerror("Error", f"Could not fetch lyrics:\n{error}")
            return
        if not ss:
            self._set_status(f"Not found: {title}")
            return
        existing = self.catalog.get(artist, title)
        ss_album = getattr(ss, "album", {}) or {}
        album_name = ss_album.get("name") or (existing or {}).get("album", "")
        year = _release_year(ss_album) or (existing or {}).get("year", "")
        track = (existing or {}).get("track", 0)
        self.catalog.add(artist, title, album_name, year, ss.to_text(), track=track)
        self._refresh_tree()
        self._set_status(f"Updated: {title}", duration_ms=4000)
        entry = self.catalog.get(artist, title)
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
            self._ui(mb.showerror, "Error", f"Could not fetch album:\n{e}")
            self._ui(self._set_busy, False)
            return
        if ss and ss.tracks:
            artist_name = _extract_name(getattr(ss, "artist", None), artist)
            album_name = getattr(ss, "name", "").strip() or album
            album_year = _release_year(ss)
            try:
                self.catalog.add_many(
                    _build_track_entries(ss.tracks, artist_name, album_name, album_year)
                )
            except Exception as e:
                self._ui(mb.showerror, "Error", f"Could not save album:\n{e}")
                self._ui(self._set_busy, False)
                return
            self._ui(self._set_busy, False)
            self._ui(self._refresh_tree)
            self._ui(
                lambda msg=f"Updated album: {album_name} ({len(ss.tracks)} tracks)": (
                    self._set_status(msg, duration_ms=4000)
                )
            )
        else:
            self._ui(self._set_busy, False)
            self._ui(self._set_status, f"Album not found: {album}")

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
                        self.catalog.add_many(
                            _build_track_entries(ss.tracks, a_name, alb_name, alb_year)
                        )
                    except Exception as exc:
                        logging.getLogger(__name__).warning(
                            "Album save failed for %s / %s: %s", artist, album_name, exc
                        )
                        failed += len(ss.tracks)
                        continue
                    updated += len(ss.tracks)
                    continue
            # Fallback: update songs individually
            for a, t in songs:
                if self._closing:
                    break
                try:
                    ss = self.genius.search_song(t, a)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "Song fetch failed for %s / %s: %s", a, t, exc
                    )
                    failed += 1
                    continue
                if ss:
                    existing = self.catalog.get(a, t)
                    ss_album = getattr(ss, "album", {}) or {}
                    alb = ss_album.get("name") or (existing or {}).get("album", "")
                    yr = _release_year(ss_album) or (existing or {}).get("year", "")
                    trk = (existing or {}).get("track", 0)
                    self.catalog.add(a, t, alb, yr, ss.to_text(), track=trk)
                    updated += 1
                else:
                    failed += 1
        self._ui(self._set_busy, False)
        self._ui(self._refresh_tree)
        msg = f"Updated {updated} songs" + (f", {failed} failed" if failed else "")
        self._ui(lambda m=msg: self._set_status(m, duration_ms=6000))

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

    # ── Fill Years ────────────────────────────────────────────────────────────

    def _fill_years(self):
        # Collect unique (artist, album) pairs with no year
        missing: set[tuple[str, str]] = set()
        for e in self.catalog.all_entries():
            if not e.get("year") and e.get("album"):
                missing.add((e["artist"], e["album"]))
        if not missing:
            self._set_status("All albums already have years.", duration_ms=4000)
            return
        self._set_busy(True)
        self._set_status(f"Filling years for {len(missing)} album(s)…")
        threading.Thread(
            target=self._run_fill_years, args=(list(missing),), daemon=True
        ).start()

    def _run_fill_years(self, pairs: list):
        updated = failed = 0
        for i, (artist, album) in enumerate(pairs, 1):
            if self._closing:
                break
            self._ui(
                self._set_status,
                f"Filling years {i}/{len(pairs)}: {artist} — {album or '?'}…",
            )
            try:
                ss = self.genius.search_album(album, artist)
                if ss:
                    year = _release_year(ss)
                    if year:
                        self.catalog.set_album_year(artist, album, year)
                        updated += 1
                    else:
                        failed += 1
                else:
                    failed += 1
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Year fill failed for %s / %s: %s", artist, album, exc
                )
                failed += 1
        self._ui(self._set_busy, False)
        self._ui(self._refresh_tree)
        msg = f"Years filled: {updated}" + (f", {failed} not found" if failed else "")
        self._ui(lambda m=msg: self._set_status(m, duration_ms=6000))

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
        new = [p for p in mp3s if not self._mp3_in_catalog(p, tag_cache[p])]
        if not new:
            self._ui(self._set_busy, False)
            self._ui(
                mb.showinfo, "Scan", f"All {len(mp3s)} MP3s are already in the catalog."
            )
            return
        by_dir: dict[Path, list[Path]] = {}
        for p in new:
            by_dir.setdefault(p.parent, []).append(p)
        self._ui(self._set_status, f"Scanning 0/{len(new)}…")
        self._run_scan(by_dir, len(new), tag_cache)

    def _mp3_in_catalog(self, path: Path, info: tuple | None = None) -> bool:
        if info is None:
            info = _read_mp3_info(path)
        artist, title = info[0], info[1]
        return bool(artist and title and self.catalog.get(artist, title))

    def _fetch_and_add_single(
        self, path: Path, tag_cache: dict, done: int, total: int
    ) -> tuple[int, int, int]:
        """Fetch lyrics for one MP3 and add to catalog. Returns (added, skipped, failed)."""
        artist, title, album, track_num = tag_cache[path]
        if not artist or not title:
            self._ui(
                self._set_status,
                f"Scanning {done}/{total} — skipped: {path.name}",
            )
            return 0, 1, 0
        self._ui(
            self._set_status,
            f"Scanning {done}/{total} — {artist} – {title}",
        )
        try:
            ss = self.genius.search_song(title, artist)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Song fetch failed for %s / %s: %s", artist, title, exc
            )
            return 0, 0, 1
        if ss:
            ss_album = getattr(ss, "album", {}) or {}
            self.catalog.add(
                artist,
                title,
                album or ss_album.get("name", ""),
                _release_year(ss_album),
                ss.to_text(),
                track=track_num,
            )
            return 1, 0, 0
        return 0, 0, 1

    def _run_scan(self, by_dir: dict, total: int, tag_cache: dict):
        added = skipped = failed = done = 0
        for dir_path, mp3s in by_dir.items():
            if self._closing:
                break
            album_info = _detect_album(mp3s, tag_cache)
            if album_info:
                artist, album = album_info
                folder_year = _year_from_folder(dir_path.name)
                self._ui(
                    self._set_status,
                    f"Scanning {done + 1}/{total} — album: {artist} – {album}",
                )
                try:
                    a, s, f, matched_titles = self._scan_album_dir(
                        artist,
                        album,
                        mp3s,
                        folder_year=folder_year,
                        tag_cache=tag_cache,
                    )
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "Album scan failed for %s / %s: %s", artist, album, exc
                    )
                    a = s = 0
                    f = len(mp3s)
                    matched_titles = set()

                # If album-level match failed, fall back to per-file lookups
                if a == 0 and f == len(mp3s):
                    for path in mp3s:
                        if self._closing:
                            break
                        done += 1
                        a_, s_, f_ = self._fetch_and_add_single(
                            path, tag_cache, done, total
                        )
                        added += a_
                        skipped += s_
                        failed += f_
                    continue

                added += a
                skipped += s
                failed += f

                # Retry unmatched tracks individually; advance done per file so the
                # progress counter moves rather than freezing during the retry phase.
                unmatched: list[Path] = []
                for p in mp3s:
                    t = (tag_cache[p][1] or "").strip().lower()
                    if t not in matched_titles:
                        unmatched.append(p)
                done += len(mp3s) - len(unmatched)
                for path in unmatched:
                    if self._closing:
                        break
                    done += 1
                    a_, s_, f_ = self._fetch_and_add_single(
                        path, tag_cache, done, total
                    )
                    added += a_
                    skipped += s_
                    failed += f_
            else:
                for path in mp3s:
                    if self._closing:
                        break
                    done += 1
                    a_, s_, f_ = self._fetch_and_add_single(
                        path, tag_cache, done, total
                    )
                    added += a_
                    skipped += s_
                    failed += f_
        self._ui(self._on_scan_done, added, skipped, failed, total)

    def _scan_album_dir(
        self,
        artist: str,
        album: str,
        mp3s: list,
        folder_year: str = "",
        tag_cache: dict | None = None,
    ) -> tuple[int, int, int, set[str]]:
        try:
            ss = self.genius.search_album(album, artist)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Album scan fetch failed for %s / %s: %s", artist, album, exc
            )
            return 0, 0, len(mp3s), set()
        if not ss or not ss.tracks:
            return 0, 0, len(mp3s), set()

        # Use cached tags to build wanted_titles; avoids a third parse of each file
        if tag_cache is not None:
            wanted_titles = {
                (tag_cache[p][1] or "").strip().lower() for p in mp3s if p.exists()
            }
        else:
            wanted_titles = {
                (_read_mp3_tags(p)[1] or "").strip().lower() for p in mp3s if p.exists()
            }

        matched_tracks: list[tuple[int | None, object]] = []
        matched_mp3_titles: set[str] = set()
        mp3_track_nums: dict[str, int] = {}
        for p in mp3s:
            title = (
                (tag_cache[p][1] or "").strip().lower()
                if tag_cache
                else (_read_mp3_tags(p)[1] or "").strip().lower()
            )
            track_num = tag_cache[p][3] if tag_cache else _read_mp3_info(p)[3]
            mp3_track_nums[title] = track_num

        remaining_wanted = set(wanted_titles)
        for item in ss.tracks:
            num, track = _unpack_track(item)
            track_title_lower = track.title.strip().lower()
            resolved_num = (
                num
                if isinstance(num, int)
                else (getattr(track, "number", None) or None)
            )
            for wanted in list(remaining_wanted):
                has_track_num = mp3_track_nums.get(wanted, 0) > 0
                title_exact = wanted == track_title_lower
                title_substr = (
                    wanted in track_title_lower or track_title_lower in wanted
                )
                track_match = (
                    resolved_num is not None
                    and mp3_track_nums.get(wanted) == resolved_num
                )
                if title_exact and (not has_track_num or track_match):
                    matched_tracks.append((resolved_num, track))
                    matched_mp3_titles.add(wanted)
                    remaining_wanted.discard(wanted)
                    break
                elif title_substr and track_match:
                    matched_tracks.append((resolved_num, track))
                    matched_mp3_titles.add(wanted)
                    remaining_wanted.discard(wanted)
                    break

        # If we couldn't confidently match most of the folder, let caller fall back to per-file.
        if len(matched_tracks) < max(1, int(0.6 * len(mp3s))):
            return 0, 0, len(mp3s), set()

        artist_name = _extract_name(getattr(ss, "artist", None), artist)
        if not _artist_matches(artist, artist_name):
            return 0, 0, len(mp3s), set()
        album_name = getattr(ss, "name", "").strip() or album
        album_year = _release_year(ss) or folder_year

        entries_to_add = []
        for num, track in matched_tracks:
            track_num = num if isinstance(num, int) else 0
            entries_to_add.append(
                {
                    "artist": artist_name,
                    "title": track.title,
                    "album": album_name,
                    "year": album_year,
                    "lyrics": track.to_text(),
                    "track": track_num,
                }
            )
        self.catalog.add_many(entries_to_add)

        added = len(matched_tracks)
        skipped = 0  # unmatched files are retried individually by the caller
        failed = 0  # Only count genuine API failures here; fallbacks handle misses.
        return added, skipped, failed, matched_mp3_titles

    def _on_scan_done(self, added, skipped, failed, total):
        self._set_busy(False)
        self._refresh_tree()
        msg = f"Scan complete — {added} added, {skipped} skipped, {failed} not found (of {total})"
        self._set_status(msg, duration_ms=8000)

    # ── Import single file ────────────────────────────────────────────────────

    def import_file(self):
        path_str = fd.askopenfilename(
            title="Select an MP3 file",
            filetypes=[("MP3 files", "*.mp3 *.MP3"), ("All files", "*.*")],
        )
        if not path_str:
            return
        path = Path(path_str)
        artist, title, album, track_num = _read_mp3_info(path)
        if not artist or not title:
            mb.showerror(
                "Import", f"Could not read artist/title tags from:\n{path.name}"
            )
            return
        if self.catalog.get(artist, title):
            mb.showinfo("Import", f'"{title}" is already in the catalog.')
            return
        self._set_busy(True)
        self._set_status(f"Importing: {title}…")
        threading.Thread(
            target=self._run_import_file,
            args=(artist, title, album, track_num),
            daemon=True,
        ).start()

    def _run_import_file(self, artist: str, title: str, album: str, track_num: int = 0):
        try:
            ss = self.genius.search_song(title, artist)
        except Exception as e:
            self._ui(self._set_busy, False)
            self._ui(mb.showerror, "Import Error", f"Could not fetch lyrics:\n{e}")
            return
        self._ui(self._finish_import_file, ss, artist, title, album, track_num)

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
        self.catalog.add(artist, title, album_name, year, ss.to_text(), track=track_num)
        self._refresh_tree()
        self._set_status(f"Imported: {title}", duration_ms=4000)
        entry = self.catalog.get(artist, title)
        if entry:
            self._current_entry = entry
            self._edit_btn.configure(state="normal")
            self._copy_btn.configure(state="normal")
            self._show_entry(entry)

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
            if genius_available:
                menu.add_command(label="Update Lyrics", command=self._update_selected)
                menu.add_separator()
            menu.add_command(label="Remove Song", command=self._remove_selected)
        elif "album" in tags:
            if genius_available:
                menu.add_command(
                    label="Update Album Lyrics", command=self._update_selected
                )
                menu.add_separator()
            menu.add_command(label="Remove Album", command=self._remove_selected)
        elif "artist" in tags:
            if genius_available:
                menu.add_command(
                    label="Update All Lyrics", command=self._update_selected
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
                "entries": {Catalog._key(e["artist"], e["title"]): e for e in entries},
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
                self.catalog.add(
                    e["artist"],
                    e["title"],
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
        self._ui(lambda m=msg: self._set_status(m, duration_ms=6000))

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
        first = self.catalog.get(entries[0]["artist"], entries[0]["title"])
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
        self._current_entry = self.catalog.get(e["artist"], e["title"])
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
