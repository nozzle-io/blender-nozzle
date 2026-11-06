#ifndef NOZZLE_BLENDER_PIXEL_ROWS_H
#define NOZZLE_BLENDER_PIXEL_ROWS_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <nozzle/nozzle_c.h>

/* Copy logical rows of a borrowed mapping, never its padding or a contiguous
 * span starting at the last physical row of a negative-stride mapping. */
static int nozzle_blender_copy_rows(const NozzleMappedPixels *pixels,
                                   size_t row_bytes, void *output, size_t capacity) {
    if (!pixels || !pixels->data || !output || !pixels->width || !pixels->height ||
        !row_bytes || row_bytes > PTRDIFF_MAX || pixels->row_stride_bytes == INT64_MIN) return 0;
    uint64_t stride = pixels->row_stride_bytes < 0
        ? (uint64_t)-pixels->row_stride_bytes : (uint64_t)pixels->row_stride_bytes;
    if (stride < row_bytes || stride > PTRDIFF_MAX ||
        pixels->height > capacity / row_bytes ||
        (pixels->height > 1 && stride > (PTRDIFF_MAX - row_bytes) / (pixels->height - 1))) return 0;
    for (uint32_t y = 0; y < pixels->height; ++y) {
        memcpy((unsigned char *)output + (size_t)y * row_bytes,
               (const unsigned char *)pixels->data + (ptrdiff_t)y * (ptrdiff_t)pixels->row_stride_bytes,
               row_bytes);
    }
    return 1;
}

#endif
