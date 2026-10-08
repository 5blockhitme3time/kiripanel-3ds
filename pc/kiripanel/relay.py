#!/usr/bin/env python3
r"""
PC-side relay between the game hook and the 3DS dialogue panel.

    hook (kiripanel/ in the game)         --_hkstate.ksd-->  relay  --HTTP-->  3DS
                                          <--_hkcmd.ksd---         <--HTTP--

The hook mirrors the game's own backlog, the line being typed, the choice on
screen and which system buttons are usable, and writes all of it to
_hkstate.ksd whenever something changes. The relay turns that into history
with stable ids, localised status text and short result notes, and serves it
to the console incrementally, so a poll that finds nothing new costs a few
dozen bytes.

Commands go the other way as a short numbered queue in _hkcmd.ksd; the hook
executes each entry once, through the game's own functions, and acknowledges
it in the state file. Both files use KiriKiri's saveStruct text format,
because Scripts.evalStorage() is the only way the hook can read a file.

Which game: the hook's tpm writes %LOCALAPPDATA%\kiripanel\session.txt with
the running game's save folder, and the relay follows it (also when a
different game is started later). Without the tpm, or to pin one game, pass
--savedir. Per-game extras (choice captions, a default save folder) live in
profiles/<adapter>.json, picked by the adapter the hook reports.

Run:  python relay.py                 (follows whichever game is running)
      python relay.py --help
Then open http://localhost:8787/ for a debug console that shows what the 3DS
would see and can send the same commands.

Access: the server listens on all interfaces so the 3DS can reach it, and has
no authentication. By default it only answers loopback and private-network
addresses (--allow any lifts that), and it refuses requests that a web page
from another site makes a browser send (cross-site fetches, DNS rebinding).
Anyone on the LAN can still advance the game or load the quick save; run it
only on a network you trust.
"""
import argparse
import ipaddress
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(HERE, "profiles")
SESSION_FILE = os.path.expandvars(r"%LOCALAPPDATA%\kiripanel\session.txt")
STATE_NAME = "_hkstate.ksd"
CMD_NAME = "_hkcmd.ksd"
HOOK_PROTO = 3
PANEL_PROTO = 2

# The hook rewrites its state at least every 2 s; older than this means the
# game is gone (or hung).
HOOK_STALE_S = 6.0
HISTORY_KEEP = 300
HISTORY_SEND = 60          # newest entries sent to a client that is behind
NOTE_TTL_S = 4.0
CMD_TTL_S = 10.0
# A console that stops polling (closed, slept, switched to mirroring the
# screen) gets the game's textbox back after this long.
TEXTBOX_HOLD_S = 3.0
# How often the "keep it hidden" request is refreshed; the hook gives up on
# it after 4 s without a refresh.
TEXTBOX_BEAT_S = 1.5

COMMANDS = ("click", "auto", "skip", "voice", "qsave", "qload", "choose",
            # the game's own screens, and the title menu
            "config", "save", "load", "title", "continue", "newgame",
            # a report on the game for writing its adapter (_hkscout.ksd)
            "scout")

# Where the game is, as the hook's adapter reports it -> (code the console
# understands, status label). "play" is the normal reading state and needs
# no label; the console mirrors the PC screen for every "menu".
LOCATIONS = {
    "play": ("play", ""),
    "title": ("title", "标题画面"),
    "save": ("menu", "存档画面"),
    "load": ("menu", "读档画面"),
    "config": ("menu", "设置画面"),
    "backlog": ("menu", "游戏内回想"),
    "gallery": ("menu", "鉴赏模式"),
    "ending": ("menu", "片尾"),
    "menu": ("menu", "菜单"),
    "detecting": ("menu", "正在识别游戏"),
    "unsupported": ("menu", "这款游戏暂不支持台词面板"),
}

# Hook result codes -> note shown on the console. None means "no note": the
# effect is visible anyway (text advanced, button lit up, choice closed).
RESULTS = {
    "ok": None,
    "chosen": None,
    "saved": "已快速存档",
    "loaded": "已读取快速存档",
    "empty": "还没有快速存档",
    "menu": "请先关闭游戏里的菜单",
    "dialog": "电脑上有确认框，请在电脑上处理",
    "select": "请先做出选择",
    "noselect": "现在没有选项",
    "stale": "选项已经变了",
    "disabled": "现在不能用",
    "busy": "游戏正忙，请稍后再试",
    "novoice": "这句没有语音",
    "scene": "回想中不能存读档",
    "totitle": "已回到标题，可用“继续游戏”回到这里",
    "nocontinue": "没有可以继续的进度",
    "unknown": "游戏不认识这个命令",
    "unsupported": "这款游戏不支持这个操作",
    "scouted": "侦察报告已写入存档目录的 _hkscout.ksd",
    "error": "执行出错（详见 relay 输出）",
}


