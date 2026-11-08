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
    values[:3] = [v/12.92 if v <= 0.04045 else ((v+0.055)/1.055)**2.4 for v in values[:3]]
    return values


def check_cpu_contract(formats):
    from nozzle_blender.engine import _image_pixels
    for order in ("RGBA8_UNORM", "BGRA8_UNORM"):
        # Low and high sRGB branches, alpha linear, nonzero row padding.
        rgba = (3, 100, 203, 149)
        storage = bytes(rgba if order.startswith("RGBA") else (203, 100, 3, 149))
        data = storage + b"padding!"
        result = (data, 1, 1, formats[order], 12)
        expected = [v/255/12.92 if v <= 10 else ((v/255+0.055)/1.055)**2.4 for v in rgba[:3]] + [149/255]
        for semantic in ("RGBA8_UNORM", "RGBA8_SRGB"):
            actual = _image_pixels(result, formats[semantic])
            if any(not math.isfinite(a) or abs(a-e) > 1e-6 for a, e in zip(actual, expected)):
                raise AssertionError(f"synthetic sRGB/alpha mismatch {order}/{semantic}: {actual}")
    bad = [(b"", 1, 1, formats["RGBA8_UNORM"], 4),
           (b"abcd", 1, 1, formats["RGBA8_UNORM"], 3),
           (b"abcd", 0, 1, formats["RGBA8_UNORM"], 4)]
    for result in bad:
        try:
            _image_pixels(result, formats["RGBA8_UNORM"])
        except ValueError:
            pass
        else:
            raise AssertionError("malformed image layout was accepted")
    print("BLENDER_CPU_CONTRACT PASS sRGB/alpha/padding/invalid-layout (synthetic, not native sRGB)", flush=True)


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
    peer.blender_peer_publish_external.argtypes = [ctypes.c_char_p, ctypes.c_int]
    peer.blender_peer_publish_external.restype = ctypes.c_void_p
    peer.blender_peer_destroy_external.argtypes = [ctypes.c_void_p]
    peer.blender_peer_destroy_external.restype = None
    formats = native.get_format_constants()
    failed = total = 0
    engine = NozzleEngine()
    reference = bpy.data.images.new("float-role-reference", width=1, height=1, float_buffer=True)
    default_float_space = reference.colorspace_settings.name
    if reference.colorspace_settings.is_data:
        raise AssertionError("default float role unexpectedly uses non-color data")
    bpy.data.images.remove(reference)
    cases = [(kind, width, bottom) for kind in
             ("RGBA8_UNORM", "BGRA8_UNORM", "RGBA16_FLOAT", "RGBA32_FLOAT")
             for width in (65, 641) for bottom in (0, 1)]
    # No padding: isolates the pre-fix 0..255 -> unit float assignment bug.
    cases.insert(0, ("RGBA8_UNORM", 64, 0))
    legacy = bpy.data.images.new(engine._receive_image_name, width=64, height=3, float_buffer=False)
    material = bpy.data.materials.new("nozzle-receive-peer")
    material.use_nodes = True
    image_node = material.node_tree.nodes.new("ShaderNodeTexImage")
    image_node.image = legacy
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
                if total % 2:
                    previous.colorspace_settings.name = "sRGB"
                else:
                    previous.colorspace_settings.is_data = True
            receiver = native.create_receiver(name)
            if not engine.receive_to_image(bpy.context, receiver):
                raise AssertionError("production receive_to_image returned false")
            image = bpy.data.images[engine._receive_image_name]
            if image_node.image != image:
                raise AssertionError("replacing the receive image disconnected its material user")
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
            if image.colorspace_settings.is_data or image.colorspace_settings.name != default_float_space:
                raise AssertionError(f"unexpected color space: {image.colorspace_settings.name}")
            print(f"BLENDER_IMAGE_PASS {label}", flush=True)
        except Exception as error:
            failed += 1
            print(f"BLENDER_IMAGE_FAIL {label}: {type(error).__name__}: {error}", flush=True)
        finally:
            if receiver is not None:
                native.destroy_receiver(receiver)
            peer.blender_peer_destroy(sender)
    # Current publication is BGRA throughout. A separate test-only descriptor
    # override emulates legacy UNKNOWN; neither mode changes production code.
    for legacy_unknown in (0, 1):
        label = "emulated legacy UNKNOWN descriptor" if legacy_unknown else "current native BGRA semantics"
        name = f"blender-external-{os.getpid()}-{legacy_unknown}"
        sender = peer.blender_peer_publish_external(name.encode(), legacy_unknown)
        if not sender:
            raise RuntimeError(f"native external publication setup failed: {label}; no SKIP")
        receiver = None
        total += 1
        try:
            receiver = native.create_receiver(name)
            frame = native.receiver_acquire_frame(receiver, 1000)
            if frame is None:
                raise AssertionError(f"external metadata acquire timed out: {label}")
            try:
                info = native.frame_get_info(frame)
                expected_raw = formats["UNKNOWN"] if legacy_unknown else formats["BGRA8_UNORM"]
                if (info["raw_semantic_format"] != expected_raw
                        or info["semantic_format"] != formats["BGRA8_UNORM"]
                        or info["format"] != formats["BGRA8_UNORM"]):
                    raise AssertionError(f"production C extension semantic contract: {label}: {info}")
            finally:
                native.receiver_release_frame(frame)
            native.destroy_receiver(receiver)
            receiver = None
            # A fresh receiver can acquire the same publication for the image path.
            receiver = native.create_receiver(name)
            if not engine.receive_to_image(bpy.context, receiver):
                raise AssertionError("external receive returned false")
            image = bpy.data.images[engine._receive_image_name]
            if (not image.is_float or image.colorspace_settings.is_data
                    or image.colorspace_settings.name != default_float_space or image_node.image != image):
                raise AssertionError("external receive image contract/user mismatch")
            expected = [v for row in range(3) for x in range(65) for v in expected_pixel("BGRA8_UNORM", x, 2-row)]
            actual = list(image.pixels)
            if len(actual) != len(expected) or any(not math.isfinite(a) or abs(a-e) > 1e-6 for a, e in zip(actual, expected)):
                raise AssertionError("external receive pixels mismatch")
            print(f"BLENDER_IMAGE_PASS external {label}; production C extension and image pixels", flush=True)
        finally:
            if receiver is not None:
                native.destroy_receiver(receiver)
            peer.blender_peer_destroy_external(sender)

    # Same-size color frames must update the existing float image, not replace it.
    for kind in ("RGBA32_FLOAT", "RGBA8_UNORM"):
        previous = bpy.data.images[engine._receive_image_name]
        previous_pointer = previous.as_pointer()
        previous_pixels = list(previous.pixels)
        image_pointers = {item.as_pointer() for item in bpy.data.images}
        if tuple(previous.size) != (65, 3) or previous.colorspace_settings.name != default_float_space:
            raise AssertionError("reuse fixture must start at the correct size and float role")
        name = f"blender-reuse-{os.getpid()}-{kind}"
        sender = peer.blender_peer_publish(name.encode(), 65, 3, formats[kind], 0)
        if not sender:
            raise RuntimeError(f"native reuse setup failed: {kind}; no SKIP")
        receiver = None
        total += 1
        try:
            receiver = native.create_receiver(name)
            if not engine.receive_to_image(bpy.context, receiver):
                raise AssertionError("same-size receive returned false")
            image = bpy.data.images[engine._receive_image_name]
            if image.as_pointer() != previous_pointer or image != previous or image_node.image != image:
                raise AssertionError("same-size receive replaced the image or lost its material user")
            expected = [v for row in range(3) for x in range(65) for v in expected_pixel(kind, x, 2-row)]
            actual = list(image.pixels)
            exact = kind == "RGBA32_FLOAT"
            if actual == previous_pixels:
                raise AssertionError("same-size receive kept stale pixels")
            if len(actual) != len(expected) or any(
                    not math.isfinite(a) or (a != e if exact else abs(a-e) > 1e-6)
                    for a, e in zip(actual, expected)):
                raise AssertionError(f"same-size receive pixels mismatch: {kind}")
            if not image.is_float or image.colorspace_settings.is_data or image.colorspace_settings.name != default_float_space:
                raise AssertionError("same-size receive changed float image/color role")
            if {item.as_pointer() for item in bpy.data.images} != image_pointers or any(
                    item.name.startswith("__nozzle_float_role__")
                    or item.name.startswith(engine._receive_image_name + ".")
                    for item in bpy.data.images):
                raise AssertionError("same-size receive left a temporary or duplicate image")
            print(f"BLENDER_IMAGE_PASS reuse format={kind} same_image/new_pixels/material/no_leaks", flush=True)
        finally:
            if receiver is not None:
                native.destroy_receiver(receiver)
            peer.blender_peer_destroy(sender)

    # Unsupported native input must preserve the previously received image.
    previous = bpy.data.images[engine._receive_image_name]
    previous_pixels = list(previous.pixels)
    name = f"blender-unsupported-{os.getpid()}"
    sender = peer.blender_peer_publish(name.encode(), 65, 3, formats["R8_UNORM"], 0)
    if not sender:
        raise RuntimeError("native R8 setup failed; no SKIP")
    receiver = None
    total += 1
    try:
        receiver = native.create_receiver(name)
        try:
            engine.receive_to_image(bpy.context, receiver)
        except ValueError as error:
            if "unsupported image format" not in str(error):
                raise
        else:
            raise AssertionError("unsupported native R8 was accepted")
        if bpy.data.images[engine._receive_image_name] != previous or list(previous.pixels) != previous_pixels:
            raise AssertionError("unsupported format changed previous image")
        native.destroy_receiver(receiver)
        receiver = None
        receiver = native.create_receiver(name)
        from nozzle_blender import operators
        reports = []
        class Reporter:
            def report(self, levels, message):
                reports.append((levels, message))
        props = bpy.context.scene.nozzle_props
        saved_handle = props.receiver_handle
        props.receiver_handle = receiver
        try:
            outcome = operators.NOZZLE_OT_receive_texture.execute(Reporter(), bpy.context)
        finally:
            props.receiver_handle = saved_handle
        if outcome != {"CANCELLED"} or not any(levels == {"ERROR"} and "unsupported image format" in text for levels, text in reports):
            raise AssertionError(f"single-frame operator did not report the error: {outcome}, {reports}")
        if bpy.data.images[engine._receive_image_name] != previous or list(previous.pixels) != previous_pixels:
            raise AssertionError("operator error changed previous image")
        print("BLENDER_OPERATOR_PASS real R8 receive reports ERROR and cancels (report callback captured)", flush=True)
        print("BLENDER_IMAGE_PASS unsupported-native-R8 preserves previous image", flush=True)
    finally:
        if receiver is not None:
            native.destroy_receiver(receiver)
        peer.blender_peer_destroy(sender)
    check_cpu_contract(formats)
    print(f"BLENDER_IMAGE_PEER failed={failed} total={total}", flush=True)
    if failed:
        raise RuntimeError("Blender native-peer image mismatch")


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        traceback.print_exc()
        raise
