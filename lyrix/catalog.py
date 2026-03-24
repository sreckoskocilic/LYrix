import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from threading import Lock

ENV_ABS_PATH = Path(__file__).parent.parent  # project root
_FROZEN = getattr(sys, "frozen", False)

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


def _song_header(song):
    album = getattr(song, "album", {}) or {}
    album_name = album.get("name") or "Unknown album"
    year = _release_year(album)
    year_suffix = f" ({year})" if year else ""
    return (
        f"{SEPARATOR}\n"
        f"Artist: {song.artist}\n"
        f"Song: {song.title}\n"
        f"Album: {album_name}{year_suffix}\n"
        f"{SEPARATOR}\n\n"
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
            except Exception as exc:
                logging.getLogger(__name__).error(
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
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    @staticmethod
    def _key(artist: str, title: str, album: str = "") -> str:
        return f"{artist.lower().strip()}\t{title.lower().strip()}\t{(album or '').lower().strip()}"

    def _rebuild_index(self):
        """Rebuild the secondary title index for fast lookups."""
        self._title_index.clear()
        for key in self._data:
            parts = key.split("\t")
            if len(parts) >= 2:
                artist_title = (parts[0], parts[1])
                self._title_index.setdefault(artist_title, []).append(key)

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
            return [
                e
                for e in self._data.values()
                if e["artist"].lower().strip() == al
                and (e.get("album") or "").lower().strip() == alb
            ]

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
                    if at in self._title_index:
                        self._title_index[at] = [
                            k for k in self._title_index[at] if k != key
                        ]
                        if not self._title_index[at]:
                            del self._title_index[at]
                    del self._data[key]
                    self._save()
            else:
                at = (artist.lower().strip(), title.lower().strip())
                keys = self._title_index.get(at, [])
                for k in keys:
                    if k in self._data:
                        del self._data[k]
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
                    if at in self._title_index:
                        self._title_index[at] = [
                            k for k in self._title_index[at] if k != key
                        ]
                        if not self._title_index[at]:
                            del self._title_index[at]
                    del self._data[key]
                    removed += 1
            if removed:
                self._save()
        return removed

    def remove_artist(self, artist: str) -> int:
        artist_lower = artist.lower().strip()
        with self._lock:
            keys = [
                k
                for k, v in self._data.items()
                if v["artist"].lower().strip() == artist_lower
            ]
            for k in keys:
                parts = k.split("\t")
                at = (parts[0], parts[1])
                if at in self._title_index:
                    self._title_index[at] = [
                        key for key in self._title_index[at] if key != k
                    ]
                    if not self._title_index[at]:
                        del self._title_index[at]
                del self._data[k]
            if keys:
                self._save()
            return len(keys)

    def set_album_year(self, artist: str, album: str, year: str) -> int:
        """Set year on all songs for (artist, album). Returns count updated."""
        updated = 0
        al = artist.lower().strip()
        alb = album.lower().strip()
        with self._lock:
            for entry in self._data.values():
                if (
                    entry["artist"].lower().strip() == al
                    and (entry.get("album") or "").lower().strip() == alb
                ):
                    entry["year"] = year
                    updated += 1
            if updated:
                self._save()
        return updated

    def reload(self):
        """Re-read the catalog file, picking up changes made by another process."""
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            with self._lock:
                self._data = raw.get("entries", {})
                self._rebuild_index()
        except Exception:
            pass  # keep existing in-memory data on read error

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

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
