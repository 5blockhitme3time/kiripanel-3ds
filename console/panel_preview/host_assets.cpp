/*
 * PC stand-in for panel_assets.cpp: the same font file, read from disk
 * instead of linked into the binary.
 */
#include "panel_assets.hpp"

#include <cstdio>
#include <vector>

namespace {
std::vector<uint8_t> g_font;
}

bool host_load_font(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        return false;
    }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    g_font.resize(n > 0 ? (size_t)n : 0);
    size_t got = fread(g_font.data(), 1, g_font.size(), f);
    fclose(f);
    return got == g_font.size() && !g_font.empty();
}

const uint8_t *panel_font_data() { return g_font.data(); }

size_t panel_font_size() { return g_font.size(); }
