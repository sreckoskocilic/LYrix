import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from lyrix.browser import LyricsBrowser
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

    def test_album_scan_adds_only_matching_tracks(self):
        mp3s = [Path(self.tmpdir.name) / f"Title {i}.mp3" for i in range(3)]
        for p in mp3s:
            p.touch()

        def fake_tags(path):
            title = path.stem
            return "Artist", title, "Album A"

        album_tracks = [
            (1, _FakeTrack("Title 0")),
            (2, _FakeTrack("Other Song")),
            (3, _FakeTrack("Title 2")),
        ]
        album = _FakeAlbum("Album A", album_tracks)
        browser = self._make_browser(album)

        with patch("lyrix.browser._read_mp3_tags", side_effect=fake_tags):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Artist", "Album A", mp3s
            )

        self.assertEqual((added, skipped, failed), (2, 0, 0))
        self.assertEqual(matched, {"title 0", "title 2"})
        self.assertEqual(len(self.catalog), 2)
        titles = {e["title"] for e in self.catalog.all_entries()}
        self.assertEqual(titles, {"Title 0", "Title 2"})

    def test_album_scan_returns_failure_when_no_matches(self):
        mp3s = [Path(self.tmpdir.name) / f"Title {i}.mp3" for i in range(2)]
        for p in mp3s:
            p.touch()

        def fake_tags(path):
            title = path.stem
            return "Artist", title, "Album A"

        album_tracks = [(1, _FakeTrack("Different 1")), (2, _FakeTrack("Different 2"))]
        album = _FakeAlbum("Album A", album_tracks)
        browser = self._make_browser(album)

        with patch("lyrix.browser._read_mp3_tags", side_effect=fake_tags):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Artist", "Album A", mp3s
            )

        self.assertEqual((added, skipped, failed), (0, 0, len(mp3s)))
        self.assertEqual(len(self.catalog), 0)

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
        entry = self.catalog.get("Nile", "In Their Darkened Shrines: IV. Ruins")
        self.assertIsNotNone(entry)

    def test_partial_album_scan_triggers_per_file_retry(self):
        mp3s = [
            Path(self.tmpdir.name) / "Keep.mp3",
            Path(self.tmpdir.name) / "Missing.mp3",
        ]
        for p in mp3s:
            p.touch()

        album_tracks = [(1, _FakeTrack("Keep"))]  # album only returns one track
        album = _FakeAlbum("Album A", album_tracks)

        def fake_tags(path):
            title = path.stem
            return "Artist", title, "Album A"

        class DummyGenius:
            def search_album(self, album_name, artist):
                return album

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

        with patch("lyrix.browser._read_mp3_tags", side_effect=fake_tags):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Artist", "Album A", mp3s
            )

        self.assertEqual((added, skipped, failed), (1, 0, 0))
        titles = {e["title"] for e in self.catalog.all_entries()}
        self.assertEqual(titles, {"Keep"})
        self.assertEqual(matched, {"keep"})


if __name__ == "__main__":
    unittest.main()