# ------------------------------------------------------ saveStruct format ----

class StructError(ValueError):
    pass


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b",
            "f": "\f", "v": "\v", "0": "\0", '"': '"', "'": "'", "\\": "\\"}
_NUMBER = re.compile(r"[-+]?(0x[0-9a-fA-F.]+(p[-+]?\d+)?|\d+\.?\d*([eE][-+]?\d+)?)")


class _Parser:
    """Recursive-descent reader for the text Dictionary.saveStruct writes:
    %[ "k" => v, ... ], [ v, ... ], strings, numbers, void; each container
    may carry a "(const)" prefix."""

    def __init__(self, text):
        self.s = text
        self.i = 0

    def ws(self):
        s, i = self.s, self.i
        while i < len(s):
            if s[i] in " \t\r\n\ufeff":
                i += 1
            elif s.startswith("//", i):
                j = s.find("\n", i)
                i = len(s) if j < 0 else j
            elif s.startswith("/*", i):   # saveStruct: 0x1.Ep10 /* 1920 */
                j = s.find("*/", i + 2)
                i = len(s) if j < 0 else j + 2
            else:
                break
        self.i = i

    def peek(self, lit):
        self.ws()
        return self.s.startswith(lit, self.i)

    def take(self, lit):
        if not self.peek(lit):
            raise StructError("expected %r at %d" % (lit, self.i))
        self.i += len(lit)

    def value(self):
        self.ws()
        if self.peek("(const)"):
            self.take("(const)")
            self.ws()
        s = self.s
        if self.i >= len(s):
            raise StructError("unexpected end")
        c = s[self.i]
        if s.startswith("%[", self.i):
            return self.dict_()
        if c == "[":
            return self.list_()
        if c in "\"'":
            return self.string()
        for word, val in (("void", None), ("null", None),
                          ("true", 1), ("false", 0)):
            if s.startswith(word, self.i):
                self.i += len(word)
                return val
        m = _NUMBER.match(s, self.i)
        if m:
            self.i = m.end()
            tok = m.group(0)
            body = tok.lstrip("+-")
            if body.lower().startswith("0x"):
                if "." in body or "p" in body.lower():
                    v = float.fromhex(body)
                    return -v if tok.startswith("-") else v
                v = int(body, 16)
                return -v if tok.startswith("-") else v
            if any(ch in tok for ch in ".eE"):
                return float(tok)
            return int(tok)
        raise StructError("unexpected %r at %d" % (c, self.i))

    def dict_(self):
        self.take("%[")
        out = {}
        while True:
            if self.peek("]"):
                self.take("]")
                return out
            key = self.value()
            self.take("=>")
            out[str(key)] = self.value()
            if self.peek(","):
                self.take(",")

    def list_(self):
        self.take("[")
        out = []
        while True:
            if self.peek("]"):
                self.take("]")
                return out
            out.append(self.value())
            if self.peek(","):
                self.take(",")

    def string(self):
        s = self.s
        q = s[self.i]
        i = self.i + 1
        buf = []
        while True:
            if i >= len(s):
                raise StructError("unterminated string")
            c = s[i]
            if c == q:
                self.i = i + 1
                return "".join(buf)
            if c == "\\" and i + 1 < len(s):
                n = s[i + 1]
                if n == "x":
                    j = i + 2
                    while j < len(s) and j < i + 6 and s[j] in "0123456789abcdefABCDEF":
                        j += 1
                    buf.append(chr(int(s[i + 2:j] or "0", 16)))
                    i = j
                    continue
                buf.append(_ESCAPES.get(n, n))
                i += 2
                continue
            buf.append(c)
            i += 1


def parse_struct(text):
    p = _Parser(text)
    v = p.value()
    p.ws()
    if p.i != len(text):
        raise StructError("trailing data at %d" % p.i)
    return v


def decode_struct_bytes(raw):
    if raw[:2] == b"\xff\xfe":
        return raw[2:].decode("utf-16-le")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    if len(raw) >= 2 and raw[1:2] == b"\x00":
        return raw.decode("utf-16-le")
    return raw.decode("utf-8")


def _tjs_string(s):
    out = ['"']
    for ch in str(s):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            # dropped rather than written as \xHH: how many hex digits the
            # engine's lexer takes after \x is not something to rely on
            continue
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def format_struct(v):
    """Python value -> TJS expression text that Scripts.evalStorage() reads."""
    if v is None:
        return "void"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return _tjs_string(v)
    if isinstance(v, dict):
        return "%[" + ", ".join("%s => %s" % (_tjs_string(k), format_struct(x))
                                for k, x in v.items()) + "]"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(format_struct(x) for x in v) + "]"
    raise TypeError("cannot store %r" % type(v))


