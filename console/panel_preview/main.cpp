/*
 * PC harness for the 3DS dialogue panel.
 *
 *   panel_preview FONT OUT.png   render every panel state into a sheet
 *   panel_preview FONT --test    run the behaviour tests
 *
 * Builds the console's own panel/relay sources against the host's FreeType
 * (see build.sh), so what it shows and checks is the code that ships.
 */
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "panel_assets.hpp"
#include "panel_draw.hpp"
#include "panel_ui.hpp"
#include "png.hpp"
#include "relay_protocol.hpp"

bool host_load_font(const char *path);

using relay::HistoryEntry;
using relay::Option;
using relay::Snapshot;

namespace {

const int W = PanelUI::kWidth, H = PanelUI::kHeight;

struct Fonts {
    PanelFont body, small, tiny;
    Fonts() {
        body.load(panel_font_data(), panel_font_size(), 20);
        small.load(panel_font_data(), panel_font_size(), 15);
        tiny.load(panel_font_data(), panel_font_size(), 12);
    }
    PanelFonts ref() { return PanelFonts{&body, &small, &tiny}; }
};

Fonts *g_fonts = nullptr;

struct Sent {
    std::vector<std::string> cmds;
    PanelUI::Sender sender() {
        return [this](const char *c, int arg, int sel) {
            char buf[64];
            snprintf(buf, sizeof(buf), "%s %d %d", c, arg, sel);
            cmds.push_back(buf);
        };
    }
};

HistoryEntry line(int id, const char *name, const char *text) {
    HistoryEntry e;
    e.id = id;
    e.name = name;
    e.text = text;
    return e;
}

HistoryEntry divider(int id, const char *text) {
    HistoryEntry e;
    e.id = id;
    e.divider = true;
    e.text = text;
    return e;
}

// Placeholder dialogue for the preview and the tests: invented lines, so the
// sheet can be shown anywhere without quoting a game's script.
std::vector<HistoryEntry> prologue(int n = 9) {
    std::vector<HistoryEntry> h = {
        line(1, "小林", "「今天放学以后有空吗？想跟你商量一下社团的事」"),
        line(2, "阿澈", "「可以是可以，不过我今天要先去一趟图书馆」"),
        line(3, "小林", "「那就图书馆门口见，我等你」"),
        line(4, "", "她说完就转身跑回了教室，完全没给我拒绝的余地。"),
        line(5, "阿澈", "「……至少先问问我到底方不方便吧」"),
        line(6, "", "我叹了口气，把没写完的笔记塞回书包。"),
        divider(7, ""),
        line(8, "", "窗外的天色已经暗了下来，走廊里只剩下我们两个人的脚步声————"),
        line(9, "小林", "「说起来，你觉得这次的企划真的能赶上文化祭吗？我总觉得时间不太够」"),
    };
    h.resize(n);
    return h;
}

Snapshot playing() {
    Snapshot s;
    s.rev = 1;
    s.relay_up = true;
    s.game_up = true;
    s.loc = "play";
    s.buttons = 1 | 2 | 4 | 8 | 16 | 32 | 64 | 128 | 256;
    s.history = prologue();
    return s;
}

Snapshot title_screen(bool can_continue) {
    Snapshot s;
    s.rev = 1;
    s.relay_up = true;
    s.game_up = true;
    s.loc = "title";
    s.loc_label = "标题画面";
    s.buttons = 32 | 128 | 1024 | (can_continue ? 512 : 0);
    s.history = prologue(4);
    return s;
}

// ------------------------------------------------------------ scenes ----

struct Scene {
    std::string name;
    std::vector<uint16_t> fb;
};

Scene render(const char *name, PanelUI &ui, uint64_t now) {
    ui.tick(now);
    Scene s;
    s.name = name;
    s.fb.assign((size_t)W * H, 0);
    ui.render(s.fb.data(), now);
    return s;
}

std::vector<Scene> scenes() {
    std::vector<Scene> out;
    uint64_t t = 10000;   // blink phase "on"
    Sent sent;

    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        ui.set_relay_label("192.168.1.20:8787");
        Snapshot s;
        s.rev = 1;
        ui.update(s, t);
        out.push_back(render("relay down", ui, t));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        Snapshot s;
        s.rev = 1;
        s.relay_up = true;
        s.loc = "off";
        s.loc_label = "游戏未运行";
        ui.update(s, t);
        out.push_back(render("game not running", ui, t));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        ui.update(playing(), t);
        out.push_back(render("reading, waiting for tap", ui, t));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        Snapshot s = playing();
        s.history.resize(5);
        s.typing = true;
        s.cur_name = "小林";
        s.cur_text = "「那就图书馆门口见，我等你";
        s.auto_mode = true;
        ui.update(s, t);
        out.push_back(render("typing, auto mode", ui, t));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        Snapshot s = playing();
        s.history.resize(5);
        s.history.push_back(line(6, "学姐",
            "「你们两个来得正好，学生会那边说要我们把企划书重写一遍，"
            "下周一之前交上去，字数还要翻一倍，我实在是不想一个人熬夜改啊」"));
        ui.update(s, t);
        out.push_back(render("very long line", ui, t));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        Snapshot s = playing();
        s.select = 0;
        s.buttons = 8 | 16;
        Option a, b;
        a.index = 0;
        a.text = "现在就去";
        a.read = true;
        b.index = 1;
        b.text = "明天再说";
        s.options = {a, b};
        ui.update(s, t);
        out.push_back(render("choice (2)", ui, t));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        Snapshot s = playing();
        s.select = 33;
        s.buttons = 8 | 16;
        const char *texts[] = {"学生会室", "图书室", "天台", "社办",
                               "应该没有吧"};
        for (int i = 0; i < 5; i++) {
            Option o;
            o.index = i;
            o.text = texts[i];
            o.enabled = i != 1 && i != 3;
            o.read = i == 0;
            s.options.push_back(o);
        }
        ui.update(s, t);
        ui.key_down(PANEL_KEY_DOWN, t);
        ui.key_up(PANEL_KEY_DOWN, t);
        ui.key_down(PANEL_KEY_DOWN, t);
        ui.key_up(PANEL_KEY_DOWN, t);
        out.push_back(render("choice (5, two locked), D-pad", ui, t));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        Snapshot s = playing();
        s.history = prologue(6);
        ui.update(s, t);
        ui.touch_down(160, 60, t);
        ui.touch_move(160, 80, t + 16);
        ui.touch_move(160, 150, t + 32);
        ui.touch_up(t + 400);
        Snapshot s2 = s;
        s2.rev++;
        s2.history = prologue(9);
        ui.update(s2, t + 500);
        out.push_back(render("scrolled back, new lines", ui, t + 600));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        Snapshot s = playing();
        s.loc = "menu";
        s.loc_label = "读档画面";
        s.buttons = 0;
        ui.update(s, t);
        out.push_back(render("game menu open", ui, t));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        Snapshot s = playing();
        s.note = "已快速存档";
        s.note_seq = 1;
        s.skip_mode = true;
        ui.update(s, t);
        out.push_back(render("note toast, skip mode", ui, t + 100));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        ui.update(playing(), t);
        int qx = (4 + 5 * (W - 8) / 7 + 4 + 6 * (W - 8) / 7) / 2;
        ui.touch_down(qx, 220, t);
        ui.touch_up(t + 50);
        out.push_back(render("quick load armed", ui, t + 100));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        Snapshot s = playing();
        s.loc = "dialog";
        s.loc_label = "电脑上有确认框";
        ui.update(s, t);
        out.push_back(render("PC dialog open", ui, t));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        ui.update(playing(), t);
        ui.key_down(PANEL_KEY_MENU, t);
        ui.key_down(PANEL_KEY_DOWN, t);
        ui.key_down(PANEL_KEY_DOWN, t);
        ui.key_down(PANEL_KEY_DOWN, t);
        ui.key_down(PANEL_KEY_ADVANCE, t);
        out.push_back(render("menu, back to title armed", ui, t + 50));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        ui.update(title_screen(true), t);
        out.push_back(render("title screen", ui, t));
    }
    {
        PanelUI ui(g_fonts->ref(), sent.sender());
        ui.update(title_screen(false), t);
        ui.key_down(PANEL_KEY_DOWN, t);
        out.push_back(render("title screen, no suspend save", ui, t));
    }
    return out;
}

