import json
import os
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from lyrix.catalog import Catalog, _release_year


class ReleaseYearTests(unittest.TestCase):
    def test_rdc_dict_with_year(self):
        album = {"release_date_components": {"year": 1994}}
        self.assertEqual(_release_year(album), "1994")

    def test_rdc_dict_with_no_year(self):
        album = {"release_date_components": {}, "release_date_for_display": "2001"}
        self.assertEqual(_release_year(album), "2001")

    def test_rdc_object_with_year(self):
        rdc = SimpleNamespace(year=2001)
        album = SimpleNamespace(
            release_date_components=rdc, release_date_for_display=""
        )
        self.assertEqual(_release_year(album), "2001")

    def test_rdc_object_with_no_year(self):
        rdc = SimpleNamespace(year=None)
        album = SimpleNamespace(
            release_date_components=rdc, release_date_for_display="January 01, 2005"
        )
        self.assertEqual(_release_year(album), "2005")


class CatalogTests(unittest.TestCase):
    def test_add_get_remove(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "Song", "Album", "2020", "lyrics", track=1)
            self.assertEqual(len(cat), 1)
            entry = cat.get("A", "Song", "Album")
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
            entry = cat.get("A", "One", "Album")
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

    def test_remove_artist_ignores_unindexed_data(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("Artist", "One", "Album", "", "lyrics")
            # Inject a malformed key directly into _data (bypasses the index)
            cat._data["malformed"] = {
                "artist": "Artist",
                "title": "Bad",
                "album": "",
                "year": "",
                "track": 0,
                "lyrics": "",
                "added": "",
            }
            # Should not raise; only the indexed entry is removed — the unindexed
            # malformed key is invisible to the index-based remove_artist.
            removed = cat.remove_artist("Artist")
            self.assertEqual(removed, 1)
            self.assertIn("malformed", cat._data)

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
                    {
                        "entries": {
                            "a\tb\t": {
                                "artist": "A",
                                "title": "B",
                                "album": "",
                                "lyrics": "x",
                                "year": "",
                                "track": 0,
                                "added": "2024-01-01T00:00:00",
                            }
                        }
                    }
                )
            )
            cat = Catalog(cat_path)
            self.assertEqual(len(cat), 1)
            self.assertIsNotNone(cat.get("A", "B", ""))

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

    def test_reload_skips_when_mtime_unchanged(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "Song", "Album", "2020", "lyrics")

            # Tamper with the file without changing mtime
            original_mtime = cat_path.stat().st_mtime_ns
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
            # Restore original mtime so reload skips
            os.utime(cat_path, ns=(original_mtime, original_mtime))

            cat.reload()
            self.assertEqual(len(cat), 1)  # reload was skipped

    def test_reload_skips_when_in_memory_newer_than_disk(self):
        """reload() does not overwrite in-memory state when _file_mtime is ahead of disk mtime."""
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "Song", "Album", "2020", "lyrics")
            # Bump _file_mtime past what is on disk (simulates a concurrent _save())
            cat._file_mtime = cat_path.stat().st_mtime_ns + 1_000_000_000
            cat.reload()
            self.assertEqual(len(cat), 1)  # stale disk version not applied

    def test_add_preserves_added_timestamp_on_update(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "Song", "Album", "2020", "original lyrics")
            original_added = cat.get("A", "Song", "Album")["added"]

            cat.add("A", "Song", "Album", "2020", "updated lyrics")
            self.assertEqual(cat.get("A", "Song", "Album")["added"], original_added)
            self.assertEqual(cat.get("A", "Song", "Album")["lyrics"], "updated lyrics")

    def test_add_many_preserves_added_timestamp_on_update(self):
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat = Catalog(cat_path)
            cat.add("A", "Song", "Album", "2020", "original lyrics")
            original_added = cat.get("A", "Song", "Album")["added"]

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
            self.assertEqual(cat.get("A", "Song", "Album")["added"], original_added)
            self.assertEqual(cat.get("A", "Song", "Album")["lyrics"], "updated lyrics")

    def test_same_song_different_albums_are_separate_entries(self):
        """Same (artist, title) under two albums must be stored as independent entries."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add(
                "Suffocation", "Infecting the Crypts", "Human Waste", "1991", "lyrics A"
            )
            cat.add(
                "Suffocation",
                "Infecting the Crypts",
                "Effigy of the Forgotten",
                "1991",
                "lyrics B",
            )
            self.assertEqual(len(cat), 2)
            a = cat.get("Suffocation", "Infecting the Crypts", "Human Waste")
            b = cat.get(
                "Suffocation", "Infecting the Crypts", "Effigy of the Forgotten"
            )
            self.assertEqual(a["lyrics"], "lyrics A")
            self.assertEqual(b["lyrics"], "lyrics B")

    def test_get_exact_album_only(self):
        """get must match exactly by (artist, title, album) — no cross-album fallback."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album X", "", "lyrics")
            self.assertIsNotNone(cat.get("Artist", "Song", "Album X"))
            self.assertIsNone(cat.get("Artist", "Song", "Album Y"))
            self.assertIsNone(cat.get("Artist", "Song"))

    def test_remove_album_entries_does_not_touch_other_albums(self):
        """remove_album_entries must only delete the exact (artist, title, album) triple."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add(
                "Suffocation", "Infecting the Crypts", "Human Waste", "1991", "lyrics A"
            )
            cat.add(
                "Suffocation",
                "Infecting the Crypts",
                "Effigy of the Forgotten",
                "1991",
                "lyrics B",
            )
            removed = cat.remove_album_entries(
                [("Suffocation", "Infecting the Crypts", "Human Waste")]
            )
            self.assertEqual(removed, 1)
            self.assertEqual(len(cat), 1)
            # Effigy entry must still exist
            entry = cat.get(
                "Suffocation", "Infecting the Crypts", "Effigy of the Forgotten"
            )
            self.assertIsNotNone(entry)
            self.assertEqual(entry["lyrics"], "lyrics B")

    def test_remove_entries_deletes_all_album_variants(self):
        """remove_entries (artist, title) tuples deletes all album variants — existing behaviour."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album A", "", "lyrics A")
            cat.add("Artist", "Song", "Album B", "", "lyrics B")
            removed = cat.remove_entries([("Artist", "Song")])
            self.assertEqual(removed, 2)
            self.assertEqual(len(cat), 0)

    def test_migration_two_tab_keys(self):
        """Old 2-tab keys (artist\\ttitle) must be migrated to 3-tab keys on load."""
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            cat_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": {
                            "artist\tsong": {
                                "artist": "Artist",
                                "title": "Song",
                                "album": "My Album",
                                "year": "2000",
                                "track": 0,
                                "lyrics": "old lyrics",
                                "added": "2024-01-01T00:00:00",
                            }
                        },
                    }
                )
            )
            cat = Catalog(cat_path)
            # Entry should be accessible and key migrated to 3-tab form
            entry = cat.get("Artist", "Song", "My Album")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["lyrics"], "old lyrics")

    def test_load_already_migrated_keys(self):
        """Keys already in 3-tab format must be preserved as-is (no double migration)."""
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            key = Catalog._key("Artist", "Song", "Album")
            cat_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": {
                            key: {
                                "artist": "Artist",
                                "title": "Song",
                                "album": "Album",
                                "year": "2000",
                                "track": 0,
                                "lyrics": "lyrics",
                                "added": "2024-01-01T00:00:00",
                            }
                        },
                    }
                )
            )
            cat = Catalog(cat_path)
            self.assertEqual(len(cat), 1)
            entry = cat.get("Artist", "Song", "Album")
            self.assertIsNotNone(entry)

    def test_remove_with_album_specific(self):
        """remove(artist, title, album) must delete only the exact album variant."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album A", "", "lyrics A")
            cat.add("Artist", "Song", "Album B", "", "lyrics B")
            cat.remove("Artist", "Song", "Album A")
            self.assertEqual(len(cat), 1)
            # Album B entry must survive
            remaining = cat.get("Artist", "Song", "Album B")
            self.assertIsNotNone(remaining)
            self.assertEqual(remaining["album"], "Album B")

    def test_find_returns_entry_across_any_album(self):
        """find() locates a song regardless of which album it was stored under."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album X", "2020", "some lyrics")
            result = cat.find("Artist", "Song")
            self.assertIsNotNone(result)
            self.assertEqual(result["album"], "Album X")

    def test_find_prefers_entry_with_lyrics(self):
        """find() returns the entry that has lyrics when multiple albums exist."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album A", "", "")  # no lyrics
            cat.add("Artist", "Song", "Album B", "", "real lyrics")
            result = cat.find("Artist", "Song")
            self.assertIsNotNone(result)
            self.assertEqual(result["album"], "Album B")

    def test_find_returns_none_when_not_found(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            self.assertIsNone(cat.find("Ghost", "Nobody"))

    def test_find_returns_first_entry_when_none_have_lyrics(self):
        """find() returns the first match when no entry carries lyrics."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album A", "", "")
            result = cat.find("Artist", "Song")
            self.assertIsNotNone(result)
            self.assertEqual(result["album"], "Album A")

    def test_all_entries_returns_all(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("A", "One", "Album", "", "lyrics1")
            cat.add("B", "Two", "Album", "", "lyrics2")
            entries = cat.all_entries()
            self.assertEqual(len(entries), 2)
            self.assertEqual({e["title"] for e in entries}, {"One", "Two"})

    def test_remove_with_album_sole_entry_cleans_index(self):
        """Removing the only album variant cleans the title index entry."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album A", "", "lyrics")
            cat.remove("Artist", "Song", "Album A")
            self.assertEqual(len(cat), 0)
            self.assertIsNone(cat.find("Artist", "Song"))

    def test_remove_album_entries_sole_variant_cleans_index(self):
        """Removing the last album variant via remove_album_entries cleans the title index."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album A", "", "lyrics")
            removed = cat.remove_album_entries([("Artist", "Song", "Album A")])
            self.assertEqual(removed, 1)
            self.assertEqual(len(cat), 0)
            self.assertIsNone(cat.find("Artist", "Song"))

    def test_index_remove_key_missing_lookup_is_noop(self):
        """_index_remove_key does nothing when the lookup key is absent."""
        index: dict = {}
        Catalog._index_remove_key(index, ("x", "y"), "some_key")
        self.assertEqual(index, {})

    def test_index_remove_key_absent_entry_is_noop(self):
        """_index_remove_key does nothing when the entry key is not in the list."""
        index: dict = {("x", "y"): ["other_key"]}
        Catalog._index_remove_key(index, ("x", "y"), "missing_key")
        self.assertEqual(index, {("x", "y"): ["other_key"]})

    def test_find_returns_none_when_index_stale(self):
        """find() returns None when index keys are not present in _data (defensive guard)."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat._title_index[("ghost", "track")] = ["ghost\ttrack\t"]
            self.assertIsNone(cat.find("ghost", "track"))

    def test_find_is_case_insensitive(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("The Beatles", "Hey Jude", "Past Masters", "", "na na na")
            result = cat.find("the beatles", "hey jude")
            self.assertIsNotNone(result)
            self.assertEqual(result["title"], "Hey Jude")

    def test_find_album_returns_all_entries(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "One", "Album", "2020", "lyrics1")
            cat.add("Artist", "Two", "Album", "2020", "lyrics2")
            cat.add("Artist", "Three", "Other", "2020", "lyrics3")
            results = cat.find_album("Artist", "Album")
            self.assertEqual(len(results), 2)
            self.assertEqual({e["title"] for e in results}, {"One", "Two"})

    def test_find_album_case_insensitive(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album", "", "lyrics")
            results = cat.find_album("artist", "album")
            self.assertEqual(len(results), 1)

    def test_find_album_empty_when_not_found(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album", "", "lyrics")
            results = cat.find_album("Other", "Album")
            self.assertEqual(len(results), 0)

    def test_find_duplicates_returns_groups(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album A", "", "lyrics A")
            cat.add("Artist", "Song", "Album B", "", "lyrics B")
            cat.add("Artist", "Other", "Album A", "", "lyrics")
            duplicates = cat.find_duplicates()
            self.assertEqual(len(duplicates), 1)
            self.assertEqual(len(duplicates[0]), 2)

    def test_find_duplicates_ignores_entries_without_lyrics(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album A", "", "")
            cat.add("Artist", "Song", "Album B", "", "lyrics")
            duplicates = cat.find_duplicates()
            self.assertEqual(len(duplicates), 0)

    def test_find_duplicates_empty_when_no_duplicates(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "One", "Album", "", "lyrics")
            cat.add("Artist", "Two", "Album", "", "lyrics")
            duplicates = cat.find_duplicates()
            self.assertEqual(len(duplicates), 0)

    def test_stats(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist A", "Song 1", "Album 1", "2020", "lyrics")
            cat.add("Artist A", "Song 2", "Album 1", "2020", "")  # no lyrics
            cat.add("Artist A", "Song 1", "Album 2", "2021", "lyrics")  # duplicate
            cat.add("Artist B", "Song 3", "Album 3", "2022", "lyrics")
            s = cat.stats()
            self.assertEqual(s["artists"], 2)
            self.assertEqual(s["albums"], 3)
            self.assertEqual(s["songs"], 4)
            self.assertEqual(s["with_lyrics"], 3)
            self.assertEqual(s["without_lyrics"], 1)
            self.assertEqual(s["duplicates"], 1)

    def test_export_csv(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist", "Song", "Album", "2020", "lyrics", track=1)
            csv_path = Path(tmp) / "export.csv"
            count = cat.export_csv(csv_path)
            self.assertEqual(count, 1)
            content = csv_path.read_text(encoding="utf-8")
            self.assertIn("artist", content)
            self.assertIn("Artist", content)
            self.assertIn("Song", content)

    def test_export_csv_empty_catalog(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            csv_path = Path(tmp) / "export.csv"
            count = cat.export_csv(csv_path)
            self.assertEqual(count, 0)
            content = csv_path.read_text(encoding="utf-8")
            self.assertIn("artist", content)  # header still present

    def test_find_by_artist(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist A", "Song 1", "Album 1", "2020", "lyrics1")
            cat.add("Artist A", "Song 2", "Album 1", "2020", "lyrics2")
            cat.add("Artist B", "Song 3", "Album 2", "2021", "lyrics3")
            results = cat.find_by_artist("Artist A")
            self.assertEqual(len(results), 2)
            titles = {e["title"] for e in results}
            self.assertEqual(titles, {"Song 1", "Song 2"})

    def test_find_by_artist_case_insensitive(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist A", "Song 1", "Album 1", "2020", "lyrics")
            results = cat.find_by_artist("artist a")
            self.assertEqual(len(results), 1)

    def test_find_by_artist_not_found(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist A", "Song 1", "Album", "2020", "lyrics")
            self.assertEqual(cat.find_by_artist("Unknown"), [])

    def test_all_artist_album_pairs(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add("Artist A", "Song 1", "Album 1", "2020", "lyrics1")
            cat.add("Artist A", "Song 2", "Album 2", "2021", "lyrics2")
            cat.add("Artist B", "Song 3", "Album 1", "2020", "lyrics3")
            pairs = cat.all_artist_album_pairs()
            self.assertIn(("artist a", "album 1"), pairs)
            self.assertIn(("artist a", "album 2"), pairs)
            self.assertIn(("artist b", "album 1"), pairs)
            self.assertEqual(len(pairs), 3)

    def test_all_artist_album_pairs_empty(self):
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            self.assertEqual(cat.all_artist_album_pairs(), set())

    def test_add_many_new_entries_updates_title_index(self):
        """add_many with brand-new entries must update _title_index (is_new=True path)."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            cat.add_many(
                [
                    {
                        "artist": "A",
                        "title": "One",
                        "album": "Album",
                        "year": "",
                        "lyrics": "x",
                        "track": 1,
                    },
                    {
                        "artist": "A",
                        "title": "Two",
                        "album": "Album",
                        "year": "",
                        "lyrics": "y",
                        "track": 2,
                    },
                ]
            )
            self.assertEqual(len(cat), 2)
            self.assertIsNotNone(cat.find("A", "One"))
            self.assertIsNotNone(cat.find("A", "Two"))

    def test_load_stat_oserror_is_swallowed(self):
        """OSError on stat() after loading the catalog file must not propagate."""
        with TemporaryDirectory() as tmp:
            cat_path = Path(tmp) / "catalog.json"
            key = Catalog._key("A", "Song", "Album")
            cat_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": {
                            key: {
                                "artist": "A",
                                "title": "Song",
                                "album": "Album",
                                "year": "",
                                "track": 0,
                                "lyrics": "x",
                                "added": "2024-01-01T00:00:00",
                            },
                        },
                    }
                )
            )
            with patch.object(Path, "stat", side_effect=OSError("no stat")):
                cat = Catalog(cat_path)
            self.assertEqual(len(cat), 1)

    def test_save_stat_oserror_is_swallowed(self):
        """OSError on stat() after writing the catalog file must not propagate."""
        with TemporaryDirectory() as tmp:
            cat = Catalog(Path(tmp) / "catalog.json")
            with patch.object(Path, "stat", side_effect=OSError("no stat")):
                cat._save()  # must not raise


if __name__ == "__main__":
    unittest.main()