def write_struct_file(path, value, retries=20):
    """Atomic write: the game only ever sees the old file or the new one.

    os.replace can fail with a sharing violation for the moment the game has
    the file open, so it is retried briefly."""
    data = b"\xff\xfe" + format_struct(value).encode("utf-16-le")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    for attempt in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(0.01)


# --------------------------------------------------------------- the hub ----

def clean_name(name):
    """'【伊织】' -> '伊织'. The game stores names with the plate brackets."""
    name = (name or "").strip()
    if name.startswith("【") and name.endswith("】"):
        name = name[1:-1]
    return name.strip()


def clean_text(text):
    return (text or "").replace("\r", "").replace("\n", "")


_profiles = {}


def load_profile(adapter):
    """profiles/<adapter>.json as a dict ({} if there is none). Cached."""
    if adapter in _profiles:
        return _profiles[adapter]
    prof = {}
    if adapter and re.fullmatch(r"[A-Za-z0-9_-]+", adapter):
        path = os.path.join(PROFILE_DIR, adapter + ".json")
        try:
            with open(path, encoding="utf-8") as f:
                prof = json.load(f)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as ex:
            print("! cannot read %s: %s" % (path, ex))
    _profiles[adapter] = prof
    return prof


def choice_caption(label, img, captions=None):
    """Text for an option. Image options (caption baked into the picture)
    are looked up by file name in the game's profile."""
    captions = captions or {}
    key = img[6:] if img.startswith("MS_sl_") else img
    if key and key in captions:
        return captions[key]
    if label in captions:
        return captions[label]
    return label or key or "（选项）"


