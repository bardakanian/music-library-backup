import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import app_qt
import copy_core


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class WindowsNameTests(unittest.TestCase):
    def test_bad_characters_are_replaced(self):
        self.assertEqual(copy_core.windows_part('What? Is: "It" <a|b>*'), 'What_ Is_ _It_ _a_b__')

    def test_trailing_dots_and_spaces_are_removed(self):
        self.assertEqual(copy_core.windows_part("Vol. 1. "), "Vol. 1")
        self.assertEqual(copy_core.windows_part("..."), "_")

    def test_reserved_names_get_a_prefix(self):
        self.assertEqual(copy_core.windows_part("con.mp3"), "_con.mp3")
        self.assertEqual(copy_core.windows_part("COM1"), "_COM1")
        self.assertEqual(copy_core.windows_part("Console.mp3"), "Console.mp3")

    def test_names_are_normalized_to_nfc(self):
        decomposed = "Beyonce\u0301"
        self.assertEqual(copy_core.windows_part(decomposed), "Beyonc\u00e9")

    def test_windows_path_length_counts_from_the_drive(self):
        length = copy_core.windows_path_length(
            Path("/Volumes/STICK/Music"), Path("A/b.mp3")
        )
        self.assertEqual(length, len("E:\\Music\\A\\b.mp3"))


class FolderTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "Music" / "Media" / "Music"
        self.destination = self.root / "stick" / "Music"
        self.destination.mkdir(parents=True)

        write(self.source / "AC:DC" / "Back in Black" / "01 Hells Bells?.m4a", "hells")
        write(self.source / "Artist" / "Album" / "02 Song.m4a", "song")
        write(self.source / "Artist" / "Album" / ".DS_Store", "junk")
        write(self.source / "Artist" / "Album" / "._02 Song.m4a", "junk")

    def plan(self, windows_safe=True):
        return copy_core.build_plan(
            self.source, self.destination, windows_safe=windows_safe
        )


class WindowsSafePlanTests(FolderTestCase):
    def test_plan_renames_and_ignores_mac_junk(self):
        plan = self.plan()

        self.assertEqual(
            sorted(str(item.target) for item in plan.items),
            ["AC_DC/Back in Black/01 Hells Bells_.m4a", "Artist/Album/02 Song.m4a"],
        )
        self.assertEqual(plan.renamed, 1)
        self.assertEqual(plan.ignored, 2)

    def test_copy_writes_the_windows_name(self):
        for item in self.plan().items:
            self.assertTrue(copy_core.copy_item(item, self.destination))

        self.assertEqual(
            (self.destination / "AC_DC" / "Back in Black" / "01 Hells Bells_.m4a").read_text(),
            "hells",
        )
        self.assertEqual(self.plan().items, [])

    def test_copy_made_before_renaming_counts_as_there(self):
        write(self.destination / "AC:DC" / "Back in Black" / "01 Hells Bells?.m4a", "hells")

        plan = self.plan()

        self.assertEqual([str(i.target) for i in plan.items], ["Artist/Album/02 Song.m4a"])
        self.assertEqual(plan.existing, 1)

    def test_names_that_clash_on_windows_are_skipped(self):
        write(self.source / "Artist" / "Album" / "02 Song?.m4a", "other")
        write(self.source / "Artist" / "Album" / "02 Song_.m4a", "other 2")

        plan = self.plan()
        targets = [str(i.target) for i in plan.items]

        self.assertEqual(targets.count("Artist/Album/02 Song_.m4a"), 1)
        self.assertEqual(len(plan.skipped), 1)
        self.assertIn("same name on Windows", plan.skipped[0][1])

    def test_links_are_copied_as_files(self):
        os.symlink("Album/02 Song.m4a", self.source / "Artist" / "link.m4a")
        os.symlink("Album", self.source / "Artist" / "Folder Link")

        plan = self.plan()
        link = next(i for i in plan.items if i.relative.name == "link.m4a")

        self.assertFalse(link.is_symlink)
        self.assertTrue(copy_core.copy_item(link, self.destination))
        self.assertFalse((self.destination / "Artist" / "link.m4a").is_symlink())
        self.assertEqual((self.destination / "Artist" / "link.m4a").read_text(), "song")
        self.assertIn(
            (Path("Artist/Folder Link"), "link to a folder (exFAT can't store links)"),
            plan.skipped,
        )

    def test_long_paths_are_flagged(self):
        write(self.source / ("x" * 150) / ("y" * 150 + ".m4a"), "long")

        plan = self.plan()

        self.assertEqual(len(plan.warnings), 1)
        self.assertIn("characters", plan.warnings[0][1])

    def test_without_windows_safe_names_are_kept(self):
        plan = self.plan(windows_safe=False)

        self.assertIn(
            "AC:DC/Back in Black/01 Hells Bells?.m4a",
            [str(i.target) for i in plan.items],
        )
        self.assertEqual(plan.ignored, 2)


