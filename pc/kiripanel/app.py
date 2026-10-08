"""
KiriPanel manager: the PC side of the 3DS dialogue panel in one program.

  - the relay the 3DS talks to (relay.py), following whichever game runs
    with the plugin, on the LAN port the console uses (8787);
  - a window (a local page, shown as an app window by Edge) to add games,
    install / remove the plugin with one click, check how well a game is
    supported, and see what the console sees.

The window's page is served on 127.0.0.1 only, on a random port, and every
API call must carry a token that only the page knows (in its URL), so
other web pages and other machines cannot drive the manager.

    KiriPanel.exe            (or: python -m kiripanel.app)
    KiriPanel.exe --browser  use the default browser instead of an app window
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import compat, games, install, relay, report, winproc

VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.expandvars(r"%LOCALAPPDATA%\kiripanel")
SETTINGS = os.path.join(DATA_DIR, "settings.json")
INSTANCE = os.path.join(DATA_DIR, "manager.json")
REPORTS = os.path.join(DATA_DIR, "reports")
EDGE = [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"]


def ui_html():
    with open(os.path.join(HERE, "ui.html"), encoding="utf-8") as f:
        return f.read()


class Manager:
    def __init__(self, relay_port=8787):
        self.library = games.Library()
        self.settings = self.load_settings()
        self.relay_port = relay_port
        self.relay_error = ""
        self.lock = threading.Lock()
        self.check = None                 # the running (or last) compat.Check
        self.scan_state = None            # {"root", "running", "found"}
        self.states = {}                  # path -> (time, install state)
        self.task = None                  # {"path", "kind", "log", "done", "ok"}
        self.saved_session = None
        self.stop = threading.Event()
        self.last_ping = time.time()
        self.bye_at = 0.0                 # when the page said it is closing

        self.hub = relay.Hub()
        self.hub.textbox = self.settings.get("textbox", "auto")
        self.hub.log = lambda m: None
        self.watcher = relay.Watcher(None, self.hub, locate=self.locate)
        self.watcher.last_locate = -10    # look for a running game at once
        self.watcher.start()
        self.relay_httpd = None
        try:
            relay.Handler.hub = self.hub
            self.relay_httpd = relay.Server(("0.0.0.0", relay_port), relay.Handler)
            threading.Thread(target=self.relay_httpd.serve_forever, daemon=True).start()
        except OSError as ex:
            self.relay_error = "端口 %d 被占用（是不是已经开着一个 relay.py？）：%s" % (relay_port, ex)

    # ---- settings ----------------------------------------------------------

    def load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, ensure_ascii=False, indent=1)

    # ---- the relay follows the running game -----------------------------

    def locate(self):
        """Save folder of the game the relay should follow, from the
        plugin's session file; nothing while a compatibility check runs its
        own copy of a game."""
        if self.check is not None and self.check.is_alive():
            return None
        ses = relay.read_session()
        if not ses:
            return None
        pid = ses.get("pid", "")
        if pid.isdigit() and not winproc.alive(int(pid)):
            return None
        d = ses.get("datapath")
        return d if d and os.path.isdir(d) else None

    def connected_game(self):
        """Library entry of the game the relay follows now, or None."""
        ses = relay.read_session()
        if not ses or not ses.get("exe"):
            return None
        folder = os.path.dirname(ses["exe"])
        g = self.library.get(folder)
        return g or {"path": folder, "name": games.display_name(folder),
                     "exe": os.path.basename(ses["exe"]), "unlisted": True}

    # ---- overview ------------------------------------------------------------

    def install_state(self, g, fresh=False):
        key = os.path.normcase(g["path"])
        now = time.time()
        with self.lock:
            hit = self.states.get(key)
        if hit and not fresh and now - hit[0] < 20:
            return hit[1]
        st = games.install_state(g["path"], g.get("arch", "x86"))
        st["exists"] = os.path.isdir(g["path"])
        with self.lock:
            self.states[key] = (now, st)
        return st

    def overview(self):
        snap = self.hub.snapshot()
        live = None
        if snap["link"]:
            cg = self.connected_game()
            live = {"game": cg, "snap": snap}
        out = {
            "version": VERSION,
            "relay": {"port": self.relay_port, "error": self.relay_error,
                      "addresses": winproc.lan_addresses(),
                      "console": snap.get("console"), "textbox": self.hub.textbox},
            "live": live,
            "games": [],
            "check": self.check.snapshot() if self.check else None,
            "scan": dict(self.scan_state) if self.scan_state else None,
            "task": dict(self.task, log=list(self.task["log"])) if self.task else None,
        }
        for g in self.library.all():
            g["install"] = self.install_state(g)
            out["games"].append(g)
        return out

    # ---- actions -------------------------------------------------------------

    def add_folder(self, path):
        if not path or not os.path.isdir(path):
            return {"error": "文件夹不存在"}
        g = games.describe(path)
        if g:
            return {"added": [self.library.add(g)["path"]]}
        # a folder of games: look one or two levels down
        found = games.scan(path, depth=2)
        if not found:
            return {"error": "这个文件夹里没有找到吉里吉里（KiriKiri）游戏。"}
        return {"added": [self.library.add(x)["path"] for x in found]}

    def start_scan(self, root):
        if self.scan_state and self.scan_state.get("running"):
            return {"error": "正在扫描"}
        self.scan_state = {"root": root, "running": True, "found": 0}

        def work():
            try:
                for g in games.scan(root, depth=3):
                    self.library.add(g)
                    self.scan_state["found"] += 1
            finally:
                self.scan_state["running"] = False
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def game(self, path):
        g = self.library.get(path)
        if not g:
            raise KeyError("这个游戏不在列表里")
        return g

    def busy(self):
        return (self.check is not None and self.check.is_alive()) or \
            (self.task is not None and not self.task["done"])

    def run_task(self, path, kind, fn):
        if self.busy():
            return {"error": "另一个操作还没结束"}
        self.task = {"path": path, "kind": kind, "log": [], "done": False, "ok": False}

        def work():
            try:
                self.task["ok"] = fn(self.task["log"].append)
            except SystemExit as ex:
                self.task["log"].append(str(ex))
            except Exception as ex:
                self.task["log"].append("出错：%s: %s" % (type(ex).__name__, ex))
            finally:
                g = self.library.get(path)
                if g:
                    self.install_state(g, fresh=True)
                self.task["done"] = True
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def do_install(self, path, uninstall=False):
        g = self.game(path)
        running = self.running_pids(g)
        if running:
            return {"error": "游戏正在运行，请先关闭游戏再%s。" % ("卸载" if uninstall else "安装")}
        return self.run_task(g["path"], "uninstall" if uninstall else "install",
                             lambda log: install.apply(g["path"], uninstall=uninstall,
                                                       arch=g.get("arch"), log=log) == 0)

    def running_pids(self, g):
        """Processes started from the game's folder (the game is running)."""
        exes = {n.lower() for n, _ in g.get("engines", [])} | \
            {n.lower() for n in g.get("launchers", [])}
        out = []
        for pid, (_, name) in winproc.processes().items():
            if name.lower() in exes:
                out.append(pid)
        return out

    def do_check(self, path, exe=None):
        g = self.game(path)
        if self.busy():
            return {"error": "另一个操作还没结束"}
        if self.running_pids(g):
            return {"error": "游戏正在运行，请先关闭游戏再检测。"}
        # the program the player starts the game with, if they chose one
        c = compat.Check(g, exe=exe or g.get("launch") or g["exe"])
        self.check = c

        def done():
            c.join()
            r = c.snapshot()["result"]
            if r:
                self.library.update(g["path"], check=r)
            self.install_state(g, fresh=True)
        c.start()
        threading.Thread(target=done, daemon=True).start()
        return {"ok": True}

    def do_launch(self, path, exe=None):
        g = self.game(path)
        exe = exe or g.get("launch") or g["exe"]
        p = os.path.join(g["path"], exe)
        if not os.path.isfile(p):
            return {"error": "找不到 %s" % exe}
        subprocess.Popen([p], cwd=g["path"])
        return {"ok": True}

    def do_report(self):
        """Scout report of the game that is connected now."""
        cg = self.connected_game()
        if not cg or not self.hub.hook_alive():
            return {"error": "先启动装好插件的游戏，等它连上再导出。"}
        ok, seq = self.hub.enqueue("scout")
        if not ok:
            return {"error": "游戏没有响应"}
        savedir = os.path.dirname(self.hub.cmd_path)
        src = os.path.join(savedir, "_hkscout.ksd")
        end = time.time() + 30
        while time.time() < end:
            st = self.hub.hook or {}
            acks = dict((a[0], a[1]) for a in st.get("acks") or [] if isinstance(a, list))
            if acks.get(seq):
                if acks[seq] != "scouted" or not os.path.exists(src):
                    return {"error": "游戏无法生成报告：%s" % acks[seq]}
                with open(src, "rb") as f:
                    rep = relay.parse_struct(relay.decode_struct_bytes(f.read()))
                os.makedirs(REPORTS, exist_ok=True)
                name = "".join(c for c in cg["name"] if c not in '\\/:*?"<>|')[:60]
                out = os.path.join(REPORTS, "%s-%s.txt" % (name, time.strftime("%Y%m%d-%H%M%S")))
                with open(out, "w", encoding="utf-8") as f:
                    f.write(report.render(rep))
                try:
                    os.remove(src)
                except OSError:
                    pass
                return {"ok": True, "file": out}
            time.sleep(0.2)
        return {"error": "等待报告超时"}

    def set_textbox(self, mode):
        if mode not in ("auto", "never", "always"):
            return {"error": "bad mode"}
        self.hub.textbox = mode
        self.settings["textbox"] = mode
        self.save_settings()
        return {"ok": True}

    def set_exe(self, path, exe, launch=None):
        g = self.game(path)
        names = dict(g["engines"])
        if exe not in names:
            return {"error": "不是这个游戏的引擎程序"}
        fields = {"exe": exe, "arch": names[exe], "exe_chosen": True}
        if launch is not None:
            if launch and launch not in g.get("launchers", []) and launch not in names:
                return {"error": "找不到这个程序"}
            fields["launch"] = launch
        self.library.update(path, **fields)
        return {"ok": True}

    def pick_folder(self):
        """A native folder dialog (the page cannot give us a path)."""
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            d = filedialog.askdirectory(title="选择游戏文件夹（或放游戏的总文件夹）", mustexist=True)
        finally:
            root.destroy()
        return os.path.normpath(d) if d else None