def read_session(path=SESSION_FILE):
    """The running game's session.txt (written by the tpm) as a dict, or
    None. Keys: pid, proto, loader, datapath, exe, boot."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    out = {}
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        if sep:
            out[k.strip()] = v.strip()
    return out if out.get("datapath") else None


def profile_savedirs():
    """Default save folders named by the profiles, for hooks without the tpm."""
    out = []
    try:
        names = sorted(os.listdir(PROFILE_DIR))
    except OSError:
        return out
    for n in names:
        if n.endswith(".json"):
            d = load_profile(n[:-5]).get("savedir")
            if d:
                out.append(os.path.expandvars(d))
    return out


def escape_field(s):
    return (str(s).replace("\\", "\\\\").replace("\t", "\\t")
            .replace("\n", "\\n").replace("\r", ""))


class Hub:
    """Everything the relay knows, behind one lock.

    `rev` changes whenever anything a client would draw changes; history
    entries carry ids that only grow, so a client that already holds up to id
    H needs only what came after."""

    def __init__(self, cmd_path=None, clock=time.time, epoch=None):
        self.lock = threading.Condition()
        self.clock = clock
        self.epoch = int(epoch if epoch is not None else clock())
        self.cmd_path = cmd_path
        self.rev = 1

        self.hook = None           # last state dict from the hook
        self.hook_seen = 0.0       # clock() of the last fresh state file
        self.hook_error = ""

        self.history = []          # dicts: id, kind ('L'|'D'), name, text
        self.hist_id = 0
        self.boot = None
        self.hook_line = 0         # last hook line id taken into history
        self.hook_gen = None

        self.cmd_seq = 0
        self.cmds = []             # dicts: seq, cmd, arg, sel, t
        self.cmd_names = {}        # seq -> cmd
        self.ack_seen = 0
        self.note = None           # (id, text, clock)
        self.note_id = 0
        self.log = print
        self.console_seen = 0.0    # clock() of the last /panel.txt poll

        # Hiding the game's own textbox: "auto" while a console shows the
        # dialogue (it polls with hide=1), "never", or "always".
        self.textbox = "auto"
        self.hide_until = 0.0      # clock() until which a console asked
        self.hide_sent = False     # what the command file currently says
        self.beat = 0
        self.beat_at = 0.0

    # -- hook side ----------------------------------------------------------

    def hook_alive(self):
        return (self.hook is not None
                and self.clock() - self.hook_seen < HOOK_STALE_S)

    def _add_history(self, kind, name, text):
        self.hist_id += 1
        self.history.append({"id": self.hist_id, "kind": kind,
                             "name": name, "text": text})
        if len(self.history) > HISTORY_KEEP:
            del self.history[:len(self.history) - HISTORY_KEEP]

    def _set_note(self, text):
        self.note_id += 1
        self.note = (self.note_id, text, self.clock())

    def ingest(self, st, when=None):
        """Take a freshly read state dict from the hook."""
        with self.lock:
            now = self.clock() if when is None else when
            was_alive = self.hook_alive()
            self.hook_seen = now
            if not isinstance(st, dict) or st.get("proto") != HOOK_PROTO:
                if self.hook_error != "proto":
                    self.log("! hook state has protocol %r, expected %d - is "
                             "the game's kiripanel folder up to date?"
                             % (st.get("proto") if isinstance(st, dict) else None,
                                HOOK_PROTO))
                self.hook_error = "proto"
                self.hook = None
                self.rev += 1
                self.lock.notify_all()
                return
            self.hook_error = ""
            changed = not was_alive or self.hook is None \
                or st.get("rev") != self.hook.get("rev")

            boot = st.get("boot")
            if boot != self.boot:
                if self.boot is not None:
                    self.log("* game restarted")
                    if self.history:
                        self._add_history("D", "", "游戏重新启动")
                self.boot = boot
                self.hook_line = 0
                self.hook_gen = None
                self.ack_seen = 0
                changed = True
            if not was_alive:
                self.log("* game hook connected")

            for entry in st.get("lines") or []:
                try:
                    lid, gen, name, text = entry[0], entry[1], entry[2], entry[3]
                except (TypeError, IndexError):
                    continue
                if lid <= self.hook_line:
                    continue
                if self.hook_gen is not None and gen != self.hook_gen \
                        and self.history and self.history[-1]["kind"] != "D":
                    self._add_history("D", "", "")
                elif self.hook_line and lid > self.hook_line + 1 \
                        and self.history and self.history[-1]["kind"] != "D":
                    # the relay missed lines (it was not running)
                    self._add_history("D", "", "部分台词未同步")
                self._add_history("L", clean_name(name), clean_text(text))
                self.hook_line = lid
                self.hook_gen = gen
                changed = True

            # acks are numbered by the relay that sent the commands; ones from
            # an earlier relay run (or before the hook saw this one) are not
            # ours to report
            acks = st.get("acks") if st.get("cmdEpoch") == self.epoch else []
            for a in acks or []:
                try:
                    seq, res = int(a[0]), str(a[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if seq <= self.ack_seen:
                    continue
                self.ack_seen = seq
                cmd = self.cmd_names.pop(seq, "?")
                self.log("  cmd #%d %s -> %s" % (seq, cmd, res))
                text = RESULTS.get(res, "结果：%s" % res)
                if text:
                    self._set_note(text)
                    changed = True

            err = st.get("err") or ""
            if err and err != (self.hook or {}).get("err"):
                self.log("! hook error: %s" % err)
            prev = self.hook or {}
            if st.get("adapter") != prev.get("adapter") or \
                    st.get("degraded") != prev.get("degraded"):
                self.log("* game adapter: %s (loaded by %s)%s"
                         % (st.get("adapter"), st.get("loader"),
                            "; dropped after errors: %s" % st["degraded"]
                            if st.get("degraded") else ""))

            self.hook = st
            if changed:
                self.rev += 1
                self.lock.notify_all()

    def check_liveness(self):
        """Called periodically; bumps rev when the hook goes quiet."""
        with self.lock:
            if self.hook is not None and not self.hook_alive():
                self.log("* game hook went quiet")
                self.hook = None
                self.rev += 1
                self.lock.notify_all()

    # -- console side -------------------------------------------------------

    def status(self):
        """(link, loc code, loc label). Caller holds the lock."""
        if self.hook_error == "proto" and \
                self.clock() - self.hook_seen < HOOK_STALE_S:
            return 0, "hookold", "游戏钩子版本不匹配"
        if not self.hook_alive():
            return 0, "off", "游戏未运行"
        st = self.hook
        if st.get("dlg"):
            return 1, "dialog", "电脑上有确认框"
        code, label = LOCATIONS.get(st.get("loc"), ("menu", "菜单"))
        return 1, code, label

    def current_line(self):
        """(name, text) of the line being typed, or None. Caller holds lock."""
        st = self.hook
        if not st or not st.get("cur"):
            return None
        name, text = clean_name(st.get("curName")), clean_text(st.get("cur"))
        last = self.history[-1] if self.history else None
        if last and last["kind"] == "L" and last["text"] == text \
                and last["name"] == name:
            return None
        return name, text

    def choices(self):
        """(sel, [(idx, enabled, read, caption)]) or (None, []). Lock held."""
        st = self.hook
        if not st or st.get("loc") != "play" or st.get("dlg"):
            return None, []
        sel = st.get("sel", -1)
        if sel is None or sel < 0:
            return None, []
        captions = load_profile(st.get("adapter")).get("choice_captions")
        out = []
        for o in st.get("opts") or []:
            try:
                idx, label, img, en, rd = o[0], o[1] or "", o[2] or "", o[3], o[4]
            except (TypeError, IndexError):
                continue
            out.append((int(idx), bool(en), bool(rd),
                        choice_caption(label, img, captions)))
        return sel, out

    def panel_text(self, client_epoch=None, client_rev=None, client_hist=0,
                   wait=0.0, hide=False):
        """Line protocol for the console; see the 3DS relay_protocol.hpp.

        hide: the console is showing the dialogue right now, so the game's
        own textbox may be hidden on the PC."""
        with self.lock:
            self.console_seen = self.clock()
            if wait > 0 and client_epoch == self.epoch and client_rev == self.rev:
                self.lock.wait(min(wait, 5.0))
            if hide:
                self.hide_until = self.clock() + TEXTBOX_HOLD_S
            out = ["v\t%d" % PANEL_PROTO, "epoch\t%d" % self.epoch,
                   "rev\t%d" % self.rev]
            if client_epoch == self.epoch and client_rev == self.rev:
                out.append("same")
                out.append("end")
                return "\n".join(out) + "\n"

            link, loc, label = self.status()
            out.append("link\t%d" % link)
            out.append("loc\t%s\t%s" % (loc, escape_field(label)))
            st = self.hook or {}
            mode = "auto" if st.get("auto") else ("skip" if st.get("skip") else "-")
            out.append("mode\t%s" % (mode if link else "-"))
            out.append("btn\t%d" % (int(st.get("btn") or 0) if link else 0))

            cur = self.current_line() if link else None
            if cur:
                out.append("cur\t%s\t%s" % (escape_field(cur[0]),
                                            escape_field(cur[1])))

            oldest = self.history[0]["id"] if self.history else self.hist_id + 1
            if client_epoch != self.epoch or client_hist < oldest - 1 \
                    or client_hist > self.hist_id:
                out.append("hreset")
                send = self.history[-HISTORY_SEND:]
            else:
                send = [h for h in self.history if h["id"] > client_hist]
                if len(send) > HISTORY_SEND:
                    out.append("hreset")
                    send = send[-HISTORY_SEND:]
            for h in send:
                if h["kind"] == "L":
                    out.append("h\t%d\tL\t%s\t%s" % (h["id"], escape_field(h["name"]),
                                                     escape_field(h["text"])))
                else:
                    out.append("h\t%d\tD\t%s" % (h["id"], escape_field(h["text"])))

            sel, opts = self.choices() if link else (None, [])
            if sel is not None and opts:
                out.append("sel\t%d" % sel)
                for idx, en, rd, cap in opts:
                    flags = ("e" if en else "") + ("r" if rd else "")
                    out.append("opt\t%d\t%s\t%s" % (idx, flags or "-",
                                                    escape_field(cap)))

            if self.note and self.clock() - self.note[2] < NOTE_TTL_S:
                out.append("note\t%d\t%s" % (self.note[0], escape_field(self.note[1])))
            out.append("end")
            return "\n".join(out) + "\n"

    # -- commands -----------------------------------------------------------

    def _write_commands(self):
        """Rewrite the command file. Caller holds the lock."""
        if not self.cmd_path:
            return
        payload = {"epoch": self.epoch,
                   "cmds": [{"seq": c["seq"], "cmd": c["cmd"], "arg": c["arg"],
                             "sel": c["sel"]} for c in self.cmds],
                   "hide": 1 if self.hide_sent else 0,
                   "beat": self.beat}
        write_struct_file(self.cmd_path, payload)

    def reset_command_file(self):
        with self.lock:
            self._write_commands()

    def set_savedir(self, path):
        """Follow a game in another save folder (another game started)."""
        with self.lock:
            self.cmd_path = os.path.join(path, CMD_NAME)
            self.hook = None
            self.cmds = []
            self.cmd_names = {}
            self.rev += 1
            self.lock.notify_all()
        try:
            self.reset_command_file()
        except OSError as ex:
            self.log("! cannot write %s: %s" % (self.cmd_path, ex))

    def hide_wanted(self):
        if self.textbox == "always":
            return True
        if self.textbox == "never":
            return False
        return self.clock() < self.hide_until

    def maintain(self):
        """Periodic: keeps the textbox request (and its heartbeat) current.

        While hiding, the file is rewritten with a new `beat` every
        TEXTBOX_BEAT_S; the hook shows the box again if that stops, so a
        relay that dies cannot leave the game's text invisible."""
        with self.lock:
            want = self.hide_wanted()
            now = self.clock()
            if want != self.hide_sent:
                self.log("* game textbox %s" % ("hidden (console is showing "
                                                 "the dialogue)" if want
                                                 else "shown"))
            elif not want or now - self.beat_at < TEXTBOX_BEAT_S:
                return
            self.hide_sent = want
            self.beat += 1
            self.beat_at = now
            try:
                self._write_commands()
            except OSError as ex:
                self.log("! cannot write the command file: %s" % ex)

    def enqueue(self, cmd, arg=0, sel=-1):
        """Queue a command for the hook. Returns (ok, seq_or_reason)."""
        if cmd not in COMMANDS:
            return False, "badcmd"
        with self.lock:
            if not self.hook_alive():
                self._set_note("游戏未运行或钩子未加载")
                self.rev += 1
                self.lock.notify_all()
                return False, "nohook"
            now = self.clock()
            self.cmd_seq += 1
            seq = self.cmd_seq
            self.cmds = [c for c in self.cmds if now - c["t"] < CMD_TTL_S][-7:]
            self.cmds.append({"seq": seq, "cmd": cmd, "arg": int(arg),
                              "sel": int(sel), "t": now})
            self.cmd_names[seq] = cmd
            if len(self.cmd_names) > 64:
                for k in sorted(self.cmd_names)[:-64]:
                    del self.cmd_names[k]
            self._write_commands()
            return True, seq

    def snapshot(self):
        """Everything, as JSON-friendly data, for the debug console."""
        with self.lock:
            link, loc, label = self.status()
            st = self.hook or {}
            sel, opts = self.choices()
            return {
                "epoch": self.epoch, "rev": self.rev, "link": link,
                "loc": loc, "label": label, "raw_loc": st.get("loc"),
                "auto": st.get("auto"), "skip": st.get("skip"),
                "btn": st.get("btn"), "cur": self.current_line(),
                "history": self.history[-40:], "sel": sel,
                "opts": [{"idx": i, "enabled": e, "read": r, "text": t}
                         for i, e, r, t in opts],
                "note": self.note[1] if self.note and
                self.clock() - self.note[2] < NOTE_TTL_S else None,
                "hook_err": st.get("err"),
                "textbox_hidden": bool(st.get("tb")),
                "textbox_repairs": st.get("tbHealed", 0),
                "textbox_mode": self.textbox,
                "adapter": st.get("adapter"), "loader": st.get("loader"),
                "degraded": st.get("degraded"),
                "savedir": os.path.dirname(self.cmd_path) if self.cmd_path else None,
                "console": self.clock() - self.console_seen < 5.0,
            }


