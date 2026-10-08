/*
 * kiripanel.tpm - loads the 3DS dialogue panel hook into a KiriKiri game.
 *
 * KiriKiri 2 and Z load every *.tpm in the exe folder, system\ and plugin\
 * (plugin64\ on 64-bit Z) before startup.tjs runs. At that point there is
 * no `kag` yet, so V2Link only installs a small TJS bootstrap: a Timer that
 * waits for the game's KAG window and then runs hook.tjs from the folder
 * this tpm was loaded from. Nothing else in the game is touched.
 *
 * The plugin also gives TJS a few things it cannot do itself, as the global
 * dictionary KiriPanelNative:
 *   version             this build
 *   pluginSub           folder of this module relative to the exe, e.g.
 *                       "plugin/", or "" for the exe folder itself
 *   underExe            1 when pluginSub applies, 0 when the module is
 *                       somewhere else
 *   pluginStorage       the same folder as an absolute storage name
 *   log(text)           append a line to %LOCALAPPDATA%\kiripanel\kiripanel.log
 *                       (also to the engine's own log)
 *   members(obj)        names of obj's members (for the scout report)
 *   modules()           the game's loaded plugin files (for the scout report)
 *   writeSession(text)  replace %LOCALAPPDATA%\kiripanel\session.txt with
 *                       "pid=<this process>" and text, which tells relay.py
 *                       where this game keeps its save data
 *
 * The same code also builds as version.dll (KP_PROXY, see proxy.inc) for
 * engines that do not load *.tpm files by themselves.
 *
 * Failure policy: the game must start exactly as it would without us. Every
 * engine function used is looked up first (the stub otherwise crashes on a
 * missing one), and the bootstrap catches its own errors in TJS so no
 * exception crosses into the engine's plugin loader.
 *
 * tp_stub.h/.cpp come from the KiriKiri 2 sources; its licence allows them
 * to be built into plugins without counting as derived use.
 */
#include <windows.h>
#include <psapi.h>   // K32EnumProcessModules (in kernel32 since Windows 7)
#include <stdio.h>
#include <string>

#include "tp_stub/tp_stub.h"

#define KP_VERSION L"1.0.0"

