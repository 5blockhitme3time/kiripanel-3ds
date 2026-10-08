"""List the non-Windows modules (DLL/tpm) loaded in a process.
    python listmods.py PID"""
import ctypes
import ctypes.wintypes as W
import sys

TH32CS_SNAPMODULE = 0x8
TH32CS_SNAPMODULE32 = 0x10


class ME(ctypes.Structure):
    _fields_ = [("dwSize", W.DWORD), ("th32ModuleID", W.DWORD),
                ("th32ProcessID", W.DWORD), ("GlblcntUsage", W.DWORD),
                ("ProccntUsage", W.DWORD), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", W.DWORD), ("hModule", W.HMODULE),
                ("szModule", ctypes.c_wchar * 256),
                ("szExePath", ctypes.c_wchar * 260)]


def modules(pid):
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = W.HANDLE
    h = k.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    e = ME()
    e.dwSize = ctypes.sizeof(ME)
    out = []
    ok = k.Module32FirstW(h, ctypes.byref(e))
    while ok:
        if "\\windows\\" not in e.szExePath.lower():
            out.append(e.szExePath)
        ok = k.Module32NextW(h, ctypes.byref(e))
    k.CloseHandle(h)
    return out


if __name__ == "__main__":
    for m in modules(int(sys.argv[1])):
        print(m.rsplit("\\", 2)[-2] + "\\" + m.rsplit("\\", 1)[-1])
