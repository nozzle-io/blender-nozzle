"""Real built-extension metadata contracts against the existing Metal peer.

Each native getter runs in its own subprocess: malformed C varargs may crash,
and discovery must not hide connected-info evidence. No Python API stubs or
synthetic counters/FPS are used. This does not test Blender UI or pixel values.
The small writable stride is a tuple contract check, not a 64-bit overflow test.
"""

import argparse
import ctypes
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import unittest


CASES = ("discovery", "connected", "constants", "writable")


class SenderSnapshot(ctypes.Structure):
    # Public NozzleSenderInfoSnapshot from the pinned nozzle_c.h.
    _fields_ = [("name", ctypes.c_char * 64),
                ("application_name", ctypes.c_char * 64),
                ("id", ctypes.c_char * 37), ("backend", ctypes.c_int)]


def run_case(build_dir, peer_path, case):
    check = unittest.TestCase()
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not suffix:
        raise RuntimeError("build Python must provide EXT_SUFFIX")
    module_path = build_dir / f"_nozzle_native{suffix}"
    if not module_path.is_file():
        raise RuntimeError(f"built extension missing: {module_path}")
    spec = importlib.util.spec_from_file_location("_nozzle_native", module_path)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    if case == "constants":
        # The 19 constants advertised by this wrapper, with public C enum values.
        expected = {"UNKNOWN": 0, "R8_UNORM": 1, "RG8_UNORM": 2,
                    "RGBA8_UNORM": 4, "BGRA8_UNORM": 5, "RGBA8_SRGB": 6,
                    "BGRA8_SRGB": 7, "R16_UNORM": 8, "RG16_UNORM": 9,
                    "RGBA16_UNORM": 11, "R16_FLOAT": 12, "RG16_FLOAT": 13,
                    "RGBA16_FLOAT": 15, "R32_FLOAT": 16, "RG32_FLOAT": 17,
                    "RGBA32_FLOAT": 19, "R32_UINT": 20, "RGBA32_UINT": 21,
                    "DEPTH32_FLOAT": 23}
        print("CALL get_format_constants (CPU-only)", flush=True)
        actual = native.get_format_constants()
        print(f"RETURN get_format_constants {actual!r}", flush=True)
        check.assertEqual(actual, expected)
        for value in actual.values():
            check.assertIs(type(value), int)
        print("NATIVE_METADATA constants PASS", flush=True)
        return
    if case == "writable":
        sender = native.create_sender(name=f"metadata-265-writable-{os.getpid()}")
        frame = None
        try:
            frame = native.sender_acquire_writable_frame(sender, 65, 3, 5)
            print("CALL frame_lock_writable_pixels", flush=True)
            pixels = native.frame_lock_writable_pixels(frame)
            check.assertIs(type(pixels), tuple)
            check.assertEqual(len(pixels), 5)
            data, width, height, storage, stride = pixels
            print(f"RETURN writable size={width}x{height} storage={storage} stride={stride}", flush=True)
            check.assertIs(type(data), bytes)
            for value in (width, height, storage, stride):
                check.assertIs(type(value), int)
            check.assertEqual((width, height, storage), (65, 3, 5))
            check.assertGreaterEqual(stride, 65 * 4)
            check.assertEqual(len(data), stride * height)
            print("NATIVE_METADATA writable PASS (small native stride only)", flush=True)
        finally:
            if frame is not None:
                native.receiver_release_frame(frame)
            native.destroy_sender(sender)
        return
    peer = ctypes.CDLL(str(peer_path))
    peer.blender_peer_publish.argtypes = [ctypes.c_char_p] + [ctypes.c_int] * 4
    peer.blender_peer_publish.restype = ctypes.c_void_p
    peer.blender_peer_destroy.argtypes = [ctypes.c_void_p]
    peer.blender_peer_destroy.restype = None
    peer.nozzle_sender_get_info_snapshot.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(SenderSnapshot)]
    peer.nozzle_sender_get_info_snapshot.restype = ctypes.c_int

    name = f"metadata-265-{case}-{os.getpid()}"
    bgra = 5  # NOZZLE_FORMAT_BGRA8_UNORM; independent of the constants regression.
    sender = peer.blender_peer_publish(name.encode(), 65, 3, bgra, 0)
    if not sender:
        raise RuntimeError("real Metal publisher setup failed; no SKIP")
    receiver = frame = None
    try:
        snapshot = SenderSnapshot()
        check.assertEqual(peer.nozzle_sender_get_info_snapshot(
            sender, ctypes.byref(snapshot)), 0)
        identity = {"name": name, "application_name": "Blender native peer",
                    "id": snapshot.id.decode("ascii"), "backend": 2}
        check.assertEqual(snapshot.name.decode(), name)
        check.assertEqual(snapshot.application_name.decode(), identity["application_name"])
        check.assertEqual(snapshot.backend, 2)  # NOZZLE_BACKEND_METAL
        check.assertEqual(len(identity["id"]), 36)
        print(f"NATIVE_METADATA {case} SETUP {identity!r}", flush=True)

        if case == "discovery":
            print("CALL enumerate_senders", flush=True)
            rows = native.enumerate_senders()
            print(f"RETURN enumerate_senders {rows!r}", flush=True)
            check.assertIs(type(rows), list)
            matches = [row for row in rows if row.get("name") == name]
            check.assertEqual(len(matches), 1)
            info = matches[0]
            check.assertEqual(set(info), set(identity))
        else:
            # Connect by the known name, never through the broken discovery API.
            receiver = native.create_receiver(name=name)
            frame = native.receiver_acquire_frame(receiver, 1000)
            check.assertIsNotNone(frame)
            print("CALL receiver_get_connected_info", flush=True)
            info = native.receiver_get_connected_info(receiver)
            print(f"RETURN receiver_get_connected_info {info!r}", flush=True)
            check.assertEqual(set(info), set(identity) | {
                "width", "height", "format", "estimated_fps",
                "frame_counter", "last_update_time_ns"})
            for key in ("width", "height", "format", "frame_counter", "last_update_time_ns"):
                check.assertIs(type(info[key]), int, key)
            check.assertEqual((info["width"], info["height"], info["format"]), (65, 3, bgra))
            check.assertEqual(info["frame_counter"], 1)
            check.assertGreater(info["last_update_time_ns"], 0)
            check.assertIs(type(info["estimated_fps"]), float)
            check.assertEqual(info["estimated_fps"], 0.0)  # Core currently leaves this unset.
        for key, expected in identity.items():
            check.assertIs(type(info[key]), type(expected), key)
            check.assertEqual(info[key], expected, key)
        print(f"NATIVE_METADATA {case} PASS", flush=True)
    finally:
        if frame is not None:
            native.receiver_release_frame(frame)
        if receiver is not None:
            native.destroy_receiver(receiver)
        peer.blender_peer_destroy(sender)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_dir", type=Path)
    parser.add_argument("peer", type=Path)
    parser.add_argument("--case", choices=CASES)
    args = parser.parse_args()
    build_dir, peer = args.build_dir.resolve(), args.peer.resolve()
    if args.case:
        run_case(build_dir, peer, args.case)
        return
    failed = 0
    for case in CASES:
        command = [sys.executable, "-u", str(Path(__file__).resolve()),
                   str(build_dir), str(peer), "--case", case]
        try:
            result = subprocess.run(command, timeout=30)
            passed = result.returncode == 0
            print(f"NATIVE_METADATA_RESULT {case} exit={result.returncode}", flush=True)
        except subprocess.TimeoutExpired:
            passed = False
            print(f"NATIVE_METADATA_RESULT {case} TIMEOUT after 30s", flush=True)
        failed += not passed
    print(f"NATIVE_METADATA_SUMMARY total={len(CASES)} failed={failed}", flush=True)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
