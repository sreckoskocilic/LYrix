import threading
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
import tkinter.scrolledtext as st
from pathlib import Path
from tkinter import ttk

from .base_app import LyricsBaseApp, _year_sort
from .catalog import (
    Catalog,
    CATALOG_PATH,
    FONT_NAME,
    SEPARATOR,
    _detect_album,
    _extract_name,
    _read_mp3_tags,
    _release_year,
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

        self._load_custom_font()
        self._apply_styles()
        self._build_ui()
        self._restore_geometry(default="1000x680")
        sash = self._read_settings().get("sash", {}).get(type(self).__name__)
        self.master.after(
            100, lambda: self._paned.sashpos(0, sash if sash is not None else 420)
        )

        # Genius is optional — only needed for Scan / Update
        self.genius = self._create_genius_client(warn=False)
        if self.genius is None:
            self._scan_btn.configure(state="disabled")
            self._import_btn.configure(state="disabled")
            self._update_btn.configure(state="disabled")
            self._fill_years_btn.configure(state="disabled")

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

        ttk.Label(frame, textvariable=self.status_var, font=(FONT_NAME, 9)).pack(
            anchor="w", pady=(4, 0)
        )

        return frame

    def _build_viewer_panel(self, parent):
        frame = ttk.Frame(parent, padding=(8, 0, 0, 0))
        self.lyrics_window = st.ScrolledText(
            frame,
            font=(FONT_NAME, 10),
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
        after_id = getattr(self, "_filter_after_id", None)
        if after_id is not None:
            self.master.after_cancel(after_id)
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
        if raw_filter:
            all_entries = [
                e
                for e in all_entries
                if raw_filter in e["artist"].lower()
                or raw_filter in e["title"].lower()
                or raw_filter in e.get("album", "").lower()
            ]

        # Canonical year per (artist_lower, album_lower): first non-empty year wins.
        # This keeps all songs in an album together and gives the album a stable label.
        canon_year: dict[tuple, str] = {}
        for e in all_entries:
            bk = (e["artist"].lower(), (e.get("album") or "").lower())
            if bk not in canon_year or not canon_year[bk]:
                canon_year[bk] = e.get("year", "")

        entries = sorted(
            all_entries,
            key=lambda e: (
                e["artist"].lower(),
                _year_sort(
                    canon_year.get(
                        (e["artist"].lower(), (e.get("album") or "").lower()), ""
                    )
                ),
                (e.get("album") or "").lower(),
                e.get("track") or 9999,
                e["title"].lower(),
            ),
        )

        self.tree.delete(*self.tree.get_children())
        artist_nodes: dict[str, str] = {}
        album_nodes: dict[tuple, str] = {}

        for entry in entries:
            artist = entry["artist"] or "Unknown Artist"
            album = entry.get("album") or "Unknown Album"
            ak = artist.lower()
            bk = (ak, album.lower())
            year = canon_year.get(bk, "")

            if ak not in artist_nodes:
                artist_nodes[ak] = self.tree.insert(
                    "", "end", text=artist, open=True, tags=("artist",)
                )
            if bk not in album_nodes:
                album_label = f"{album}  ({year})" if year else album
                album_nodes[bk] = self.tree.insert(
                    artist_nodes[ak],
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

        count = len(self.catalog)
        self.catalog_count_var.set(f"{count} song{'s' if count != 1 else ''}")

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
                (self.tree.item(c, "values")[0], self.tree.item(c, "values")[1])
                for c in self.tree.get_children(item)
                if self.tree.item(c, "values")
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
                (self.tree.item(c, "values")[0], self.tree.item(c, "values")[1])
                for c in self.tree.get_children(item)
                if self.tree.item(c, "values")
            ]
            if not songs:
                return
            entry = self.catalog.get(songs[0][0], songs[0][1])
            tree_text = self.tree.item(item, "text").rsplit("  (", 1)[
                0
            ]  # strip "  (year)" suffix
            album_name = (entry or {}).get("album") or tree_text
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
                    (self.tree.item(c, "values")[0], self.tree.item(c, "values")[1])
                    for c in self.tree.get_children(album_node)
                    if self.tree.item(c, "values")
                ]
                if songs:
                    entry = self.catalog.get(songs[0][0], songs[0][1])
                    alb = (entry or {}).get("album", "") or ""
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
            album_year = _release_year(
                {
                    "release_date_for_display": getattr(
                        ss, "release_date_for_display", ""
                    )
                }
            )
            try:
                for item in ss.tracks:
                    num, track = item if isinstance(item, tuple) else (None, item)
                    track_num = num if isinstance(num, int) else 0
                    self.catalog.add(
                        artist_name,
                        track.title,
                        album_name,
                        album_year,
                        track.to_text(),
                        track=track_num,
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
                except Exception:
                    ss = None
                if ss and ss.tracks:
                    a_name = _extract_name(getattr(ss, "artist", None), artist)
                    alb_name = getattr(ss, "name", "").strip() or album_name
                    alb_year = _release_year(
                        {
                            "release_date_for_display": getattr(
                                ss, "release_date_for_display", ""
                            )
                        }
                    )
                    try:
                        for item in ss.tracks:
                            num, track = (
                                item if isinstance(item, tuple) else (None, item)
                            )
                            track_num = num if isinstance(num, int) else 0
                            self.catalog.add(
                                a_name,
                                track.title,
                                alb_name,
                                alb_year,
                                track.to_text(),
                                track=track_num,
                            )
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
        missing = {}
        for e in self.catalog.all_entries():
            if not e.get("year"):
                key = (e["artist"], e.get("album") or "")
                missing[key] = None
        if not missing:
            self._set_status("All albums already have years.", duration_ms=4000)
            return
        self._set_busy(True)
        self._set_status(f"Filling years for {len(missing)} album(s)…")
        threading.Thread(
            target=self._run_fill_years, args=(list(missing.keys()),), daemon=True
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
                    year = _release_year(
                        {
                            "release_date_for_display": getattr(
                                ss, "release_date_for_display", ""
                            )
                        }
                    )
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
        new = [p for p in mp3s if not self._mp3_in_catalog(p)]
        if not new:
            mb.showinfo("Scan", f"All {len(mp3s)} MP3s are already in the catalog.")
            return
        by_dir: dict[Path, list[Path]] = {}
        for p in new:
            by_dir.setdefault(p.parent, []).append(p)
        self._set_busy(True)
        self._set_status(f"Scanning 0/{len(new)}…")
        threading.Thread(
            target=self._run_scan, args=(by_dir, len(new)), daemon=True
        ).start()

    def _mp3_in_catalog(self, path: Path) -> bool:
        artist, title, _ = _read_mp3_tags(path)
        return bool(artist and title and self.catalog.get(artist, title))

    def _run_scan(self, by_dir: dict, total: int):
        added = skipped = failed = done = 0
        for dir_path, mp3s in by_dir.items():
            if self._closing:
                break
            album_info = _detect_album(mp3s)
            if album_info:
                artist, album = album_info
                folder_year = _year_from_folder(dir_path.name)
                self._ui(
                    self._set_status,
                    f"Scanning {done + 1}/{total} — album: {artist} – {album}",
                )
                try:
                    a, s, f = self._scan_album_dir(
                        artist, album, mp3s, folder_year=folder_year
                    )
                except Exception:
                    failed += len(mp3s)
                    done += len(mp3s)
                    continue
                added += a
                skipped += s
                failed += f
                done += len(mp3s)
            else:
                for path in mp3s:
                    if self._closing:
                        break
                    done += 1
                    artist, title, album = _read_mp3_tags(path)
                    if not artist or not title:
                        skipped += 1
                        self._ui(
                            self._set_status,
                            f"Scanning {done}/{total} — skipped: {path.name}",
                        )
                        continue
                    self._ui(
                        self._set_status,
                        f"Scanning {done}/{total} — {artist} – {title}",
                    )
                    try:
                        ss = self.genius.search_song(title, artist)
                    except Exception:
                        failed += 1
                        continue
                    if ss:
                        ss_album = getattr(ss, "album", {}) or {}
                        self.catalog.add(
                            ss.artist,
                            ss.title,
                            ss_album.get("name", album),
                            _release_year(ss_album),
                            ss.to_text(),
                        )
                        added += 1
                    else:
                        failed += 1
        self._ui(self._on_scan_done, added, skipped, failed, total)

    def _scan_album_dir(
        self, artist: str, album: str, mp3s: list, folder_year: str = ""
    ) -> tuple[int, int, int]:
        try:
            ss = self.genius.search_album(album, artist)
        except Exception:
            return 0, 0, len(mp3s)
        if not ss or not ss.tracks:
            return 0, 0, len(mp3s)
        artist_name = _extract_name(getattr(ss, "artist", None), artist)
        album_name = getattr(ss, "name", "").strip() or album
        album_year = (
            _release_year(
                {
                    "release_date_for_display": getattr(
                        ss, "release_date_for_display", ""
                    )
                }
            )
            or folder_year
        )
        for item in ss.tracks:
            num, track = item if isinstance(item, tuple) else (None, item)
            track_num = num if isinstance(num, int) else 0
            self.catalog.add(
                artist_name,
                track.title,
                album_name,
                album_year,
                track.to_text(),
                track=track_num,
            )
        added = len(ss.tracks)
        return added, max(0, len(mp3s) - added), 0

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
        album_name = ss_album.get("name") or album
        year = _release_year(ss_album)
        self.catalog.add(ss.artist, ss.title, album_name, year, ss.to_text())
        self._refresh_tree()
        self._set_status(f"Imported: {ss.title}", duration_ms=4000)
        entry = self.catalog.get(ss.artist, ss.title)
        if entry:
            self._show_entry(entry)

    # ── Close ─────────────────────────────────────────────────────────────────

    def _on_close(self):
        data = self._read_settings()
        data.setdefault("sash", {})[type(self).__name__] = self._paned.sashpos(0)
        self._write_settings(data)
        super()._on_close()

    # ── Busy state ────────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.master.configure(cursor="watch" if busy else "")
        # Gate Scan and Update; Remove stays available during scan
        if busy:
            self._scan_btn.configure(state="disabled")
            self._import_btn.configure(state="disabled")
            self._update_btn.configure(state="disabled")
            self._fill_years_btn.configure(state="disabled")
        elif self.genius is not None:
            self._scan_btn.configure(state="normal")
            self._import_btn.configure(state="normal")
            self._update_btn.configure(state="normal")
            self._fill_years_btn.configure(state="normal")


def main():
    import dotenv

    dotenv.load_dotenv(get_resource_path(".env"), override=True)
    root = tk.Tk()
    root.title("Lyrics Browser")
    LyricsBrowser(root)
    root.mainloop()


if __name__ == "__main__":
    main()
