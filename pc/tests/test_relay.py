#!/usr/bin/env python3
"""Tests for relay.py.  Run:  python -m unittest test_relay -v"""
import os
import tempfile
import unittest

import sys

# pc/ on the path, so the tests run from the repo root or from pc/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from kiripanel import relay as R  # noqa: E402

# Written by the game's own Dictionary.saveStruct (from an earlier hook run),
# so the parser is checked against real engine output, not just its own.
REAL_KSD = ('﻿(const) %[\r\n "calls" => 1496,\r\n "log" => "history\\t\\n'
            'ch\\t伊织\\neval\\t\\nch\\t「\\n"\r\n]\r\n')


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def state(**kw):
    st = {"proto": 3, "boot": 1, "rev": 1, "adapter": "koihazi",
          "loader": "tpm", "degraded": "", "loc": "play", "dlg": 0,
          "auto": 0, "skip": 0, "btn": 31, "gen": 1, "lines": [], "cur": "",
          "curName": "", "sel": -1, "opts": [], "acks": [], "cmdEpoch": -1,
          "err": ""}
    st.update(kw)
    return st


def parse_panel(text):
    """Minimal reader mirroring the console's parser, for assertions."""
    out = {"h": [], "opt": [], "flags": set()}
    for line in text.splitlines():
        f = line.split("\t")
        if f[0] in ("same", "hreset", "end"):
            out["flags"].add(f[0])
        elif f[0] == "h":
            out["h"].append(f[1:])
        elif f[0] == "opt":
            out["opt"].append(f[1:])
        else:
            out[f[0]] = f[1:]
    return out


