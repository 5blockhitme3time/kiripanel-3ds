/*
 * Minimal PNG writer (RGB, uncompressed deflate blocks) so the preview tool
 * needs nothing beyond FreeType.
 */
#include "png.hpp"

#include <cstdio>
#include <vector>

namespace {

uint32_t crc_table[256];
bool crc_ready = false;

uint32_t crc(const uint8_t *p, size_t n, uint32_t c = 0xFFFFFFFFu) {
    if (!crc_ready) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t v = i;
            for (int k = 0; k < 8; k++) {
                v = (v & 1) ? 0xEDB88320u ^ (v >> 1) : v >> 1;
            }
            crc_table[i] = v;
        }
        crc_ready = true;
    }
    for (size_t i = 0; i < n; i++) {
        c = crc_table[(c ^ p[i]) & 0xFF] ^ (c >> 8);
    }
    return c;
}

void be32(std::vector<uint8_t> &v, uint32_t x) {
    v.push_back(x >> 24);
    v.push_back(x >> 16);
    v.push_back(x >> 8);
    v.push_back(x);
}

void chunk(FILE *f, const char *type, const std::vector<uint8_t> &data) {
    std::vector<uint8_t> buf;
    be32(buf, (uint32_t)data.size());
    buf.insert(buf.end(), type, type + 4);
    buf.insert(buf.end(), data.begin(), data.end());
    uint32_t c = crc(buf.data() + 4, buf.size() - 4) ^ 0xFFFFFFFFu;
    be32(buf, c);
    fwrite(buf.data(), 1, buf.size(), f);
}

}  // namespace

bool write_png(const char *path, const uint16_t *rgb565, int w, int h,
               int scale) {
    int ow = w * scale, oh = h * scale;
    std::vector<uint8_t> raw;
    raw.reserve((size_t)(ow * 3 + 1) * oh);
    for (int y = 0; y < oh; y++) {
        raw.push_back(0);
        for (int x = 0; x < ow; x++) {
            uint16_t p = rgb565[(y / scale) * w + x / scale];
            int r = (p >> 11) & 31, g = (p >> 5) & 63, b = p & 31;
            raw.push_back((uint8_t)((r * 527 + 23) >> 6));
            raw.push_back((uint8_t)((g * 259 + 33) >> 6));
            raw.push_back((uint8_t)((b * 527 + 23) >> 6));
        }
    }
    std::vector<uint8_t> z = {0x78, 0x01};
    uint32_t a = 1, bsum = 0;
    for (uint8_t c : raw) {
        a = (a + c) % 65521;
        bsum = (bsum + a) % 65521;
    }
    size_t pos = 0;
    do {
        size_t n = raw.size() - pos > 65535 ? 65535 : raw.size() - pos;
        z.push_back(pos + n == raw.size() ? 1 : 0);
        z.push_back(n & 0xFF);
        z.push_back(n >> 8);
        z.push_back(~n & 0xFF);
        z.push_back((~n >> 8) & 0xFF);
        z.insert(z.end(), raw.begin() + pos, raw.begin() + pos + n);
        pos += n;
    } while (pos < raw.size());
    be32(z, (bsum << 16) | a);

    FILE *f = fopen(path, "wb");
    if (!f) {
        return false;
    }
    const uint8_t sig[8] = {0x89, 'P', 'N', 'G', 13, 10, 26, 10};
    fwrite(sig, 1, 8, f);
    std::vector<uint8_t> ihdr;
    be32(ihdr, ow);
    be32(ihdr, oh);
    ihdr.insert(ihdr.end(), {8, 2, 0, 0, 0});
    chunk(f, "IHDR", ihdr);
    chunk(f, "IDAT", z);
    chunk(f, "IEND", {});
    fclose(f);
    return true;
}