void write_sheet(const char *path, const std::vector<Scene> &sc) {
    const int cols = 3, gap = 6, label_h = 20;
    int rows = ((int)sc.size() + cols - 1) / cols;
    int sw = cols * W + (cols + 1) * gap;
    int sh = rows * (H + label_h + gap) + gap;
    std::vector<uint16_t> sheet((size_t)sw * sh, rgb(60, 60, 64));
    Canvas c(sheet.data(), sw, sh);
    for (size_t i = 0; i < sc.size(); i++) {
        int x = gap + (int)(i % cols) * (W + gap);
        int y = gap + (int)(i / cols) * (H + label_h + gap);
        c.text(g_fonts->tiny, x + 2, y + 2, sc[i].name.c_str(),
               rgb(230, 230, 230));
        for (int j = 0; j < H; j++) {
            memcpy(&sheet[(size_t)(y + label_h + j) * sw + x],
                   &sc[i].fb[(size_t)j * W], W * sizeof(uint16_t));
        }
    }
    write_png(path, sheet.data(), sw, sh, 2);
}

// ------------------------------------------------------------- tests ----

int g_fail = 0, g_pass = 0;

#define CHECK(cond)                                                          \
    do {                                                                     \
        if (cond) {                                                          \
            g_pass++;                                                        \
        } else {                                                             \
            g_fail++;                                                        \
            printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);           \
        }                                                                    \
    } while (0)