class StructFormat(unittest.TestCase):
    def test_parses_engine_output(self):
        v = R.parse_struct(REAL_KSD)
        self.assertEqual(v["calls"], 1496)
        self.assertEqual(v["log"], "history\t\nch\t伊织\neval\t\nch\t「\n")

    def test_nested_and_scalars(self):
        v = R.parse_struct('(const) %["a" => (const) [1, -2, "x\\"y", void],'
                           ' "b" => %[], "c" => 0x1F, "d" => 1.5]')
        self.assertEqual(v, {"a": [1, -2, 'x"y', None], "b": {}, "c": 31,
                             "d": 1.5})

    def test_hex_escape(self):
        self.assertEqual(R.parse_struct('"a\\x01-"'), "a\x01-")

    def test_real_with_comment(self):
        # how saveStruct writes real numbers
        self.assertEqual(R.parse_struct('[0x1.E000000000000p10 /* 1920 */, 0]'),
                         [1920.0, 0])

    def test_writer_drops_control_chars(self):
        self.assertEqual(R.format_struct("a\x01b"), '"ab"')

    def test_roundtrip(self):
        value = {"epoch": 1727000000, "cmds": [
            {"seq": 3, "cmd": "choose", "arg": 1, "sel": 12},
            {"seq": 4, "cmd": 'we"ird\\\n\t', "arg": 0, "sel": -1}]}
        self.assertEqual(R.parse_struct(R.format_struct(value)), value)

    def test_truncated_is_an_error(self):
        with self.assertRaises(R.StructError):
            R.parse_struct('%["lines" => [[1, 1, "a", "b"]')

    def test_file_roundtrip_utf16(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.ksd")
            R.write_struct_file(p, {"k": "台词"})
            with open(p, "rb") as f:
                raw = f.read()
            self.assertEqual(raw[:2], b"\xff\xfe")
            self.assertEqual(R.parse_struct(R.decode_struct_bytes(raw)),
                             {"k": "台词"})


class History(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.hub = R.Hub(clock=self.clock, epoch=77)
        self.hub.log = lambda *a: None

    def kinds(self):
        return [(h["kind"], h["name"], h["text"]) for h in self.hub.history]

    def test_lines_names_and_dedupe(self):
        self.hub.ingest(state(lines=[[1, 1, "【伊织】", "「你好」"],
                                     [2, 1, "", "旁白"]]))
        self.hub.ingest(state(rev=2, lines=[[1, 1, "【伊织】", "「你好」"],
                                            [2, 1, "", "旁白"]]))
        self.assertEqual(self.kinds(), [("L", "伊织", "「你好」"),
                                        ("L", "", "旁白")])

    def test_generation_change_inserts_divider(self):
        self.hub.ingest(state(lines=[[1, 1, "", "a"]]))
        self.hub.ingest(state(rev=2, gen=2, lines=[[1, 1, "", "a"],
                                                   [2, 2, "", "b"]]))
        self.assertEqual([k for k, _, _ in self.kinds()], ["L", "D", "L"])

    def test_gap_inserts_divider(self):
        self.hub.ingest(state(lines=[[1, 1, "", "a"]]))
        self.hub.ingest(state(rev=2, lines=[[5, 1, "", "e"]]))
        self.assertEqual(self.kinds()[1][0], "D")

    def test_restart_divider(self):
        self.hub.ingest(state(lines=[[1, 1, "", "a"]]))
        self.hub.ingest(state(boot=2, lines=[[1, 1, "", "again"]]))
        self.assertEqual([k for k, _, _ in self.kinds()], ["L", "D", "L"])
        self.assertEqual(self.hub.history[-1]["text"], "again")

    def test_current_line_hidden_once_committed(self):
        self.hub.ingest(state(cur="「半句", curName="【天使】"))
        with self.hub.lock:
            self.assertEqual(self.hub.current_line(), ("天使", "「半句"))
        self.hub.ingest(state(rev=2, cur="「半句", curName="【天使】",
                              lines=[[1, 1, "【天使】", "「半句"]]))
        with self.hub.lock:
            self.assertIsNone(self.hub.current_line())

    def test_history_is_bounded(self):
        lines = [[i, 1, "", "l%d" % i] for i in range(1, R.HISTORY_KEEP + 50)]
        self.hub.ingest(state(lines=lines))
        self.assertEqual(len(self.hub.history), R.HISTORY_KEEP)


class PanelProtocol(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.hub = R.Hub(clock=self.clock, epoch=77)
        self.hub.log = lambda *a: None

    def test_offline(self):
        p = parse_panel(self.hub.panel_text())
        self.assertEqual(p["link"], ["0"])
        self.assertEqual(p["loc"][0], "off")
        self.assertIn("end", p["flags"])

    def test_incremental_history(self):
        self.hub.ingest(state(lines=[[1, 1, "", "a"], [2, 1, "", "b"]]))
        p = parse_panel(self.hub.panel_text())
        self.assertIn("hreset", p["flags"])
        self.assertEqual([h[0] for h in p["h"]], ["1", "2"])
        rev = int(p["rev"][0])

        self.assertIn("same", parse_panel(
            self.hub.panel_text(77, rev, 2))["flags"])

        self.hub.ingest(state(rev=2, lines=[[1, 1, "", "a"], [2, 1, "", "b"],
                                            [3, 1, "【宁子】", "c\td"]]))
        p = parse_panel(self.hub.panel_text(77, rev, 2))
        self.assertNotIn("hreset", p["flags"])
        self.assertEqual(p["h"], [["3", "L", "宁子", "c\\td"]])

    def test_new_epoch_forces_reset(self):
        self.hub.ingest(state(lines=[[1, 1, "", "a"]]))
        p = parse_panel(self.hub.panel_text(12, 5, 1))
        self.assertIn("hreset", p["flags"])

    def test_choices_with_image_captions(self):
        self.hub.ingest(state(sel=5, opts=[
            [0, "1_恋丸1", "MS_sl_1_恋丸1", 1, 0],
            [1, "1_姫乃1", "MS_sl_1_姫乃1", 0, 1],
            [2, "反驳她", "", 1, 1]]))
        p = parse_panel(self.hub.panel_text())
        self.assertEqual(p["sel"], ["5"])
        self.assertEqual(p["opt"], [["0", "e", "前往图书室"],
                                    ["1", "r", "当然是回家打游戏了"],
                                    ["2", "er", "反驳她"]])

    def test_menu_location(self):
        self.hub.ingest(state(loc="load"))
        p = parse_panel(self.hub.panel_text())
        self.assertEqual(p["loc"], ["menu", "读档画面"])

    def test_unknown_game_mirrors_the_screen(self):
        self.hub.ingest(state(adapter="none", loc="unsupported", btn=0))
        p = parse_panel(self.hub.panel_text())
        self.assertEqual(p["link"], ["1"])
        self.assertEqual(p["loc"], ["menu", "这款游戏暂不支持台词面板"])
        self.assertEqual(p["btn"], ["0"])

    def test_unknown_location_is_a_menu(self):
        self.hub.ingest(state(loc="somethingnew"))
        self.assertEqual(parse_panel(self.hub.panel_text())["loc"][0], "menu")

    def test_dialog_wins_over_location(self):
        self.hub.ingest(state(dlg=1))
        self.assertEqual(parse_panel(self.hub.panel_text())["loc"][0], "dialog")

    def test_captions_only_from_the_games_profile(self):
        opts = [[0, "1_恋丸1", "MS_sl_1_恋丸1", 1, 0]]
        self.hub.ingest(state(adapter="kag3", sel=1, opts=opts))
        p = parse_panel(self.hub.panel_text())
        self.assertEqual(p["opt"], [["0", "e", "1_恋丸1"]])

    def test_v2_hook_is_reported(self):
        self.hub.ingest(state(proto=2))
        self.assertEqual(parse_panel(self.hub.panel_text())["loc"][0], "hookold")

    def test_old_hook_is_reported(self):
        self.hub.ingest({"calls": 3, "log": "x"})   # v1 hook's file shape
        p = parse_panel(self.hub.panel_text())
        self.assertEqual(p["link"], ["0"])
        self.assertEqual(p["loc"][0], "hookold")

    def test_goes_offline_when_stale(self):
        self.hub.ingest(state())
        self.clock.t += R.HOOK_STALE_S + 1
        self.hub.check_liveness()
        self.assertEqual(parse_panel(self.hub.panel_text())["link"], ["0"])


class Commands(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "_hkcmd.ksd")
        self.hub = R.Hub(cmd_path=self.path, clock=self.clock, epoch=77)
        self.hub.log = lambda *a: None

    def tearDown(self):
        self.tmp.cleanup()

    def read_queue(self):
        with open(self.path, "rb") as f:
            return R.parse_struct(R.decode_struct_bytes(f.read()))

    def test_refused_without_hook(self):
        ok, why = self.hub.enqueue("click")
        self.assertFalse(ok)
        self.assertEqual(why, "nohook")

    def test_queue_file_and_ack_note(self):
        self.hub.ingest(state())
        ok, seq = self.hub.enqueue("qsave")
        self.assertTrue(ok)
        q = self.read_queue()
        self.assertEqual(q["epoch"], 77)
        self.assertEqual(q["cmds"], [{"seq": seq, "cmd": "qsave", "arg": 0,
                                      "sel": -1}])
        self.hub.ingest(state(rev=2, cmdEpoch=77, acks=[[seq, "saved"]]))
        self.assertEqual(parse_panel(self.hub.panel_text())["note"][1],
                         "已快速存档")

    def test_acks_from_another_epoch_are_ignored(self):
        self.hub.ingest(state(cmdEpoch=12, acks=[[1, "saved"]]))
        self.assertNotIn("note", parse_panel(self.hub.panel_text()))

    def test_old_commands_expire(self):
        self.hub.ingest(state())
        self.hub.enqueue("click")
        self.clock.t += R.CMD_TTL_S + 1
        self.hub.ingest(state(rev=2))
        _, seq = self.hub.enqueue("auto")
        self.assertEqual([c["seq"] for c in self.read_queue()["cmds"]], [seq])

    def test_unknown_command(self):
        self.hub.ingest(state())
        self.assertEqual(self.hub.enqueue("rm -rf"), (False, "badcmd"))


class Textbox(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "_hkcmd.ksd")
        self.hub = R.Hub(cmd_path=self.path, clock=self.clock, epoch=77)
        self.hub.log = lambda *a: None
        self.hub.reset_command_file()

    def tearDown(self):
        self.tmp.cleanup()

    def queue(self):
        with open(self.path, "rb") as f:
            return R.parse_struct(R.decode_struct_bytes(f.read()))

    def test_hidden_only_while_a_console_asks(self):
        self.assertEqual(self.queue()["hide"], 0)
        self.hub.panel_text(hide=False)
        self.hub.maintain()
        self.assertEqual(self.queue()["hide"], 0)
        self.hub.panel_text(hide=True)
        self.hub.maintain()
        q = self.queue()
        self.assertEqual(q["hide"], 1)
        # heartbeat keeps moving while the console keeps asking
        beat = q["beat"]
        self.clock.t += R.TEXTBOX_BEAT_S + 0.1
        self.hub.panel_text(hide=True)
        self.hub.maintain()
        self.assertGreater(self.queue()["beat"], beat)
        # console goes quiet: the box comes back
        self.clock.t += R.TEXTBOX_HOLD_S + 0.1
        self.hub.maintain()
        self.assertEqual(self.queue()["hide"], 0)

    def test_no_rewrites_when_nothing_to_do(self):
        self.hub.maintain()
        before = os.stat(self.path).st_mtime_ns
        for _ in range(5):
            self.clock.t += 2
            self.hub.maintain()
        self.assertEqual(os.stat(self.path).st_mtime_ns, before)

    def test_commands_survive_heartbeats(self):
        self.hub.ingest(state())
        _, seq = self.hub.enqueue("click")
        self.hub.panel_text(hide=True)
        self.hub.maintain()
        self.assertEqual([c["seq"] for c in self.queue()["cmds"]], [seq])

    def test_modes(self):
        self.hub.textbox = "never"
        self.hub.panel_text(hide=True)
        self.hub.maintain()
        self.assertEqual(self.queue()["hide"], 0)
        self.hub.textbox = "always"
        self.hub.maintain()
        self.assertEqual(self.queue()["hide"], 1)


class Discovery(unittest.TestCase):
    def test_session_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "session.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("pid=12\nproto=3\nloader=tpm\n"
                        "datapath=f:\\game\\存档\\\nexe=f:\\game\\g.exe\n")
            s = R.read_session(p)
            self.assertEqual(s["datapath"], "f:\\game\\存档\\")
            self.assertEqual(s["loader"], "tpm")
            self.assertIsNone(R.read_session(os.path.join(d, "missing.txt")))

    def test_profile(self):
        prof = R.load_profile("koihazi")
        self.assertIn("1_恋丸1", prof["choice_captions"])
        self.assertEqual(R.load_profile("../etc/passwd"), {})
        self.assertEqual(R.load_profile("nosuchgame"), {})

    def test_watcher_follows_another_game(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            hub = R.Hub(cmd_path=os.path.join(a, R.CMD_NAME), epoch=5)
            hub.log = lambda *x: None
            where = [a]
            w = R.Watcher(os.path.join(a, R.STATE_NAME), hub, locate=lambda: where[0])
            R.write_struct_file(os.path.join(a, R.STATE_NAME), state())
            w.poll_once()
            self.assertTrue(hub.hook_alive())
            where[0] = b
            w.follow()
            self.assertEqual(hub.cmd_path, os.path.join(b, R.CMD_NAME))
            self.assertTrue(os.path.exists(os.path.join(b, R.CMD_NAME)))
            self.assertFalse(hub.hook_alive())


class Access(unittest.TestCase):
    def test_private_only(self):
        self.assertTrue(R.address_allowed("192.168.1.20", False))
        self.assertTrue(R.address_allowed("127.0.0.1", False))
        self.assertTrue(R.address_allowed("::ffff:10.0.0.3", False))
        self.assertFalse(R.address_allowed("8.8.8.8", False))
        self.assertTrue(R.address_allowed("8.8.8.8", True))

    def test_host_names(self):
        for h in ("192.168.1.20:8787", "127.0.0.1:8787", "localhost:8787",
                  "[::1]:8787", "DESKTOP-PC:8787", "mypc.local:8787", "10.0.0.2"):
            self.assertTrue(R.host_allowed(h), h)
        for h in ("", None, "evil.example.com:8787", "attacker.net"):
            self.assertFalse(R.host_allowed(h), h)

    def test_console_request(self):
        # what the 3DS (curl) sends
        self.assertTrue(R.browser_request_allowed({"Host": "192.168.1.20:8787"}))

    def test_debug_page_same_origin(self):
        self.assertTrue(R.browser_request_allowed(
            {"Host": "localhost:8787", "Sec-Fetch-Site": "same-origin",
             "Origin": "http://localhost:8787"}))
        self.assertTrue(R.browser_request_allowed(
            {"Host": "192.168.1.20:8787", "Sec-Fetch-Site": "none"}))

    def test_other_sites_refused(self):
        self.assertFalse(R.browser_request_allowed(
            {"Host": "192.168.1.20:8787", "Sec-Fetch-Site": "cross-site"}))
        self.assertFalse(R.browser_request_allowed(
            {"Host": "192.168.1.20:8787", "Origin": "https://evil.example.com"}))
        self.assertFalse(R.browser_request_allowed(
            {"Host": "192.168.1.20:8787", "Origin": "null"}))
        # DNS rebinding: same origin as far as the browser knows
        self.assertFalse(R.browser_request_allowed(
            {"Host": "rebind.example.com:8787", "Sec-Fetch-Site": "same-origin"}))


if __name__ == "__main__":
    unittest.main()
