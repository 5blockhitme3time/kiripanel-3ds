"""
Small Windows helpers for the manager: the processes a game started, the
message boxes they show, ending them, and the PC's LAN addresses.
"""
import ctypes
import ctypes.wintypes as W
import socket
import subprocess

_k = ctypes.WinDLL("kernel32", use_last_error=True)
_u = ctypes.WinDLL("user32", use_last_error=True)
_k.CreateToolhelp32Snapshot.restype = W.HANDLE
_k.OpenProcess.restype = W.HANDLE
_ENUM = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
TH32CS_SNAPPROCESS = 0x2
CREATE_NO_WINDOW = 0x08000000


class _PE(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD),
                ("th32ProcessID", W.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", W.DWORD), ("cntThreads", W.DWORD),
                ("th32ParentProcessID", W.DWORD), ("pcPriClassBase", W.LONG),
                ("dwFlags", W.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def processes():
    """{pid: (parent pid, exe file name)} of every process."""
    out = {}
    h = _k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not h or h == W.HANDLE(-1).value:
        return out
    e = _PE()
    e.dwSize = ctypes.sizeof(_PE)
    ok = _k.Process32FirstW(h, ctypes.byref(e))
    while ok:
        out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
        ok = _k.Process32NextW(h, ctypes.byref(e))
    _k.CloseHandle(h)
    return out


def tree(pid):
    """pid and every process descended from it (a launcher's game)."""
    procs = processes()
    out, todo = {pid}, [pid]
    while todo:
        p = todo.pop()
        for c, (parent, _) in procs.items():
            if parent == p and c not in out:
                out.add(c)
                todo.append(c)
    return out


def alive(pid):
    return pid in processes()


def kill_tree(pid):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True, creationflags=CREATE_NO_WINDOW)


def _text(h):
    n = _u.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1)
    _u.GetWindowTextW(h, b, n + 1)
    return b.value


def _cls(h):
    b = ctypes.create_unicode_buffer(256)
    _u.GetClassNameW(h, b, 256)
    return b.value


def dialogs(pids):
    """Standard message boxes owned by any of `pids`, as
    [(title, text, [button captions])]. A game that shows one of these is
    waiting for the player (a first-start question, or an error)."""
    out = []

    def cb(h, _):
        p = W.DWORD()
        _u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value in pids and _u.IsWindowVisible(h) and _cls(h) == "#32770":
            texts, buttons = [], []

            def kcb(k, _):
                t = _text(k)
                if t:
                    (buttons if _cls(k) == "Button" else texts).append(t)
                return True
            _u.EnumChildWindows(h, _ENUM(kcb), 0)
            out.append((_text(h), "\n".join(texts), buttons))
        return True
    _u.EnumWindows(_ENUM(cb), 0)
    return out


def bring_to_front(pids):
    """Raise the first visible top-level window of `pids` (for a message
    box the player has to answer)."""
    found = []

    def cb(h, _):
        p = W.DWORD()
        _u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value in pids and _u.IsWindowVisible(h) and _cls(h) == "#32770":
            found.append(h)
        return True
    _u.EnumWindows(_ENUM(cb), 0)
    if found:
        _u.SetForegroundWindow(found[0])
    return bool(found)


def lan_addresses():
    """The PC's IPv4 addresses a console on the same network can reach,
    most likely first."""
    addrs = []
    try:
        # the address used for outgoing traffic (no packet is sent)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        addrs.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            a = info[4][0]
            if a not in addrs:
                addrs.append(a)
    except OSError:
        pass
    return [a for a in addrs if not a.startswith(("127.", "169.254."))]