// The embedded subset, then a full font from the SD card for what it lacks.
void test_fallback(const char *extra) {
    PanelFont f;
    CHECK(f.load(panel_font_data(), panel_font_size(), 20));
    const uint32_t kanji = 0x5927;     // 大: in the subset
    const uint32_t hangul = 0xD55C;    // 한: Korean is not
    int before = f.advance(kanji);
    CHECK(!f.glyph(kanji)->missing);
    CHECK(f.glyph(hangul)->missing);
    CHECK(!f.add_fallback("/no/such/font.ttf"));
    CHECK(f.fallbacks() == 0);
    if (!extra) {
        printf("(fallback font not given; skipping the SD-card part)\n");
        return;
    }
    CHECK(f.add_fallback(extra));
    CHECK(f.fallbacks() == 1);
    CHECK(!f.glyph(hangul)->missing);   // found in the fallback now
    CHECK(f.glyph(hangul)->width > 0);
    CHECK(f.advance(kanji) == before);  // the subset still draws what it has
    CHECK(f.text_width("한국어") > 0);
}

void test_protocol() {
    std::string body =
        "v\t2\nepoch\t77\nrev\t5\nlink\t1\nloc\tplay\t\nmode\tauto\n"
        "btn\t27\ncur\t小林\t「半句\\t话\nhreset\n"
        "h\t1\tL\t阿澈\t「你好」\nh\t2\tD\t游戏重新启动\n"
        "sel\t3\nopt\t0\ter\t现在就去\nopt\t1\t-\t明天再说\nnote\t4\t已快速存档\nend\n";
    relay::Update u = relay::parse(body);
    CHECK(u.complete);
    CHECK(u.epoch == 77 && u.rev == 5 && u.game_up && u.mode == "auto");
    CHECK(u.has_cur && u.cur_text == "「半句\t话");
    CHECK(u.history.size() == 2 && u.history[1].divider &&
          u.history[1].text == "游戏重新启动");
    CHECK(u.options.size() == 2 && u.options[0].enabled && u.options[0].read &&
          !u.options[1].enabled);

    Snapshot s;
    relay::SyncState sync;
    CHECK(relay::apply(s, sync, u));
    CHECK(s.relay_up && s.auto_mode && s.select == 3 && s.note == "已快速存档");
    CHECK(sync.hist == 2 && sync.epoch == 77);
    uint32_t note_seq = s.note_seq;

    // "same" changes nothing
    relay::Update same = relay::parse("v\t2\nepoch\t77\nrev\t5\nsame\nend\n");
    uint32_t rev = s.rev;
    CHECK(!relay::apply(s, sync, same));
    CHECK(s.rev == rev);

    // incremental history; repeated note id is not shown again
    relay::Update inc = relay::parse(
        "v\t2\nepoch\t77\nrev\t6\nlink\t1\nloc\tplay\t\nmode\t-\nbtn\t27\n"
        "h\t2\tD\tdup\nh\t3\tL\t\t旁白\nnote\t4\t已快速存档\nend\n");
    CHECK(relay::apply(s, sync, inc));
    CHECK(s.history.size() == 3 && s.history.back().text == "旁白");
    CHECK(s.note_seq == note_seq && !s.typing && !s.auto_mode);

    // a truncated read is ignored entirely
    rev = s.rev;
    CHECK(!relay::apply(s, sync, relay::parse("v\t2\nepoch\t77\nrev\t9\nlink\t0\n")));
    CHECK(s.rev == rev && s.game_up);

    // a restarted relay numbers notes from 1 again
    relay::Update fresh = relay::parse(
        "v\t2\nepoch\t99\nrev\t1\nlink\t1\nloc\tplay\t\nhreset\nnote\t1\t新\nend\n");
    CHECK(relay::apply(s, sync, fresh));
    CHECK(s.history.empty() && s.note == "新" && s.note_seq == note_seq + 1);

    // protocol mismatch
    relay::apply(s, sync, relay::parse("v\t3\nend\n"));
    CHECK(s.proto_mismatch && !s.game_up);
}

