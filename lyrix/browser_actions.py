"""Action methods for LyricsBrowser — update, import, fetch missing."""

import logging
import sys
import threading
import tkinter.messagebox as mb

try:
    from .catalog import (
        SONGS_CATEGORY,
        _extract_name,
        _release_year,
        _unpack_track,
    )
except ImportError:
    import pathlib

    sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))
    from catalog import (  # type: ignore
        SONGS_CATEGORY,
        _extract_name,
        _release_year,
        _unpack_track,
    )


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


class BrowserActions:
    """Mixin providing action functionality for LyricsBrowser."""

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

    # ── Import Artist All Releases ────────────────────────────────────────────

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
