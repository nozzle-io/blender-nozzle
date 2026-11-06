#include "../src/pixel_rows.h"
#include <stdio.h>

int main(void) {
    unsigned char source[36], output[28];
    memset(source, 0xee, sizeof(source));
    for (int y = 0; y < 3; ++y)
        for (int x = 0; x < 8; ++x) source[y*12+x] = (unsigned char)(y*30+x);
    NozzleMappedPixels pixels = {0};
    pixels.width = 2;
    pixels.height = 3;
    for (int negative = 0; negative < 2; ++negative) {
        pixels.data = source + (negative ? 24 : 0);
        pixels.row_stride_bytes = negative ? -12 : 12;
        memset(output, 0xa5, sizeof(output));
        if (!nozzle_blender_copy_rows(&pixels, 8, output, 24)) return 1;
        for (int y = 0; y < 3; ++y)
            for (int x = 0; x < 8; ++x)
                if (output[y*8+x] != (negative ? 2-y : y)*30+x) return 2;
        for (int i = 24; i < 28; ++i) if (output[i] != 0xa5) return 3;
    }
    const int64_t bad_strides[] = {0, 7, -7, INT64_MIN, INT64_MAX};
    pixels.data = source;
    for (size_t i = 0; i < sizeof(bad_strides)/sizeof(*bad_strides); ++i) {
        pixels.row_stride_bytes = bad_strides[i];
        memset(output, 0xa5, sizeof(output));
        if (nozzle_blender_copy_rows(&pixels, 8, output, sizeof(output))) return 4;
        for (size_t j = 0; j < sizeof(output); ++j) if (output[j] != 0xa5) return 5;
    }
    pixels.row_stride_bytes = 12;
    if (nozzle_blender_copy_rows(&pixels, 8, output, 23)) return 6;
    puts("BLENDER_PIXEL_ROWS PASS padded/negative/guards/short-capacity/overflow (synthetic, not GPU evidence)");
    return 0;
}