void test_wrap() {
    PanelFont &f = g_fonts->small;
    int cw = f.advance(0x4E00);   // one CJK cell
    // kinsoku: a line may not start with closing punctuation
    std::string t = "一二三四五六七八九十，好";
    auto lines = wrap_text(f, t, cw * 10, cw * 10, 0);
    for (size_t i = 1; i < lines.size(); i++) {
        CHECK(lines[i].compare(0, 3, "，") != 0);
    }
    // hanging: with a margin the comma stays on the first line
    lines = wrap_text(f, t, cw * 10, cw * 10, cw);
    CHECK(lines.size() == 2 && lines[1] == "好");
    // an opening bracket never ends a line
    lines = wrap_text(f, "一二三四五六七八九「十」", cw * 10, cw * 10, 0);
    for (const auto &l : lines) {
        CHECK(l.size() < 3 || l.compare(l.size() - 3, 3, "「") != 0);
    }
    // every line fits (allowing the hang)
    std::string longer = "「我说你啊，以后最好多加小心哦？尤其是宗教宣传之类的。你难道没发现我刚刚只是随口说说？」";
    lines = wrap_text(f, longer, 200, 120, 8);
    CHECK(lines.size() > 2);
    CHECK(f.text_width(lines[0].c_str()) <= 128);
    for (size_t i = 1; i < lines.size(); i++) {
        CHECK(f.text_width(lines[i].c_str()) <= 208);
    }
    std::string joined;
    for (const auto &l : lines) {
        joined += l;
    }
    CHECK(joined == longer);   // nothing lost or duplicated
    // ASCII words are kept whole
    int hw = f.text_width("hello wonderful");
    lines = wrap_text(f, "hello wonderful world", hw, hw, 0);
    CHECK(lines.size() == 2 && lines[0] == "hello wonderful" &&
          lines[1] == "world");
    lines = wrap_text(f, "say hello", f.text_width("say hel"),
                      f.text_width("say hel"), 0);
    CHECK(lines.size() == 2 && lines[1] == "hello");
    // ellipsis
    std::string e = ellipsize(f, longer, 100);
    CHECK(f.text_width(e.c_str()) <= 100 && e.size() < longer.size());
}