class LibraryBackupTests(FolderTestCase):
    def setUp(self):
        super().setUp()
        self.library = self.root / "Music" / "Music Library.musiclibrary"
        write(self.library / "Library.musicdb", "database")
        write(self.library / "Preferences" / "prefs.plist", "prefs")

    def test_library_is_found_above_the_source(self):
        self.assertEqual(copy_core.find_music_library(self.source), self.library)

    def test_snapshots_never_reuse_a_folder(self):
        first = copy_core.snapshot_folder(self.destination, "2026-11-11 10.00.00")
        second = copy_core.snapshot_folder(self.destination, "2026-11-11 10.00.00")

        self.assertNotEqual(first, second)
        self.assertEqual(first.parent.name, copy_core.BACKUPS_FOLDER)
        self.assertNotIn(":", copy_core.snapshot_stamp(datetime(2026, 11, 11, 10, 5)))

    def test_copy_tree_copies_the_bundle(self):
        folder = copy_core.snapshot_folder(self.destination, "stamp")
        copy_core.copy_tree(self.library, folder / self.library.name)

        self.assertEqual(
            (folder / self.library.name / "Preferences" / "prefs.plist").read_text(),
            "prefs",
        )
        self.assertFalse(any(
            p.name.startswith(copy_core.PARTIAL_PREFIX) for p in folder.iterdir()
        ))

    def test_cancelled_copy_tree_leaves_nothing(self):
        folder = copy_core.snapshot_folder(self.destination, "stamp")

        with self.assertRaises(copy_core.Cancelled):
            copy_core.copy_tree(self.library, folder / self.library.name, lambda: True)

        self.assertEqual(list(folder.iterdir()), [])

    def test_playlists_use_relative_windows_paths(self):
        for item in self.plan().items:
            copy_core.copy_item(item, self.destination)

        playlists = [
            ("Road: Trip", [
                self.source / "AC:DC" / "Back in Black" / "01 Hells Bells?.m4a",
                self.source / "Artist" / "Album" / "02 Song.m4a",
                Path("/elsewhere/track.m4a"),
                None,
            ]),
            ("Road? Trip", []),
        ]
        folder = copy_core.snapshot_folder(self.destination, "stamp") / "Playlists"

        written, tracks, left_out = copy_core.write_playlists(
            playlists, self.source, self.destination, folder, windows_safe=True
        )

        self.assertEqual((written, tracks, left_out), (2, 2, 2))
        lines = (folder / "Road_ Trip.m3u8").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, [
            "#EXTM3U",
            "..\\..\\..\\AC_DC\\Back in Black\\01 Hells Bells_.m4a",
            "..\\..\\..\\Artist\\Album\\02 Song.m4a",
        ])
        self.assertTrue((folder / "Road_ Trip (2).m3u8").is_file())

    def test_playlist_permission_error_is_explained(self):
        failed = (1, "", "Not authorized (-1743)")

        with mock.patch.object(copy_core, "run_cancellable", return_value=failed):
            with self.assertRaises(copy_core.PlaylistError) as caught:
                copy_core.read_playlists()

        self.assertIn("Automation", str(caught.exception))


class AppleDoubleTests(FolderTestCase):
    """macOS writes "._" files beside new items on exFAT; simulate that."""

    def test_copied_file_and_new_folders_lose_their_dot_underscore_files(self):
        real_mkdir = Path.mkdir

        def mkdir_with_sidecar(path, *args, **kwargs):
            real_mkdir(path, *args, **kwargs)
            path.with_name("._" + path.name).write_text("xattrs")

        real_rename = os.rename

        def rename_with_sidecar(old, new):
            real_rename(old, new)
            Path(new).with_name("._" + Path(new).name).write_text("xattrs")

        with mock.patch.object(Path, "mkdir", mkdir_with_sidecar), \
                mock.patch.object(copy_core.os, "rename", rename_with_sidecar):
            for item in self.plan().items:
                copy_core.copy_item(item, self.destination)

        leftovers = [p for p in self.destination.rglob("._*")]
        self.assertEqual(leftovers, [])

    def test_existing_dot_underscore_files_of_other_items_are_kept(self):
        write(self.destination / "._Someone Elses.m4a", "keep")

        for item in self.plan().items:
            copy_core.copy_item(item, self.destination)

        self.assertTrue((self.destination / "._Someone Elses.m4a").exists())

    def test_copy_tree_removes_dot_underscore_files(self):
        library = self.root / "lib.musiclibrary"
        write(library / "a.db", "a")

        real_copyfile = shutil.copyfile

        def copyfile_with_sidecar(source, target):
            real_copyfile(source, target)
            Path(target).with_name("._" + Path(target).name).write_text("x")

        folder = copy_core.snapshot_folder(self.destination, "stamp")

        with mock.patch.object(copy_core.shutil, "copyfile", copyfile_with_sidecar):
            copy_core.copy_tree(library, folder / library.name)

        self.assertEqual(list((folder / library.name).rglob("._*")), [])
        self.assertTrue((folder / library.name / "a.db").is_file())


