"""
The player's KiriKiri games: finding them, describing them, and what of
ours is installed in each. Used by the manager (app.py).

A game is a folder holding at least one KiriKiri executable. The library
(the folders the player added or that a scan found, plus the last
compatibility result of each) lives in %LOCALAPPDATA%\\kiripanel\\library.json.
"""
import ctypes
import ctypes.wintypes as W
import hashlib
import json
import os
import threading
import time

from . import install

LIBRARY = os.path.expandvars(r"%LOCALAPPDATA%\kiripanel\library.json")
# Folder names that say nothing about the game; the parent is used instead.
GENERIC_NAMES = {"game", "data", "bin", "app", "files", "system", "pc"}
# Exe names that are seldom what the player starts (tools, original builds
# next to a patched one).
PREFER = ("_chs", "chs", "_cn", "汉化", "crack", "_patch")


# ------------------------------------------------------------ exe facts ----

def version_strings(path):
    """ProductName / FileDescription / FileVersion / CompanyName of an exe
    (whatever its resource has), {} if none."""
    v = ctypes.windll.version
    v.GetFileVersionInfoSizeW.argtypes = [W.LPCWSTR, ctypes.POINTER(W.DWORD)]
    v.GetFileVersionInfoW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p]
    v.VerQueryValueW.argtypes = [ctypes.c_void_p, W.LPCWSTR,
                                 ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint)]
    size = v.GetFileVersionInfoSizeW(path, None)
    if not size:
        return {}
    buf = ctypes.create_string_buffer(size)
    pbuf = ctypes.cast(buf, ctypes.c_void_p)
    if not v.GetFileVersionInfoW(path, 0, size, pbuf):
        return {}
    p, n = ctypes.c_void_p(), ctypes.c_uint()
    langs = []
    if v.VerQueryValueW(pbuf, r"\VarFileInfo\Translation", ctypes.byref(p), ctypes.byref(n)) \
            and n.value >= 4:
        arr = (ctypes.c_ushort * (n.value // 2)).from_address(p.value)
        langs = ["%04x%04x" % (arr[i], arr[i + 1]) for i in range(0, len(arr) - 1, 2)]
    langs += ["041104b0", "040904b0", "000004b0", "040904e4", "041103a4"]
    out = {}
    for key in ("ProductName", "FileDescription", "FileVersion", "CompanyName"):
        for lg in langs:
            if v.VerQueryValueW(pbuf, "\\StringFileInfo\\%s\\%s" % (lg, key),
                                ctypes.byref(p), ctypes.byref(n)) and n.value > 1:
                s = ctypes.wstring_at(p.value, n.value).rstrip("\0").strip()
                if s:
                    out[key] = s
                    break
    return out


def engine_of(path):
    """('krkrz' | 'krkr2', version string or '') for a KiriKiri exe."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return "", ""
    kind = "krkrz" if "kirikiriz".encode("utf-16-le") in data.lower() or \
        b"KIRIKIRI Z" in data or "KIRIKIRI Z".encode("utf-16-le") in data else "krkr2"
    ver = version_strings(path).get("FileVersion", "")
    ver = ver.replace(",", ".").replace(" ", "")
    return kind, ver


def default_exe(names):
    """The engine exe a player most likely starts, of those in a folder."""
    if not names:
        return None
    for key in PREFER:
        for n in names:
            if key in n.lower():
                return n
    return min(names, key=len)


def display_name(folder):
    base = os.path.basename(folder.rstrip("\\/"))
    if base.lower() in GENERIC_NAMES or len(base) <= 2:
        parent = os.path.basename(os.path.dirname(folder.rstrip("\\/")))
        if parent:
            return parent
    return base


# -------------------------------------------------------- install state ----

def _digest(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return None


def install_state(folder, arch="x86"):
    """What of ours is in `folder`:
      state   'none' | 'installed' | 'outdated' | 'blocked'
      proxy   the proxy DLL name in use (or that an install would use)
      legacy  True if the version 0.2 layout (plugin\\kiripanel*) is there
    'blocked': the game has its own version.dll and mpr.dll."""
    out = {"state": "none", "proxy": install.proxy_name(folder), "legacy": False}
    # the hook copy that version 0.2 kept under plugin\
    out["legacy"] = os.path.isdir(os.path.join(folder, "plugin", "kiripanel"))
    tpm = os.path.exists(os.path.join(folder, "plugin", "kiripanel.tpm"))
    mine = [n for n in install.PROXY_NAMES
            if os.path.exists(os.path.join(folder, n)) and install.ours(os.path.join(folder, n))]
    if not mine:
        if out["proxy"] is None:
            out["state"] = "blocked"
        elif out["legacy"] or tpm:
            out["state"] = "outdated"
        return out
    name = mine[0]
    out["proxy"] = name
    same = _digest(os.path.join(folder, name)) == _digest(os.path.join(install.OUT, arch, name))
    for n in install.hook_files():
        if _digest(os.path.join(folder, "kiripanel", n)) != _digest(os.path.join(install.HOOK_DIR, n)):
            same = False
            break
    out["state"] = "installed" if same and not out["legacy"] else "outdated"
    return out


# --------------------------------------------------------------- scan ----

def describe(folder):
    """A game record for `folder`, or None if it holds no KiriKiri exe."""
    folder = os.path.normpath(os.path.abspath(folder))
    try:
        engines = install.find_engines(folder)
    except OSError:
        return None
    if not engines:
        return None
    names = [n for n, _ in engines]
    exe = default_exe(names)
    arch = dict(engines)[exe]
    # engine facts from the biggest KiriKiri exe: a launcher next to it is
    # small and may carry the engine's marker without being the engine
    core = max(names, key=lambda n: os.path.getsize(os.path.join(folder, n)))
    kind, ver = engine_of(os.path.join(folder, core))
    name = display_name(folder)
    desc = version_strings(os.path.join(folder, core)).get("FileDescription", "")
    if desc and not any(k in desc.upper() for k in ("KIRIKIRI", "TVP", "SCRIPTING")):
        name = desc
    others = []
    try:
        others = sorted(n for n in os.listdir(folder)
                        if n.lower().endswith(".exe") and n not in names)
    except OSError:
        pass
    return {"path": folder, "name": name, "engines": engines,
            "exe": exe, "arch": arch, "engine": kind, "version": ver,
            "launchers": others}


def scan(root, depth=2, stop=None):
    """Game folders under `root` (root itself included), to `depth` levels."""
    found = []
    root = os.path.abspath(root)

    def walk(d, level):
        if stop is not None and stop.is_set():
            return
        g = describe(d)
        if g:
            found.append(g)
            return            # a game's own subfolders are not other games
        if level >= depth:
            return
        try:
            subs = sorted(e.path for e in os.scandir(d)
                          if e.is_dir(follow_symlinks=False) and not e.name.startswith((".", "$")))
        except OSError:
            return
        for s in subs:
            walk(s, level + 1)
    walk(root, 0)
    return found


# ------------------------------------------------------------ library ----

class Library:
    """Thread-safe list of game records, saved as JSON."""

    def __init__(self, path=LIBRARY):
        self.path = path
        self.lock = threading.Lock()
        self.games = {}           # normcase(path) -> record
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        for g in data.get("games", []):
            if isinstance(g, dict) and g.get("path"):
                self.games[os.path.normcase(g["path"])] = g

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"games": list(self.games.values())}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def add(self, record):
        """Adds or refreshes a game; keeps the player's exe choice and the
        last check of an existing entry."""
        key = os.path.normcase(record["path"])
        with self.lock:
            old = self.games.get(key, {})
            merged = dict(record)
            for k in ("check", "added"):
                if k in old:
                    merged[k] = old[k]
            if old.get("exe_chosen") and old.get("exe") in [n for n, _ in record["engines"]]:
                merged["exe"], merged["arch"] = old["exe"], dict(record["engines"])[old["exe"]]
                merged["exe_chosen"] = True
            if old.get("launch"):
                merged["launch"] = old["launch"]
            merged.setdefault("added", time.time())
            self.games[key] = merged
            self.save()
            return merged

    def get(self, path):
        with self.lock:
            g = self.games.get(os.path.normcase(os.path.normpath(path)))
            return dict(g) if g else None

    def update(self, path, **fields):
        with self.lock:
            g = self.games.get(os.path.normcase(os.path.normpath(path)))
            if g is None:
                return None
            g.update(fields)
            self.save()
            return dict(g)

    def remove(self, path):
        with self.lock:
            self.games.pop(os.path.normcase(os.path.normpath(path)), None)
            self.save()

    def all(self):
        with self.lock:
            return sorted((dict(g) for g in self.games.values()),
                          key=lambda g: g.get("name", "").lower())
