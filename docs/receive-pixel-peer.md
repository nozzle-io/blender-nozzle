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
`frame_get_info` exposes effective `semantic_format` and `raw_semantic_format`;
only an unknown raw semantic format falls back to core's resolved semantics.
The current-core public C++ external-texture producer must report raw BGRA8 and
resolved BGRA8 to an independent native C API observer before Blender receives
it. A separate compatibility mode overrides only its test-owned texture
descriptor's semantic field to UNKNOWN before external publication, emulating
the old descriptor defect. This is not an old-binary test or a claim that the
current sender naturally produces UNKNOWN. No shared-memory fields or production
fallback code are patched. Both modes exercise the packaged C extension's actual
`frame_get_info`: raw semantics remain observable, while effective semantics are
BGRA8. Fresh receivers then exercise production `receive_to_image` and compare
every pixel, including alpha. These are macOS native-peer compatibility checks,
not Python-stub normalization tests.

The image helper accepts RGBA8/BGRA8 UNORM or sRGB, RGBA16_FLOAT and
RGBA32_FLOAT. Channel order comes from the mapping; semantic metadata must be
compatible. All 8-bit color is assumed sRGB-encoded (including untagged UNORM),
normalized and decoded once. Alpha is only normalized, and half/float values retain their range and float32
precision. Unsupported layouts/semantic combinations raise before touching the
previous image. This does not add arbitrary integer/depth/channel expansion.
Untagged linear 8-bit content cannot be distinguished automatically; it is decoded
under this image-receiver policy. Float input remains linear. The received image
keeps the active OCIO default float role, never a Non-Color/data tag. A temporary
1x1 float image supplies the current role without hardcoded OCIO names. The CI
oracle compares with its own fresh float reference and requires `is_data == False`;
it starts with a same-size byte image, resizes, and replaces existing images with
incorrect sRGB or Non-Color settings.
An attached material image node must keep pointing to the received image through
byte-to-float upgrades and resizing. Replacement buffers are populated before
existing users are remapped and the old image is removed.
Separate same-size RGBA32_FLOAT then RGBA8_UNORM receives leave the correct role
untouched and must reuse the same image datablock, update every pixel to the new
values, preserve its material user, and leave no temporary role image or numbered
duplicate. These use independent native senders; they test Blender image reuse,
not sender reconnection or multiple-frame streaming on one native receiver.
The single-frame receive operator catches conversion/native errors, reports an
ERROR and cancels. Its production `execute` method is exercised with a real R8
native peer and a captured report callback, not a rendered GUI notification.

The native matrix covers UNORM byte, half and float only: pinned core rejects
sRGB IOSurface creation. sRGB conversion is supplementary CPU-only evidence,
not a claim that the Metal native transport supports sRGB.

Received images are live float buffers. They are updated without per-frame
`image.pack()` encoding; automatic packed-file persistence is not part of the
receive contract. Frame handles are always released in `finally`. The writable
byte-export API and GL sender remain outside this change.
For 8-bit sources the float image consumes approximately four times the image
pixel-buffer memory of the former byte image (excluding temporary CPU copies).