class CancellableRunTests(unittest.TestCase):
    def test_cancel_kills_the_command(self):
        with self.assertRaises(copy_core.Cancelled):
            copy_core.run_cancellable(["sleep", "30"], lambda: True)

    def test_output_is_returned(self):
        self.assertEqual(
            copy_core.run_cancellable(["echo", "hi"]),
            (0, "hi\n", ""),
        )


class WorkerLibraryTests(LibraryBackupTests):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def run_worker(self, preview=False):
        worker = app_qt.CopyWorker(
            self.source,
            self.destination,
            preview,
            windows_safe=True,
            library=self.library,
            export_playlists=True,
        )
        results = []
        worker.finished.connect(results.append)
        playlists = [("Mix", [self.source / "Artist" / "Album" / "02 Song.m4a"])]

        with mock.patch.object(copy_core, "read_playlists", return_value=playlists), \
                mock.patch.object(copy_core, "music_is_running", return_value=False):
            worker.run()

        return results[0]

    def test_backup_saves_files_database_and_playlists(self):
        stats = self.run_worker()

        self.assertEqual(stats["phase"], "done")
        self.assertEqual(stats["copied"], 2)
        self.assertEqual(stats["failed"], 0)

        snapshot = Path(stats["snapshot"])
        self.assertTrue((snapshot / self.library.name / "Library.musicdb").is_file())
        self.assertTrue((snapshot / "Playlists" / "Mix.m3u8").is_file())

        # A second run adds a new snapshot and keeps the first.
        second = self.run_worker()
        self.assertEqual(second["copied"], 0)
        self.assertNotEqual(second["snapshot"], stats["snapshot"])
        self.assertTrue(snapshot.is_dir())

    def test_failed_playlist_export_leaves_no_empty_folder(self):
        worker = app_qt.CopyWorker(
            self.source, self.destination, False,
            windows_safe=True, library=None, export_playlists=True,
        )
        results = []
        worker.finished.connect(results.append)
        error = copy_core.PlaylistError("Not allowed")

        with mock.patch.object(copy_core, "read_playlists", side_effect=error):
            worker.run()

        stats = results[0]
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["snapshot"], "")
        self.assertEqual(list((self.destination / copy_core.BACKUPS_FOLDER).iterdir()), [])

    def test_preview_saves_no_snapshot(self):
        stats = self.run_worker(preview=True)

        self.assertEqual(stats["snapshot"], "")
        self.assertFalse((self.destination / copy_core.BACKUPS_FOLDER).exists())


class SettingsMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_old_settings_are_read_when_new_ones_are_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            old = Path(temp) / "old" / "settings.json"
            write(old, json.dumps({"source": temp, "destination": temp}))

            with mock.patch.object(app_qt, "CONFIG_FILE", Path(temp) / "new" / "settings.json"), \
                    mock.patch.object(app_qt, "OLD_CONFIG_FILE", old):
                window = app_qt.MainWindow()

                try:
                    self.assertEqual(window.source_field.text(), temp)
                finally:
                    window.deleteLater()


if __name__ == "__main__":
    unittest.main()


class FolderMismatchTests(unittest.TestCase):
    """Backups one folder level apart would copy the whole library again."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.library = root / "Music" / "Media.localized" / "Music"
        self.stick = root / "stick"

        for artist in ("Alpha", "Beta", "Gamma", "Delta"):
            write(self.library / artist / "Album" / "01 Song.m4a", artist)
            write(self.stick / "Music" / artist / "Album" / "01 Song.m4a", artist)

    def test_destination_one_level_too_high_is_spotted(self):
        match = copy_core.better_folder_match(self.library, self.stick)

        self.assertEqual(match["role"], "destination")
        self.assertEqual(match["path"], str(self.stick / "Music"))

    def test_music_folder_one_level_too_high_is_spotted(self):
        match = copy_core.better_folder_match(
            self.library.parent.parent, self.stick / "Music"
        )

        self.assertEqual(match["role"], "source")
        self.assertEqual(match["path"], str(self.library))

    def test_matching_folders_and_empty_destination_are_fine(self):
        self.assertIsNone(copy_core.better_folder_match(self.library, self.stick / "Music"))

        empty = self.stick.parent / "empty"
        empty.mkdir()
        self.assertIsNone(copy_core.better_folder_match(self.library, empty))

    def run_worker(self, preview, check_folders=True):
        worker = app_qt.CopyWorker(
            self.library, self.stick, preview,
            windows_safe=True, check_folders=check_folders,
        )
        results = []
        worker.finished.connect(results.append)
        worker.run()
        return results[0]

    def test_backup_stops_before_copying_a_second_time(self):
        stats = self.run_worker(preview=False)

        self.assertEqual(stats["phase"], "mismatch")
        self.assertEqual(stats["copied"], 0)
        self.assertFalse((self.stick / "Alpha").exists())

    def test_preview_warns_and_back_up_anyway_copies(self):
        self.assertIsNotNone(self.run_worker(preview=True)["mismatch"])

        stats = self.run_worker(preview=False, check_folders=False)

        self.assertEqual(stats["phase"], "done")
        self.assertEqual(stats["copied"], 4)
