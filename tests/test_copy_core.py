import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import app_qt
import copy_core


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def partial_files(folder):
    return [
        path for path in Path(folder).rglob("*")
        if path.name.startswith(copy_core.PARTIAL_PREFIX)
    ]


class FolderTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)

        self.source = root / "source"
        self.destination = root / "destination"

        write(self.source / "album" / "a.mp3", "new a")
        write(self.source / "album" / "sub" / "b.mp3", "b")
        write(self.source / "album" / "c.mp3", "longer c")
        os.symlink("album/a.mp3", self.source / "link.mp3")

        write(self.destination / "album" / "a.mp3", "old a")
        write(self.destination / "album" / "c.mp3", "c")


class CopyCoreTests(FolderTestCase):
    def plan(self):
        return copy_core.build_plan(self.source, self.destination)

    def test_plan_finds_only_missing_items(self):
        plan = self.plan()

        self.assertEqual(
            sorted(str(item.relative) for item in plan.items),
            ["album/sub/b.mp3", "link.mp3"],
        )
        self.assertEqual(plan.existing, 1)
        self.assertEqual(
            plan.skipped,
            [(Path("album/c.mp3"), "a different version is already there")],
        )

    def test_copy_adds_missing_and_never_overwrites(self):
        for item in self.plan().items:
            self.assertTrue(copy_core.copy_item(item, self.destination))

        self.assertEqual((self.destination / "album" / "sub" / "b.mp3").read_text(), "b")
        self.assertEqual(os.readlink(self.destination / "link.mp3"), "album/a.mp3")
        self.assertEqual((self.destination / "album" / "a.mp3").read_text(), "old a")
        self.assertEqual((self.destination / "album" / "c.mp3").read_text(), "c")
        self.assertEqual(partial_files(self.destination), [])

    def test_failed_copy_leaves_no_partial_file(self):
        item = next(i for i in self.plan().items if not i.is_symlink)

        def fail(_count):
            raise OSError(errno.EIO, "Input/output error")

        with self.assertRaises(OSError):
            copy_core.copy_item(item, self.destination, on_bytes=fail)

        self.assertFalse((self.destination / item.relative).exists())
        self.assertEqual(partial_files(self.destination), [])
        self.assertIn(item.relative, [i.relative for i in self.plan().items])

    def test_cancel_inside_a_file_removes_partial_copy(self):
        item = next(i for i in self.plan().items if not i.is_symlink)
        write(item.source, "x" * 100)
        calls = []

        def should_cancel():
            calls.append(1)
            return len(calls) > 2

        with mock.patch.object(copy_core, "CHUNK_SIZE", 10):
            with self.assertRaises(copy_core.Cancelled):
                copy_core.copy_item(item, self.destination, should_cancel=should_cancel)

        self.assertFalse((self.destination / item.relative).exists())
        self.assertEqual(partial_files(self.destination), [])

    def test_missing_destination_root_is_not_recreated(self):
        plan = self.plan()
        gone = self.destination.parent / "unplugged"

        with self.assertRaises(copy_core.DestinationGone):
            copy_core.copy_item(plan.items[0], gone)

        self.assertFalse(gone.exists())

    @unittest.skipIf(os.geteuid() == 0, "root can read any folder")
    def test_unreadable_folder_is_reported(self):
        locked = self.source / "locked"
        write(locked / "d.mp3", "d")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)

        plan = self.plan()

        self.assertTrue(plan.errors)
        self.assertIn("locked", str(plan.errors[0][0]))

    def test_unplugged_drive_is_rejected(self):
        with self.assertRaises(copy_core.FolderError) as caught:
            copy_core.check_folders(
                str(self.source),
                "/Volumes/No Such Drive 7f3a/Music",
            )

        self.assertEqual(caught.exception.title, "Drive Not Connected")

    def test_destination_inside_source_is_rejected_without_creating_it(self):
        inside = self.source / "backup"

        with self.assertRaises(copy_core.FolderError):
            copy_core.check_folders(str(self.source), str(inside))

        self.assertFalse(inside.exists())

    def test_destination_with_missing_parent_is_rejected(self):
        with self.assertRaises(copy_core.FolderError):
            copy_core.check_folders(
                str(self.source),
                str(self.destination.parent / "nope" / "Music"),
            )


class WorkerTests(FolderTestCase):
    """Runs the Qt worker synchronously against the same folders."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def run_worker(self, preview):
        worker = app_qt.CopyWorker(self.source, self.destination, preview)
        results = []
        worker.finished.connect(results.append)
        worker.run()
        return results[0]

    def test_preview_changes_nothing(self):
        stats = self.run_worker(preview=True)

        self.assertEqual(stats["phase"], "done")
        self.assertEqual(stats["to_copy"], 2)
        self.assertFalse((self.destination / "album" / "sub").exists())

    def test_copy_reports_counts(self):
        stats = self.run_worker(preview=False)

        self.assertEqual(stats["phase"], "done")
        self.assertEqual(stats["copied"], 2)
        self.assertEqual(stats["existing"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(stats["done_bytes"], stats["total_bytes"])

    def test_not_enough_space_copies_nothing(self):
        with mock.patch.object(copy_core, "free_space", return_value=0):
            stats = self.run_worker(preview=False)

        self.assertEqual(stats["phase"], "stopped")
        self.assertEqual(stats["copied"], 0)
        self.assertFalse((self.destination / "album" / "sub").exists())


if __name__ == "__main__":
    unittest.main()