# ---------------------------------------------------------------- watcher ---

class Watcher(threading.Thread):
    daemon = True

    def __init__(self, path, hub, interval=0.05, locate=None):
        """path: the state file. locate: optional callable returning the
        save folder to watch now (or None to keep the current one); checked
        every couple of seconds, so a game started later is picked up."""
        super().__init__()
        self.path = path
        self.hub = hub
        self.interval = interval
        self.locate = locate
        self.last_locate = 0.0
        self.stopped = threading.Event()
        self.last_sig = None
        self.last_liveness = 0.0

    def follow(self):
        now = time.time()
        if not self.locate or now - self.last_locate < 2.0:
            return
        self.last_locate = now
        d = self.locate()
        if not d:
            return
        path = os.path.join(d, STATE_NAME)
        if self.path and os.path.normcase(path) == os.path.normcase(self.path):
            return
        self.hub.log("* following the game in %s" % d)
        self.path = path
        self.last_sig = None
        self.hub.set_savedir(d)

    def poll_once(self):
        if not self.path:
            return
        try:
            stt = os.stat(self.path)
        except FileNotFoundError:
            return
        sig = (stt.st_mtime_ns, stt.st_size)
        if sig == self.last_sig:
            return
        try:
            with open(self.path, "rb") as f:
                raw = f.read()
            st = parse_struct(decode_struct_bytes(raw))
        except (OSError, UnicodeDecodeError, StructError):
            return          # caught mid-write; the next poll sees it whole
        self.last_sig = sig
        # Liveness is judged by when the game wrote the file, not when it was
        # read: a state file left over from the last session must not make a
        # closed game look connected.
        self.hub.ingest(st, when=stt.st_mtime)

    def run(self):
        while not self.stopped.is_set():
            self.follow()
            self.poll_once()
            now = time.time()
            if now - self.last_liveness > 1.0:
                self.last_liveness = now
                self.hub.check_liveness()
            self.hub.maintain()
            self.stopped.wait(self.interval)


