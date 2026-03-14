import threading
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
    import sys

    sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))
    from base_app import LyricsBaseApp, _year_sort  # type: ignore
    from catalog import (  # type: ignore
        Catalog,
        CATALOG_PATH,
        FONT_NAME,
        SEPARATOR,
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


class LyricsBrowser(LyricsBaseApp):
    def __init__(self, master):
        super().__init__(master)
        self.master.minsize(900, 540)
        self.catalog = Catalog(CATALOG_PATH)

        self._filter_after_id = None

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
        filter_entry = ttk.Entry(
            frame, textvariable=self.filter_var, font=(FONT_NAME, 10)
        )
        filter_entry.pack(fill="x", pady=(0, 4))
        filter_entry.bind("<FocusIn>", lambda e: self._filter_focus_in(filter_entry))
        filter_entry.bind("<FocusOut>", lambda e: self._filter_focus_out(filter_entry))
        self._filter_placeholder = True
        filter_entry.insert(0, "Filter…")
        filter_entry.configure(foreground="#6c7086")

        # Treeview
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.filter_var.trace_add("write", self._on_filter_change)
        self.tree.tag_configure("artist", font=(FONT_NAME, 9, "bold"))
        self.tree.tag_configure("album", foreground=self.FG)
        self.tree.tag_configure("song", foreground=self.ACCENT)

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

        # Buttons — row 3: remove
        btn_row3 = ttk.Frame(frame)
        btn_row3.pack(fill="x")
        ttk.Button(
            btn_row3, text="Remove", width=13, command=self._remove_selected
        ).pack(side="left")

        # Collect genius-gated buttons for bulk enable/disable
        self._gated_buttons = [
            self._scan_btn,
            self._import_btn,
            self._update_btn,
            self._fill_years_btn,
        ]

        ttk.Label(frame, textvariable=self.status_var, font=(FONT_NAME, 9)).pack(
            anchor="w", pady=(4, 0)
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
        return frame

    # ── Filter placeholder ────────────────────────────────────────────────────

    def _on_filter_change(self, *_):
        if self._filter_after_id is not None:
            self.master.after_cancel(self._filter_after_id)
        self._filter_after_id = self.master.after(200, self._refresh_tree)

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
        raw_filter = (
            "" if self._filter_placeholder else self.filter_var.get().strip().lower()
        )

        all_entries = self.catalog.all_entries()
        total_count = len(all_entries)
        if raw_filter:
            all_entries = [
                e
                for e in all_entries
                if raw_filter in e["artist"].lower()
                or raw_filter in e["title"].lower()
                or raw_filter in e.get("album", "").lower()
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

        self.tree.delete(*self.tree.get_children())
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
                album_label = f"{album}  ({year})" if year else album
                album_nodes[bk] = self.tree.insert(
                    artist_nodes[al],
                    "end",
                    text=album_label,
                    open=False,
                    tags=("album",),
                )
            track_num = entry.get("track", 0)
            song_label = (
                f"{track_num}. {entry['title']}" if track_num else entry["title"]
            )
            self.tree.insert(
                album_nodes[bk],
                "end",
                text=song_label,
                values=(entry["artist"], entry["title"]),
                tags=("song",),
            )

        self.catalog_count_var.set(
            f"{total_count} song{'s' if total_count != 1 else ''}"
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
                self.catalog.remove_entries(songs)
                self._refresh_tree()
                self._set_status(f"Removed {len(songs)} songs", duration_ms=4000)

        elif "artist" in tags:
            artist_name = self.tree.item(item, "text")
            count = sum(
                len(self.tree.get_children(album_node))
                for album_node in self.tree.get_children(item)
            )
            if count and mb.askyesno(
                "Remove Artist",
                f'Remove all {count} song(s) by "{artist_name}" from the catalog?',
            ):
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
            album_name = self.tree.item(item, "text").rsplit("  (", 1)[0]
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
            # Collect {album_name: [(artist, title), ...]}
            album_map: dict[str, list] = {}
            for album_node in self.tree.get_children(item):
                songs = [
                    (v[0], v[1])
                    for c in self.tree.get_children(album_node)
                    if (v := self.tree.item(c, "values"))
                ]
                if songs:
                    alb = self.tree.item(album_node, "text").rsplit("  (", 1)[0]
                    album_map[alb] = songs
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
        self.catalog.add(
            ss.artist, ss.title, album_name, year, ss.to_text(), track=track
        )
        self._refresh_tree()
        self._set_status(f"Updated: {ss.title}", duration_ms=4000)
        entry = self.catalog.get(ss.artist, ss.title)
        if entry:
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
                entries_to_add = []
                for item in ss.tracks:
                    num, track = _unpack_track(item)
                    track_num = (
                        num
                        if isinstance(num, int)
                        else (getattr(track, "number", 0) or 0)
                    )
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
                except Exception:
                    ss = None
                if ss and ss.tracks:
                    a_name = _extract_name(getattr(ss, "artist", None), artist)
                    alb_name = getattr(ss, "name", "").strip() or album_name
                    alb_year = _release_year(ss)
                    try:
                        entries_to_add = []
                        for item in ss.tracks:
                            num, track = _unpack_track(item)
                            track_num = (
                                num
                                if isinstance(num, int)
                                else (getattr(track, "number", 0) or 0)
                            )
                            entries_to_add.append(
                                {
                                    "artist": a_name,
                                    "title": track.title,
                                    "album": alb_name,
                                    "year": alb_year,
                                    "lyrics": track.to_text(),
                                    "track": track_num,
                                }
                            )
                        self.catalog.add_many(entries_to_add)
                    except Exception:
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
                except Exception:
                    failed += 1
                    continue
                if ss:
                    existing = self.catalog.get(a, t)
                    ss_album = getattr(ss, "album", {}) or {}
                    alb = ss_album.get("name") or (existing or {}).get("album", "")
                    yr = _release_year(ss_album) or (existing or {}).get("year", "")
                    trk = (existing or {}).get("track", 0)
                    self.catalog.add(
                        ss.artist, ss.title, alb, yr, ss.to_text(), track=trk
                    )
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
            if not e.get("year"):
                missing.add((e["artist"], e.get("album") or ""))
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
            except Exception:
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
        mp3s = [p for p in Path(folder).rglob("*") if p.suffix.lower() == ".mp3"]
        if not mp3s:
            mb.showinfo("Scan", "No MP3 files found in the selected folder.")
            return

        # Read all MP3 tags once (eliminates 2-3x redundant parses per file)
        tag_cache = {p: _read_mp3_info(p) for p in mp3s}
        new = [p for p in mp3s if not self._mp3_in_catalog(p, tag_cache[p])]
        if not new:
            mb.showinfo("Scan", f"All {len(mp3s)} MP3s are already in the catalog.")
            return
        by_dir: dict[Path, list[Path]] = {}
        for p in new:
            by_dir.setdefault(p.parent, []).append(p)
        self._set_busy(True)
        self._set_status(f"Scanning 0/{len(new)}…")
        threading.Thread(
            target=self._run_scan, args=(by_dir, len(new), tag_cache), daemon=True
        ).start()

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
        except Exception:
            return 0, 0, 1
        if ss:
            ss_album = getattr(ss, "album", {}) or {}
            self.catalog.add(
                ss.artist,
                ss.title,
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
                except Exception:
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
                done += len(mp3s)

                # Retry unmatched tracks individually to recover anything the album match missed.
                unmatched: list[Path] = []
                for p in mp3s:
                    title = (tag_cache[p][1] or "").strip().lower()
                    if title not in matched_titles:
                        unmatched.append(p)
                for path in unmatched:
                    if self._closing:
                        break
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
        except Exception:
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
        artist, title, album = _read_mp3_tags(path)
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
            target=self._run_import_file, args=(artist, title, album), daemon=True
        ).start()

    def _run_import_file(self, artist: str, title: str, album: str):
        try:
            ss = self.genius.search_song(title, artist)
        except Exception as e:
            self._ui(self._set_busy, False)
            self._ui(mb.showerror, "Import Error", f"Could not fetch lyrics:\n{e}")
            return
        self._ui(self._finish_import_file, ss, artist, title, album)

    def _finish_import_file(self, ss, artist: str, title: str, album: str):
        self._set_busy(False)
        if not ss:
            self._set_status(f"Not found: {title}")
            return
        ss_album = getattr(ss, "album", {}) or {}
        album_name = album or ss_album.get("name", "")
        year = _release_year(ss_album)
        self.catalog.add(ss.artist, ss.title, album_name, year, ss.to_text())
        self._refresh_tree()
        self._set_status(f"Imported: {ss.title}", duration_ms=4000)
        entry = self.catalog.get(ss.artist, ss.title)
        if entry:
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
            state = "disabled"
        elif self.genius is not None:
            state = "normal"
        else:
            return
        for btn in self._gated_buttons:
            btn.configure(state=state)


def main():
    import dotenv

    dotenv.load_dotenv(get_resource_path(".env"), override=True)
    root = tk.Tk()
    root.title("Lyrics Browser")
    LyricsBrowser(root)
    root.mainloop()


if __name__ == "__main__":
    main()
