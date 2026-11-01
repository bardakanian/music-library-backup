import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import app_qt


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class VersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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


if __name__ == "__main__":
    unittest.main()
