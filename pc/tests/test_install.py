"""Installer and game library, on a fake game folder (a real PE file with
the KiriKiri marker appended, so detection sees an engine)."""
import os
import shutil
import sys
import tempfile
import unittest

# pc/ on the path, so the tests run from the repo root or from pc/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from kiripanel import games, install  # noqa: E402


def find_pe():
    """A real PE file from this machine to stand in for a game's exe. A
    Windows Server install (CI) has no notepad.exe in SysWOW64, hence the
    list; anything 32-bit is fine, the tests only read its headers."""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    for p in (os.path.join(root, "SysWOW64", "notepad.exe"),
              os.path.join(root, "System32", "notepad.exe"),
              os.path.join(root, "System32", "rundll32.exe"),
              os.path.join(root, "SysWOW64", "rundll32.exe"),
              sys.executable):
        if p and os.path.isfile(p):
            with open(p, "rb") as f:
                if f.read(2) == b"MZ":
                    return p
    raise unittest.SkipTest("no PE file on this machine to use as a game exe")


PE32 = find_pe()
QUIET = lambda m: None   # noqa: E731


class FakeGame:
    def __init__(self, own=()):
        self.dir = tempfile.mkdtemp(prefix="kp_test_")
        with open(PE32, "rb") as f:
            data = f.read()
        with open(os.path.join(self.dir, "game.exe"), "wb") as f:
            f.write(data + "TVP(KIRIKIRI)".encode("utf-16-le"))
        for n in own:     # DLLs the game ships itself
            with open(os.path.join(self.dir, n), "wb") as f:
                f.write(b"MZ the game's own " + n.encode())

    def path(self, *p):
        return os.path.join(self.dir, *p)

    def files(self):
        out = set()
        for dp, _, fns in os.walk(self.dir):
            for fn in fns:
                out.add(os.path.relpath(os.path.join(dp, fn), self.dir).replace("\\", "/"))
        return out

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.g = FakeGame()

    def tearDown(self):
        self.g.close()

    def test_detects_engine(self):
        self.assertEqual(install.find_engines(self.g.dir), [("game.exe", "x86")])
        d = games.describe(self.g.dir)
        self.assertEqual(d["exe"], "game.exe")
        self.assertEqual(d["arch"], "x86")

    def test_install_and_uninstall_leave_nothing(self):
        before = self.g.files()
        self.assertEqual(install.apply(self.g.dir, log=QUIET), 0)
        after = self.g.files()
        self.assertIn("version.dll", after)
        self.assertIn("kiripanel/hook.tjs", after)
        self.assertEqual(games.install_state(self.g.dir)["state"], "installed")
        self.assertEqual(install.apply(self.g.dir, uninstall=True, log=QUIET), 0)
        self.assertEqual(self.g.files(), before)
        self.assertEqual(games.install_state(self.g.dir)["state"], "none")

    def test_never_replaces_the_games_own_files(self):
        self.g.close()
        self.g = FakeGame(own=("version.dll",))
        self.assertEqual(install.proxy_name(self.g.dir), "mpr.dll")
        self.assertEqual(install.apply(self.g.dir, log=QUIET), 0)
        with open(self.g.path("version.dll"), "rb") as f:
            self.assertTrue(f.read().startswith(b"MZ the game's own"))
        self.assertIn("mpr.dll", self.g.files())
        install.apply(self.g.dir, uninstall=True, log=QUIET)
        self.assertIn("version.dll", self.g.files())   # still there
        self.assertNotIn("mpr.dll", self.g.files())

    def test_blocked_when_both_names_are_taken(self):
        self.g.close()
        self.g = FakeGame(own=("version.dll", "mpr.dll"))
        self.assertIsNone(install.proxy_name(self.g.dir))
        self.assertEqual(games.install_state(self.g.dir)["state"], "blocked")
        with self.assertRaises(SystemExit):
            install.apply(self.g.dir, log=QUIET)

    def test_outdated_when_a_hook_file_differs(self):
        install.apply(self.g.dir, log=QUIET)
        with open(self.g.path("kiripanel", "core.tjs"), "a") as f:
            f.write("// kiripanel older\n")
        self.assertEqual(games.install_state(self.g.dir)["state"], "outdated")
        install.apply(self.g.dir, log=QUIET)       # the update
        self.assertEqual(games.install_state(self.g.dir)["state"], "installed")

    def test_old_layout_is_cleaned_up(self):
        os.makedirs(self.g.path("plugin", "kiripanel"))
        with open(self.g.path("plugin", "kiripanel", "hook.tjs"), "w") as f:
            f.write("// kiripanel 0.2\n")
        self.assertEqual(games.install_state(self.g.dir)["state"], "outdated")
        install.apply(self.g.dir, log=QUIET)
        self.assertFalse(os.path.exists(self.g.path("plugin", "kiripanel")))
        self.assertEqual(games.install_state(self.g.dir)["state"], "installed")

    def test_hook_is_ascii(self):
        install.check_ascii()   # raises if not


class LibraryTest(unittest.TestCase):
    def test_scan_add_and_keep_choices(self):
        root = tempfile.mkdtemp(prefix="kp_lib_")
        try:
            a = FakeGame()
            b = FakeGame()
            shutil.move(a.dir, os.path.join(root, "GameA"))
            shutil.move(b.dir, os.path.join(root, "Series", "GameB"))
            found = games.scan(root, depth=3)
            self.assertEqual(sorted(os.path.basename(g["path"]) for g in found), ["GameA", "GameB"])
            lib = games.Library(os.path.join(root, "lib.json"))
            for g in found:
                lib.add(g)
            p = found[0]["path"]
            lib.update(p, check={"tier": "ready"}, launch="game.exe")
            lib.add(games.describe(p))              # a rescan keeps both
            lib2 = games.Library(os.path.join(root, "lib.json"))
            self.assertEqual(lib2.get(p)["check"]["tier"], "ready")
            self.assertEqual(lib2.get(p)["launch"], "game.exe")
            lib2.remove(p)
            self.assertIsNone(games.Library(os.path.join(root, "lib.json")).get(p))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_default_exe_prefers_patched_builds(self):
        self.assertEqual(games.default_exe(["Game.exe", "Game_CHS.exe"]), "Game_CHS.exe")
        self.assertEqual(games.default_exe(["game.exe", "game_crack.exe"]), "game_crack.exe")
        self.assertEqual(games.default_exe(["longname.exe", "g.exe"]), "g.exe")


if __name__ == "__main__":
    unittest.main()
