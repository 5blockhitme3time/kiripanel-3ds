#pragma once

#include <cstdint>

// Writes an RGB565 buffer as a PNG, each pixel repeated scale x scale.
bool write_png(const char *path, const uint16_t *rgb565, int w, int h,
               int scale);
