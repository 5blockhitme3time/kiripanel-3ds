/*
 * proxy.inc - the system-DLL proxy builds of kiripanel (KP_PROXY).
 *
 * Some engines never load *.tpm files by themselves (KiriKiri Z 1.2.x
 * scans for them but does not call the loader). Every KiriKiri engine we
 * have seen imports version.dll and mpr.dll, and Windows looks for those in
 * the exe folder first, so a DLL of that name there is loaded with the game
 * without changing any of the game's files. version.dll is the normal one;
 * mpr.dll (KP_PROXY_MPR) is for games that already ship a version.dll of
 * their own, which is common for patches.
 *
 * It does two things:
 *  - forwards all of the DLL's functions to the real one in the system
 *    folder (the exports are 16-byte jump stubs patched at load time, so
 *    no function signature has to be repeated here; the export tables are
 *    generated from the system DLL by gen_proxy.py);
 *  - hooks GetProcAddress. When the engine looks up "V2Link" in any of its
 *    own plugins it gets a thunk that hands the engine's function exporter
 *    to kp_link() - exactly as if the engine had loaded kiripanel.tpm - and
 *    then jumps on to the plugin's V2Link, which sees the engine's own call.
 * So a game that loads no plugin at all is not reached this way; the log
 * then says so when the game exits.
 *
 * The hook stays in place for the life of the process: removing it would
 * suspend the game's threads for nothing, and a lookup that is not
 * "V2Link" only costs one string comparison.
 *
 * MinHook (minhook/, BSD 2-clause, v1.3.3) does the hooking.
 */
#include "minhook/include/MinHook.h"

namespace {

// ---- forwarders ----------------------------------------------------------

// kForwarded, KP_PROXY_DLL and KP_FOR_EACH_STUB. The .def exports
// kForwarded[i] as kp_s<i>; the DLL's own forwarders (version.dll's
// VerLanguageNameA/W -> kernel32) are forwarders in the .def as well.
#ifdef KP_PROXY_MPR
#include "gen/mpr_exports.h"
#else
#include "gen/version_exports.h"
#endif
const int kForwardCount = sizeof(kForwarded) / sizeof(kForwarded[0]);

HMODULE g_real = nullptr;

}  // namespace

// One stub per export, in a section of their own that is made writable only
// while load_real() fills in the targets. A stub jumps to its target without
// touching the stack or any register the callee reads, so it works for every
// calling convention.
//   x86:  push imm32 ; ret                 68 xx xx xx xx C3
//   x64:  jmp [rip+0] ; dq target          FF 25 00 00 00 00 xx*8
#pragma section(".kpstub", read, execute)
#ifdef _WIN64
#define KP_STUB {0xFF, 0x25, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xCC, 0xCC}
#define KP_STUB_AT 6
#else
#define KP_STUB {0x68, 0, 0, 0, 0, 0xC3, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC}
#define KP_STUB_AT 1
#endif
// Each stub is its own symbol so the .def can export it by name.
#define KP_S(n) __declspec(allocate(".kpstub")) unsigned char kp_s##n[16] = KP_STUB;
extern "C" {
KP_FOR_EACH_STUB(KP_S)
}
#undef KP_S

