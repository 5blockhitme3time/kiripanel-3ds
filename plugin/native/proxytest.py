"""Checks a proxy build: exports match the system DLL (names, ordinals,
forwarders), and calls through the stubs give the same answers as the real
functions.
    python proxytest.py PATH_TO_version.dll|mpr.dll   (same bitness as this Python)"""
import ctypes
import ctypes.wintypes as W
import os
import sys

import pefile

proxy = os.path.abspath(sys.argv[1])
name = os.path.basename(proxy).lower()
system = os.path.join(os.environ["SystemRoot"],
                      "System32" if sys.maxsize > 2**32 else "SysWOW64", name)


def exports(p):
    pe = pefile.PE(p)
    return sorted((s.ordinal, s.name.decode(), s.forwarder)
                  for s in pe.DIRECTORY_ENTRY_EXPORT.symbols)


a, b = exports(proxy), exports(system)
print("exports match:", [(o, n, bool(f)) for o, n, f in a] == [(o, n, bool(f)) for o, n, f in b],
      "(%d)" % len(a))

k = ctypes.WinDLL("kernel32", use_last_error=True)
k.LoadLibraryW.restype = W.HMODULE
h = k.LoadLibraryW(proxy)
print("loaded:", bool(h), ctypes.get_last_error() if not h else "")
mine, real = ctypes.WinDLL(proxy, handle=h), ctypes.WinDLL(system)
for lib, tag in ((mine, "proxy "), (real, "system")):
    if name == "version.dll":
        f = lib.GetFileVersionInfoSizeW
        f.argtypes = [W.LPCWSTR, ctypes.POINTER(W.DWORD)]
        f.restype = W.DWORD
        target = os.path.join(os.environ["SystemRoot"], "System32", "kernel32.dll")
        print(tag, "GetFileVersionInfoSizeW =", f(target, None))
    else:
        f = lib.WNetGetUserW   # local user name, no network needed
        f.argtypes = [W.LPCWSTR, W.LPWSTR, ctypes.POINTER(W.DWORD)]
        buf, n = ctypes.create_unicode_buffer(256), W.DWORD(256)
        print(tag, "WNetGetUserW =", f(None, buf, ctypes.byref(n)), repr(buf.value[:1] + "..."))

k.GetProcAddress.restype = ctypes.c_void_p
k.GetProcAddress.argtypes = [W.HMODULE, ctypes.c_char_p]
print("GetProcAddress still works:", bool(k.GetProcAddress(h, a[0][1].encode())))

# A plugin's V2Link, looked up through the hooked GetProcAddress, must come
# back as a thunk outside the plugin that still reaches the plugin's code.
tpm = os.path.join(os.path.dirname(proxy), "kiripanel.tpm")
if os.path.exists(tpm):
    t = k.LoadLibraryW(tpm)
    thunk = k.GetProcAddress(t, b"V2Link")
    mi = (ctypes.c_size_t * 3)()
    ctypes.windll.psapi.GetModuleInformation(k.GetCurrentProcess(), W.HMODULE(t), mi, ctypes.sizeof(mi))
    inside = mi[0] <= thunk < mi[0] + mi[1]
    print("V2Link is a thunk (outside the plugin):", not inside)
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(thunk)
    print("thunk -> plugin V2Link(NULL) returns:", fn(None))   # both ignore NULL
    print("again (same thunk for the same plugin):", k.GetProcAddress(t, b"V2Link") == thunk)