# ----------------------------------------------------------------- server ---

PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10")]


def address_allowed(addr, allow_any):
    if allow_any:
        return True
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return any(ip in n for n in PRIVATE_NETS)


# Host names a console or a person on the LAN reaches this PC by. A web page
# elsewhere that rebinds its own name to a LAN address still sends that name.
LAN_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal")


def host_allowed(host):
    """Whether a Host header names this PC the way a LAN client would: an IP
    address, localhost, a bare machine name, or a local-network name."""
    host = (host or "").strip().lower()
    if host.startswith("["):                 # [v6]:port
        name = host[1:host.find("]")] if "]" in host else ""
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    name = name.rstrip(".")
    return name == "localhost" or "." not in name or name.endswith(LAN_SUFFIXES)


def browser_request_allowed(headers):
    """Refuses what another web site makes a browser on this LAN send (a
    page cannot advance the game or load the quick save behind the player's
    back). The console and the debug console page are unaffected: the
    console sends none of these headers, the page sends same-origin ones."""
    if not host_allowed(headers.get("Host")):
        return False
    if headers.get("Sec-Fetch-Site", "").lower() in ("cross-site", "same-site"):
        return False
    origin = headers.get("Origin")   # "null" from sandboxed pages fails too
    if origin is not None and urllib.parse.urlparse(origin).netloc.lower() != \
            (headers.get("Host") or "").lower():
        return False
    return True


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A keep-alive client that goes away (3DS sleeping, WiFi drop) shows
        # up as a reset or timeout on the idle connection; that is normal.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def _int(params, key, default):
    try:
        return int(params.get(key, [default])[0])
    except (TypeError, ValueError):
        return default


