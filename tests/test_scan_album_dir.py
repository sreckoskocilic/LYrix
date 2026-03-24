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

    def test_album_scan_adds_all_tracks(self):
        """All album tracks are imported, regardless of local MP3s."""
        mp3s = [Path(self.tmpdir.name) / f"Title {i}.mp3" for i in range(3)]
        for p in mp3s:
            p.touch()

        def fake_info(path):
            title = path.stem
            return "Artist", title, "Album A", 0

        album_tracks = [
            (1, _FakeTrack("Title 0")),
            (2, _FakeTrack("Other Song")),
            (3, _FakeTrack("Title 2")),
        ]
        album = _FakeAlbum("Album A", album_tracks)
        browser = self._make_browser(album)

        with patch("lyrix.browser._read_mp3_info", side_effect=fake_info):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Artist", "Album A", mp3s
            )

        # All 3 tracks imported with lyrics (new behavior: no matching with local MP3s)
        self.assertEqual((added, skipped, failed), (3, 0, 0))
        self.assertEqual(len(self.catalog), 3)

    def test_album_scan_imports_all_tracks(self):
        """All album tracks are imported regardless of local MP3s."""
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

        self.assertEqual((added, skipped, failed), (2, 0, 0))
        self.assertEqual(len(self.catalog), 2)

    def test_album_scan_partial_title_match(self):
        """Partial title matching no longer needed - all tracks imported."""
        mp3s = [Path(self.tmpdir.name) / "Ruins.mp3"]
        for p in mp3s:
            p.touch()

        album_tracks = [(4, _FakeTrack("In Their Darkened Shrines: IV. Ruins"))]
        album = _FakeAlbum(
            "In Their Darkened Shrines", album_tracks, artist_name="Nile"
        )
        browser = self._make_browser(album)

        with patch(
            "lyrix.browser._read_mp3_info", return_value=("Nile", "Ruins", "Album", 4)
        ):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Nile", "In Their Darkened Shrines", mp3s
            )

        self.assertEqual((added, skipped, failed), (1, 0, 0))
        self.assertEqual(len(self.catalog), 1)

    def test_album_scan_ignores_local_files(self):
        """All album tracks are imported regardless of local MP3s."""
        mp3s = [
            Path(self.tmpdir.name) / "Keep.mp3",
            Path(self.tmpdir.name) / "Missing.mp3",
        ]
        for p in mp3s:
            p.touch()

        album_tracks = [(1, _FakeTrack("Keep"))]
        album = _FakeAlbum("Album A", album_tracks)
        browser = self._make_browser(album)

        with patch(
            "lyrix.browser._read_mp3_info",
            return_value=("Artist", "Missing", "Album", 2),
        ):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Artist", "Album A", mp3s
            )

        # All album tracks imported
        self.assertEqual((added, skipped, failed), (1, 0, 0))
        self.assertEqual(len(self.catalog), 1)


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

    def test_no_double_match_via_stem_after_title_match(self):
        """All album tracks are imported."""
        p = Path(self.tmpdir.name) / "Walk This Way.mp3"
        p.touch()
        mp3s = [p]

        album_tracks = [
            (1, _FakeTrack("Walk This Way")),
            (2, _FakeTrack("Rocks (Walk This Way Extended)")),
        ]
        album = _FakeAlbum("Album", album_tracks)
        browser = self._make_browser(album)

        with patch(
            "lyrix.browser._read_mp3_info",
            return_value=("Artist", "Walk This Way", "Album", 1),
        ):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Artist", "Album", mp3s
            )

        self.assertEqual(added, 2)

    def test_folder_year_used_as_fallback_when_album_year_empty(self):
        """When Genius returns no release date, folder_year fills in the year."""
        p = Path(self.tmpdir.name) / "Song.mp3"
        p.touch()
        mp3s = [p]

        def fake_info(path):
            return "Artist", "Song", "Album", 1

        album = _FakeAlbum("Album", [(1, _FakeTrack("Song"))], release_date="")
        browser = self._make_browser(album)

        with patch("lyrix.browser._read_mp3_info", side_effect=fake_info):
            browser._scan_album_dir("Artist", "Album", mp3s, folder_year="1999")

        entry = self.catalog.get("Artist", "Song", "Album")
        self.assertEqual(entry["year"], "1999")

    def test_album_year_takes_precedence_over_folder_year(self):
        """Genius release date wins over the folder_year fallback."""
        p = Path(self.tmpdir.name) / "Song.mp3"
        p.touch()
        mp3s = [p]

        def fake_info(path):
            return "Artist", "Song", "Album", 1

        album = _FakeAlbum("Album", [(1, _FakeTrack("Song"))], release_date="2005")
        browser = self._make_browser(album)

        with patch("lyrix.browser._read_mp3_info", side_effect=fake_info):
            browser._scan_album_dir("Artist", "Album", mp3s, folder_year="1999")

        entry = self.catalog.get("Artist", "Song", "Album")
        self.assertEqual(entry["year"], "2005")

    def test_existing_catalog_entry_preserved(self):
        """All album tracks are imported."""
        self.catalog.add(
            "Artist", "Track A", "Album", "2020", "Existing lyrics", track=1
        )

        p = Path(self.tmpdir.name) / "track_b.mp3"
        p.touch()
        mp3s = [p]

        album_tracks = [(1, _FakeTrack("Track A")), (2, _FakeTrack("Track B"))]
        album = _FakeAlbum("Album", album_tracks)
        browser = self._make_browser(album)

        with patch(
            "lyrix.browser._read_mp3_info",
            return_value=("Artist", "Track B", "Album", 2),
        ):
            added, skipped, failed, matched = browser._scan_album_dir(
                "Artist", "Album", mp3s
            )

        self.assertEqual(added, 2)
        self.assertEqual(len(self.catalog), 2)