namespace {

HMODULE g_module = nullptr;
iTVPFunctionExporter *g_exporter = nullptr;
bool g_linked = false;
bool g_idle = false;   // linked, but another copy owns KiriPanelNative

std::wstring module_dir(HMODULE m) {
    wchar_t buf[MAX_PATH * 2];
    DWORD n = GetModuleFileNameW(m, buf, (DWORD)(sizeof(buf) / sizeof(buf[0])));
    if (n == 0 || n >= sizeof(buf) / sizeof(buf[0])) return L"";
    std::wstring p(buf, n);
    size_t s = p.find_last_of(L"\\/");
    return s == std::wstring::npos ? L"" : p.substr(0, s + 1);
}

std::wstring local_dir() {
    wchar_t buf[MAX_PATH];
    DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", buf, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) return L"";
    std::wstring d = std::wstring(buf, n) + L"\\kiripanel\\";
    CreateDirectoryW(d.c_str(), nullptr);
    return d;
}

std::string to_utf8(const wchar_t *s) {
    if (!s || !*s) return "";
    int n = WideCharToMultiByte(CP_UTF8, 0, s, -1, nullptr, 0, nullptr, nullptr);
    if (n <= 1) return "";
    std::string out((size_t)n - 1, '\0');
    WideCharToMultiByte(CP_UTF8, 0, s, -1, &out[0], n, nullptr, nullptr);
    return out;
}

// Appends one line, independent of the engine (works before its log exists
// and in builds whose console log is off).
void file_log(const wchar_t *text) {
    std::wstring dir = local_dir();
    if (dir.empty()) return;
    std::wstring path = dir + L"kiripanel.log";
    WIN32_FILE_ATTRIBUTE_DATA fa;
    if (GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &fa) &&
        (fa.nFileSizeHigh || fa.nFileSizeLow > 1024 * 1024)) {
        std::wstring old = dir + L"kiripanel.old.log";
        MoveFileExW(path.c_str(), old.c_str(), MOVEFILE_REPLACE_EXISTING);
    }
    HANDLE h = CreateFileW(path.c_str(), FILE_APPEND_DATA,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    SYSTEMTIME t;
    GetLocalTime(&t);
    char head[64];
    snprintf(head, sizeof(head), "%04d-%02d-%02d %02d:%02d:%02d [%lu] ",
             t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond,
             GetCurrentProcessId());
    std::string line = head + to_utf8(text) + "\r\n";
    DWORD w;
    WriteFile(h, line.data(), (DWORD)line.size(), &w, nullptr);
    CloseHandle(h);
}

void kp_log(const wchar_t *text) {
    file_log(text);
    if (g_linked) {
        TVPAddLog(ttstr(L"kiripanel: ") + text);
    }
}

// The engine functions this plugin calls. Looked up before any is used.
const char *kRequired[] = {
    "void ::TVPExecuteScript(const ttstr &,tTJSVariant *)",
    "iTJSDispatch2 * ::TVPGetScriptDispatch()",
    "tTJSNativeClassMethod * ::TJSCreateNativeClassMethod(tTJSNativeClassMethodCallback)",
    "iTJSDispatch2 * ::TJSCreateDictionaryObject(iTJSDispatch2 * *)",
    "iTJSDispatch2 * ::TJSCreateArrayObject(iTJSDispatch2 * *)",
    "ttstr ::TVPNormalizeStorageName(const ttstr &)",
    "void ::TVPAddLog(const ttstr &)",
};

bool engine_has_everything() {
    for (const char *name : kRequired) {
        const char *n = name;
        void *ptr = nullptr;
        if (!g_exporter->QueryFunctionsByNarrowString(&n, &ptr, 1) || !ptr) {
            std::wstring w = L"engine lacks ";
            for (const char *c = name; *c; c++) w += (wchar_t)(unsigned char)*c;
            file_log(w.c_str());
            return false;
        }
    }
    return true;
}

// ---- KiriPanelNative methods ---------------------------------------------

tjs_error TJS_INTF_METHOD m_log(tTJSVariant *result, tjs_int numparams,
                                tTJSVariant **param, iTJSDispatch2 *) {
    if (numparams < 1) return TJS_E_BADPARAMCOUNT;
    ttstr s(*param[0]);
    kp_log(s.c_str());
    if (result) result->Clear();
    return TJS_S_OK;
}

tjs_error TJS_INTF_METHOD m_write_session(tTJSVariant *result, tjs_int numparams,
                                          tTJSVariant **param, iTJSDispatch2 *) {
    if (numparams < 1) return TJS_E_BADPARAMCOUNT;
    ttstr s(*param[0]);
    bool ok = false;
    std::wstring dir = local_dir();
    if (!dir.empty()) {
        std::wstring tmp = dir + L"session.tmp", path = dir + L"session.txt";
        HANDLE h = CreateFileW(tmp.c_str(), GENERIC_WRITE, 0, nullptr,
                               CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (h != INVALID_HANDLE_VALUE) {
            char pid[32];
            snprintf(pid, sizeof(pid), "pid=%lu\n", GetCurrentProcessId());
            std::string body = pid + to_utf8(s.c_str());
            DWORD w;
            ok = WriteFile(h, body.data(), (DWORD)body.size(), &w, nullptr) &&
                 w == body.size();
            CloseHandle(h);
            ok = ok && MoveFileExW(tmp.c_str(), path.c_str(),
                                   MOVEFILE_REPLACE_EXISTING);
        }
    }
    if (result) *result = (tjs_int)(ok ? 1 : 0);
    return TJS_S_OK;
}

// Receives one call per member from EnumMembers: (name, flags, value).
class MemberCollector : public tTJSDispatch {
  public:
    iTJSDispatch2 *array = nullptr;
    tjs_error TJS_INTF_METHOD FuncCall(tjs_uint32, const tjs_char *membername,
                                       tjs_uint32 *, tTJSVariant *result,
                                       tjs_int numparams, tTJSVariant **param,
                                       iTJSDispatch2 *) {
        if (membername) return TJS_E_MEMBERNOTFOUND;
        if (numparams >= 1 && array) {
            tTJSVariant name(*param[0]);
            tTJSVariant *args[] = {&name};
            array->FuncCall(0, L"add", nullptr, nullptr, 1, args, array);
        }
        if (result) *result = (tjs_int)1;   // go on
        return TJS_S_OK;
    }
};

// members(obj) -> array of member names. Values are not fetched, so no
// property getter runs. For the scout report only.
tjs_error TJS_INTF_METHOD m_members(tTJSVariant *result, tjs_int numparams,
                                    tTJSVariant **param, iTJSDispatch2 *) {
    if (numparams < 1) return TJS_E_BADPARAMCOUNT;
    if (param[0]->Type() != tvtObject) return TJS_E_INVALIDPARAM;
    tTJSVariantClosure target = param[0]->AsObjectClosureNoAddRef();
    if (!target.Object) return TJS_E_INVALIDPARAM;
    iTJSDispatch2 *arr = TJSCreateArrayObject();
    MemberCollector *col = new MemberCollector();
    col->array = arr;
    tTJSVariantClosure cb(col, nullptr);
    target.Object->EnumMembers(TJS_IGNOREPROP | TJS_ENUM_NO_VALUE, &cb,
                               target.Object);
    col->Release();
    if (result) *result = tTJSVariant(arr, arr);
    arr->Release();
    return TJS_S_OK;
}

// modules() -> array of the file names of every DLL/tpm in the process
// below the exe folder (the game's plugins), for the scout report.
tjs_error TJS_INTF_METHOD m_modules(tTJSVariant *result, tjs_int,
                                    tTJSVariant **, iTJSDispatch2 *) {
    iTJSDispatch2 *arr = TJSCreateArrayObject();
    std::wstring exe = module_dir(nullptr);
    HMODULE mods[512];
    DWORD need = 0;
    if (K32EnumProcessModules(GetCurrentProcess(), mods, sizeof(mods), &need)) {
        DWORD n = need / sizeof(HMODULE);
        if (n > 512) n = 512;
        for (DWORD i = 0; i < n; i++) {
            wchar_t buf[MAX_PATH * 2];
            DWORD len = GetModuleFileNameW(mods[i], buf, MAX_PATH * 2);
            if (!len || len >= MAX_PATH * 2) continue;
            std::wstring p(buf, len);
            if (p.size() <= exe.size() ||
                CompareStringOrdinal(p.c_str(), (int)exe.size(), exe.c_str(),
                                     (int)exe.size(), TRUE) != CSTR_EQUAL)
                continue;
            tTJSVariant v(ttstr(p.substr(exe.size()).c_str()));
            tTJSVariant *args[] = {&v};
            arr->FuncCall(0, L"add", nullptr, nullptr, 1, args, arr);
        }
    }
    if (result) *result = tTJSVariant(arr, arr);
    arr->Release();
    return TJS_S_OK;
}

void put(iTJSDispatch2 *obj, const wchar_t *name, const tTJSVariant &v) {
    tTJSVariant val(v);
    obj->PropSet(TJS_MEMBERENSURE, name, nullptr, &val, obj);
}

void put_method(iTJSDispatch2 *obj, const wchar_t *name,
                tTJSNativeClassMethodCallback cb) {
    iTJSDispatch2 *m = TJSCreateNativeClassMethod(cb);
    tTJSVariant v(m);   // holds its own reference
    m->Release();
    put(obj, name, v);
}

// Folder of this module relative to the exe folder: "plugin/", or "" when
// it is the exe folder itself. under_exe is false when it is not below it.
std::wstring plugin_sub(bool *under_exe) {
    std::wstring exe = module_dir(nullptr), me = module_dir(g_module);
    *under_exe = !exe.empty() && me.size() >= exe.size() &&
                 CompareStringOrdinal(me.c_str(), (int)exe.size(), exe.c_str(),
                                      (int)exe.size(), TRUE) == CSTR_EQUAL;
    if (!*under_exe) return L"";
    std::wstring sub = me.substr(exe.size());
    for (auto &c : sub)
        if (c == L'\\') c = L'/';
    return sub;
}

bool global_has(const wchar_t *name) {
    iTJSDispatch2 *global = TVPGetScriptDispatch();
    if (!global) return false;
    tTJSVariant v;
    bool has = TJS_SUCCEEDED(global->PropGet(0, name, nullptr, &v, global)) &&
               v.Type() != tvtVoid;
    global->Release();
    return has;
}

void put_global_string(const wchar_t *name, const wchar_t *value) {
    iTJSDispatch2 *global = TVPGetScriptDispatch();
    if (!global) return;
    tTJSVariant v{ttstr(value)};
    global->PropSet(TJS_MEMBERENSURE, name, nullptr, &v, global);
    global->Release();
}

void install_native() {
    iTJSDispatch2 *global = TVPGetScriptDispatch();
    if (!global) return;
    iTJSDispatch2 *obj = TJSCreateDictionaryObject();
    bool under_exe = false;
    std::wstring sub = plugin_sub(&under_exe);
    put(obj, L"version", ttstr(KP_VERSION));
    put(obj, L"pluginSub", ttstr(sub.c_str()));
    put(obj, L"underExe", (tjs_int)(under_exe ? 1 : 0));
    put(obj, L"pluginStorage",
        TVPNormalizeStorageName(ttstr(module_dir(g_module).c_str())));
    put_method(obj, L"log", m_log);
    put_method(obj, L"writeSession", m_write_session);
    put_method(obj, L"members", m_members);
    put_method(obj, L"modules", m_modules);
    tTJSVariant v(obj, obj);
    obj->Release();
    global->PropSet(TJS_MEMBERENSURE, L"KiriPanelNative", nullptr, &v, global);
    global->Release();
}

// Plain ASCII on purpose. Executed without a context, so every function
// expression here must be `incontextof global`; otherwise unqualified names
// such as System resolve against an empty object. Runs once at load; every failure is caught here
// and logged, so a game that is not KAG-based, or not one we know, starts
// as normal. hook.tjs is looked up in a kiripanel/ folder (see hookDir); with
// this module under the exe folder, System.exePath gives the storage form the
// engine expects.
const wchar_t kBootstrap[] = LR"TJS(
try {
	global.kiripanel_boot = %[];
	kiripanel_boot.started = System.getTickCount();
	// kiripanel/ next to this module, else next to the exe, else under
	// plugin/ (the layout of version 0.2).
	kiripanel_boot.hookDir = function()
	{
		var n = global.KiriPanelNative;
		var base = n.underExe ? System.exePath + n.pluginSub : n.pluginStorage;
		if (base.length == 0 || base.charAt(base.length - 1) != "/")
			base += "/";
		var dirs = [base + "kiripanel/", System.exePath + "kiripanel/",
			System.exePath + "plugin/kiripanel/"];
		for (var i = 0; i < dirs.count; i++)
			if (Storages.isExistentStorage(dirs[i] + "hook.tjs"))
				return dirs[i];
		return dirs[0];
	} incontextof global;
	kiripanel_boot.onTimer = function()
	{
		var b = global.kiripanel_boot;
		var step = "check";
		try
		{
			var ready = typeof global.kag != "undefined" && global.kag !== void
				&& isvalid(global.kag) && typeof global.kag.conductor != "undefined"
				&& global.kag.conductor !== void;
			if (!ready)
			{
				if (System.getTickCount() - b.started > 120000)
				{
					b.timer.enabled = false;
					KiriPanelNative.log("no KAG window after 120 s; hook not installed");
				}
				return;
			}
			b.timer.enabled = false;
			if (typeof global.hk_panel_bridge != "undefined")
			{
				KiriPanelNative.log("hook already installed by the game folder's script");
				return;
			}
			step = "locate";
			global.kiripanel_dir = b.hookDir();
			var s = global.kiripanel_dir + "hook.tjs";
			if (!Storages.isExistentStorage(s))
			{
				KiriPanelNative.log("hook script missing: " + s);
				return;
			}
			step = "run " + s;
			Scripts.execStorage(s);
			KiriPanelNative.log("hook installed from " + s);
		}
		catch(e)
		{
			try { b.timer.enabled = false; } catch(e2) {}
			var msg = "hook failed at " + step + ": " + e.message;
			try { if (e.trace !== void) msg += " | trace: " + e.trace; } catch(e3) {}
			KiriPanelNative.log(msg);
		}
	} incontextof global;
	kiripanel_boot.timer = new Timer(kiripanel_boot.onTimer, "");
	kiripanel_boot.timer.interval = 100;
	kiripanel_boot.timer.enabled = true;
	KiriPanelNative.log("loaded " + KiriPanelNative.version + ", waiting for the game");
} catch(e) {
	try { KiriPanelNative.log("bootstrap failed: " + e.message); } catch(e2) {}
}
)TJS";

// Links to the engine (once) and starts the bootstrap. Called from the
// tpm's V2Link, or by the proxy build with the exporter it caught.
void kp_link(iTVPFunctionExporter *exporter, const wchar_t *how) {
    if (g_linked || !exporter) return;
    g_exporter = exporter;
    if (!engine_has_everything()) {
        file_log(L"not installing: this engine build lacks functions it needs");
        return;   // idle: the game is unaffected
    }
    TVPInitImportStub(exporter);
    g_linked = true;
    try {
        if (global_has(L"KiriPanelNative")) {
            // e.g. both kiripanel.tpm and the version.dll proxy installed
            g_idle = true;
            file_log(L"another copy of kiripanel is already loaded; this one stays idle");
            return;
        }
        install_native();
        put_global_string(L"kiripanel_loaded_by", how);
        TVPExecuteScript(ttstr(kBootstrap));
    } catch (...) {
        // the bootstrap catches its own TJS errors; this is a last resort
        file_log(L"bootstrap raised past its own handler");
    }
}

}  // namespace

#ifdef KP_PROXY
#include "proxy.inc"
#else

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = inst;
        DisableThreadLibraryCalls(inst);
    }
    return TRUE;
}

extern "C" HRESULT __stdcall V2Link(iTVPFunctionExporter *exporter) {
    kp_link(exporter, L"tpm");
    return S_OK;
}

extern "C" HRESULT __stdcall V2Unlink() {
    if (g_linked) {
        try {
            iTJSDispatch2 *global = g_idle ? nullptr : TVPGetScriptDispatch();
            if (global) {
                // stop the timer before this module's code goes away
                TVPExecuteScript(ttstr(
                    L"try { if (typeof global.kiripanel_boot != 'undefined') "
                    L"invalidate kiripanel_boot.timer; } catch(e) {}"));
                global->DeleteMember(0, L"KiriPanelNative", nullptr, global);
                global->Release();
            }
        } catch (...) {
        }
        TVPUninitImportStub();
        g_linked = false;
    }
    return S_OK;
}

#endif
