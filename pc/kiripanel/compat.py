"""
Compatibility check: does the panel work with this game, and how well?

Starts the game once with an EMPTY temporary save folder (-datapath, so the
player's saves are never touched), with the plugin installed for the
duration if it was not, and watches what the hook reports. On the way it
taps through logos and caution screens with the panel's own "click"
command, until the title menu or a time limit. Then the game is closed and
everything the check added is removed again.

Result tiers:
  ready      a full adapter knows this game: dialogue, choices, auto/skip,
             saves and the game's menus from the console
  basic      only the generic KAG3 adapter: dialogue and the speaker's
             name, [link] choices, advance, auto/skip, back to title; the
             game's own title menu and its save/load/settings screens come
             through where the game is built the way the KAG3 samples are
  adapter    the plugin runs in the game, but no adapter understands it
             (a scout report helps to write one)
  launcher   the exe needs a launcher that ignores -datapath; start the
             game the normal way with the plugin installed to see it work
  no         the plugin could not run in this game (reason given)
"""
import os
import shutil
import subprocess
import tempfile
import threading
import time

from . import install, relay, winproc

LOG = os.path.expandvars(r"%LOCALAPPDATA%\kiripanel\kiripanel.log")
FULL = {"koihazi", "kagex"}
# message boxes of an engine exe that refuses to run without its launcher
LAUNCHER_WORDS = ("launcher", "ランチャー", "ランチャ", "启动器", "啟動器", "起動ツール")
# exes next to a game that are never its launcher
NOT_LAUNCHERS = ("unins", "setup", "config", "update", "vcredist", "dxsetup", "设置", "設定")
FEATURES = {
    "full": ["台词", "选项", "前进", "自动", "快进", "快存/快读", "存读档界面", "设置", "回到标题", "隐藏电脑上的对话框"],
    "kag3": ["台词与说话人", "链接选项", "前进", "自动", "快进", "回到标题",
             "隐藏电脑上的对话框", "游戏自带菜单（照搬画面）"],
}


def log_lines(pids, since):
    """kiripanel.log lines written by any of `pids` since `since` (epoch s)."""
    out = []
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-400:]
    except OSError:
        return out
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since - 1))
    for ln in lines:
        if ln[:19] < stamp or "[" not in ln:
            continue
        try:
            pid = int(ln[ln.index("[") + 1:ln.index("]")])
        except ValueError:
            continue
        if pid in pids:
            out.append(ln[ln.index("]") + 2:].rstrip())
    return out