class FinishImportFileTests(unittest.TestCase):
    """Tests for _finish_import_file — specifically that ss.title is used."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.catalog = Catalog(Path(self.tmpdir.name) / "catalog.json")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_browser(self):
        browser = LyricsBrowser.__new__(LyricsBrowser)
        browser.catalog = self.catalog
        browser._busy = False
        browser._closing = False
        browser._current_entry = None
        browser._status_after_id = None
        browser.status_var = SimpleNamespace(set=lambda *_: None)
        browser.master = SimpleNamespace(
            after=lambda *_: None,
            after_cancel=lambda *_: None,
        )
        browser._edit_btn = SimpleNamespace(configure=lambda **_: None)
        browser._copy_btn = SimpleNamespace(configure=lambda **_: None)
        # Stub out methods that touch tkinter
        browser._set_busy = lambda *_: None
        browser._refresh_tree = lambda *_: None
        browser._set_status = lambda *_, **__: None
        browser._show_entry = lambda *_: None
        return browser

    def test_genius_title_used_when_differs_from_id3_title(self):
        """Catalog entry uses ss.title (Genius), not the original ID3 title."""
        browser = self._make_browser()

        fake_song = SimpleNamespace(
            title="Correct Genius Title",
            artist="Artist",
            album={"name": "Album", "release_date_for_display": "2020"},
            to_text=lambda: "lyrics",
        )
        browser._finish_import_file(fake_song, "Artist", "Wrong ID3 Title", "Album", 1)

        entry = self.catalog.get("Artist", "Correct Genius Title", "Album")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["title"], "Correct Genius Title")
        self.assertIsNone(self.catalog.get("Artist", "Wrong ID3 Title", "Album"))

    def test_genius_title_same_as_id3(self):
        """When titles match, entry is stored normally."""
        browser = self._make_browser()

        fake_song = SimpleNamespace(
            title="Same Title",
            artist="Artist",
            album={"name": "Album", "release_date_for_display": "2020"},
            to_text=lambda: "lyrics",
        )
        browser._finish_import_file(fake_song, "Artist", "Same Title", "Album", 1)

        entry = self.catalog.get("Artist", "Same Title", "Album")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["lyrics"], "lyrics")


if __name__ == "__main__":
    unittest.main()
