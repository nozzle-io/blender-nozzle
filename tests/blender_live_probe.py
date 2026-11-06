"""Invoke real UI operators; observation timers never drive addon processing."""
import ctypes
import json
import math
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
        self.phase = "initial"
        self.stopped_pixels = None
        self.pattern_offset = 0
        self.first_image_pointer = None

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
        from nozzle_blender import live
        self.native = _nozzle_native
        self.live = live
        self.peer = ctypes.CDLL(str(Path(peer_path).resolve()))
        self.peer.blender_peer_publish.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.peer.blender_peer_publish.restype = ctypes.c_void_p
        self.peer.blender_peer_destroy.argtypes = [ctypes.c_void_p]
        self.peer.blender_peer_destroy.restype = None
        self.peer.blender_peer_update.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.peer.blender_peer_update.restype = ctypes.c_bool
        self.formats = self.native.get_format_constants()
        self.check_native_float_publish()
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
            if time.perf_counter() - self.started < (0.3 if self.phase == "stopped" else 1.0):
                return 0.2
            if self.phase == "stopped":
                image = bpy.data.images.get("__nozzle_received__")
                if self.live._sessions or bpy.app.timers.is_registered(self.live._tick):
                    raise AssertionError("callbacks remain after both stop operators")
                if image is None or list(image.pixels) != self.stopped_pixels:
                    raise AssertionError("image changed after stop")
                image.pixels.foreach_set([0.0] * len(image.pixels))
                with self.context():
                    if bpy.ops.nozzle.start_sender() != {"FINISHED"} or bpy.ops.nozzle.start_receiver() != {"FINISHED"}:
                        raise AssertionError("restart failed")
                self.phase = "restarted"
                self.started = time.perf_counter()
                return 0.2
            self.record("event_loop_observations", self.observations)
            image = bpy.data.images.get("__nozzle_received__")
            self.record("live_image_arrived", image is not None)
            if image is None:
                self.fail("live_receive", "no image after Start Receiver and one second of event-loop time")
            else:
                expected = [(192+self.pattern_offset)/255, 20/255, (42+self.pattern_offset)/255]
                expected = [((v+0.055)/1.055)**2.4 for v in expected] + [149/255]
                actual = list(image.pixels[:4])
                if tuple(image.size) != (65, 3) or any(not math.isfinite(a) or abs(a-e) > 1e-6 for a, e in zip(actual, expected)):
                    self.fail("live_receive_pixels", f"{actual} != {expected}")
            session = self.live._sessions.get(self.window.scene.as_pointer())
            if session is None or session.received < 1 or session.sent < 2:
                self.fail("live_progress", "expected receive plus multiple automatic captures")
            else:
                self.record(f"runtime_{self.phase}", {"received": session.received, "sent": session.sent,
                            "receive_seconds": session.receive_seconds, "capture_publish_seconds": session.send_seconds})
            if self.phase == "updated" and (session is None or session.received < 2
                    or image.as_pointer() != self.first_image_pointer):
                self.fail("live_update", "same sender/receiver must update the same image with a second frame")
            try:
                self.check_live_sender()
            except Exception as error:
                self.fail("live_send", f"{type(error).__name__}: {error}")
            if self.phase == "initial" and not self.failures:
                self.first_image_pointer = image.as_pointer()
                self.pattern_offset = 1
                if not self.peer.blender_peer_update(self.sender, self.pattern_offset):
                    raise AssertionError("independent native update failed")
                self.phase = "updated"
                self.started = time.perf_counter()
                return 0.2
            if self.phase == "updated" and not self.failures:
                self.stop_operators()
                if session.draw_handle is not None or session.engine._offscreen is not None:
                    raise AssertionError("draw callback/offscreen remained after stop")
                self.stopped_pixels = list(image.pixels)
                self.phase = "stopped"
                self.started = time.perf_counter()
                return 0.2
        except Exception as error:
            traceback.print_exc()
            self.fail("observation", error)
        try:
            self.stop_operators()
        except Exception as error:
            self.fail("stop_operators", error)
        try:
            self.measure_conversion()
            self.measure_receive()
        except Exception as error:
            self.fail("conversion", error)
        try:
            self.check_unregister()
        except Exception as error:
            self.fail("unregister", error)
        self.finish()
        return None

    def check_native_float_publish(self):
        check = self.peer.blender_peer_check_rgba32f
        check.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        check.restype = ctypes.c_bool
        name = f"blender-float-publish-{os.getpid()}"
        sender = self.native.create_sender(name)
        data = b"".join(struct.pack("=4I", 0x3f800001+x+y, 0xbf000001+x, 0x40000001+y, 0x3f000001+x)
                        for y in range(3) for x in range(65))
        try:
            for bad in (b"", data[:-1], data+b"x"):
                try:
                    self.native.sender_publish_rgba32f(sender, 65, 3, bad)
                except ValueError:
                    pass
                else:
                    raise AssertionError("malformed float buffer accepted")
            self.native.sender_publish_rgba32f(sender, 65, 3, data)
            expected = ctypes.create_string_buffer(data)
            if not check(name.encode(), 65, 3, expected):
                raise AssertionError("independent float observer mismatch")
            self.record("native_float_publish", "PASS exact low mantissa/HDR/negative/orientation/padding; invalid sizes rejected")
        finally:
            self.native.destroy_sender(sender)

    def check_live_sender(self):
        if self.sender_started:
            with self.context():
                props = bpy.context.scene.nozzle_props
                observer = self.native.create_receiver(props.sender_name)
                try:
                    try:
                        frame = self.native.receiver_acquire_frame(observer, 100)
                    except RuntimeError as error:
                        self.record("live_sender_frame_arrived", False)
                        self.fail("live_send", f"acquire failed: {error}")
                        return
                    self.record("live_sender_frame_arrived", frame is not None)
                    if frame is None:
                        self.fail("live_send", "no frame after Start Sender")
                    else:
                        try:
                            info = self.native.frame_get_info(frame)
                            result = self.native.frame_lock_pixels(frame)
                            if info["semantic_format"] != self.formats["RGBA32_FLOAT"] or result[3] != self.formats["RGBA32_FLOAT"]:
                                raise AssertionError("viewport float32 was narrowed or misdeclared")
                            self.record("live_sender_frame", info)
                        finally:
                            self.native.receiver_release_frame(frame)
                finally:
                    self.native.destroy_receiver(observer)

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
                        remaining = getattr(props, f"{kind}_handle")
                        if remaining >= 0:
                            try:
                                getattr(self.native, f"destroy_{kind}")(remaining)
                                setattr(props, f"{kind}_handle", -1)
                            except Exception as cleanup_error:
                                self.fail(f"cleanup_{kind}", cleanup_error)

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

    def measure_receive(self):
        from nozzle_blender.engine import NozzleEngine
        for kind in ("BGRA8_UNORM", "RGBA32_FLOAT"):
            name = f"blender-live-perf-{os.getpid()}-{kind}"
            sender = self.peer.blender_peer_publish(name.encode(), 1920, 1080, self.formats[kind], 0)
            if not sender:
                raise RuntimeError("native performance peer failed")
            engine = NozzleEngine()
            samples = []
            try:
                for _ in range(3):
                    receiver = self.native.create_receiver(name)
                    try:
                        start = time.perf_counter()
                        if not engine.receive_to_image(None, receiver, timeout_ms=0):
                            raise AssertionError("performance receive timed out")
                        samples.append(time.perf_counter() - start)
                    finally:
                        self.native.destroy_receiver(receiver)
            finally:
                self.peer.blender_peer_destroy(sender)
            self.record(f"receive_to_image_{kind}", {"size": [1920, 1080], "samples": samples,
                        "median": statistics.median(samples), "includes": "acquire/map/copy/convert/image update; excludes discovery"})

    def check_unregister(self):
        with self.context():
            bpy.ops.nozzle.start_sender()
            bpy.ops.nozzle.start_receiver()
            props = bpy.context.scene.nozzle_props
            handles = {"sender": props.sender_handle, "receiver": props.receiver_handle}
            session = self.live._sessions[bpy.context.scene.as_pointer()]
            bpy.ops.preferences.addon_disable(module="nozzle_blender")
            if self.live._sessions or bpy.app.timers.is_registered(self.live._tick) or session.draw_handle is not None:
                raise AssertionError("addon disable left a callback/session")
            for kind, handle in handles.items():
                try:
                    getattr(self.native, f"destroy_{kind}")(handle)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"addon disable leaked {kind}")
            self.record("unregister_cleanup", "PASS handles/callbacks")
            self.native = None

    def finish(self):
        try:
            self.stop_operators()
        except Exception as error:
            self.fail("cleanup_operators", error)
        try:
            if self.sender:
                self.peer.blender_peer_destroy(self.sender)
                self.sender = None
        except Exception as error:
            self.fail("cleanup_peer", error)
        try:
            self.report["failures"] = self.failures
            output = Path("artifacts/blender-live-probe.json")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(self.report, indent=2) + "\n")
            print(f"BLENDER_LIVE_RESULT failures={len(self.failures)} evidence={output}", flush=True)
        except Exception as error:
            self.fail("persist_evidence", error)
            print(json.dumps(self.report), flush=True)
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                os._exit(1 if self.failures else 0)


probe = LiveProbe()
bpy.app.timers.register(probe.start, first_interval=1.0)
