#include <nozzle/nozzle_c.h>
#include <nozzle/nozzle.hpp>
#include <nozzle/pixel_access.hpp>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <new>

namespace {
struct resources {
    NozzleSender *sender = nullptr;
    NozzleReceiver *receiver = nullptr;
    NozzleFrame *frame = nullptr;
    NozzlePixelMapping *mapping = nullptr;
    bool committed = false;
    bool owns_sender = true;
    ~resources() {
        nozzle_pixel_mapping_unlock(&mapping);
        if(frame) {
            if(sender && !committed) (void)nozzle_sender_discard_frame(sender, frame);
            nozzle_frame_release(frame);
        }
        if(sender && owns_sender) nozzle_sender_destroy(sender);
        if(receiver) nozzle_receiver_destroy(receiver);
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
    const bool monochrome = requested == NOZZLE_FORMAT_R8_UNORM;
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
       (monochrome ? pixels.format != requested :
        ((component_bytes == 1 && !bgra) || (component_bytes != 1 && pixels.format != requested)))) {
        std::fprintf(stderr, "unexpected native storage requested=%d actual=%d\n", format, (int)pixels.format);
        return nullptr;
    }
    std::printf("BLENDER_NATIVE requested=%d storage=%d size=%dx%d stride=%lld producer_bottom_up=%d\n",
                format, (int)pixels.format, width, height, (long long)pixels.row_stride_bytes, bottom_up);
    for(int row_index = 0; row_index < height; ++row_index) {
        const int y = bottom_up ? height - 1 - row_index : row_index;
        auto *row = static_cast<uint8_t *>(pixels.data) + row_index * pixels.row_stride_bytes;
        for(int x = 0; x < width; ++x) {
            if(monochrome) {
                row[x] = (uint8_t)(40+y);
                continue;
            }
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

extern "C" bool blender_peer_update(void *sender, int offset) {
    resources peer;
    peer.sender = static_cast<NozzleSender *>(sender);
    peer.owns_sender = false;
    if(!ok(nozzle_sender_acquire_writable_frame(peer.sender, 65, 3, NOZZLE_FORMAT_RGBA8_UNORM,
        &peer.frame), "live update acquire")) return false;
    NozzleMappedPixels pixels{};
    if(!ok(nozzle_frame_lock_writable_pixels_mapping_with_origin(peer.frame, NOZZLE_ORIGIN_TOP_LEFT,
        &peer.mapping, &pixels), "live update map")) return false;
    if(pixels.format != NOZZLE_FORMAT_BGRA8_UNORM || !pixels.data || pixels.row_stride_bytes < 65*4) return false;
    for(int y = 0; y < 3; ++y) {
        auto *row = static_cast<uint8_t *>(pixels.data) + y*pixels.row_stride_bytes;
        for(int x = 0; x < 65; ++x) {
            row[4*x] = 40+y+offset;
            row[4*x+1] = 20+x%193;
            row[4*x+2] = 190+y+offset;
            row[4*x+3] = 149+x%73;
        }
    }
    if(!ok(nozzle_pixel_mapping_unlock_checked(&peer.mapping), "live update unlock") ||
       !ok(nozzle_sender_commit_frame(peer.sender, peer.frame), "live update commit")) return false;
    peer.committed = true;
    return true;
}

extern "C" bool blender_peer_check_rgba32f(const char *name, int width, int height, const void *expected) {
    resources peer;
    NozzleReceiverDesc desc{};
    desc.name = name;
    NozzleAcquireDesc acquire{};
    acquire.timeout_ms = 1000;
    if(!ok(nozzle_receiver_create(&desc, &peer.receiver), "float observer create") ||
       !ok(nozzle_receiver_acquire_frame(peer.receiver, &acquire, &peer.frame), "float observer acquire")) return false;
    NozzleMappedPixels pixels{};
    if(!ok(nozzle_frame_lock_pixels_mapping_with_origin(peer.frame, NOZZLE_ORIGIN_BOTTOM_LEFT,
        &peer.mapping, &pixels), "float observer map")) return false;
    if(pixels.format != NOZZLE_FORMAT_RGBA32_FLOAT || pixels.width != (uint32_t)width ||
       pixels.height != (uint32_t)height || pixels.origin != NOZZLE_ORIGIN_BOTTOM_LEFT || !pixels.data) return false;
    for(int y = 0; y < height; ++y) {
        if(std::memcmp(static_cast<const char *>(pixels.data) + y*pixels.row_stride_bytes,
                       static_cast<const char *>(expected) + y*width*16, width*16) != 0) return false;
    }
    return ok(nozzle_pixel_mapping_unlock_checked(&peer.mapping), "float observer unlock");
}

namespace {
struct external_peer {
    nozzle::sender sender;
    nozzle::writable_frame frame;
    ~external_peer() {
        if(frame.valid()) (void)sender.discard_frame(frame);
    }
};
}

extern "C" void *blender_peer_publish_external(const char *name) {
    std::unique_ptr<external_peer> peer(new (std::nothrow) external_peer);
    if(!peer) return nullptr;
    nozzle::sender_desc desc;
    desc.name = name;
    auto sender = nozzle::sender::create(desc);
    if(!sender.ok()) return nullptr;
    peer->sender = std::move(sender.value());
    auto frame = peer->sender.acquire_writable_frame({65, 3, nozzle::texture_format::bgra8_unorm});
    if(!frame.ok()) return nullptr;
    peer->frame = std::move(frame.value());
    auto mapping = nozzle::lock_writable_pixels_mapping_with_origin(peer->frame, nozzle::texture_origin::top_left);
    if(!mapping.ok()) return nullptr;
    const auto &pixels = mapping.value().pixels();
    if(pixels.format != nozzle::texture_format::bgra8_unorm || !pixels.data || pixels.row_stride_bytes < 65*4) return nullptr;
    for(int y = 0; y < 3; ++y) {
        auto *row = static_cast<uint8_t *>(pixels.data) + y*pixels.row_stride_bytes;
        for(int x = 0; x < 65; ++x) {
            row[4*x] = 40+y;
            row[4*x+1] = 20+x%193;
            row[4*x+2] = 190+y;
            row[4*x+3] = 149+x%73;
        }
    }
    if(!mapping.value().unlock_checked().ok() ||
       !peer->sender.publish_external_texture(peer->frame.get_texture()).ok()) return nullptr;

    resources observer;
    NozzleReceiverDesc receiver_desc{};
    receiver_desc.name = name;
    NozzleAcquireDesc acquire{};
    acquire.timeout_ms = 3000;
    NozzleFrameInfo raw{};
    NozzleResolvedTextureFormat resolved{};
    if(!ok(nozzle_receiver_create(&receiver_desc, &observer.receiver), "external observer create") ||
       !ok(nozzle_receiver_acquire_frame(observer.receiver, &acquire, &observer.frame), "external observer acquire") ||
       !ok(nozzle_frame_get_info(observer.frame, &raw), "external raw info") ||
       !ok(nozzle_frame_get_resolved_format(observer.frame, &resolved), "external resolved info")) return nullptr;
    std::printf("BLENDER_EXTERNAL raw_semantic=%d resolved_semantic=%d storage=%d\n",
                (int)raw.semantic_format, (int)resolved.semantic_format, (int)resolved.storage_format);
    if(raw.semantic_format != NOZZLE_FORMAT_UNKNOWN ||
       resolved.semantic_format != NOZZLE_FORMAT_BGRA8_UNORM ||
       resolved.storage_format != NOZZLE_FORMAT_BGRA8_UNORM) return nullptr;
    return peer.release();
}

extern "C" void blender_peer_destroy_external(void *handle) {
    delete static_cast<external_peer *>(handle);
}
