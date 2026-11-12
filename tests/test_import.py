import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import app_qt
import copy_core


def write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class FindBackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_root_with_library_backups_is_the_folder(self):
        (self.root / "Library Backups").mkdir()
        self.assertEqual(copy_core.find_backup_folder(self.root), self.root)

    def test_child_with_library_backups_wins_over_music_folder(self):
        write(self.root / "Music" / "a.m4a")
        write(self.root / "Tunes" / "Library Backups" / "2026" / "x.txt")
        self.assertEqual(
            copy_core.find_backup_folder(self.root), self.root / "Tunes"
        )

    def test_plain_music_folder_is_only_a_loose_match(self):
        write(self.root / "music" / "a.m4a")
        self.assertIsNone(copy_core.find_backup_folder(self.root))
        self.assertEqual(
            copy_core.find_backup_folder(self.root, loose=True),
            self.root / "music",
        )

    def test_nothing_found(self):
        write(self.root / "Photos" / "a.jpg")
        self.assertIsNone(copy_core.find_backup_folder(self.root))

    def test_drives_without_a_backup_are_left_out(self):
        stick = self.root / "stick"
        own_music = self.root / "own"
        other = self.root / "other"
        write(stick / "Music" / "Library Backups" / "d" / "x.txt")
        write(own_music / "Music" / "a.m4a")
        write(other / "Photos" / "a.jpg")
        self.assertEqual(
            copy_core.find_backup_drives([stick, own_music, other]),
            [(stick, stick / "Music")],
        )
        self.assertEqual(
            len(copy_core.find_backup_drives([stick, own_music, other], loose=True)),
            2,
        )


