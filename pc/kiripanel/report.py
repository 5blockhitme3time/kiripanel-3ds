"""The hook's scout report (_hkscout.ksd) as readable text, for writing an
adapter for a game the panel does not know yet."""


def render(st):
    """Parsed _hkscout.ksd -> text."""
    L = []
    w = L.append
    w("engine   %s" % st.get("version"))
    w("exe      %s" % st.get("exe"))
    w("title    %s" % st.get("title"))
    w("hook     %s  adapter=%s  tpm=%s" % (st.get("hook"), st.get("adapter"),
                                            st.get("native")))
    w("")
    w("== loaded plugins")
    for m in st.get("modules") or []:
        w("  " + m)
    k = st.get("kag")
    if k:
        w("")
        w("== kag  (%s)" % k.get("classes"))
        for name, t, v in k.get("members") or []:
            if t != "undefined":
                w("  %-28s %-9s %s" % (name, t, v))
        missing = [n for n, t, _ in k.get("members") or [] if t == "undefined"]
        w("  (absent: %s)" % ", ".join(missing))
        w("")
        w("== KAG plugins")
        for p in k.get("plugins") or []:
            w("  " + p)
        w("")
        w("== message layers")
        for m in k.get("messages") or []:
            w("  %-4s %d  %-20s vis=%s op=%s rect=%s links=%s current=%s frame=%s %s"
              % (m.get("page"), m.get("index"), m.get("name"), m.get("visible"),
                 m.get("opacity"), m.get("rect"), m.get("numLinks"),
                 m.get("isCurrent"), m.get("frameGraphic"), m.get("cls") or ""))
        if k.get("history"):
            h = k["history"]
            w("")
            w("== history layer (%s)" % h.get("cls"))
            for n, v in h.get("values") or []:
                w("  %-14s %s" % (n, v))
            w("  members: " + " ".join(h.get("members") or []))
        w("")
        w("== layer tree (fore.base)")
        for d, name, cls, vis, op, wd, ht, nch in k.get("layers") or []:
            w("  %s%s  [%s] vis=%s op=%s %sx%s children=%s"
              % ("  " * d, name, cls, vis, op, wd, ht, nch))
        for fl, rows in (k.get("flags") or {}).items():
            w("")
            w("== %s (%d)" % (fl, len(rows)))
            for n, v in rows:
                w("  %-32s %s" % (n, v))
        w("")
        w("== current message layer members")
        w("  " + " ".join(k.get("currentMembers") or []))
        w("")
        w("== kag members")
        w("  " + " ".join(k.get("allMembers") or []))
    w("")
    w("== suspicious globals")
    for n, cls, mem in st.get("suspects") or []:
        w("  %s  [%s]" % (n, cls))
        w("      " + " ".join(mem))
    w("")
    w("== all globals")
    for n, t, v in st.get("globals") or []:
        w("  %-36s %-8s %s" % (n, t, v))
    return "\n".join(L) + "\n"
