import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from threading import Lock

ENV_ABS_PATH = Path(__file__).parent.parent  # project root
_FROZEN = getattr(sys, "frozen", False)
_log = logging.getLogger(__name__)

if sys.platform == "win32":  # pragma: no cover
    _BASE_DIR = (
        Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Lyrix"
    )
else:
    _BASE_DIR = Path.home() / ".lyrix"

CATALOG_PATH = _BASE_DIR / "lyrics_catalog.json"

SEPARATOR = "=" * 40
FONT_NAME = "Roboto Mono for Powerline"
SONGS_CATEGORY = "Songs"


def get_resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", None)
    return (Path(base_path) if base_path else ENV_ABS_PATH) / relative_path


def _release_year(album_data) -> str:
    """Extract a 4-digit year string from a Genius album dict or object."""
    if album_data is None:
        return ""
    # Prefer release_date_components (datetime or dict with 'year')
    rdc = (
        album_data.get("release_date_components")
        if isinstance(album_data, dict)
        else getattr(album_data, "release_date_components", None)
    )
    if rdc is not None:
        if isinstance(rdc, dict):
            year = rdc.get("year")
            if year:
                return str(year)
        else:
            year = getattr(rdc, "year", None)
            if year:
                return str(year)
    # Fallback: release_date_for_display string
    if isinstance(album_data, dict):
        release_date = album_data.get("release_date_for_display", "") or ""
    else:
        release_date = getattr(album_data, "release_date_for_display", "") or ""
    if not release_date:
        return ""
    if len(release_date) == 4 and release_date.isdigit():
        return release_date
    for fmt in ("%B %d, %Y", "%B %Y"):
        try:
            return str(datetime.strptime(release_date, fmt).year)
        except ValueError:
            continue
    return ""


def _format_song_header(artist: str, title: str, album: str, year: str) -> str:
    """Build a song header block from plain strings."""
    year_suffix = f" ({year})" if year else ""
    return (
        f"{SEPARATOR}\n"
        f"Artist: {artist}\n"
        f"Song: {title}\n"
        f"Album: {album}{year_suffix}\n"
        f"{SEPARATOR}\n\n"
    )


def _format_album_header(artist: str, album: str, year: str) -> str:
    """Build an album header block from plain strings."""
    year_suffix = f" ({year})" if year else ""
    return (
        f"{SEPARATOR}\nArtist: {artist}\nAlbum: {album}{year_suffix}\n{SEPARATOR}\n\n"
    )


def _extract_name(obj, fallback="Unknown"):
    if isinstance(obj, dict):
        return obj.get("name") or fallback
    return getattr(obj, "name", None) or fallback


def _unpack_track(item):
    """Return (track_num_or_None, track_obj) from a (num, track) tuple or bare track."""
    return item if isinstance(item, tuple) else (None, item)


def _format_track(item):
    num, track = _unpack_track(item)
    prefix = f"{num}. " if num is not None else ""
    return f"{SEPARATOR}\n{prefix}{track.title}\n{SEPARATOR}\n{track.to_text()}\n\n\n"


# ── Catalog ───────────────────────────────────────────────────────────────────


