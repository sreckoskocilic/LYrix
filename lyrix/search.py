import sys
import threading
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
import tkinter.scrolledtext as st
from pathlib import Path
from tkinter import ttk

import ttkbootstrap as tb

try:
    from .base_app import LyricsBaseApp, THEME_BG, THEME_FG, THEME_SELECTBG
    from .catalog import (
        Catalog,
        CATALOG_PATH,
        FONT_NAME,
        SEPARATOR,
        SONGS_CATEGORY,
        _extract_name,
        _release_year,
        _song_header,
        _unpack_track,
        get_resource_path,
    )
except ImportError:
    # Allow running as a script: python lyrix/search.py
    import pathlib
    import sys

    sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))
    from base_app import LyricsBaseApp, THEME_BG, THEME_FG, THEME_SELECTBG  # type: ignore
    from catalog import (  # type: ignore
        Catalog,
        CATALOG_PATH,
        FONT_NAME,
        SEPARATOR,
        SONGS_CATEGORY,
        _extract_name,
        _release_year,
        _song_header,
        _unpack_track,
        get_resource_path,
    )


class LyricsApp(LyricsBaseApp):
    def __init__(self, master):
        super().__init__(master)
        self.master.minsize(720, 520)
        self._return_binding = None
        self._return_cmd = None
        self.default_filename = "lyrics"
        self.catalog = Catalog(CATALOG_PATH)

        self._load_custom_font()

        settings = self._read_settings()
        self._restore_font_size(settings)
        self._build_ui()
        self._restore_geometry(default="900x620", settings=settings)

        self.genius = self._create_genius_client(warn=True)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        frame = ttk.Frame(self.master, padding=10)
        frame.pack(fill="both", expand=True)

        # Input fields
        input_frame = ttk.Frame(frame)
        input_frame.pack(fill="x", pady=(0, 6))
        input_frame.columnconfigure(1, weight=1)
        for i, (label, attr) in enumerate(
            (
                ("Artist:", "artist_entry"),
                ("Song:", "song_entry"),
                ("Album:", "album_entry"),
            )
        ):
            ttk.Label(input_frame, text=label).grid(
                row=i, column=0, sticky="e", padx=(0, 6), pady=3
            )
            entry = ttk.Entry(input_frame, font=(FONT_NAME, 11))
            entry.grid(row=i, column=1, sticky="ew", pady=3)
            setattr(self, attr, entry)

        # Lyrics display (read-only)
        self.lyrics_window = st.ScrolledText(
            frame,
            height=30,
            font=(FONT_NAME, self._font_size),
            fg=THEME_FG,
            bg=THEME_BG,
            selectbackground=THEME_SELECTBG,
            selectforeground=THEME_FG,
            insertbackground=THEME_FG,
            borderwidth=0,
            relief="flat",
            padx=8,
            pady=8,
            state="disabled",
        )
        self.lyrics_window.pack(fill="both", expand=True, pady=(0, 6))

        # Button bar
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x")
        self._gated_buttons = []
        for text, cmd, gated in [
            ("Song Lyrics", self.get_lyrics, True),
            ("Album Lyrics", self.get_album, True),
            ("Clear", self.clear_output, False),
            ("Save", self.save_to_file, True),
        ]:
            btn = ttk.Button(btn_frame, text=text, command=cmd)
            btn.pack(side="left", padx=(0, 6))
            if gated:
                self._gated_buttons.append(btn)

        # Keyboard shortcuts
        self.artist_entry.bind(
            "<FocusIn>", lambda e: self._bind_return(self.get_lyrics)
        )
        self.song_entry.bind("<FocusIn>", lambda e: self._bind_return(self.get_lyrics))
        self.album_entry.bind("<FocusIn>", lambda e: self._bind_return(self.get_album))
        self._bind_return(self.get_lyrics)
        mod = "Command" if sys.platform == "darwin" else "Control"
        self.master.bind(f"<{mod}-l>", lambda e: self.clear_output())
        self.master.bind(f"<{mod}-s>", lambda e: self.save_to_file())
        self._bind_font_size_keys()

        ttk.Label(frame, textvariable=self.status_var, font=(FONT_NAME, 9)).pack(
            anchor="w", pady=(4, 0)
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _bind_return(self, cmd):
        if cmd is self._return_cmd:
            return
        if self._return_binding:
            self.master.unbind("<Return>", self._return_binding)
        self._return_binding = self.master.bind(
            "<Return>", lambda e: cmd() if not self._busy else None
        )
        self._return_cmd = cmd

    def _require_genius_client(self):
        if self.genius is None:
            mb.showerror(
                "Error", "GENIUS_TOKEN is missing, so Genius searches are disabled."
            )
            return False
        return True

    def _set_busy(self, busy: bool):
        self._busy = busy
        state, cursor = ("disabled", "watch") if busy else ("normal", "")
        for btn in self._gated_buttons:
            btn.configure(state=state)
        self.master.configure(cursor=cursor)

    def _handle_fetch_result(self, exc, result, render, valid=bool):
        if self._closing:
            return
        self._set_busy(False)
        if exc:
            mb.showerror("Error", f"Genius request failed:\n{exc}")
        elif not valid(result):
            self._set_status("No lyrics found.")
        else:
            render(result)

    def _schedule_result(self, exc, result, render, valid=bool):
        self._ui(self._handle_fetch_result, exc, result, render, valid)

    # ── Search ────────────────────────────────────────────────────────────────

    def get_lyrics(self):
        artist = self.artist_entry.get().strip()
        song = self.song_entry.get().strip()
        if not artist or not song:
            mb.showerror("Error", "Artist or song input is empty!")
            return
        self.album_entry.delete(0, tk.END)
        # Serve from catalog if available — no network call needed
        self.catalog.reload()
        cached = self.catalog.find(artist, song)
        if cached and cached.get("lyrics", "").strip():
            self._render_cached_song(cached)
            return
        if not self._require_genius_client():
            return
        self._set_busy(True)
        self._set_status("Searching…")
        threading.Thread(
            target=self._fetch_song, args=(artist, song), daemon=True
        ).start()

    def _render_cached_song(self, entry: dict):
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
        if entry.get("album"):
            self.album_entry.delete(0, tk.END)
            self.album_entry.insert(0, entry["album"])
        self.default_filename = entry["title"].strip() or "lyrics"
        self.master.title(f"{entry['title']} — Lyrics Search")
        self._set_status(f"Loaded from catalog: {entry['title']}")

    def _fetch_song(self, artist, song):
        result, exc = None, None
        try:
            result = self.genius.search_song(song, artist)
        except Exception as e:
            exc = e
        self._schedule_result(exc, result, self._render_song)

    def _render_song(self, ss):
        lyrics = ss.to_text()
        album = getattr(ss, "album", {}) or {}
        year = _release_year(album)
        album_name = album.get("name", "")
        if not album_name:
            album_name = SONGS_CATEGORY
        self._set_output(_song_header(ss) + lyrics)
        self.catalog.add(ss.artist, ss.title, album_name, year, lyrics)
        self.default_filename = ss.title.strip() or "lyrics"
        self.master.title(f"{ss.title} — Lyrics Search")
        self._set_status(f"Loaded: {ss.title}")

    def get_album(self):
        artist = self.artist_entry.get().strip()
        album = self.album_entry.get().strip()
        if not artist or not album:
            mb.showerror("Error", "Artist or album input is empty!")
            return
        self.song_entry.delete(0, tk.END)
        # Serve from catalog if all tracks with lyrics are available
        self.catalog.reload()
        artist_lower = artist.lower().strip()
        album_lower = album.lower().strip()
        cached_tracks = sorted(
            [
                e
                for e in self.catalog.all_entries()
                if e["artist"].lower().strip() == artist_lower
                and (e.get("album") or "").lower().strip() == album_lower
                and e.get("lyrics", "").strip()
            ],
            key=lambda e: (e.get("track") or 9999, e["title"].lower()),
        )
        if cached_tracks:
            self._render_cached_album(cached_tracks)
            return
        if not self._require_genius_client():
            return
        self._set_busy(True)
        self._set_status("Searching…")
        threading.Thread(
            target=self._fetch_album, args=(artist, album), daemon=True
        ).start()

    def _render_cached_album(self, tracks: list[dict]):
        artist_name = tracks[0]["artist"]
        album_name = tracks[0].get("album") or "Unknown album"
        year_str = tracks[0].get("year", "")
        header = (
            f"{SEPARATOR}\nArtist: {artist_name}\nAlbum: {album_name}"
            f"{' (' + year_str + ')' if year_str else ''}\n{SEPARATOR}\n\n"
        )
        parts = []
        for e in tracks:
            num = e.get("track")
            prefix = f"{num}. " if num else ""
            parts.append(
                f"{SEPARATOR}\n{prefix}{e['title']}\n{SEPARATOR}\n{e['lyrics']}\n\n\n"
            )
        self._set_output(header + "".join(parts))
        self.default_filename = album_name
        self.master.title(f"{album_name} — Lyrics Search")
        self._set_status(f"Loaded from catalog: {album_name} ({len(tracks)} tracks)")

    def _fetch_album(self, artist, album):
        result, exc = None, None
        try:
            result = self.genius.search_album(album, artist)
        except Exception as e:
            exc = e
        self._schedule_result(
            exc, result, self._render_album, valid=lambda ss: ss and ss.tracks
        )

    def _render_album(self, ss):
        artist_name = _extract_name(getattr(ss, "artist", None), "Unknown artist")
        album_name = getattr(ss, "name", "").strip() or "Unknown album"
        album_year = _release_year(ss)
        header = (
            f"{SEPARATOR}\nArtist: {artist_name}\nAlbum: {album_name}"
            f"{' (' + album_year + ')' if album_year else ''}\n{SEPARATOR}\n\n"
        )
        # Single pass: build display text and catalog entries, calling to_text() once each
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
        self._set_output(header + "".join(tracks_text_parts))
        self.catalog.add_many(entries_to_add)
        self.default_filename = album_name
        self.master.title(f"{album_name} — Lyrics Search")
        self._set_status(f"Loaded: {album_name} ({len(ss.tracks)} tracks)")

    # ── Clear & Save ──────────────────────────────────────────────────────────

    def clear_output(self):
        self._set_output("")
        self.default_filename = "lyrics"
        self.master.title("Lyrics Search")
        self._set_status("")

    def save_to_file(self):
        content = self.lyrics_window.get("1.0", "end-1c")
        if not content.strip():
            self._set_status("Nothing to save.")
            return
        self._set_busy(True)
        path = fd.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=self.default_filename,
        )
        self._set_busy(False)
        if not path:
            return
        try:
            Path(path).write_text(content, encoding="utf-8")
            self._set_status(f"Saved to {path}", duration_ms=4000)
        except OSError as exc:
            mb.showerror("Save Error", f"Could not write file:\n{exc}")


def main():
    import dotenv

    dotenv.load_dotenv(get_resource_path(".env"), override=True)
    root = tb.Window(themename="darkly")
    root.title("Lyrics Search")
    LyricsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
