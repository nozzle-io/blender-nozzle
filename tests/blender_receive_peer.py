"""Real Blender image values from an independent native C API producer."""
import ctypes
import math
import os
import struct
import sys
import traceback
from pathlib import Path

import bpy


def expected_pixel(kind, x, y):
    if kind == "RGBA16_FLOAT":
        return [struct.unpack("=e", struct.pack("=H", bits + x % 19 + y * 23))[0]
                for bits in (0x3c01, 0xb801, 0x4001, 0x3801)]
    if kind == "RGBA32_FLOAT":
        return [struct.unpack("=f", struct.pack("=I", bits + x % 19 + y * 23))[0]
                for bits in (0x3f800001, 0xbf000001, 0x40000001, 0x3f000001)]
    values = [(190+y)/255, (20+x%193)/255, (40+y)/255, (149+x%73)/255]
    if kind.endswith("SRGB"):
        values[:3] = [v/12.92 if v <= 0.04045 else ((v+0.055)/1.055)**2.4 for v in values[:3]]
    return values


def main():
    zip_path, peer_path = sys.argv[sys.argv.index("--")+1:]
    bpy.ops.preferences.addon_install(filepath=str(Path(zip_path).resolve()), overwrite=True)
    bpy.ops.preferences.addon_enable(module="nozzle_blender")
    from nozzle_blender import _nozzle_native as native
    from nozzle_blender.engine import NozzleEngine

    peer = ctypes.CDLL(str(Path(peer_path).resolve()))
    peer.blender_peer_publish.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    peer.blender_peer_publish.restype = ctypes.c_void_p
    peer.blender_peer_destroy.argtypes = [ctypes.c_void_p]
    peer.blender_peer_destroy.restype = None
    formats = native.get_format_constants()
    failed = total = 0
    engine = NozzleEngine()
    cases = [(kind, width, bottom) for kind in
             ("RGBA8_UNORM", "BGRA8_UNORM", "RGBA16_FLOAT", "RGBA32_FLOAT")
             for width in (65, 641) for bottom in (0, 1)]
    # No padding: isolates the pre-fix 0..255 -> unit float assignment bug.
    cases.insert(0, ("RGBA8_UNORM", 64, 0))
    bpy.data.images.new(engine._receive_image_name, width=64, height=3, float_buffer=False)
    for kind, width, bottom in cases:
        total += 1
        name = f"blender-peer-{os.getpid()}-{total}"
        sender = peer.blender_peer_publish(name.encode(), width, 3, formats[kind], bottom)
        if not sender:
            raise RuntimeError(f"native producer setup failed: {kind} {width} {bottom}; no SKIP")
        receiver = None
        label = f"format={kind} size={width}x3 producer_bottom_up={bottom}"
        try:
            previous = bpy.data.images.get(engine._receive_image_name)
            if previous is not None:
                previous.colorspace_settings.name = "sRGB"
            receiver = native.create_receiver(name)
            if not engine.receive_to_image(bpy.context, receiver):
                raise AssertionError("production receive_to_image returned false")
            image = bpy.data.images[engine._receive_image_name]
            values = list(image.pixels)
            if len(values) != width * 3 * 4:
                raise AssertionError(f"image component count={len(values)}")
            for row in range(3):
                for x in range(width):
                    expected = expected_pixel(kind, x, 2-row)  # Blender image rows are bottom-up.
                    for c, value in enumerate(expected):
                        actual = values[(row*width+x)*4+c]
                        exact = kind in ("RGBA16_FLOAT", "RGBA32_FLOAT")
                        if not math.isfinite(actual) or (actual != value if exact else abs(actual-value) > 1e-6):
                            raise AssertionError(f"pixel x={x} bottom_row={row} c={c} actual={actual!r} expected={value!r}")
            if not image.is_float:
                raise AssertionError("received image is not a float buffer")
            if not image.colorspace_settings.is_data:
                raise AssertionError(f"unexpected color space: {image.colorspace_settings.name}")
            print(f"BLENDER_IMAGE_PASS {label}", flush=True)
        except Exception as error:
            failed += 1
            print(f"BLENDER_IMAGE_FAIL {label}: {type(error).__name__}: {error}", flush=True)
        finally:
            if receiver is not None:
                native.destroy_receiver(receiver)
            peer.blender_peer_destroy(sender)
    print(f"BLENDER_IMAGE_PEER failed={failed} total={total}", flush=True)
    if failed:
        raise RuntimeError("Blender native-peer image mismatch")


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        traceback.print_exc()
        raise
