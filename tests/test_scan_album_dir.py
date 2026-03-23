import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from lyrix.browser import LyricsBrowser, _folder_album_info
from lyrix.catalog import Catalog


class _FakeTrack:
    def __init__(self, title: str):
        self.title = title

    def to_text(self):
        return f"Lyrics for {self.title}"


class _FakeAlbum:
    def __init__(self, name: str, tracks, artist_name="Artist", release_date="2020"):
        self.name = name
        self.tracks = tracks
        self.artist = {"name": artist_name}
        self.release_date_for_display = release_date


class ScanAlbumDirTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.catalog = Catalog(Path(self.tmpdir.name) / "catalog.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_browser(self, album):
        fake_genius = SimpleNamespace(search_album=lambda album_name, artist: album)
        browser = LyricsBrowser.__new__(LyricsBrowser)
        browser.genius = fake_genius
        browser.catalog = self.catalog
        return browser

    def test_album_scan_adds_all_tracks_matching_with_lyrics(self):
        """Matched local MP3s get lyrics; unmatched album tracks get empty lyrics."""
        mp3s = [Path(self.tmpdir.name) / f"Title {i}.mp3" for i in range(3)]
        for p in mp3s:
            p.touch()

        def fake_info(path):
            title = path.stem
            return "Artist", title, "Album A", 0

        album_tracks = [
            (1, _FakeTrack("Title 0")),
            (2, _FakeTrack("Other Song")),  # not on disk
            (3, _FakeTrack("Title 2")),
        ]
        album = _FakeAlbum("Album A", album_tracks)
        browser = self._make_browser(album)

        with patch("lyrix.browser._read_mp3_info", side_effect=fake_info):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Artist", "Album A", mp3s
            )

        # 2 tracks matched local MP3s (with lyrics), 1 album track has no local file
        self.assertEqual((added, skipped, failed), (2, 1, 0))
        self.assertEqual(matched, {"title 0", "title 2"})
        self.assertEqual(len(self.catalog), 3)
        entries = {e["title"]: e for e in self.catalog.all_entries()}
        self.assertIn("Title 0", entries)
        self.assertIn("Title 2", entries)
        self.assertIn("Other Song", entries)
        self.assertTrue(entries["Title 0"]["lyrics"])
        self.assertTrue(entries["Title 2"]["lyrics"])
        self.assertEqual(entries["Other Song"]["lyrics"], "")

    def test_album_scan_adds_all_tracks_when_no_mp3_matches(self):
        """All album tracks are added with empty lyrics when no local MP3 matches."""
        mp3s = [Path(self.tmpdir.name) / f"Title {i}.mp3" for i in range(2)]
        for p in mp3s:
            p.touch()

        def fake_info(path):
            title = path.stem
            return "Artist", title, "Album A", 0

        album_tracks = [(1, _FakeTrack("Different 1")), (2, _FakeTrack("Different 2"))]
        album = _FakeAlbum("Album A", album_tracks)
        browser = self._make_browser(album)

        with patch("lyrix.browser._read_mp3_info", side_effect=fake_info):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Artist", "Album A", mp3s
            )

        self.assertEqual((added, skipped, failed), (0, 2, 0))
        self.assertEqual(matched, set())
        self.assertEqual(len(self.catalog), 2)
        for entry in self.catalog.all_entries():
            self.assertEqual(entry["lyrics"], "")

    def test_album_scan_partial_title_match(self):
        mp3s = [Path(self.tmpdir.name) / "Ruins.mp3"]
        for p in mp3s:
            p.touch()

        def fake_tags(path):
            return "Nile", "Ruins", "In Their Darkened Shrines", 4

        # Album track has full title "In Their Darkened Shrines: IV. Ruins"
        album_tracks = [(4, _FakeTrack("In Their Darkened Shrines: IV. Ruins"))]
        album = _FakeAlbum(
            "In Their Darkened Shrines", album_tracks, artist_name="Nile"
        )
        browser = self._make_browser(album)

        with patch("lyrix.browser._read_mp3_info", side_effect=fake_tags):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Nile", "In Their Darkened Shrines", mp3s
            )

        self.assertEqual((added, skipped, failed), (1, 0, 0))
        self.assertIn("ruins", matched)
        self.assertEqual(len(self.catalog), 1)
        entry = self.catalog.get(
            "Nile", "In Their Darkened Shrines: IV. Ruins", "In Their Darkened Shrines"
        )
        self.assertIsNotNone(entry)

    def test_partial_album_scan_triggers_per_file_retry(self):
        mp3s = [
            Path(self.tmpdir.name) / "Keep.mp3",
            Path(self.tmpdir.name) / "Missing.mp3",
        ]
        for p in mp3s:
            p.touch()

        album_tracks = [(1, _FakeTrack("Keep"))]  # album only returns one track

        def fake_info(path):
            title = path.stem
            return "Artist", title, "Album A", 0

        class DummyGenius:
            def search_album(self, album_name, artist):
                return _FakeAlbum("Album A", album_tracks)

            def search_song(self, title, artist):
                if title == "Missing":
                    return _FakeTrack("Missing")
                return None

        browser = LyricsBrowser.__new__(LyricsBrowser)
        browser.genius = DummyGenius()
        browser.catalog = self.catalog
        browser._closing = False
        browser._ui = lambda fn, *args, **kwargs: fn(*args, **kwargs)
        browser._set_status = lambda *_, **__: None

        with patch("lyrix.browser._read_mp3_info", side_effect=fake_info):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Artist", "Album A", mp3s
            )

        # Only "Keep" is in the album; "Missing" stays unmatched for per-file retry
        self.assertEqual((added, skipped, failed), (1, 0, 0))
        titles = {e["title"] for e in self.catalog.all_entries()}
        self.assertEqual(titles, {"Keep"})
        self.assertEqual(matched, {"keep"})


