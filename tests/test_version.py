import os
import plistlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import app_qt
import make_launcher


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class VersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Never touch the real settings file.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = mock.patch.object(
            app_qt, "CONFIG_FILE", Path(temp.name) / "settings.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        old_patcher = mock.patch.object(
            app_qt, "OLD_CONFIG_FILE", Path(temp.name) / "old-settings.json"
        )
        old_patcher.start()
        self.addCleanup(old_patcher.stop)

    def test_version_is_semantic(self):
        self.assertRegex(app_qt.VERSION, r"^\d+\.\d+\.\d+$")

    def test_window_title_shows_version(self):
        window = app_qt.MainWindow()

        try:
            self.assertEqual(
                window.windowTitle(),
                app_qt.APP_NAME + " " + app_qt.VERSION,
            )
        finally:
            window.deleteLater()

    def test_changelog_has_entry_for_version(self):
        changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(
            encoding="utf-8"
        )
        heading = re.compile(
            r"^## " + re.escape(app_qt.VERSION) + r" — \d{4}-\d{2}-\d{2}$",
            re.MULTILINE,
        )

        self.assertRegex(changelog, heading)

    def test_bundle_carries_version(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = make_launcher.build_bundle(temp)

            with (bundle / "Contents" / "Info.plist").open("rb") as file:
                info = plistlib.load(file)

            self.assertEqual(info["CFBundleShortVersionString"], app_qt.VERSION)
            self.assertEqual(info["CFBundleVersion"], app_qt.VERSION)
            self.assertTrue((bundle / "Contents" / "Resources" / "AppIcon.icns").is_file())
            self.assertTrue(os.access(bundle / "Contents" / "MacOS" / "launcher", os.X_OK))


if __name__ == "__main__":
    unittest.main()