class Check(threading.Thread):
    """One compatibility check, run in the background. Read `snapshot()`
    for progress; `cancel()` to stop early."""
    daemon = True

    def __init__(self, game, exe=None, hook_wait=45, title_wait=60):
        super().__init__()
        self.game = game                    # a games.describe() record
        self.exe = exe or game["exe"]
        self.hook_wait = hook_wait
        self.title_wait = title_wait
        self.lock = threading.Lock()
        self.steps = []                     # (time, text)
        self.status = "running"
        self.result = None
        self.dialog = None                  # (title, text, buttons) shown now
        self.stop = threading.Event()
        self.proc = None
        self.pids = set()
        self.seen = set()                   # every pid the game ever had
        self.save_backup = None             # copy of the player's savedata
        self.real_saves_used = False

    # ---- progress ----------------------------------------------------------

    def step(self, text):
        with self.lock:
            self.steps.append((time.time(), text))

    def snapshot(self):
        with self.lock:
            return {"path": self.game["path"], "status": self.status,
                    "steps": [t for _, t in self.steps], "result": self.result,
                    "dialog": self.dialog}

    def cancel(self):
        self.stop.set()

    # ---- the check ---------------------------------------------------------

    def run(self):
        added_install = False
        data = None
        try:
            folder = self.game["path"]
            st = install.proxy_name(folder)
            if st is None:
                self.finish("no", "游戏自带的 version.dll 和 mpr.dll 都已占用，插件没有可用的加载方式。")
                return
            if not os.path.exists(os.path.join(folder, st)):
                self.step("临时安装插件（检测结束后移除）")
                problems = install.apply(folder, arch=self.game.get("arch"), log=lambda m: None)
                if problems:
                    self.finish("no", "安装失败：有文件属于游戏本身，不能覆盖。")
                    return
                added_install = True
            data = tempfile.mkdtemp(prefix="kiripanel_check_")
            self.run_game(data)
        except Exception as ex:   # never leave a half-finished check behind
            self.finish("no", "检测出错：%s: %s" % (type(ex).__name__, ex))
        finally:
            if self.proc is not None:
                self.refresh()
                for pid in self.seen | {self.proc.pid}:
                    winproc.kill_tree(pid)
                time.sleep(0.8)
            if added_install:
                install.apply(self.game["path"], uninstall=True, log=lambda m: None)
                self.step("已移除临时安装的插件")
            if data:
                shutil.rmtree(data, ignore_errors=True)
            with self.lock:
                if self.status == "running":
                    self.status = "done"

    def finish(self, tier, why, **extra):
        r = {"tier": tier, "why": why, "when": time.time(), "exe": self.exe}
        r.update(extra)
        with self.lock:
            self.result = r
            self.status = "done"

    # ---- the player's own save folder ---------------------------------------
    #
    # A launcher may start the game without passing -datapath on, so the game
    # writes into the player's real save folder. The usual one (savedata next
    # to the exe) is copied first and put back exactly afterwards; while the
    # game uses it, the check only watches and sends nothing.

    def backup_saves(self):
        src = os.path.join(self.game["path"], "savedata")
        if not os.path.isdir(src):
            return None
        dst = tempfile.mkdtemp(prefix="kiripanel_savebackup_")
        shutil.copytree(src, os.path.join(dst, "savedata"))
        return dst

    def restore_saves(self, backup):
        src = os.path.join(backup, "savedata")
        dst = os.path.join(self.game["path"], "savedata")
        shutil.copytree(src, dst, dirs_exist_ok=True)
        keep = {os.path.relpath(os.path.join(dp, f), src).lower()
                for dp, _, fs in os.walk(src) for f in fs}
        for dp, _, fs in os.walk(dst):
            for f in fs:
                p = os.path.join(dp, f)
                if os.path.relpath(p, dst).lower() not in keep:
                    os.remove(p)
        # verify before throwing the copy away
        for dp, _, fs in os.walk(src):
            for f in fs:
                a = os.path.join(dp, f)
                b = os.path.join(dst, os.path.relpath(a, src))
                with open(a, "rb") as x, open(b, "rb") as y:
                    if x.read() != y.read():
                        raise OSError("存档目录没能完全恢复，备份保留在 %s" % backup)
        shutil.rmtree(backup, ignore_errors=True)

    def run_game(self, data):
        exe = os.path.join(self.game["path"], self.exe)
        self.step("启动 %s（使用空的临时存档目录）" % self.exe)
        t0 = time.time()
        self.save_backup = self.backup_saves()
        self.proc = subprocess.Popen([exe, "-datapath=" + data.rstrip("\\/") + "\\"],
                                     cwd=self.game["path"])
        hub = relay.Hub(cmd_path=os.path.join(data, relay.CMD_NAME))
        hub.log = lambda m: None
        hub.textbox = "never"
        hub.reset_command_file()
        watcher = relay.Watcher(os.path.join(data, relay.STATE_NAME), hub)
        watcher.start()
        try:
            self.watch(hub, data, t0)
        finally:
            watcher.stopped.set()
            if self.save_backup:
                if self.real_saves_used:
                    for pid in self.seen | {self.proc.pid}:
                        winproc.kill_tree(pid)
                    time.sleep(1.5)
                    self.restore_saves(self.save_backup)
                    self.step("游戏写过你的存档目录；已恢复成检测前的样子")
                else:
                    shutil.rmtree(self.save_backup, ignore_errors=True)

    def refresh(self):
        """Processes of the game (a launcher may have started it) and any
        message box they show."""
        if self.proc is not None:
            # every process seen so far: a launcher may exit after starting
            # the game, which then has no living parent to be found by
            self.pids = (self.pids | winproc.tree(self.proc.pid)) & set(winproc.processes())
            self.seen |= self.pids
        boxes = winproc.dialogs(self.pids) if self.pids else []
        with self.lock:
            self.dialog = boxes[0] if boxes else None
        return boxes

    def hook(self, hub):
        with hub.lock:
            return dict(hub.hook) if hub.hook_alive() and hub.hook else None

    def watch(self, hub, data, t0):
        # 1. the hook comes up
        seen_box = None
        end = t0 + self.hook_wait
        st = None
        while time.time() < end and not self.stop.is_set():
            boxes = self.refresh()
            if boxes and boxes[0] != seen_box:
                seen_box = boxes[0]
                if self.wants_launcher(boxes[0][1]):
                    self.step("游戏提示：%s" % boxes[0][1].replace("\n", " ")[:80])
                    self.finish("launcher", self.launcher_hint())
                    return
                self.step("游戏弹出了对话框：%s（请在游戏窗口里回答）" % boxes[0][1].replace("\n", " ")[:80])
                end = max(end, time.time() + 30)     # the player needs time
            st = self.hook(hub)
            if st:
                break
            if self.proc.poll() is not None and not (self.pids - {self.proc.pid}):
                break
            time.sleep(0.3)
        if self.stop.is_set():
            self.finish("no", "已取消。")
            return
        lines = log_lines(self.pids | {self.proc.pid}, t0)
        if not st:
            other = self.elsewhere(data)
            if other:
                self.watch_elsewhere(other, t0)
                return
            self.diagnose(lines, data)
            return
        self.step("插件已在游戏中运行（加载方式：%s）" % st.get("loader"))

        # 2. an adapter is chosen (game objects appear a little later)
        end = time.time() + 15
        while time.time() < end and st.get("adapter") in (None, "none") and not st.get("degraded"):
            time.sleep(0.3)
            st = self.hook(hub) or st
        adapter = st.get("adapter")
        if st.get("degraded"):
            self.finish("adapter", "适配器 %s 在这个游戏里出错，已自动停用：%s"
                        % (st["degraded"], st.get("err", "")), adapter="none", loader=st.get("loader"))
            return
        if adapter in (None, "none"):
            self.finish("adapter", "插件能运行，但还没有适配器认识这个游戏。可以导出诊断报告，用来编写适配器。",
                        adapter="none", loader=st.get("loader"))
            return
        self.step("识别为 %s" % adapter)

        # 3. through logos to the title menu, as a player would tap
        title_since = None
        end = time.time() + self.title_wait
        last_click = 0
        tick = time.time()
        while time.time() < end and not self.stop.is_set():
            boxes = self.refresh()
            now = time.time()
            if boxes or st.get("dlg"):
                end += now - tick          # time the player spends answering does not count
                if boxes and boxes[0] != seen_box:
                    seen_box = boxes[0]
                    self.step("游戏弹出了对话框：%s（请在游戏窗口里回答）" % boxes[0][1].replace("\n", " ")[:80])
            tick = now
            st = self.hook(hub) or st
            if st.get("loc") == "title":
                # give the menu a moment to fade in before judging it
                title_since = title_since or time.time()
                if int(st.get("btn") or 0) & 1024 or time.time() - title_since > 5:
                    break
                time.sleep(0.3)
                continue
            title_since = None
            if st.get("degraded"):
                break
            if self.dialog is None and not st.get("dlg") and time.time() - last_click > 1.5 \
                    and st.get("loc") in ("menu", "play"):
                hub.enqueue("click")
                last_click = time.time()
            time.sleep(0.3)
        if self.stop.is_set():
            self.finish("no", "已取消。")
            return
        if st.get("degraded"):
            self.finish("adapter", "适配器 %s 在这个游戏里出错，已自动停用：%s"
                        % (st["degraded"], st.get("err", "")), adapter="none", loader=st.get("loader"))
            return
        menu = bool(int(st.get("btn") or 0) & 1024)
        title = st.get("loc") == "title"
        extra = {"adapter": adapter, "loader": st.get("loader"), "title_reached": title,
                 "title_menu": menu, "err": st.get("err", "")}
        if title:
            self.step("到达标题画面" + ("，面板可以开始游戏" if menu else "（标题菜单需要在触屏镜像模式下操作）"))
        if adapter in FULL:
            if title and menu:
                why = "完整支持。"
            elif title:
                why = "完整支持；这款游戏的标题菜单要用鼠标悬停才出现，在 3DS 上请用触屏镜像模式点开始。"
            elif st.get("loc") in ("config", "menu") and st.get("rawLoc"):
                why = ("完整适配器已识别。首次启动停在了游戏自己的「%s」画面（例如语言选择），"
                       "第一次玩时在电脑上点一下即可。" % st.get("rawLoc"))
            else:
                why = "完整适配器已识别；%d 秒内没有到达标题画面（片头可能较长），不影响使用。" % self.title_wait
            self.finish("ready", why, features=FEATURES["full"], **extra)
        else:
            self.finish("basic", "通用 KAG3 支持：台词、说话人、选项、自动、快进、"
                        "回到标题可用；标题菜单和游戏自带的存档/读档/设置画面"
                        "按游戏自身情况提供；快存需要专用适配器。",
                        features=FEATURES["kag3"], **extra)

    def wants_launcher(self, text):
        """A message box saying the engine exe must be started by its
        launcher (only meaningful when the check started the engine)."""
        if self.exe not in dict(self.game.get("engines", [])):
            return False
        t = text.lower()
        return any(k in t for k in LAUNCHER_WORDS)

    def launcher_hint(self):
        names = [n for n in self.game.get("launchers", [])
                 if not any(k in n.lower() for k in NOT_LAUNCHERS)]
        why = "%s 不能直接运行，需要用游戏的启动器打开。" % self.exe
        if names:
            why += "请在「启动用」里选启动器（可能是 %s）再检测一次。" % "、".join(names[:4])
        else:
            why += "请在「启动用」里选启动器再检测一次。"
        return why

    def elsewhere(self, data):
        """Save folder the game uses if it is not the temporary one (a
        launcher that drops -datapath), else None."""
        ses = relay.read_session()
        if not ses or not ses.get("pid", "").isdigit() or int(ses["pid"]) not in self.seen:
            return None
        d = ses.get("datapath", "")
        if os.path.normcase(d.rstrip("\\/")) == os.path.normcase(data.rstrip("\\/")):
            return None
        return d

    def watch_elsewhere(self, savedir, t0):
        """The game runs on the player's real save folder: only read what
        the hook reports there (no command is sent), then stop."""
        self.real_saves_used = True
        self.step("这个程序没有使用临时存档目录（通常是启动器），改为只观察、不操作游戏")
        path = os.path.join(savedir, relay.STATE_NAME)
        st, end = None, time.time() + 25
        while time.time() < end and not self.stop.is_set():
            self.refresh()
            try:
                with open(path, "rb") as f:
                    st = relay.parse_struct(relay.decode_struct_bytes(f.read()))
            except (OSError, relay.StructError):
                pass
            if st and (st.get("loc") == "title" or st.get("degraded")):
                break
            time.sleep(0.5)
        if not self.save_backup:
            note = "（存档不在游戏目录的 savedata 里，没法自动备份；它只被正常读写过一次启动。）"
        else:
            note = ""
        if not st:
            self.finish("launcher", "经启动器运行时插件没有报告状态。" + note)
            return
        adapter = st.get("adapter")
        extra = {"adapter": adapter, "loader": st.get("loader"), "via_launcher": True,
                 "title_reached": st.get("loc") == "title"}
        if st.get("degraded") or adapter in (None, "none"):
            self.finish("adapter", "插件能运行（经启动器），但还没有适配器认识这个游戏。" + note, **extra)
        elif adapter in FULL:
            self.finish("ready", "完整支持（经启动器启动）。" + note, features=FEATURES["full"], **extra)
        else:
            self.finish("basic", "通用 KAG3 支持（经启动器启动）：台词、说话人、选项、"
                        "自动、快进、回到标题可用。" + note, features=FEATURES["kag3"], **extra)
        self.step("识别为 %s" % adapter)

    def diagnose(self, lines, data):
        """The hook never wrote its state; say why, from the plugin's log."""
        text = "\n".join(lines)
        if "hook installed" in text and not os.path.exists(os.path.join(data, relay.STATE_NAME)):
            self.finish("launcher", "这个程序没有使用检测用的临时存档目录（通常是启动器）。"
                        "插件装好后用平常的方式启动游戏，管理器会显示它是否连上。")
            return
        if "linked no plugin" in text or ("loaded into" in text and "first plugin" not in text):
            self.finish("no", "游戏没有加载任何插件，插件无法挂上。")
            return
        if "lacks" in text:
            self.finish("no", "这个引擎版本缺少插件需要的函数。")
            return
        if "no KAG window" in text:
            self.finish("no", "游戏不是基于 KAG 的（找不到 KAG 窗口）。")
            return
        if "hook failed" in text or "could not load" in text:
            why = next((ln for ln in lines if "failed" in ln or "could not" in ln), "")
            self.finish("no", "插件脚本出错：%s" % why)
            return
        if not lines:
            if self.proc.poll() is not None and not (self.pids - {self.proc.pid}):
                self.finish("launcher", "程序很快就退出了，也没有加载插件——它可能只是启动器，或者需要特定的启动方式。")
            else:
                self.finish("no", "插件没有被加载（这个程序可能加了壳或不导入 version.dll / mpr.dll）。")
            return
        self.finish("no", "%d 秒内插件没有开始工作。最后的日志：%s" % (self.hook_wait, lines[-1]))