namespace {

#define KP_S(n) kp_s##n,
unsigned char *const kStubs[kForwardCount] = {KP_FOR_EACH_STUB(KP_S)};
#undef KP_S

BOOL WINAPI missing_function() {
    SetLastError(ERROR_PROC_NOT_FOUND);
    return FALSE;
}

// Loads the system's copy of the DLL and points every stub at it. Without
// it the stubs would jump to 0, so failing here fails the DLL load (Windows
// then reports the missing DLL instead of the game crashing later).
bool load_real() {
    wchar_t dir[MAX_PATH];
    UINT n = GetSystemDirectoryW(dir, MAX_PATH);   // SysWOW64 for 32-bit
    if (n == 0 || n >= MAX_PATH - 16) return false;
    std::wstring path = std::wstring(dir, n) + L"\\" KP_PROXY_DLL;
    g_real = LoadLibraryW(path.c_str());
    if (!g_real) return false;
    DWORD old;
    for (int i = 0; i < kForwardCount; i++) {
        // GetProcAddress is not hooked yet here.
        void *target = (void *)GetProcAddress(g_real, kForwarded[i]);
        // Only possible for a function this Windows does not have, which
        // nothing on it can be calling.
        if (!target) target = (void *)&missing_function;
        unsigned char *s = kStubs[i];
        if (!VirtualProtect(s, 16, PAGE_EXECUTE_READWRITE, &old)) return false;
        memcpy(s + KP_STUB_AT, &target, sizeof(target));
        VirtualProtect(s, 16, old, &old);
    }
    FlushInstructionCache(GetCurrentProcess(), nullptr, 0);
    return true;
}

// ---- catching the function exporter --------------------------------------

typedef FARPROC(WINAPI *GetProcAddressProc)(HMODULE, LPCSTR);

GetProcAddressProc g_gpa_real = nullptr;
bool g_hooked = false;
volatile LONG g_plugins = 0;   // plugin V2Link calls seen

// The engine is handed a small thunk instead of a plugin's V2Link. The
// thunk passes the exporter (the call's only argument) to kp_capture() and
// then JUMPS to the real V2Link, so the plugin runs exactly as if the engine
// had called it: same stack, same return address into the engine, whatever
// its calling convention. (Some plugins check that their caller is the
// engine; a wrapper that called them would be refused.)
//   x86: push [esp+4]; call [capture]; add esp,4; jmp [target_i]
//   x64: push rcx; sub rsp,32; call [capture]; add rsp,32; pop rcx;
//        jmp [target_i]
// Targets and the capture address live in a data page next to the thunks;
// only that page is ever written after setup.
const int kSlots = 64;
const int kThunk = 32;
unsigned char *g_code = nullptr;      // kSlots thunks, read + execute
void *volatile *g_data = nullptr;     // [0] capture, [1 + i] target of thunk i

void __cdecl kp_capture(iTVPFunctionExporter *exporter) {
    if (InterlockedIncrement(&g_plugins) == 1)
        file_log(L"first plugin linking; installing kiripanel");
    kp_link(exporter, KP_PROXY_DLL);
}

void put32(unsigned char *p, uint32_t v) { memcpy(p, &v, 4); }

bool make_thunks() {
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    size_t page = si.dwPageSize;
    // code and data together, so x64 can reach the data RIP-relative
    unsigned char *mem = (unsigned char *)VirtualAlloc(
        nullptr, page * 2, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!mem) return false;
    g_code = mem;
    g_data = (void *volatile *)(mem + page);
    g_data[0] = (void *)&kp_capture;
    for (int i = 0; i < kSlots; i++) {
        unsigned char *t = g_code + i * kThunk;
        memset(t, 0xCC, kThunk);
#ifdef _WIN64
        auto rel = [&](int at, void *volatile *slot) {   // disp32 for [rip+disp]
            put32(t + at, (uint32_t)((unsigned char *)slot - (t + at + 4)));
        };
        unsigned char c[] = {
            0x51,                               // push rcx
            0x48, 0x83, 0xEC, 0x20,             // sub rsp, 32
            0xFF, 0x15, 0, 0, 0, 0,             // call [rip+capture]
            0x48, 0x83, 0xC4, 0x20,             // add rsp, 32
            0x59,                               // pop rcx
            0xFF, 0x25, 0, 0, 0, 0,             // jmp [rip+target_i]
        };
        memcpy(t, c, sizeof(c));
        rel(7, &g_data[0]);
        rel(18, &g_data[1 + i]);
#else
        unsigned char c[] = {
            0xFF, 0x74, 0x24, 0x04,             // push [esp+4]
            0xFF, 0x15, 0, 0, 0, 0,             // call [capture]
            0x83, 0xC4, 0x04,                   // add esp, 4
            0xFF, 0x25, 0, 0, 0, 0,             // jmp [target_i]
        };
        memcpy(t, c, sizeof(c));
        put32(t + 6, (uint32_t)(uintptr_t)&g_data[0]);
        put32(t + 15, (uint32_t)(uintptr_t)&g_data[1 + i]);
#endif
    }
    DWORD old;
    if (!VirtualProtect(g_code, page, PAGE_EXECUTE_READ, &old)) return false;
    FlushInstructionCache(GetCurrentProcess(), g_code, page);
    return true;
}

FARPROC WINAPI hook_gpa(HMODULE module, LPCSTR name) {
    FARPROC p = g_gpa_real(module, name);
    if (!p || g_linked || !g_code || module == g_module || IS_INTRESOURCE(name) ||
        strcmp(name, "V2Link") != 0)
        return p;
    if (g_plugins == 0) {
        wchar_t buf[MAX_PATH];
        DWORD n = GetModuleFileNameW(module, buf, MAX_PATH);
        std::wstring m(buf, n < MAX_PATH ? n : 0);
        file_log((L"first plugin lookup: " + m.substr(m.find_last_of(L"\\/") + 1)).c_str());
    }
    for (int i = 0; i < kSlots; i++) {
        PVOID was = InterlockedCompareExchangePointer(
            (PVOID volatile *)&g_data[1 + i], (PVOID)p, nullptr);
        if (was == nullptr || was == (PVOID)p)
            return (FARPROC)(g_code + i * kThunk);
    }
    return p;   // out of slots: that plugin links without us seeing it
}

std::wstring exe_name() {
    wchar_t buf[MAX_PATH * 2];
    DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH * 2);
    std::wstring p(buf, n < MAX_PATH * 2 ? n : 0);
    size_t s = p.find_last_of(L"\\/");
    return s == std::wstring::npos ? p : p.substr(s + 1);
}

void install_hook() {
    std::wstring msg = KP_PROXY_DLL L" " KP_VERSION L" loaded into " + exe_name();
    if (!make_thunks() || MH_Initialize() != MH_OK ||
        MH_CreateHookApi(L"kernel32", "GetProcAddress", (LPVOID)&hook_gpa,
                         (LPVOID *)&g_gpa_real) != MH_OK ||
        MH_EnableHook(MH_ALL_HOOKS) != MH_OK) {
        file_log((msg + L"; could not hook GetProcAddress, staying idle").c_str());
        return;
    }
    g_hooked = true;
    file_log((msg + L", waiting for the engine to link a plugin").c_str());
}

}  // namespace

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = inst;
        DisableThreadLibraryCalls(inst);
        if (!load_real()) return FALSE;
        install_hook();
    } else if (reason == DLL_PROCESS_DETACH && reserved != nullptr) {
        // The process is exiting. Leave the hook alone (other threads are
        // already gone), just say why nothing happened if nothing did.
        if (g_hooked && !g_linked)
            file_log(g_plugins
                ? L"exit: plugins were linked but kiripanel was not installed (see above)"
                : L"exit: the engine linked no plugin, so kiripanel could not start "
                  L"(not KiriKiri, a launcher, or a game without plugins)");
    }
    return TRUE;
}
