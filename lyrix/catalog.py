import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ENV_ABS_PATH = Path(__file__).parent.parent  # project root
_FROZEN = getattr(sys, "frozen", False)
CATALOG_PATH = (
    Path(sys.executable).parent if _FROZEN else ENV_ABS_PATH
) / "lyrics_catalog.json"
SEPARATOR = "=" * 40
FONT_NAME = "Roboto Mono for Powerline"


def get_resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", None)
    return (Path(base_path) if base_path else ENV_ABS_PATH) / relative_path


def _release_year(album_data):
    release_date = (album_data or {}).get("release_date_for_display", "")
    if not release_date:
        return ""
    for fmt in ("%B %d, %Y", "%B %Y", "%Y"):
        try:
            return str(datetime.strptime(release_date, fmt).year)
        except ValueError:
            continue
    return release_date


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


def _format_track(item):
    num, track = item if isinstance(item, tuple) else (None, item)
    prefix = f"{num}. " if num is not None else ""
    return f"{SEPARATOR}\n{prefix}{track.title}\n{SEPARATOR}\n{track.to_text()}\n\n\n"


def _read_mp3_tags(path: Path) -> tuple[str, str, str]:
    """Return (artist, title, album) from ID3 tags, falling back to filename parsing."""
    try:
        from mutagen.mp3 import MP3

        audio = MP3(str(path))
        if audio.tags:

            def tag(key):
                v = audio.tags.get(key)
                return str(v.text[0]).strip() if v and v.text else ""

            return tag("TPE1") or tag("TPE2"), tag("TIT2"), tag("TALB")
    except Exception:
        pass
    stem = path.stem
    if " - " in stem:
        parts = stem.split(" - ", 1)
        return parts[0].strip(), parts[1].strip(), ""
    return "", stem, ""


def _detect_album(mp3s: list) -> tuple[str, str] | None:
    """Return (artist, album) if ≥70% of files share the same album tag, else None."""
    tags = [_read_mp3_tags(p) for p in mp3s]
    valid = [(a, al) for a, _, al in tags if a and al]
    if not valid or len(valid) < max(2, len(mp3s) * 0.6):
        return None
    album_counts = Counter(al.lower() for _, al in valid)
    top_album, top_count = album_counts.most_common(1)[0]
    if top_count / len(valid) < 0.7:
        return None
    artist_counts = Counter(a.lower() for a, al in valid if al.lower() == top_album)
    top_artist = artist_counts.most_common(1)[0][0]
    album_name = next(al for _, al in valid if al.lower() == top_album)
    artist_name = next(
        a for a, al in valid if a.lower() == top_artist and al.lower() == top_album
    )
    return artist_name, album_name


# ── Catalog ───────────────────────────────────────────────────────────────────


class Catalog:
    """Persistent JSON-backed store keyed by (artist, title)."""

    def __init__(self, path: Path = CATALOG_PATH):
        self._path = path
        self._data: dict = {}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._data = raw.get("entries", {})
            except Exception:
                self._data = {}

    def _save(self):
        content = json.dumps(
            {"version": 1, "entries": self._data}, ensure_ascii=False, indent=2
        )
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    @staticmethod
    def _key(artist: str, title: str) -> str:
        return f"{artist.lower().strip()}\t{title.lower().strip()}"

    def add(
        self,
        artist: str,
        title: str,
        album: str,
        year: str,
        lyrics: str,
        track: int = 0,
    ):
        self._data[self._key(artist, title)] = {
            "artist": artist,
            "title": title,
            "album": album or "",
            "year": year or "",
            "track": track,  # track number within album; 0 = unknown
            "lyrics": lyrics,
            "added": datetime.now().isoformat(timespec="seconds"),
        }
        self._save()

    def get(self, artist: str, title: str):
        return self._data.get(self._key(artist, title))

    def remove(self, artist: str, title: str):
        key = self._key(artist, title)
        if key in self._data:
            del self._data[key]
            self._save()

    def remove_entries(self, pairs: list) -> int:
        """Remove multiple (artist, title) pairs in a single save."""
        removed = 0
        for artist, title in pairs:
            key = self._key(artist, title)
            if key in self._data:
                del self._data[key]
                removed += 1
        if removed:
            self._save()
        return removed

    def remove_artist(self, artist: str) -> int:
        keys = [
            k
            for k, v in self._data.items()
            if v["artist"].lower().strip() == artist.lower().strip()
        ]
        for k in keys:
            del self._data[k]
        if keys:
            self._save()
        return len(keys)

    def set_album_year(self, artist: str, album: str, year: str) -> int:
        """Set year on all songs for (artist, album). Returns count updated."""
        updated = 0
        al = artist.lower().strip()
        alb = album.lower().strip()
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

    def all_entries(self):
        return list(self._data.values())

    def __len__(self):
        return len(self._data)
