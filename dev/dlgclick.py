"""Press a button of a standard Windows message box owned by a process
(test harness only: first-start questions of a game under test).
    python dlgclick.py PID BUTTON_TEXT_PREFIX"""
import ctypes
import ctypes.wintypes as W
import sys

u = ctypes.windll.user32
BM_CLICK = 0x00F5


def windows_of(pid):
    out = []

    @ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def each(h, _):
        p = W.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(h):
            out.append(h)
        return True
    u.EnumWindows(each, 0)
    return out


def text(h):
    b = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(h, b, 512)
    return b.value


def cls(h):
    b = ctypes.create_unicode_buffer(64)
    u.GetClassNameW(h, b, 64)
    return b.value


def click(pid, prefix):
    for w in windows_of(pid):
        if cls(w) != "#32770":
            continue
        kids = []

        @ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        def each(h, _):
            kids.append(h)
            return True
        u.EnumChildWindows(w, each, 0)
        for k in kids:
            if cls(k) == "Button" and text(k).startswith(prefix):
                u.SendMessageW(k, BM_CLICK, 0, 0)
                return text(w), text(k)
    return None


if __name__ == "__main__":
    r = click(int(sys.argv[1]), sys.argv[2])
    print("clicked %r in %r" % (r[1], r[0]) if r else "no such button")
