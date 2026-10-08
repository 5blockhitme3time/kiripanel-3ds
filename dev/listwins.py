"""Titles and classes of the top-level windows of a process, plus the text
of any dialog box (to see an error message the game is blocked on).
    python listwins.py PID"""
import ctypes
import ctypes.wintypes as W
import sys

u = ctypes.WinDLL("user32", use_last_error=True)
ENUM = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)


def text(h):
    n = u.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(h, b, n + 1)
    return b.value


def cls(h):
    b = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(h, b, 256)
    return b.value


def windows(pid):
    out = []

    def cb(h, _):
        p = W.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(h):
            kids = []

            def kcb(k, _):
                t = text(k)
                if t:
                    kids.append("%s: %s" % (cls(k), t))
                return True
            u.EnumChildWindows(h, ENUM(kcb), 0)
            out.append((cls(h), text(h), kids))
        return True
    u.EnumWindows(ENUM(cb), 0)
    return out


if __name__ == "__main__":
    for c, t, kids in windows(int(sys.argv[1])):
        print("[%s] %s" % (c, t))
        for k in kids:
            print("    " + k)
