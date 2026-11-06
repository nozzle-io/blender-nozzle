# Blender receive pixel evidence

The macOS Blender package CI job runs `tests/blender_receive_peer.py` inside
Blender 4.3.2, using the packaged production addon. A separate test-only native
library publishes through the C API and writes actual Metal storage lanes. It
does not use the Blender sender or the Blender pixel conversion.

The oracle reads actual `bpy.data.images` pixels after the production
`NozzleEngine.receive_to_image` call. It compares all pixels, including alpha,
bottom-up image row placement, negative/HDR float values and low mantissa bits.
Widths 65 and 641 exercise native padding; width 64 isolates numeric range from
row padding. The peer writes through top-left and bottom-left views. This tests
both producer mapping origins, not two distinct receiver storage origins.

Setup failures, missing Metal, exceptions and pixel mismatches fail CI, with no
SKIP or swallowed exit status. `--python-exit-code 1` makes Blender propagate
script exceptions. These are same-process native-peer CPU image tests, not
viewport screenshots, cross-process or GL sender tests. Windows/Linux package
jobs remain build/import evidence, not equivalent pixel evidence. Test peer
libraries and scripts are not part of addon zip contents.

Blender's `Image.pixels` accepts floats, not raw byte components. Primary source:
https://github.com/blender/blender/blob/v4.3.2/source/blender/makesrna/intern/rna_image.cc#L656

## Receive contract

`frame_lock_pixels` returns an owned, top-left, tightly packed byte copy in the
actual storage format (the tuple stride describes the copy, not the borrowed
native view). It uses resolved bytes-per-pixel only when resolved storage agrees
with the mapping, checks signed row bounds and checked unlock, and never copies
native padding. `tests/pixel_rows.c` supplements the native test with synthetic
negative source stride, padding, capacity and overflow checks.

The image helper accepts RGBA8/BGRA8 UNORM or sRGB, RGBA16_FLOAT and
RGBA32_FLOAT. Channel order comes from the mapping, while sRGB transfer comes
from frame semantic metadata. Byte values are normalized, sRGB RGB is decoded
once, alpha is unchanged, and half/float values retain their range and float32
precision. Unsupported layouts/semantic combinations raise before touching the
previous image. This does not add arbitrary integer/depth/channel expansion.
UNORM without an sRGB semantic tag is assumed linear, even if a producer put
encoded values there; there is no heuristic detection. The received image uses
the active OCIO data role (`colorspace_settings.is_data = True`) to avoid any
second input color transform. The CI oracle checks that setting, starts with a
same-size byte image, resizes, and resets an existing image's wrong sRGB setting.

The native matrix covers UNORM byte, half and float only: pinned core rejects
sRGB IOSurface creation. sRGB conversion is supplementary CPU-only evidence,
not a claim that the Metal native transport supports sRGB.

Received images are live float buffers. They are updated without per-frame
`image.pack()` encoding; automatic packed-file persistence is not part of the
receive contract. Frame handles are always released in `finally`. The writable
byte-export API and GL sender remain outside this change.
For 8-bit sources the float image consumes approximately four times the image
pixel-buffer memory of the former byte image (excluding temporary CPU copies).