class FolderAlbumInfoTests(unittest.TestCase):
    """Tests for the _folder_album_info() helper."""

    def _cache(self, mp3s, artist):
        """Build a minimal tag_cache for the given paths."""
        return {p: (artist, p.stem, "Unknown", 0) for p in mp3s}

    def test_plain_folder_name_used_as_album(self):
        mp3s = [Path("/music/Thriller/01.mp3")]
        cache = self._cache(mp3s, "Michael Jackson")
        result = _folder_album_info(Path("/music/Thriller"), mp3s, cache)
        self.assertEqual(result, ("Michael Jackson", "Thriller"))

    def test_year_prefix_stripped(self):
        mp3s = [Path("/music/1982 - Thriller/01.mp3")]
        cache = self._cache(mp3s, "Michael Jackson")
        result = _folder_album_info(Path("/music/1982 - Thriller"), mp3s, cache)
        self.assertEqual(result, ("Michael Jackson", "Thriller"))

    def test_artist_dash_album_style(self):
        mp3s = [Path("/music/Michael Jackson - Thriller/01.mp3")]
        cache = self._cache(mp3s, "Michael Jackson")
        result = _folder_album_info(
            Path("/music/Michael Jackson - Thriller"), mp3s, cache
        )
        self.assertEqual(result, ("Michael Jackson", "Thriller"))

    def test_year_and_artist_dash_album(self):
        mp3s = [Path("/music/1982 - MJ - Thriller/01.mp3")]
        cache = self._cache(mp3s, "MJ")
        result = _folder_album_info(Path("/music/1982 - MJ - Thriller"), mp3s, cache)
        self.assertEqual(result, ("MJ", "Thriller"))

    def test_returns_none_when_no_artist_tags(self):
        mp3s = [Path("/music/Unknown/01.mp3")]
        cache = {mp3s[0]: ("", "", "", 0)}
        result = _folder_album_info(Path("/music/Unknown"), mp3s, cache)
        self.assertIsNone(result)

    def test_most_common_artist_chosen(self):
        mp3s = [Path(f"/music/Album/{i}.mp3") for i in range(4)]
        cache = {
            mp3s[0]: ("Artist A", "s1", "", 0),
            mp3s[1]: ("Artist A", "s2", "", 0),
            mp3s[2]: ("Artist A", "s3", "", 0),
            mp3s[3]: ("Artist B", "s4", "", 0),
        }
        result = _folder_album_info(Path("/music/Album"), mp3s, cache)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "Artist A")