class Handler(BaseHTTPRequestHandler):
    # keep-alive: the console polls several times a second
    protocol_version = "HTTP/1.1"
    timeout = 30
    hub = None
    verbose = False
    allow_any = False

    def log_message(self, fmt, *args):
        if self.verbose:
            print("[http] " + fmt % args, flush=True)

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not address_allowed(self.client_address[0], self.allow_any) or \
                not browser_request_allowed(self.headers):
            self._send(403, "forbidden\n")
            return
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path == "/panel.txt":
            body = self.hub.panel_text(
                client_epoch=_int(q, "epoch", None),
                client_rev=_int(q, "rev", None),
                client_hist=_int(q, "hist", 0),
                wait=_int(q, "wait", 0) / 1000.0,
                hide=_int(q, "hide", 0) == 1)
            self._send(200, body)
        elif url.path == "/cmd":
            cmd = q.get("c", [""])[0]
            ok, info = self.hub.enqueue(cmd, _int(q, "i", 0), _int(q, "sel", -1))
            if ok:
                self._send(200, "ok\t%d\n" % info)
            else:
                self._send(200 if info == "nohook" else 400, "err\t%s\n" % info)
        elif url.path == "/state.json":
            self._send(200, json.dumps(self.hub.snapshot(), ensure_ascii=False),
                       "application/json; charset=utf-8")
        elif url.path in ("/", "/index.html"):
            self._send(200, CONSOLE_HTML, "text/html; charset=utf-8")
        else:
            self._send(404, "not found\n")


