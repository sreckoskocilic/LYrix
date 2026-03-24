import unittest
from types import SimpleNamespace

from lyrix.catalog import (
    _release_year,
    _format_track,
    _song_header,
    _extract_name,
    get_resource_path,
)
from lyrix.base_app import _year_sort


class YearParsingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
