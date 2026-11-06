"""Invoke real UI operators; observation timers never drive addon processing."""
import ctypes
import json
import os
import statistics
import struct
import sys
import time
import traceback
from pathlib import Path

import bpy
import gpu


class LiveProbe:
    def __init__(self):
        self.failures = []
        self.report = {"observations": [], "conversion_seconds": {}}
        self.peer = None
        self.sender = None
        self.native = None
        self.window = self.area = self.region = None
        self.observations = 0
        self.started = 0.0
        self.sender_started = False

    def record(self, label, value):
        self.report["observations"].append({"label": label, "value": value})
        print(f"BLENDER_LIVE {label}: {value}", flush=True)

    def fail(self, label, error):
        self.failures.append({"label": label, "error": str(error)})
        self.record(f"FAIL {label}", str(error))

    def context(self):
        return bpy.context.temp_override(window=self.window, area=self.area, region=self.region)

    def start(self):
        try:
            self.setup()
            self.started = time.perf_counter()
            bpy.app.timers.register(self.observe, first_interval=0.2)
        except Exception as error:
            traceback.print_exc()
            self.fail("setup", error)
            self.finish()
        return None

    def setup(self):
        zip_path, peer_path = sys.argv[sys.argv.index("--") + 1:]
        if bpy.app.background:
            raise RuntimeError("foreground window required; no background/SKIP substitute")
        self.window = next(iter(bpy.context.window_manager.windows), None)
        if self.window is None:
            raise RuntimeError("no real Blender window")
        self.area = next((area for area in self.window.screen.areas if area.type == "VIEW_3D"), None)
        if self.area is None:
            raise RuntimeError("no VIEW_3D area")
        self.region = next(region for region in self.area.regions if region.type == "WINDOW")
        self.record("context", {"version": bpy.app.version_string, "background": bpy.app.background,
                                "windows": len(bpy.context.window_manager.windows),
                                "area": self.area.type, "region": self.region.type,
                                "backend": gpu.platform.backend_type_get(),
                                "wm_has_timers": hasattr(bpy.context.window_manager, "timers")})
        bpy.ops.preferences.addon_install(filepath=str(Path(zip_path).resolve()), overwrite=True)
        bpy.ops.preferences.addon_enable(module="nozzle_blender")
        from nozzle_blender import _nozzle_native
        self.native = _nozzle_native
        self.peer = ctypes.CDLL(str(Path(peer_path).resolve()))
        self.peer.blender_peer_publish.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.peer.blender_peer_publish.restype = ctypes.c_void_p
        self.peer.blender_peer_destroy.argtypes = [ctypes.c_void_p]
        self.peer.blender_peer_destroy.restype = None
        self.formats = self.native.get_format_constants()
        target = f"blender-live-peer-{os.getpid()}"
        self.sender = self.peer.blender_peer_publish(target.encode(), 65, 3, self.formats["RGBA8_UNORM"], 0)
        if not self.sender:
            raise RuntimeError("independent Metal sender failed; no SKIP")
        with self.context():
            props = bpy.context.scene.nozzle_props
            props.sender_name = f"blender-live-ui-{os.getpid()}"
            props.capture_width = 65
            props.capture_height = 3
            try:
                result = bpy.ops.nozzle.start_sender()
                self.record("start_sender_result", sorted(result))
                self.sender_started = result == {"FINISHED"}
                if not self.sender_started:
                    self.fail("start_sender", result)
            except Exception as error:
                self.fail("start_sender", f"{type(error).__name__}: {error}")
            self.record("sender_state_after_start", {"handle": props.sender_handle,
                                                      "is_sending": props.is_sending,
                                                      "timer_handle": getattr(props, "timer_handle", None)})
            props.receiver_target = target
            result = bpy.ops.nozzle.start_receiver()
            if result != {"FINISHED"}:
                raise RuntimeError(f"start_receiver returned {result}")
            self.record("start_receiver_result", sorted(result))

    def observe(self):
        try:
            self.observations += 1
            if time.perf_counter() - self.started < 1.0:
                return 0.2
            self.record("event_loop_observations", self.observations)
            image = bpy.data.images.get("__nozzle_received__")
            self.record("live_image_arrived", image is not None)
            if image is None:
                self.fail("live_receive", "no image after Start Receiver and one second of event-loop time")
            with self.context():
                # This control is separate from the live observation above.
                result = bpy.ops.nozzle.receive_texture()
                image = bpy.data.images.get("__nozzle_received__")
                if result != {"FINISHED"} or image is None or tuple(image.size) != (65, 3):
                    self.fail("single_receive_control", f"result={result}, image={image}")
                else:
                    expected = [192/255, 20/255, 42/255]
                    expected = [value/12.92 if value <= 0.04045 else ((value+0.055)/1.055)**2.4 for value in expected]
                    expected.append(149/255)
                    actual = list(image.pixels[:4])
                    if any(abs(a-b) > 1e-6 for a, b in zip(actual, expected)):
                        self.fail("single_receive_control", f"pixels={actual}, expected={expected}")
                    else:
                        self.record("single_receive_control", "PASS native asymmetric pixel")
                props = bpy.context.scene.nozzle_props
                if self.sender_started:
                    observer = self.native.create_receiver(props.sender_name)
                    try:
                        frame = self.native.receiver_acquire_frame(observer, 100)
                        self.record("live_sender_frame_arrived", frame is not None)
                        if frame is None:
                            self.fail("live_send", "no frame after Start Sender")
                        else:
                            self.native.receiver_release_frame(frame)
                    finally:
                        self.native.destroy_receiver(observer)
            self.stop_operators()
            self.measure_conversion()
        except Exception as error:
            traceback.print_exc()
            self.fail("observation", error)
        self.finish()
        return None

    def stop_operators(self):
        if self.native is None or self.area is None:
            return
        with self.context():
            props = bpy.context.scene.nozzle_props
            for kind in ("receiver", "sender"):
                handle = getattr(props, f"{kind}_handle")
                if handle >= 0:
                    try:
                        result = getattr(bpy.ops.nozzle, f"stop_{kind}")()
                        self.record(f"stop_{kind}_result", sorted(result))
                        if result != {"FINISHED"} or getattr(props, f"{kind}_handle") >= 0:
                            self.fail(f"stop_{kind}", "native handle not cleared")
                    except Exception as error:
                        self.fail(f"stop_{kind}", error)
                        # Probe cleanup only, not evidence that the operator worked.
                        getattr(self.native, f"destroy_{kind}")(handle)
                        setattr(props, f"{kind}_handle", -1)

    def measure_conversion(self):
        from nozzle_blender.engine import _image_pixels
        for kind, pixel in (("BGRA8_UNORM", bytes([40, 20, 190, 149])),
                            ("RGBA32_FLOAT", struct.pack("=4f", 1.0000001192092896, -0.5, 2.0, 0.5))):
            width, height = 1920, 1080
            data = pixel * (width * height)
            result = (data, width, height, self.formats[kind], width * len(pixel))
            samples = []
            for _ in range(3):
                start = time.perf_counter()
                values = _image_pixels(result, self.formats[kind])
                samples.append(time.perf_counter() - start)
                if len(values) != width * height * 4:
                    raise AssertionError("conversion length changed")
                del values
            self.report["conversion_seconds"][kind] = {"size": [width, height], "samples": samples,
                                                       "median": statistics.median(samples)}
            self.record(f"conversion_{kind}", self.report["conversion_seconds"][kind])

    def finish(self):
        self.stop_operators()
        if self.sender:
            self.peer.blender_peer_destroy(self.sender)
            self.sender = None
        self.report["failures"] = self.failures
        output = Path("artifacts/blender-live-probe.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.report, indent=2) + "\n")
        print(f"BLENDER_LIVE_RESULT failures={len(self.failures)} evidence={output}", flush=True)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1 if self.failures else 0)


probe = LiveProbe()
bpy.app.timers.register(probe.start, first_interval=1.0)
