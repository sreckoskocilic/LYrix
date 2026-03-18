import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lyrix.catalog import Catalog, _artist_matches, _detect_album


class DetectAlbumTests(unittest.TestCase):
    def test_detect_album_reaches_seventy_percent(self):
        mp3s = [Path(f"track_{i}.mp3") for i in range(5)]

        def fake_tags(path):
            idx = int(path.stem.split("_")[1])
            if idx < 4:
                return ("Artist", f"Title {idx}", "Album A")
            return ("Artist", f"Title {idx}", "Album B")

        with patch("lyrix.catalog._read_mp3_tags", side_effect=fake_tags):
            artist, album = _detect_album(mp3s) or (None, None)
            self.assertEqual(artist, "Artist")
            self.assertEqual(album, "Album A")

    def test_detect_album_below_threshold_returns_none(self):
        mp3s = [Path(f"track_{i}.mp3") for i in range(3)]

        def fake_tags(path):
            idx = int(path.stem.split("_")[1])
            if idx == 0:
                return ("Artist", "Title 0", "Album A")
            return ("Artist", f"Title {idx}", "Album B")

        with patch("lyrix.catalog._read_mp3_tags", side_effect=fake_tags):
            self.assertIsNone(_detect_album(mp3s))


class ArtistMatchesTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(_artist_matches("Nile", "Nile"))
        self.assertTrue(_artist_matches("Nile", "nile"))

    def test_no_match_empty(self):
        self.assertFalse(_artist_matches("", "Nile"))
        self.assertFalse(_artist_matches("Nile", ""))
        self.assertFalse(_artist_matches("", ""))

    def test_close_match(self):
        self.assertTrue(_artist_matches("Nile", "Nile "))
        self.assertTrue(_artist_matches("Nile", "NILE"))

    def test_different_artists(self):
        self.assertFalse(_artist_matches("Nile", "Other Artist"))
        self.assertFalse(_artist_matches("Nile", "Nile Rodgers"))

    def test_threshold_80_percent(self):
        self.assertFalse(_artist_matches("Nile", "Nile Rodgers"))


class CatalogTests(unittest.TestCase):
    def test_add_get_remove(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "Song", "Album", "2020", "lyrics", track=1)
            self.assertEqual(len(cat), 1)
            entry = cat.get("A", "Song")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["track"], 1)
            cat.remove("A", "Song")
            self.assertEqual(len(cat), 0)

    def test_remove_entries_and_year_update(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "One", "Album", "", "lyrics")
            cat.add("A", "Two", "Album", "", "lyrics")
            cat.add("B", "Other", "Other", "", "lyrics")
            updated = cat.set_album_year("A", "Album", "1999")
            self.assertEqual(updated, 2)
            entry = cat.get("A", "One")
            self.assertEqual(entry["year"], "1999")
            removed = cat.remove_entries([("A", "One"), ("A", "Two")])
            self.assertEqual(removed, 2)
            self.assertEqual(len(cat), 1)

    def test_remove_artist(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("Artist", "One", "Album", "", "lyrics")
            cat.add("Artist", "Two", "Album", "", "lyrics")
            removed = cat.remove_artist("Artist")
            self.assertEqual(removed, 2)
            self.assertEqual(len(cat), 0)

    def test_thread_safety_under_parallel_adds(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)

            def add_batch(offset):
                for i in range(50):
                    idx = offset + i
                    cat.add("Artist", f"Song {idx}", "Album", "", f"lyrics {idx}")

            threads = [
                threading.Thread(target=add_batch, args=(50 * t,)) for t in range(10)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(cat), 500)

    def test_load_handles_bad_json(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat_path.write_text("{bad json")
            cat = Catalog(cat_path)
            self.assertEqual(len(cat), 0)

    def test_load_reads_existing_entries(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat_path.write_text(
                json.dumps(
                    {"entries": {"a\tb": {"artist": "A", "title": "B", "lyrics": ""}}}
                )
            )
            cat = Catalog(cat_path)
            self.assertEqual(len(cat), 1)

    def test_add_many_empty_list_is_noop(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add_many([])
            self.assertEqual(len(cat), 0)

    def test_save_cleans_up_on_failure(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "Song", "Album", "", "lyrics")

            with patch.object(Path, "write_text", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    cat._save()

    def test_load_corrupt_copy_oserror_is_swallowed(self):
        """OSError when writing the .corrupt backup must not propagate."""
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat_path.write_text("{bad json")
            with patch.object(Path, "write_bytes", side_effect=OSError("disk full")):
                cat = Catalog(cat_path)
            self.assertEqual(len(cat), 0)

    def test_reload_picks_up_external_changes(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "Song", "Album", "2020", "lyrics")

            # Simulate another process writing a new entry directly to the file
            data = json.loads(cat_path.read_text())
            data["entries"][Catalog._key("B", "Other")] = {
                "artist": "B",
                "title": "Other",
                "album": "",
                "year": "",
                "track": 0,
                "lyrics": "x",
                "added": "2024-01-01T00:00:00",
            }
            cat_path.write_text(json.dumps(data))

            self.assertEqual(len(cat), 1)  # stale before reload
            cat.reload()
            self.assertEqual(len(cat), 2)  # picks up new entry

    def test_reload_keeps_data_on_parse_error(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "Song", "Album", "", "lyrics")

            cat_path.write_text("{bad json")
            cat.reload()
            self.assertEqual(len(cat), 1)  # in-memory data preserved

    def test_reload_noop_when_file_missing(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "Song", "Album", "", "lyrics")

            cat_path.unlink()
            cat.reload()
            self.assertEqual(len(cat), 1)  # in-memory data preserved

    def test_add_preserves_added_timestamp_on_update(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "Song", "Album", "2020", "original lyrics")
            original_added = cat.get("A", "Song")["added"]

            cat.add("A", "Song", "Album", "2020", "updated lyrics")
            self.assertEqual(cat.get("A", "Song")["added"], original_added)
            self.assertEqual(cat.get("A", "Song")["lyrics"], "updated lyrics")

    def test_add_many_preserves_added_timestamp_on_update(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "Song", "Album", "2020", "original lyrics")
            original_added = cat.get("A", "Song")["added"]

            cat.add_many(
                [
                    {
                        "artist": "A",
                        "title": "Song",
                        "album": "Album",
                        "year": "2020",
                        "lyrics": "updated lyrics",
                        "track": 0,
                    }
                ]
            )
            self.assertEqual(cat.get("A", "Song")["added"], original_added)
            self.assertEqual(cat.get("A", "Song")["lyrics"], "updated lyrics")


if __name__ == "__main__":
    unittest.main()
