"""Compatibility check verdicts that need no running game."""
import os
import sys
import unittest

# pc/ on the path, so the tests run from the repo root or from pc/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from kiripanel import compat  # noqa: E402

GAME = {"path": "C:\\g", "exe": "game.exe", "engines": [["game.exe", "x86"]],
        "launchers": ["unins000.exe", "Config.exe", "start.exe"]}


class LauncherNeeded(unittest.TestCase):
    def test_engine_asking_for_launcher(self):
        c = compat.Check(GAME)
        self.assertTrue(c.wants_launcher("Launcher not found."))
        self.assertTrue(c.wants_launcher("ランチャーから起動してください"))
        self.assertFalse(c.wants_launcher("音声を再生しますか？"))

    def test_launcher_itself_is_not_judged(self):
        # the player chose a launcher: its own boxes are answered as usual
        c = compat.Check(GAME, exe="start.exe")
        self.assertFalse(c.wants_launcher("Launcher not found."))

    def test_hint_names_only_plausible_launchers(self):
        why = compat.Check(GAME).launcher_hint()
        self.assertIn("start.exe", why)
        self.assertNotIn("unins000", why)
        self.assertNotIn("Config", why)


if __name__ == "__main__":
    unittest.main()