CONSOLE_HTML = """<!doctype html><meta charset="utf-8">
<title>3DS 台词中继</title>
<style>
body{font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#15181c;color:#e8eaed}
main{max-width:720px;margin:0 auto;padding:16px}
#status{font-size:13px;color:#9aa3ad;margin-bottom:8px}
#status b{color:#e8eaed}
.card{background:#1e2329;border-radius:10px;padding:12px 14px;margin:10px 0}
.name{display:inline-block;background:#3a2f1e;color:#ffc65c;border-radius:4px;padding:0 8px;font-size:13px}
#hist div{color:#8f99a3;border-bottom:1px solid #262c33;padding:4px 0}
#hist .div{color:#6b7580;text-align:center;font-size:12px}
#cur{font-size:19px}
button{font:inherit;background:#2a323b;color:#e8eaed;border:1px solid #3a444f;border-radius:8px;padding:6px 14px;margin:3px;cursor:pointer}
button:disabled{opacity:.35;cursor:default}
button.on{background:#4c6a3a;border-color:#6d9454}
.opt{display:block;width:100%;text-align:left;background:#3a2530;border-color:#b0607a}
.opt.read{color:#ffc38e}
#note{color:#ffd27a;min-height:1.6em}
</style>
<main>
<div id="status">…</div>
<div class="card"><div id="hist"></div><div id="cur"></div><div id="opts"></div></div>
<div id="note"></div>
<div>
 <button data-c="click">前进</button>
 <button data-c="auto" data-bit="1">自动</button>
 <button data-c="skip" data-bit="2">快进</button>
 <button data-c="voice" data-bit="4">语音</button>
 <button data-c="qsave" data-bit="8">快存</button>
 <button data-c="qload" data-bit="16">快读</button>
</div>
<div>
 <button data-c="config" data-bit="32">设置</button>
 <button data-c="save" data-bit="64">存档</button>
 <button data-c="load" data-bit="128">读档</button>
 <button data-c="title" data-bit="256">回到标题</button>
 <button data-c="continue" data-bit="512">继续游戏</button>
 <button data-c="newgame" data-bit="1024">开始游戏</button>
</div>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => (s||"").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
let sel = -1;
function line(name, text){ return (name ? '<span class="name">'+esc(name)+'</span> ' : '') + esc(text); }
async function tick(){
  try{
    const s = await (await fetch("state.json")).json();
    $("status").innerHTML = (s.link ? "<b>游戏已连接</b>" : "<b>游戏未连接</b>")
      + (s.label ? " · "+esc(s.label) : "") + (s.auto ? " · 自动" : "") + (s.skip ? " · 快进" : "")
      + (s.hook_err ? " · 钩子错误："+esc(s.hook_err) : "")
      + (s.adapter ? " · 适配器 "+esc(s.adapter)+(s.degraded ? "（"+esc(s.degraded)+" 出错已停用）" : "") : "");
    $("hist").innerHTML = s.history.map(h => h.kind=="D"
      ? '<div class="div">—— '+esc(h.text||"")+' ——</div>' : '<div>'+line(h.name,h.text)+'</div>').join("");
    $("cur").innerHTML = s.cur ? line(s.cur[0], s.cur[1]) : "";
    sel = s.sel == null ? -1 : s.sel;
    $("opts").innerHTML = s.opts.map(o => '<button class="opt'+(o.read?' read':'')+'" '+(o.enabled?'':'disabled')
      +' onclick="send(\\'choose\\','+o.idx+')">'+esc(o.text)+'</button>').join("");
    $("note").textContent = s.note || "";
    document.querySelectorAll("button[data-c]").forEach(b => {
      const bit = +(b.dataset.bit||0);
      b.disabled = !s.link || (bit && !(s.btn & bit));
      b.classList.toggle("on", (b.dataset.c=="auto" && !!s.auto) || (b.dataset.c=="skip" && !!s.skip));
    });
  }catch(e){ $("status").textContent = "中继无响应"; }
}
async function send(c, i){ await fetch("cmd?c="+c+"&i="+(i||0)+"&sel="+sel); tick(); }
document.querySelectorAll("button[data-c]").forEach(b => b.onclick = () => {
  if (b.dataset.c == "qload" && !confirm("读取快速存档？当前进度会丢失。")) return;
  if (b.dataset.c == "title" && !confirm("回到标题？")) return;
  send(b.dataset.c);
});
tick(); setInterval(tick, 400);
</script>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--savedir",
                    help="the game's save-data folder (holds the hook files); "
                    "default: follow the running game")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--allow", choices=("private", "any"), default="private",
                    help="which client addresses to answer (default: LAN only)")
    ap.add_argument("--textbox", choices=("auto", "never", "always"),
                    default="auto",
                    help="hide the game's own textbox on the PC: while a 3DS "
                    "shows the dialogue (auto, default), never, or always")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    args = ap.parse_args()

    locate = None
    savedir = args.savedir
    if not savedir:
        def locate():
            ses = read_session()
            return ses["datapath"] if ses and os.path.isdir(ses["datapath"]) else None
        savedir = locate()
        if not savedir:
            # a hook installed without the tpm writes no session file
            savedir = next((d for d in profile_savedirs() if os.path.isdir(d)), None)
    if savedir and not os.path.isdir(savedir):
        print("save folder %s does not exist yet - start the game once" % savedir)

    hub = Hub(cmd_path=os.path.join(savedir, CMD_NAME) if savedir else None)
    hub.textbox = args.textbox
    try:
        hub.reset_command_file()
    except OSError as ex:
        print("! cannot write %s: %s" % (hub.cmd_path, ex))

    watcher = Watcher(os.path.join(savedir, STATE_NAME) if savedir else None,
                      hub, locate=locate)
    watcher.start()

    Handler.hub = hub
    Handler.verbose = args.verbose
    Handler.allow_any = args.allow == "any"
    httpd = Server((args.host, args.port), Handler)
    if savedir:
        print("watching  %s" % os.path.join(savedir, STATE_NAME))
    else:
        print("no game found yet - waiting for one to start (%s)" % SESSION_FILE)
    if locate:
        print("following whichever game runs with the kiripanel plugin")
    print("serving   http://%s:%d/   (debug console at /, 3DS polls /panel.txt)"
          % (args.host, args.port))
    if args.allow == "private":
        print("answering loopback and private-network clients only")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stopped.set()
        httpd.server_close()


if __name__ == "__main__":
    main()
