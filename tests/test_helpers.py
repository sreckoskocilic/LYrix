import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lyrix.catalog import (
    _release_year,
    _format_track,
    _song_header,
    _read_mp3_tags,
    _read_mp3_track_num,
    _detect_album,
    _extract_name,
    get_resource_path,
)
from lyrix.base_app import _year_sort
from lyrix.browser import _year_from_folder


class YearParsingTests(unittest.TestCase):
    def test_year_from_folder_happy_path(self):
        self.assertEqual(_year_from_folder("1998 - Album"), "1998")
        self.assertEqual(_year_from_folder("1999.Album"), "1999")

    def test_year_from_folder_non_year(self):
        self.assertEqual(_year_from_folder("19a8 Album"), "")
        self.assertEqual(_year_from_folder("foo"), "")

    def test_release_year_formats(self):
        self.assertEqual(
            _release_year({"release_date_for_display": "March 5, 2001"}), "2001"
        )
        self.assertEqual(
            _release_year({"release_date_for_display": "July 2010"}), "2010"
        )
        self.assertEqual(_release_year({"release_date_for_display": "1997"}), "1997")
        self.assertEqual(_release_year({"release_date_for_display": ""}), "")
        self.assertEqual(_release_year({"release_date_for_display": "Invalid"}), "")

    def test_release_year_none_returns_empty(self):
        self.assertEqual(_release_year(None), "")

    def test_year_sort(self):
        self.assertLess(_year_sort("1990"), _year_sort("1999"))
        self.assertGreater(_year_sort(""), _year_sort("2020"))
        self.assertEqual(_year_sort(None), 9999)


class FormattingTests(unittest.TestCase):
    def test_format_track_with_tuple(self):
        track = SimpleNamespace(title="Song A", to_text=lambda: "lyrics")
        text = _format_track((1, track))
        self.assertIn("1. Song A", text)
        self.assertIn("lyrics", text)

    def test_song_header(self):
        song = SimpleNamespace(
            artist="Artist",
            title="Title",
            album={"name": "Album", "release_date_for_display": "2020"},
        )
        header = _song_header(song)
        self.assertIn("Artist: Artist", header)
        self.assertIn("Song: Title", header)
        self.assertIn("Album: Album (2020)", header)

    def test_get_resource_path(self):
        path = get_resource_path("foo.txt")
        self.assertTrue(str(path).endswith("foo.txt"))

    def test_extract_name_fallback(self):
        obj = SimpleNamespace()  # no name attribute
        self.assertEqual(_extract_name(obj), "Unknown")


class Mp3TagFallbackTests(unittest.TestCase):
    def test_read_mp3_tags_falls_back_to_filename(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "Artist - Song.mp3"
            path.touch()
            artist, title, album = _read_mp3_tags(path)
            self.assertEqual(artist, "Artist")
            self.assertEqual(title, "Song")
            self.assertEqual(album, "")

    def test_read_mp3_tags_single_word_filename(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "Lonely.mp3"
            path.touch()
            artist, title, album = _read_mp3_tags(path)
            self.assertEqual((artist, title, album), ("", "Lonely", ""))

    def test_read_mp3_tags_uses_mutagen_when_available(self):
        class FakeTag:
            def __init__(self, text):
                self.text = [text]

        class FakeAudio:
            def __init__(self):
                self.tags = {
                    "TPE1": FakeTag("Artist"),
                    "TIT2": FakeTag("Title"),
                    "TALB": FakeTag("Album"),
                }

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.mp3"
            path.touch()
            with patch("mutagen.mp3.MP3", return_value=FakeAudio()):
                artist, title, album = _read_mp3_tags(path)
        self.assertEqual((artist, title, album), ("Artist", "Title", "Album"))

    def test_detect_album_returns_none_when_no_valid_tags(self):
        mp3s = [Path("a.mp3"), Path("b.mp3")]

        with patch("lyrix.catalog._read_mp3_tags", return_value=("", "", "")):
            self.assertIsNone(_detect_album(mp3s))

    def test_detect_album_uses_tag_cache(self):
        mp3s = [Path(f"t{i}.mp3") for i in range(3)]
        tag_cache = {p: ("Artist", f"Title {i}", "Album") for i, p in enumerate(mp3s)}
        result = _detect_album(mp3s, tag_cache=tag_cache)
        self.assertIsNotNone(result)
        self.assertEqual(result, ("Artist", "Album"))


class Mp3TrackNumTests(unittest.TestCase):
    def test_read_mp3_track_num_from_tags(self):
        class FakeTag:
            def __init__(self, text):
                self.text = [text]

        class FakeAudio:
            def __init__(self, track_num):
                self.tags = {"TRCK": FakeTag(track_num)}

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.mp3"
            path.touch()
            with patch("mutagen.mp3.MP3", return_value=FakeAudio("5")):
                self.assertEqual(_read_mp3_track_num(path), 5)

    def test_read_mp3_track_num_with_slash(self):
        class FakeTag:
            def __init__(self, text):
                self.text = [text]

        class FakeAudio:
            def __init__(self, track_num):
                self.tags = {"TRCK": FakeTag(track_num)}

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.mp3"
            path.touch()
            with patch("mutagen.mp3.MP3", return_value=FakeAudio("3/12")):
                self.assertEqual(_read_mp3_track_num(path), 3)

    def test_read_mp3_track_num_no_tags(self):
        class FakeAudio:
            def __init__(self):
                self.tags = None

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.mp3"
            path.touch()
            with patch("mutagen.mp3.MP3", return_value=FakeAudio()):
                self.assertEqual(_read_mp3_track_num(path), 0)

    def test_read_mp3_track_num_mutagen_unavailable(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.mp3"
            path.touch()
            with patch("mutagen.mp3.MP3", side_effect=ImportError()):
                self.assertEqual(_read_mp3_track_num(path), 0)

    def test_read_mp3_track_num_invalid_text_falls_back_to_zero(self):
        class FakeTag:
            def __init__(self, text):
                self.text = [text]

        class FakeAudio:
            def __init__(self):
                self.tags = {"TRCK": FakeTag("not-a-number")}

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.mp3"
            path.touch()
            with patch("mutagen.mp3.MP3", return_value=FakeAudio()):
                self.assertEqual(_read_mp3_track_num(path), 0)


if __name__ == "__main__":
    unittest.main()