class LatestPlaylistsTests(unittest.TestCase):
    def test_newest_backup_with_playlists_is_chosen(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write(root / "Library Backups" / "2026-11-01 10.00.00" / "Playlists" / "a.m3u8")
            write(root / "Library Backups" / "2026-11-05 10.00.00" / "Playlists" / "a.m3u8")
            (root / "Library Backups" / "2026-11-09 10.00.00").mkdir()
            self.assertEqual(
                copy_core.latest_playlists_folder(root),
                root / "Library Backups" / "2026-11-05 10.00.00" / "Playlists",
            )

    def test_no_backups_gives_none(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(copy_core.latest_playlists_folder(temp))


class ImportWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_import_copies_music_and_library_backups(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stick = root / "stick" / "Music"
            write(stick / "Artist" / "Album" / "01 Song.m4a", "song")
            write(stick / "Artist" / "Album" / "._01 Song.m4a", "junk")
            write(stick / "Library Backups" / "d1" / "Playlists" / "Mix.m3u8", "#EXTM3U")
            target = root / "pc" / "Library"
            target.parent.mkdir()

            worker = app_qt.CopyWorker(stick, target, check_folders=False)
            results = []
            worker.finished.connect(results.append)
            worker.run()

            self.assertEqual(results[-1]["copied"], 2)
            self.assertTrue((target / "Artist" / "Album" / "01 Song.m4a").is_file())
            self.assertFalse((target / "Artist" / "Album" / "._01 Song.m4a").exists())
            self.assertTrue(
                (target / "Library Backups" / "d1" / "Playlists" / "Mix.m3u8").is_file()
            )

            again = app_qt.CopyWorker(stick, target, check_folders=False)
            results.clear()
            again.finished.connect(results.append)
            again.run()
            self.assertEqual(results[-1]["to_copy"], 0)

    def test_mode_switch_keeps_each_modes_folders(self):
        # Never touch the real settings file: closing a window saves it.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        for name, file in (("CONFIG_FILE", "new.json"), ("OLD_CONFIG_FILE", "old.json")):
            patcher = unittest.mock.patch.object(app_qt, name, Path(temp.name) / file)
            patcher.start()
            self.addCleanup(patcher.stop)

        window = app_qt.MainWindow()
        self.addCleanup(window.close)
        window.folders = {
            app_qt.MODE_BACKUP: ["/music", "/stick"],
            app_qt.MODE_IMPORT: ["/stick2", "/pc"],
        }
        window.apply_mode(app_qt.MODE_BACKUP, capture=False)
        self.assertEqual(window.source_field.text(), "/music")

        window.apply_mode(app_qt.MODE_IMPORT)
        self.assertEqual(window.source_field.text(), "/stick2")
        self.assertEqual(window.destination_field.text(), "/pc")
        self.assertEqual(window.start_button.text(), "Import")
        self.assertFalse(window.windows_box.isVisibleTo(window))

        window.apply_mode(app_qt.MODE_BACKUP)
        self.assertEqual(window.source_field.text(), "/music")
        self.assertEqual(window.start_button.text(), "Back Up")



class MusicBeePlaylistTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        write(self.root / "Artist" / "Album" / "01 Song.m4a")
        write(self.root / "Artist" / "Album" / "02 Song.m4a")
        self.folder = self.root / "Library Backups" / "d1" / "Playlists"
        self.target = self.root / "MusicBee" / "Playlists"
        self.state = self.root / "state.json"
        self.song1 = "..\\..\\..\\Artist\\Album\\01 Song.m4a"
        self.song2 = "..\\..\\..\\Artist\\Album\\02 Song.m4a"

    def playlist(self, *songs):
        write(self.folder / "Mix.m3u8", "\r\n".join(["#EXTM3U", *songs]) + "\r\n")

    def run_import(self):
        return copy_core.add_playlists_to_musicbee(
            self.folder, self.target, self.state
        )

    def test_new_playlist_gets_full_paths(self):
        self.playlist(self.song1, "..\\..\\..\\Gone\\x.m4a")
        counts = self.run_import()

        self.assertEqual((counts["written"], counts["tracks"], counts["left_out"]), (1, 1, 1))
        lines = (self.target / "Mix.m3u8").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, ["#EXTM3U", str(self.root / "Artist" / "Album" / "01 Song.m4a")])

    def test_untouched_playlist_is_updated_from_the_mac(self):
        self.playlist(self.song1)
        self.run_import()
        self.assertEqual(self.run_import()["unchanged"], 1)

        self.playlist(self.song1, self.song2)
        counts = self.run_import()

        self.assertEqual(counts["updated"], 1)
        text = (self.target / "Mix.m3u8").read_text(encoding="utf-8")
        self.assertIn("02 Song.m4a", text)

    def test_playlist_edited_in_musicbee_is_kept(self):
        self.playlist(self.song1)
        self.run_import()
        (self.target / "Mix.m3u8").write_text("mine", encoding="utf-8")

        self.playlist(self.song1, self.song2)
        counts = self.run_import()

        self.assertEqual(counts["kept_edited"], 1)
        self.assertEqual((self.target / "Mix.m3u8").read_text(encoding="utf-8"), "mine")

    def test_identical_playlist_is_adopted_when_memory_is_lost(self):
        self.playlist(self.song1)
        self.run_import()
        self.state.unlink()

        self.assertEqual(self.run_import()["unchanged"], 1)
        self.playlist(self.song1, self.song2)
        self.assertEqual(self.run_import()["updated"], 1)

    def test_memory_is_saved_even_if_a_later_playlist_fails(self):
        self.playlist(self.song1)
        write(self.folder / "Zed.m3u8", "#EXTM3U\r\n")
        real = copy_core.fingerprint
        calls = []

        def failing(data):
            calls.append(1)
            if len(calls) > 1:
                raise OSError("disk full")
            return real(data)

        with unittest.mock.patch.object(copy_core, "fingerprint", failing):
            with self.assertRaises(OSError):
                self.run_import()

        self.assertIn("mix.m3u8", copy_core.load_playlist_state(self.state)[
            str(self.target).casefold()
        ])

    def test_playlist_made_in_musicbee_is_never_touched(self):
        write(self.target / "Mix.m3u8", "mine")
        self.playlist(self.song1)
        counts = self.run_import()

        self.assertEqual(counts["kept_other"], 1)
        self.assertEqual((self.target / "Mix.m3u8").read_text(encoding="utf-8"), "mine")


class PartialNameTests(unittest.TestCase):
    def test_partial_name_is_not_a_music_file(self):
        name = copy_core.partial_path(Path("Artist") / "Song.m4a").name
        self.assertTrue(name.startswith(copy_core.PARTIAL_PREFIX))
        self.assertTrue(name.endswith(copy_core.PARTIAL_SUFFIX))

    def test_long_names_stay_within_the_limit(self):
        name = copy_core.partial_path(Path("a" * 250 + ".m4a")).name
        self.assertLessEqual(len(name.encode("utf-8")), copy_core.MAX_NAME_BYTES)
        self.assertTrue(name.endswith(copy_core.PARTIAL_SUFFIX))

    def test_junk_still_covers_partial_files(self):
        name = copy_core.partial_path(Path("Song.m4a")).name
        self.assertTrue(copy_core.is_junk(name))




if __name__ == "__main__":
    unittest.main()
