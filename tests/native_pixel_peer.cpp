#include <nozzle/nozzle_c.h>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace {
struct resources {
    NozzleSender *sender = nullptr;
    NozzleFrame *frame = nullptr;
    NozzlePixelMapping *mapping = nullptr;
    bool committed = false;
    ~resources() {
        nozzle_pixel_mapping_unlock(&mapping);
        if(frame) {
            if(sender && !committed) (void)nozzle_sender_discard_frame(sender, frame);
            nozzle_frame_release(frame);
        }
        if(sender) nozzle_sender_destroy(sender);
    }
};
bool ok(NozzleErrorCode error, const char *where) {
    if(error == NOZZLE_OK) return true;
    std::fprintf(stderr, "native peer %s error=%d\n", where, (int)error);
    return false;
}
}

extern "C" void *blender_peer_publish(const char *name, int width, int height,
                                      int format, int bottom_up) {
    resources peer;
    NozzleSenderDesc desc{};
    desc.name = name;
    desc.application_name = "Blender native peer";
    desc.ring_buffer_size = 3;
    desc.fallback_flags_valid = 1;
    desc.fallback_flags = NOZZLE_FALLBACK_SAFE_DEFAULTS;
    if(!ok(nozzle_sender_create(&desc, &peer.sender), "create")) return nullptr;
    NozzleSenderInfoSnapshot info{};
    if(!ok(nozzle_sender_get_info_snapshot(peer.sender, &info), "info") ||
       info.backend != NOZZLE_BACKEND_METAL) {
        std::fprintf(stderr, "Metal required; no SKIP\n");
        return nullptr;
    }
    auto requested = static_cast<NozzleTextureFormat>(format);
    if(!ok(nozzle_sender_acquire_writable_frame(peer.sender, width, height, requested, &peer.frame), "acquire")) return nullptr;
    NozzleMappedPixels pixels{};
    if(!ok(nozzle_frame_lock_writable_pixels_mapping_with_origin(peer.frame,
        bottom_up ? NOZZLE_ORIGIN_BOTTOM_LEFT : NOZZLE_ORIGIN_TOP_LEFT,
        &peer.mapping, &pixels), "map")) return nullptr;
    int component_bytes = 1;
    if(requested == NOZZLE_FORMAT_RGBA16_FLOAT) component_bytes = 2;
    if(requested == NOZZLE_FORMAT_RGBA32_FLOAT) component_bytes = 4;
    const bool bgra = pixels.format == NOZZLE_FORMAT_BGRA8_UNORM || pixels.format == NOZZLE_FORMAT_BGRA8_SRGB;
    if(!pixels.data || pixels.width != (uint32_t)width || pixels.height != (uint32_t)height ||
       (component_bytes == 1 && !bgra) || (component_bytes != 1 && pixels.format != requested)) {
        std::fprintf(stderr, "unexpected native storage requested=%d actual=%d\n", format, (int)pixels.format);
        return nullptr;
    }
    std::printf("BLENDER_NATIVE requested=%d storage=%d size=%dx%d stride=%lld producer_bottom_up=%d\n",
                format, (int)pixels.format, width, height, (long long)pixels.row_stride_bytes, bottom_up);
    for(int row_index = 0; row_index < height; ++row_index) {
        const int y = bottom_up ? height - 1 - row_index : row_index;
        auto *row = static_cast<uint8_t *>(pixels.data) + row_index * pixels.row_stride_bytes;
        for(int x = 0; x < width; ++x) {
            const uint8_t bytes[] = {(uint8_t)(190+y), (uint8_t)(20+x%193), (uint8_t)(40+y), (uint8_t)(149+x%73)};
            const uint16_t halves[] = {0x3c01, 0xb801, 0x4001, 0x3801};
            const uint32_t floats[] = {0x3f800001, 0xbf000001, 0x40000001, 0x3f000001};
            for(int c = 0; c < 4; ++c) {
                if(component_bytes == 1) row[4*x+c] = bytes[bgra && c != 1 && c != 3 ? 2-c : c];
                else if(component_bytes == 2) {
                    uint16_t bits = halves[c] + x%19 + y*23;
                    std::memcpy(row + (4*x+c)*2, &bits, 2);
                } else {
                    uint32_t bits = floats[c] + x%19 + y*23;
                    std::memcpy(row + (4*x+c)*4, &bits, 4);
                }
            }
        }
    }
    if(!ok(nozzle_pixel_mapping_unlock_checked(&peer.mapping), "unlock") ||
       !ok(nozzle_sender_commit_frame(peer.sender, peer.frame), "commit")) return nullptr;
    peer.committed = true;
    auto *sender = peer.sender;
    peer.sender = nullptr;
    return sender;
}

extern "C" void blender_peer_destroy(void *sender) {
    nozzle_sender_destroy(static_cast<NozzleSender *>(sender));
}