class ScanAlbumDirEdgeCaseTests(unittest.TestCase):
    """Edge-case paths inside _scan_album_dir."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.catalog = Catalog(Path(self.tmpdir.name) / "catalog.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_browser(self, album):
        fake_genius = SimpleNamespace(search_album=lambda *_: album)
        browser = LyricsBrowser.__new__(LyricsBrowser)
        browser.genius = fake_genius
        browser.catalog = self.catalog
        return browser

    def test_stem_match_when_tag_title_differs(self):
        """Track title appears in the filename stem but not in the tag title."""
        p = Path(self.tmpdir.name) / "01 - Walk This Way.mp3"
        p.touch()
        mp3s = [p]

        def fake_info(path):
            # Tag title deliberately does NOT match the track title
            return "Artist", "", "Album", 0

        album_tracks = [(1, _FakeTrack("Walk This Way"))]
        album = _FakeAlbum("Album", album_tracks)
        browser = self._make_browser(album)

        with patch("lyrix.browser._read_mp3_info", side_effect=fake_info):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Artist", "Album", mp3s
            )

        self.assertEqual(added, 1)
        entry = self.catalog.get("Artist", "Walk This Way", "Album")
        self.assertIsNotNone(entry)
        self.assertTrue(entry["lyrics"])

    def test_album_fetch_exception_returns_failure(self):
        def raise_exc(*_):
            raise RuntimeError("network error")

        browser = LyricsBrowser.__new__(LyricsBrowser)
        browser.genius = SimpleNamespace(search_album=raise_exc)
        browser.catalog = self.catalog

        mp3s = [Path(self.tmpdir.name) / "song.mp3"]
        for p in mp3s:
            p.touch()

        added, skipped, failed, matched = browser._scan_album_dir(
            "Artist", "Album", mp3s
        )
        self.assertEqual((added, skipped, failed), (0, 0, len(mp3s)))
        self.assertEqual(matched, set())

    def test_no_album_found_returns_failure(self):
        browser = LyricsBrowser.__new__(LyricsBrowser)
        browser.genius = SimpleNamespace(search_album=lambda *_: None)
        browser.catalog = self.catalog

        mp3s = [Path(self.tmpdir.name) / "song.mp3"]
        for p in mp3s:
            p.touch()

        added, skipped, failed, matched = browser._scan_album_dir(
            "Artist", "Album", mp3s
        )
        self.assertEqual((added, skipped, failed), (0, 0, len(mp3s)))

    def test_artist_mismatch_returns_failure(self):
        album = _FakeAlbum(
            "Album", [(1, _FakeTrack("Song"))], artist_name="Wrong Artist"
        )
        browser = self._make_browser(album)

        mp3s = [Path(self.tmpdir.name) / "Song.mp3"]
        for p in mp3s:
            p.touch()

        added, skipped, failed, matched = browser._scan_album_dir(
            "Correct Artist", "Album", mp3s
        )
        self.assertEqual((added, skipped, failed), (0, 0, len(mp3s)))
        self.assertEqual(len(self.catalog), 0)

    def test_existing_catalog_entry_with_lyrics_not_overwritten(self):
        """Album tracks already in catalog with lyrics are skipped (not overwritten with empty)."""
        self.catalog.add(
            "Artist", "Track A", "Album", "2020", "Existing lyrics", track=1
        )

        p = Path(self.tmpdir.name) / "track_b.mp3"
        p.touch()
        mp3s = [p]

        def fake_info(path):
            return "Artist", "Track B", "Album", 2

        # Album has two tracks; only Track B has a local mp3
        album_tracks = [(1, _FakeTrack("Track A")), (2, _FakeTrack("Track B"))]
        album = _FakeAlbum("Album", album_tracks)
        browser = self._make_browser(album)

        with patch("lyrix.browser._read_mp3_info", side_effect=fake_info):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Artist", "Album", mp3s
            )

        # Track B matched → with lyrics; Track A already in catalog → skipped (continue)
        self.assertEqual((added, skipped, failed), (1, 0, 0))
        # Existing Track A entry must be untouched
        entry_a = self.catalog.get("Artist", "Track A", "Album")
        self.assertEqual(entry_a["lyrics"], "Existing lyrics")


if __name__ == "__main__":
    unittest.main()