# ------------------------------------------------------------ web side ----

class UIHandler(BaseHTTPRequestHandler):
    manager = None
    token = ""
    port = 0

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj, ensure_ascii=False))

    def allowed_host(self):
        # a page on another site that resolves its name to 127.0.0.1 (DNS
        # rebinding) still sends its own name here
        return self.headers.get("Host", "") in ("127.0.0.1:%d" % self.port,
                                                "localhost:%d" % self.port)

    def do_GET(self):
        if not self.allowed_host():
            return self.send(403, "forbidden", "text/plain")
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path in ("/favicon.ico", "/icon.png"):
            try:
                with open(os.path.join(HERE, "icon.png"), "rb") as f:
                    return self.send(200, f.read(), "image/png")
            except OSError:
                return self.send(204, b"", "image/png")
        if url.path == "/":
            if q.get("t", [""])[0] != self.token:
                return self.send(403, "请从 KiriPanel 打开这个页面。", "text/plain; charset=utf-8")
            html = ui_html().replace("__TOKEN__", self.token)
            return self.send(200, html, "text/html; charset=utf-8")
        if not secrets.compare_digest(self.headers.get("X-Token", ""), self.token):
            return self.send(403, "forbidden", "text/plain")
        if url.path == "/api/overview":
            self.manager.bye_at = 0
            self.manager.last_ping = time.time()
            return self.json(self.manager.overview())
        self.send(404, "not found", "text/plain")

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/bye":
            # the page is closing (sendBeacon cannot set headers, so the
            # token comes in the query); only ever ends the manager
            q = urllib.parse.parse_qs(url.query)
            if self.allowed_host() and secrets.compare_digest(q.get("t", [""])[0], self.token):
                self.manager.bye_at = time.time()
            return self.send(204, b"", "text/plain")
        if not self.allowed_host() or \
                not secrets.compare_digest(self.headers.get("X-Token", ""), self.token):
            return self.send(403, "forbidden", "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
        except ValueError:
            return self.json({"error": "bad request"}, 400)
        m = self.manager
        path = body.get("path", "")
        route = urllib.parse.urlparse(self.path).path
        try:
            if route == "/api/pick":
                d = m.pick_folder()
                r = m.add_folder(d) if d else {"cancelled": True}
            elif route == "/api/add":
                r = m.add_folder(path)
            elif route == "/api/scan":
                r = m.start_scan(path)
            elif route == "/api/remove":
                m.library.remove(path)
                r = {"ok": True}
            elif route == "/api/install":
                r = m.do_install(path)
            elif route == "/api/uninstall":
                r = m.do_install(path, uninstall=True)
            elif route == "/api/check":
                r = m.do_check(path, body.get("exe"))
            elif route == "/api/check/cancel":
                if m.check:
                    m.check.cancel()
                r = {"ok": True}
            elif route == "/api/check/front":
                r = {"ok": bool(m.check and winproc.bring_to_front(m.check.pids))}
            elif route == "/api/launch":
                r = m.do_launch(path, body.get("exe"))
            elif route == "/api/exe":
                r = m.set_exe(path, body.get("exe"), body.get("launch"))
            elif route == "/api/open":
                p = body.get("file") or path
                if p and os.path.exists(p):
                    if os.path.isdir(p):
                        os.startfile(p)
                    else:
                        subprocess.Popen(["explorer", "/select,", p])
                    r = {"ok": True}
                else:
                    r = {"error": "不存在"}
            elif route == "/api/report":
                r = m.do_report()
            elif route == "/api/textbox":
                r = m.set_textbox(body.get("mode"))
            elif route == "/api/quit":
                m.stop.set()
                r = {"ok": True}
            else:
                return self.send(404, "not found", "text/plain")
        except KeyError as ex:
            r = {"error": str(ex).strip("'")}
        self.json(r)


def find_edge():
    for p in EDGE:
        p = os.path.expandvars(p)
        if os.path.isfile(p):
            return p
    return None


def open_window(url, use_browser):
    """Shows the page. Returns the window's process when it is an app
    window of our own (closing it ends the manager), else None."""
    edge = None if use_browser else find_edge()
    if edge:
        profile = os.path.join(DATA_DIR, "window")
        return subprocess.Popen([edge, "--app=" + url, "--user-data-dir=" + profile,
                                 "--window-size=1180,780", "--no-first-run",
                                 "--disable-extensions", "--no-default-browser-check"])
    os.startfile(url)
    return None


def existing_instance():
    """URL of a manager that is already running, or None."""
    try:
        with open(INSTANCE, encoding="utf-8") as f:
            info = json.load(f)
        if winproc.alive(int(info["pid"])):
            return info["url"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def main():
    # the packaged exe has no console; give stray prints somewhere to go
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    ap = argparse.ArgumentParser(description="KiriPanel manager")
    ap.add_argument("--browser", action="store_true", help="open in the default browser")
    ap.add_argument("--relay-port", type=int, default=8787)
    ap.add_argument("--no-window", action="store_true",
                    help="only print the page's address (development)")
    a = ap.parse_args()

    other = existing_instance()
    if other:
        if a.no_window:
            print(other, flush=True)
        else:
            open_window(other, a.browser)
        return 0

    m = Manager(a.relay_port)
    UIHandler.manager = m
    UIHandler.token = secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), UIHandler)
    httpd.daemon_threads = True
    UIHandler.port = httpd.server_address[1]
    url = "http://127.0.0.1:%d/?t=%s" % (UIHandler.port, UIHandler.token)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(INSTANCE, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "url": url}, f)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    if a.no_window:
        print(url, flush=True)
    else:
        open_window(url, a.browser)
    try:
        while not m.stop.is_set():
            now = time.time()
            # The window closed (a reload says goodbye too, but asks again
            # at once); or no page has asked for anything in 5 minutes.
            if m.bye_at and now - m.bye_at > 4:
                break
            if now - m.last_ping > 300:
                break
            m.stop.wait(1)
    except KeyboardInterrupt:
        pass
    finally:
        if m.check and m.check.is_alive():
            m.check.cancel()
            m.check.join(15)
        httpd.shutdown()
        if m.relay_httpd:
            m.relay_httpd.shutdown()
        try:
            os.remove(INSTANCE)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
