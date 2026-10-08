"""Checks a proxy build: the export names (and which of them are forwarded to
another DLL) match the system DLL, and calls through the stubs give the same
answers as the real functions.
    python proxytest.py PATH_TO_version.dll|mpr.dll

Ordinals are reported but not compared: they are positional, Windows
renumbers them between builds, and the proxy and its callers are all
imported by name. So a proxy generated on one Windows still serves another.

The export tables are compared for either build; the calls need this Python
to be the same bitness, and are skipped (with a line saying so) otherwise.

Reads the export tables with the standard library only, so this runs on a
machine with nothing installed."""
import ctypes
import ctypes.wintypes as W
import os
import struct
import sys


def machine_arch(path):
    """'x86', 'x64' or None, from a PE header."""
    with open(path, "rb") as f:
        data = f.read()
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        return None
    return {0x14c: "x86", 0x8664: "x64"}.get(
        struct.unpack_from("<H", data, pe + 4)[0])


proxy = os.path.abspath(sys.argv[1])
name = os.path.basename(proxy).lower()
system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                      "System32" if sys.maxsize > 2**32 else "SysWOW64", name)


def exports(path):
    """[(ordinal, name, forwarder or None)] from a PE's export directory."""
    with open(path, "rb") as f:
        data = f.read()
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise SystemExit("%s is not a PE file" % path)
    nsec, = struct.unpack_from("<H", data, pe + 6)
    opt_size, = struct.unpack_from("<H", data, pe + 20)
    opt = pe + 24
    dirs = opt + (112 if struct.unpack_from("<H", data, opt)[0] == 0x20b else 96)
    exp_rva, exp_size = struct.unpack_from("<II", data, dirs)
    sections = []
    for i in range(nsec):
        s = opt + opt_size + i * 40
        va, vsize, raw, rawsize = struct.unpack_from("<IIII", data, s + 12)
        sections.append((va, max(vsize, rawsize), raw, rawsize))

    def at(rva, n):
        for va, vsize, raw, rawsize in sections:
            if va <= rva < va + vsize:
                off = raw + (rva - va)
                return data[off:off + n] if n else b""
        raise SystemExit("rva %#x is not in any section" % rva)

    # IMAGE_EXPORT_DIRECTORY
    exp = at(exp_rva, 40)
    base, nfunc, nname = struct.unpack_from("<III", exp, 16)
    funcs_rva, names_rva, ords_rva = struct.unpack_from("<III", exp, 28)
    funcs = struct.unpack_from("<%dI" % nfunc, at(funcs_rva, 4 * nfunc)) if nfunc else ()
    name_rvas = struct.unpack_from("<%dI" % nname, at(names_rva, 4 * nname)) if nname else ()
    ords = struct.unpack_from("<%dH" % nname, at(ords_rva, 2 * nname)) if nname else ()
    by_index = {}
    for i, nr in enumerate(name_rvas):
        by_index[ords[i]] = at(nr, 256).split(b"\0")[0].decode("ascii", "replace")
    out = []
    for i, rva in enumerate(funcs):
        if not rva:
            continue
        forwarder = None
        if exp_rva <= rva < exp_rva + exp_size:
            # a forwarder is "DLL.Function" written where the code would be
            forwarder = at(rva, 256).split(b"\0")[0].decode("ascii", "replace")
        out.append((base + i, by_index.get(i, ""), forwarder))
    return sorted(out)


a, b = exports(proxy), exports(system)
# The contract that matters is the names (the proxy and every caller import
# by name) and which of them the system DLL forwards elsewhere. Ordinals are
# positional and are renumbered between Windows builds - mpr.dll on a Server
# install does not number its exports the way a desktop one does - so a
# difference there is reported, not treated as a failure.
names_a = {n: bool(f) for _, n, f in a}
names_b = {n: bool(f) for _, n, f in b}
match = names_a == names_b
print("exports match by name:", match, "(%d)" % len(a))
if not match:
    # The tables are generated from one machine's DLL; say which names differ,
    # so the fix (running gen_proxy.py again on this Windows) is obvious.
    for label, s in (("only in the proxy", set(names_a) - set(names_b)),
                     ("only in the system DLL", set(names_b) - set(names_a))):
        if s:
            print("  %s: %s" % (label, ", ".join(sorted(s)[:12])))
            if len(s) > 12:
                print("    ... and %d more" % (len(s) - 12))
ord_a = {n: o for o, n, _ in a}
ord_b = {n: o for o, n, _ in b}
differ = sorted((n, ord_a[n], ord_b[n]) for n in set(ord_a) & set(ord_b)
                if ord_a[n] != ord_b[n])
if differ:
    print("  %d ordinal(s) differ (this Windows numbers them differently; "
          "callers use names): %s" % (len(differ),
                                      ", ".join("%s %d vs %d" % d for d in differ[:4])))

# Loading the DLL only works in a process of the same bitness, so a mismatch
# (checking out\x86 from a 64-bit Python) stops after the export tables - the
# part that needs no loading - instead of failing on WinError 193.
want = "x64" if sys.maxsize > 2**32 else "x86"
if machine_arch(proxy) != want:
    print("%s is a %s build and this Python is %s, so the stubs were only "
          "compared as export tables, not called: the calls need the same "
          "bitness" % (os.path.basename(proxy), machine_arch(proxy), want))
    sys.exit(0 if match else 1)

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