class Catalog:
    """Persistent JSON-backed store keyed by (artist, title, album)."""

    def __init__(self, path: Path = CATALOG_PATH):
        self._path = path
        self._data: dict = {}
        self._lock = Lock()
        self._title_index: dict[tuple, list[str]] = {}
        self._artist_album_index: dict[tuple[str, str], list[str]] = {}
        self._file_mtime: int = 0
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                data = raw.get("entries", {})
                needs_save = False
                for old_key in [k for k in data if k.count("\t") == 1]:
                    entry = data.pop(old_key)
                    album_lower = (entry.get("album") or "").lower().strip()
                    data[f"{old_key}\t{album_lower}"] = entry
                    needs_save = True
                self._data = data
                if needs_save:
                    self._save()
                self._rebuild_index()
                try:
                    self._file_mtime = self._path.stat().st_mtime_ns
                except OSError:
                    pass
            except Exception as exc:
                _log.error(
                    "Catalog at %s is unreadable (%s) — starting empty; "
                    "original preserved as %s",
                    self._path,
                    exc,
                    self._path.with_suffix(".corrupt"),
                )
                try:
                    self._path.with_suffix(".corrupt").write_bytes(
                        self._path.read_bytes()
                    )
                except OSError:
                    pass
                self._data = {}

    def _save(self):
        content = json.dumps(
            {"version": 1, "entries": self._data}, ensure_ascii=False, indent=2
        )
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(self._path)
            try:
                self._file_mtime = self._path.stat().st_mtime_ns
            except OSError:
                pass
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    @staticmethod
    def _key(artist: str, title: str, album: str = "") -> str:
        return f"{artist.lower().strip()}\t{title.lower().strip()}\t{(album or '').lower().strip()}"

    @staticmethod
    def _index_remove_key(index: dict, lookup: tuple, key: str) -> None:
        if lookup not in index:
            return
        try:
            index[lookup].remove(key)
        except ValueError:
            pass
        if not index[lookup]:
            del index[lookup]

    def _rebuild_index(self):
        """Rebuild the secondary indexes for fast lookups."""
        self._title_index.clear()
        self._artist_album_index.clear()
        for key in self._data:
            parts = key.split("\t")
            if len(parts) >= 2:
                self._title_index.setdefault((parts[0], parts[1]), []).append(key)
            if len(parts) >= 3:
                self._artist_album_index.setdefault((parts[0], parts[2]), []).append(
                    key
                )

    def add(
        self,
        artist: str,
        title: str,
        album: str,
        year: str,
        lyrics: str,
        track: int = 0,
    ):
        with self._lock:
            key = self._key(artist, title, album)
            existing_added = self._data.get(key, {}).get("added", "")
            is_new = key not in self._data
            self._data[key] = {
                "artist": artist,
                "title": title,
                "album": album or "",
                "year": year or "",
                "track": track,  # track number within album; 0 = unknown
                "lyrics": lyrics,
                "added": existing_added or datetime.now().isoformat(timespec="seconds"),
            }
            if is_new:
                parts = key.split("\t")
                self._title_index.setdefault((parts[0], parts[1]), []).append(key)
                self._artist_album_index.setdefault((parts[0], parts[2]), []).append(
                    key
                )
            self._save()

    def add_many(self, entries: list[dict]):
        """Add multiple entries in a single save, avoiding per-track JSON writes."""
        if not entries:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            for e in entries:
                key = self._key(e["artist"], e["title"], e.get("album", ""))
                existing_added = self._data.get(key, {}).get("added", "")
                is_new = key not in self._data
                self._data[key] = {
                    "artist": e["artist"],
                    "title": e["title"],
                    "album": e.get("album") or "",
                    "year": e.get("year") or "",
                    "track": e.get("track", 0),
                    "lyrics": e["lyrics"],
                    "added": existing_added or e.get("added") or now,
                }
                if is_new:
                    parts = key.split("\t")
                    self._title_index.setdefault((parts[0], parts[1]), []).append(key)
                    self._artist_album_index.setdefault(
                        (parts[0], parts[2]), []
                    ).append(key)
            self._save()

    def get(self, artist: str, title: str, album: str = ""):
        with self._lock:
            return self._data.get(self._key(artist, title, album))

    def find(self, artist: str, title: str):
        """Return the first entry matching artist+title across any album.

        Prefers entries that carry lyrics. Use this instead of get() when the
        album name is not known (e.g. cache-check in the search UI)."""
        at = (artist.lower().strip(), title.lower().strip())
        with self._lock:
            keys = self._title_index.get(at, [])
            if not keys:
                return None
            matches = [self._data[k] for k in keys if k in self._data]
            if not matches:
                return None
            for m in matches:
                if m.get("lyrics", "").strip():
                    return m
            return matches[0]

    def find_album(self, artist: str, album: str) -> list[dict]:
        """Return all entries for a given (artist, album) pair."""
        al = artist.lower().strip()
        alb = album.lower().strip()
        with self._lock:
            keys = self._artist_album_index.get((al, alb), [])
            return [self._data[k] for k in keys if k in self._data]

    def find_duplicates(self) -> list[dict]:
        """Return entries where the same (artist, title) appears under multiple albums with lyrics."""
        with self._lock:
            groups: dict[tuple, list[dict]] = {}
            for key, entry in self._data.items():
                if not entry.get("lyrics", "").strip():
                    continue
                parts = key.split("\t")
                if len(parts) >= 2:
                    at = (parts[0], parts[1])
                    groups.setdefault(at, []).append(entry)
        return [entries for entries in groups.values() if len(entries) > 1]

    def remove(self, artist: str, title: str, album: str = ""):
        with self._lock:
            if album:
                key = self._key(artist, title, album)
                if key in self._data:
                    parts = key.split("\t")
                    at = (parts[0], parts[1])
                    aa = (parts[0], parts[2])
                    self._index_remove_key(self._title_index, at, key)
                    self._index_remove_key(self._artist_album_index, aa, key)
                    del self._data[key]
                    self._save()
            else:
                at = (artist.lower().strip(), title.lower().strip())
                keys = self._title_index.get(at, [])
                for k in keys:
                    if k in self._data:
                        del self._data[k]
                    kparts = k.split("\t")
                    aa = (kparts[0], kparts[2] if len(kparts) > 2 else "")
                    self._index_remove_key(self._artist_album_index, aa, k)
                if keys:
                    del self._title_index[at]
                    self._save()

    def remove_entries(self, pairs: list) -> int:
        """Remove multiple (artist, title) pairs in a single save, deleting ALL album variants."""
        removed = 0
        with self._lock:
            for artist, title in pairs:
                at = (artist.lower().strip(), title.lower().strip())
                keys = self._title_index.get(at, [])
                for k in keys:
                    if k in self._data:
                        del self._data[k]
                    kparts = k.split("\t")
                    aa = (kparts[0], kparts[2] if len(kparts) > 2 else "")
                    self._index_remove_key(self._artist_album_index, aa, k)
                if keys:
                    del self._title_index[at]
                removed += len(keys)
            if removed:
                self._save()
        return removed

    def remove_album_entries(self, triples: list) -> int:
        """Remove entries by exact (artist, title, album) triples in a single save."""
        removed = 0
        with self._lock:
            for artist, title, album in triples:
                key = self._key(artist, title, album)
                if key in self._data:
                    parts = key.split("\t")
                    at = (parts[0], parts[1])
                    aa = (parts[0], parts[2])
                    self._index_remove_key(self._title_index, at, key)
                    self._index_remove_key(self._artist_album_index, aa, key)
                    del self._data[key]
                    removed += 1
            if removed:
                self._save()
        return removed

    def remove_artist(self, artist: str) -> int:
        artist_lower = artist.lower().strip()
        with self._lock:
            aa_pairs = [aa for aa in self._artist_album_index if aa[0] == artist_lower]
            keys = [
                k
                for aa in aa_pairs
                for k in self._artist_album_index[aa]
                if k in self._data
            ]
            for k in keys:
                del self._data[k]
                parts = k.split("\t")
                at = (parts[0], parts[1])
                if at in self._title_index:
                    self._title_index[at] = [
                        key for key in self._title_index[at] if key != k
                    ]
                    if not self._title_index[at]:
                        del self._title_index[at]
            for aa in aa_pairs:
                del self._artist_album_index[aa]
            if keys:
                self._save()
            return len(keys)

    def set_album_year(self, artist: str, album: str, year: str) -> int:
        """Set year on all songs for (artist, album). Returns count updated."""
        updated = 0
        al = artist.lower().strip()
        alb = album.lower().strip()
        with self._lock:
            for key in self._artist_album_index.get((al, alb), []):
                if key in self._data:
                    self._data[key]["year"] = year
                    updated += 1
            if updated:
                self._save()
        return updated

    def reload(self):
        """Re-read the catalog file if it has changed on disk since last load/save."""
        try:
            mtime = self._path.stat().st_mtime_ns
        except OSError:
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            _log.warning(
                "Catalog reload failed (%s) — keeping existing in-memory data", exc
            )
            return
        with self._lock:
            if mtime <= self._file_mtime:
                # Our stat() was taken before a concurrent _save() completed;
                # the file version we read is older than what is already in memory.
                return
            self._data = raw.get("entries", {})
            self._rebuild_index()
            self._file_mtime = mtime

    def find_by_artist(self, artist: str) -> list[dict]:
        """Return all entries for a given artist."""
        al = artist.lower().strip()
        with self._lock:
            result = []
            for (a, _), keys in self._artist_album_index.items():
                if a == al:
                    result.extend(self._data[k] for k in keys if k in self._data)
            return result

    def all_artist_album_pairs(self) -> set[tuple[str, str]]:
        """Return a set of (artist_lower, album_lower) pairs across all entries."""
        with self._lock:
            return set(self._artist_album_index.keys())

    def all_entries(self) -> list[dict]:
        with self._lock:
            return list(self._data.values())

    def export_csv(self, path) -> int:
        """Export catalog to CSV file. Returns number of rows written."""
        import csv

        entries = self.all_entries()
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["artist", "title", "album", "year", "track", "lyrics"],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(entries)
        return len(entries)

    def stats(self) -> dict:
        """Return catalog statistics in a single lock acquisition."""
        with self._lock:
            entries = list(self._data.values())
            artists = {e["artist"] for e in entries}
            albums = {(e["artist"], e.get("album", "")) for e in entries}
            with_lyrics = sum(1 for e in entries if e.get("lyrics", "").strip())
            groups: dict[tuple, int] = {}
            for key, entry in self._data.items():
                if not entry.get("lyrics", "").strip():
                    continue
                parts = key.split("\t")
                if len(parts) >= 2:
                    at = (parts[0], parts[1])
                    groups[at] = groups.get(at, 0) + 1
            duplicates = sum(1 for c in groups.values() if c > 1)
        return {
            "artists": len(artists),
            "albums": len(albums),
            "songs": len(entries),
            "with_lyrics": with_lyrics,
            "without_lyrics": len(entries) - with_lyrics,
            "duplicates": duplicates,
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