void test_gestures() {
    uint64_t t = 50000;
    const int TEXT_Y = 120, BTN_Y = 220;
    auto btn_x = [](int b) {
        return (4 + b * (W - 8) / 7 + 4 + (b + 1) * (W - 8) / 7) / 2;
    };

    {   // tap advances; long press and drags do not
        Sent s;
        PanelUI ui(g_fonts->ref(), s.sender());
        ui.update(playing(), t);
        ui.touch_down(160, TEXT_Y, t);
        ui.touch_up(t + 80);
        CHECK(s.cmds.size() == 1 && s.cmds[0] == "click 0 -1");
        ui.touch_down(160, TEXT_Y, t + 1000);
        ui.touch_up(t + 1900);
        CHECK(s.cmds.size() == 1);
        ui.touch_down(160, 60, t + 3000);
        ui.touch_move(160, 90, t + 3016);
        ui.touch_move(160, 140, t + 3032);
        ui.touch_up(t + 3300);
        CHECK(s.cmds.size() == 1);
        CHECK(ui.scroll_pos() > 0);
        // tapping while scrolled back returns instead of advancing
        ui.touch_down(160, 60, t + 4000);
        ui.touch_up(t + 4050);
        CHECK(s.cmds.size() == 1);
        for (uint64_t k = 0; k < 40; k++) {
            ui.tick(t + 4050 + k * 16);
        }
        CHECK(ui.scroll_pos() == 0);
    }
    {   // choices: lock against double taps, refuse disabled options
        Sent s;
        PanelUI ui(g_fonts->ref(), s.sender());
        Snapshot snap = playing();
        snap.select = 12;
        Option a, b;
        a.index = 0;
        a.text = "摸摸她的头";
        b.index = 1;
        b.text = "拍手喝彩";
        b.enabled = false;
        snap.options = {a, b};
        ui.update(snap, t);
        CHECK(ui.showing_choices());
        // where are the bars? scan for the first one
        int y0 = -1, y1 = -1;
        for (int y = 30; y < 200 && y1 < 0; y++) {
            ui.touch_down(160, y, t);
            ui.touch_up(t + 50);
            if (!s.cmds.empty()) {
                y0 = y;
                break;
            }
            if (ui.toast_visible(t + 60) && ui.toast() != "") {
                y1 = y;
            }
        }
        CHECK(y0 > 0 && !s.cmds.empty() && s.cmds[0] == "choose 0 12");
        ui.touch_down(160, y0, t + 200);
        ui.touch_up(t + 250);
        CHECK(s.cmds.size() == 1);   // second tap swallowed
        // tapping the text area during a choice never sends click
        ui.touch_down(160, 34, t + 400);
        ui.touch_up(t + 450);
        for (const auto &c : s.cmds) {
            CHECK(c.compare(0, 5, "click") != 0);
        }
        // the locked option explains itself
        Sent s2;
        PanelUI ui2(g_fonts->ref(), s2.sender());
        ui2.update(snap, t);
        bool told = false;
        for (int y = 199; y > 30 && !told; y--) {
            ui2.touch_down(160, y, t);
            ui2.touch_up(t + 40);
            told = ui2.toast() == "条件不足，选不了这个";
        }
        CHECK(told);
        CHECK(s2.cmds.empty() || s2.cmds[0] == "choose 0 12");
    }
    {   // quick load needs a second tap within the window
        Sent s;
        PanelUI ui(g_fonts->ref(), s.sender());
        ui.update(playing(), t);
        ui.touch_down(btn_x(5), BTN_Y, t);
        ui.touch_up(t + 50);
        CHECK(s.cmds.empty() && ui.qload_armed(t + 60));
        ui.touch_down(btn_x(5), BTN_Y, t + 900);
        ui.touch_up(t + 950);
        CHECK(s.cmds.size() == 1 && s.cmds[0] == "qload 0 -1");
        // an expired arm starts over
        ui.touch_down(btn_x(5), BTN_Y, t + 2000);
        ui.touch_up(t + 2050);
        ui.tick(t + 6000);
        ui.touch_down(btn_x(5), BTN_Y, t + 6000);
        ui.touch_up(t + 6050);
        CHECK(s.cmds.size() == 1);
    }
    {   // disabled buttons explain, sliding off a button cancels it
        Sent s;
        PanelUI ui(g_fonts->ref(), s.sender());
        Snapshot snap = playing();
        snap.buttons = 1 | 8;   // no skip, voice, quick load
        snap.auto_mode = false;
        ui.update(snap, t);
        ui.touch_down(btn_x(3), BTN_Y, t);
        ui.touch_up(t + 50);
        CHECK(s.cmds.empty() && ui.toast() == "这句没有语音");
        ui.touch_down(btn_x(4), BTN_Y, t + 100);
        ui.touch_move(btn_x(4), 120, t + 150);
        ui.touch_up(t + 200);
        CHECK(s.cmds.empty());
        ui.touch_down(btn_x(4), BTN_Y, t + 300);
        ui.touch_up(t + 350);
        CHECK(s.cmds.size() == 1 && s.cmds[0] == "qsave 0 -1");
        // auto that is on can always be turned off
        snap.rev++;
        snap.auto_mode = true;
        snap.buttons = 0;
        ui.update(snap, t + 400);
        ui.touch_down(btn_x(1), BTN_Y, t + 500);
        ui.touch_up(t + 550);
        CHECK(s.cmds.size() == 2 && s.cmds[1] == "auto 0 -1");
    }
    {   // new lines while scrolled back: view stays put, pill says so
        Sent s;
        PanelUI ui(g_fonts->ref(), s.sender());
        Snapshot snap = playing();
        ui.update(snap, t);
        ui.touch_down(160, 50, t);
        ui.touch_move(160, 70, t + 16);
        ui.touch_move(160, 110, t + 32);
        ui.touch_up(t + 300);
        int before = ui.scroll_pos();
        CHECK(before > 0);
        snap.rev++;
        snap.history.push_back(line(10, "小林",
            "「那说定了，明天放学以后在图书馆门口等你，你要是敢迟到的话，"
            "下次的文化祭企划书就全部交给你一个人写，我可不管了哦」"));
        ui.update(snap, t + 400);
        CHECK(ui.new_lines_below());
        CHECK(ui.scroll_pos() > before);   // grew by what was added below
    }
    {   // not on the reading screen: taps hint instead of acting
        Sent s;
        PanelUI ui(g_fonts->ref(), s.sender());
        Snapshot snap = playing();
        snap.loc = "title";
        snap.loc_label = "标题画面";
        snap.buttons = 0;
        ui.update(snap, t);
        ui.touch_down(160, TEXT_Y, t);
        ui.touch_up(t + 50);
        CHECK(s.cmds.empty() && ui.toast_visible(t + 60));
    }
    {   // console buttons: A advances, B returns, X/R/Y act, D-pad scrolls
        Sent s;
        PanelUI ui(g_fonts->ref(), s.sender());
        ui.update(playing(), t);
        ui.key_down(PANEL_KEY_ADVANCE, t);
        ui.key_up(PANEL_KEY_ADVANCE, t + 80);
        CHECK(s.cmds.size() == 1 && s.cmds[0] == "click 0 -1");
        ui.key_down(PANEL_KEY_AUTO, t + 200);
        ui.key_down(PANEL_KEY_SKIP, t + 300);
        CHECK(s.cmds.size() == 3 && s.cmds[1] == "auto 0 -1" &&
              s.cmds[2] == "skip 0 -1");
        ui.key_down(PANEL_KEY_UP, t + 400);
        for (uint64_t k = 1; k <= 30; k++) {
            ui.tick(t + 400 + k * 16);
        }
        int one = ui.scroll_pos();
        CHECK(one > 0);
        // held: repeats after a delay and scrolls further
        for (uint64_t k = 31; k <= 80; k++) {
            ui.tick(t + 400 + k * 16);
        }
        CHECK(ui.scroll_pos() > one);
        ui.key_up(PANEL_KEY_UP, t + 1700);
        // A while scrolled back returns instead of advancing
        ui.key_down(PANEL_KEY_ADVANCE, t + 1800);
        for (uint64_t k = 0; k < 40; k++) {
            ui.tick(t + 1800 + k * 16);
        }
        CHECK(ui.scroll_pos() == 0 && s.cmds.size() == 3);
        // B from history, too
        ui.key_down(PANEL_KEY_UP, t + 2600);
        ui.key_up(PANEL_KEY_UP, t + 2650);
        for (uint64_t k = 0; k < 30; k++) {
            ui.tick(t + 2650 + k * 16);
        }
        CHECK(ui.scroll_pos() > 0);
        ui.key_down(PANEL_KEY_BACK, t + 3200);
        for (uint64_t k = 0; k < 40; k++) {
            ui.tick(t + 3200 + k * 16);
        }
        CHECK(ui.scroll_pos() == 0 && s.cmds.size() == 3);
    }
    {   // choices by D-pad: first press only highlights, disabled skipped
        Sent s;
        PanelUI ui(g_fonts->ref(), s.sender());
        Snapshot snap = playing();
        snap.select = 33;
        for (int i = 0; i < 4; i++) {
            Option o;
            o.index = i;
            o.text = "选项";
            o.enabled = i != 1;
            snap.options.push_back(o);
        }
        ui.update(snap, t);
        ui.key_down(PANEL_KEY_ADVANCE, t);
        CHECK(s.cmds.empty() && ui.focused_choice() == 0);
        ui.key_down(PANEL_KEY_DOWN, t + 100);
        ui.key_up(PANEL_KEY_DOWN, t + 150);
        CHECK(ui.focused_choice() == 2);   // 1 is locked
        ui.key_down(PANEL_KEY_DOWN, t + 200);
        ui.key_up(PANEL_KEY_DOWN, t + 250);
        ui.key_down(PANEL_KEY_DOWN, t + 300);   // already at the end
        ui.key_up(PANEL_KEY_DOWN, t + 350);
        CHECK(ui.focused_choice() == 3);
        ui.key_down(PANEL_KEY_ADVANCE, t + 400);
        CHECK(s.cmds.size() == 1 && s.cmds[0] == "choose 3 33");
        // a new prompt starts without a highlight
        snap.rev++;
        snap.select = 34;
        ui.update(snap, t + 500);
        CHECK(ui.focused_choice() == -1);
    }
    {   // 菜单: open, pick settings; back to title needs a second tap
        Sent s;
        PanelUI ui(g_fonts->ref(), s.sender());
        ui.update(playing(), t);
        ui.touch_down(btn_x(6), BTN_Y, t);
        ui.touch_up(t + 50);
        CHECK(ui.menu_shown() && s.cmds.empty());
        ui.key_down(PANEL_KEY_ADVANCE, t + 100);   // highlights 设置
        CHECK(ui.focused_tile() == 0 && s.cmds.empty());
        ui.key_down(PANEL_KEY_ADVANCE, t + 200);
        CHECK(s.cmds.size() == 1 && s.cmds[0] == "config 0 -1");
        CHECK(!ui.menu_shown());
        // Start opens it with the first entry highlighted
        ui.key_down(PANEL_KEY_MENU, t + 300);
        CHECK(ui.menu_shown() && ui.focused_tile() == 0);
        for (int i = 0; i < 3; i++) {
            ui.key_down(PANEL_KEY_DOWN, t + 400);
            ui.key_up(PANEL_KEY_DOWN, t + 410);
        }
        CHECK(ui.focused_tile() == 3);   // 回到标题
        ui.key_down(PANEL_KEY_ADVANCE, t + 500);
        CHECK(s.cmds.size() == 1 && ui.menu_shown());   // armed only
        ui.key_down(PANEL_KEY_ADVANCE, t + 900);
        CHECK(s.cmds.size() == 2 && s.cmds[1] == "title 0 -1");
        // B and a tap beside the tiles close it; neither sends anything
        ui.key_down(PANEL_KEY_MENU, t + 1000);
        ui.key_down(PANEL_KEY_BACK, t + 1100);
        CHECK(!ui.menu_shown());
        ui.key_down(PANEL_KEY_MENU, t + 1200);
        ui.touch_down(4, 30, t + 1300);
        ui.touch_up(t + 1350);
        CHECK(!ui.menu_shown() && s.cmds.size() == 2);
        // "direct operation" is handled by the controller, not the relay
        ui.key_down(PANEL_KEY_MENU, t + 1400);
        for (int i = 0; i < 4; i++) {
            ui.key_down(PANEL_KEY_DOWN, t + 1500);
            ui.key_up(PANEL_KEY_DOWN, t + 1510);
        }
        ui.key_down(PANEL_KEY_ADVANCE, t + 1600);
        CHECK(s.cmds.size() == 3 && s.cmds[2] == "@mirror 0 -1");
        // leaving the story closes the sheet
        ui.key_down(PANEL_KEY_MENU, t + 1700);
        Snapshot snap = playing();
        snap.rev++;
        snap.loc = "menu";
        snap.loc_label = "设置画面";
        ui.update(snap, t + 1800);
        CHECK(!ui.menu_shown());
        // a tap on a game screen asks to operate it directly
        ui.touch_down(160, TEXT_Y, t + 1900);
        ui.touch_up(t + 1950);
        CHECK(s.cmds.size() == 4 && s.cmds[3] == "@mirror 0 -1");
    }
    {   // title screen: continue / new game / load / settings
        Sent s;
        PanelUI ui(g_fonts->ref(), s.sender());
        ui.update(title_screen(false), t);
        CHECK(ui.title_shown());
        ui.key_down(PANEL_KEY_ADVANCE, t);
        ui.key_down(PANEL_KEY_ADVANCE, t + 100);   // 继续游戏, no save
        CHECK(s.cmds.empty() && ui.toast() == "没有可以继续的进度");
        ui.key_down(PANEL_KEY_DOWN, t + 200);
        ui.key_down(PANEL_KEY_ADVANCE, t + 300);
        CHECK(s.cmds.size() == 1 && s.cmds[0] == "newgame 0 -1");
        // the menu button is not for the title
        ui.touch_down(btn_x(6), BTN_Y, t + 400);
        ui.touch_up(t + 450);
        CHECK(!ui.menu_shown() && ui.toast() == "标题菜单就在上面");
        // story starts: back to the log
        ui.update(playing(), t + 500);
        CHECK(!ui.title_shown() && ui.focused_tile() == -1);
    }
    {   // rendering every state stays inside the buffer (run under ASan)
        std::vector<Scene> sc = scenes();
        CHECK(sc.size() >= 10);
    }
}

}  // namespace

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s FONT (OUT.png | --test)\n", argv[0]);
        return 2;
    }
    if (!host_load_font(argv[1])) {
        fprintf(stderr, "cannot read font %s\n", argv[1]);
        return 2;
    }
    Fonts fonts;
    g_fonts = &fonts;
    if (!fonts.body.ready()) {
        fprintf(stderr, "FreeType rejected the font\n");
        return 2;
    }
    if (strcmp(argv[2], "--test") == 0) {
        test_protocol();
        test_wrap();
        test_gestures();
        test_fallback(argc > 3 ? argv[3] : nullptr);
        printf("%d passed, %d failed\n", g_pass, g_fail);
        return g_fail ? 1 : 0;
    }
    std::vector<Scene> sc = scenes();
    write_sheet(argv[2], sc);
    printf("wrote %s (%zu states)\n", argv[2], sc.size());
    return 0;
}
