"""Search methods for LyricsBrowser — song, album, and artist search."""

import sys
import threading
import tkinter.messagebox as mb

try:
    from .catalog import SONGS_CATEGORY, _extract_name, _release_year, _unpack_track
except ImportError:
    import pathlib

    sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))
    from catalog import SONGS_CATEGORY, _extract_name, _release_year, _unpack_track  # type: ignore


class BrowserSearch:
    """Mixin providing search functionality for LyricsBrowser."""

    # ── Song search ────────────────────────────────────────────────────────────

    def _search_song_lyrics(self):
        """Search for song lyrics."""
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
        """Search for song lyrics."""
        try:
            ss = self.genius.search_song(song, artist)
        except Exception as e:
            self._ui(self._set_busy, False)
            self._ui(mb.showerror, "Error", f"Search failed:\n{e}")
            return

        self._ui(self._set_busy, False)
        if not ss:
            self._ui(self._set_status, f"Not found: {song}", 4000)
            return

        self._ui(self._finish_search_song, ss, artist, 0)

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

        from .catalog import SEPARATOR

        header = (
            f"{SEPARATOR}\nArtist: {ss.artist}\nSong: {title}\n"
            f"Album: {album_name}{year_suffix}\n{SEPARATOR}\n\n"
        )
        self._set_output(header + lyrics_text)
        self._set_status(f"Found and imported: {title}")
        self._refresh_tree()

    # ── Album search ───────────────────────────────────────────────────────────

    def _search_album_lyrics(self):
        """Search for album lyrics."""
        if not self._require_genius_client():
            return
        self._song_entry.delete(0, "end")
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
        """Search for album lyrics."""
        try:
            ss = self.genius.search_album(album, artist)
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

        from .catalog import SEPARATOR

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

    # ── Artist search ──────────────────────────────────────────────────────────

    def _search_artist_songs(self):
        """Search for artist and import all releases with lyrics."""
        if not self._require_genius_client():
            return
        self._song_entry.delete(0, "end")
        self._album_entry.delete(0, "end")
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
