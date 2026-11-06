"""Real event-loop regression cases for receiver availability and workspace changes."""
import ctypes
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback


def producer_main():
    # A separate PID is intentional: core #184 does not detect same-process teardown.
    peer = ctypes.CDLL(sys.argv[2])
    peer.blender_peer_publish.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int]
    peer.blender_peer_publish.restype = ctypes.c_void_p
    peer.blender_peer_update.argtypes = [ctypes.c_void_p, ctypes.c_int]
    peer.blender_peer_update.restype = ctypes.c_bool
    peer.blender_peer_destroy.argtypes = [ctypes.c_void_p]
    sender = peer.blender_peer_publish(sys.argv[3].encode(), 65, 3, int(sys.argv[4]), 0)
    if not sender:
        raise RuntimeError("native child producer failed")
    try:
        print("PEER ready", flush=True)
        for command in sys.stdin:
            if command.strip() == "quit":
                break
            if not peer.blender_peer_update(sender, int(command)):
                raise RuntimeError("native child update failed")
            print("PEER updated", flush=True)
    finally:
        peer.blender_peer_destroy(sender)


if len(sys.argv) > 1 and sys.argv[1] == "--producer":
    producer_main()
    sys.exit(0)

import bpy


class LifecycleProbe:
    def __init__(self):
        self.failures = []
        self.observations = []
        self.child = None
        self.phase = "missing"
        self.deadline = 0.0
        self.live = None

    def check(self, label, condition, detail=""):
        record = {"label": label, "pass": bool(condition), "detail": detail}
        self.observations.append(record)
        print("BLENDER_LIFECYCLE " + json.dumps(record), flush=True)
        if not condition:
            self.failures.append(record)

    def operator(self, name, area=None):
        area = area or next(a for a in self.window.screen.areas if a.type == "VIEW_3D")
        region = next(r for r in area.regions if r.type == "WINDOW")
        try:
            with bpy.context.temp_override(window=self.window, area=area, region=region):
                result = getattr(bpy.ops.nozzle, name)()
            self.check(name, result == {"FINISHED"}, sorted(result))
        except Exception as error:
            self.check(name, False, str(error))

    def session(self):
        return self.live._sessions.get(self.scene.as_pointer())

    def receiving(self):
        session = self.session()
        return bool(session and self.props.is_receiving
                    and bpy.app.timers.is_registered(self.live._tick))

    def expect_pixels(self, label, offset):
        image = bpy.data.images.get("__nozzle_received__")
        actual = list(image.pixels[:4]) if image else []
        expected = [((v / 255 + 0.055) / 1.055)**2.4 for v in (192+offset, 20, 42+offset)] + [149/255]
        self.check(label, self.receiving() and image is not None
                   and tuple(image.size) == (65, 3) and len(actual) == 4
                   and all(math.isfinite(a) and abs(a-e) < 1e-6 for a, e in zip(actual, expected)), actual)

    def reply(self, expected):
        # The outer workflow timeout bounds this IPC too; EOF is an immediate failure.
        for line in self.child.stdout:
            if line.startswith("PEER "):
                if line.strip() != expected:
                    raise RuntimeError(line)
                return
        raise RuntimeError("child exited without acknowledgement")

    def publish(self, offset):
        if self.child is None:
            self.child = subprocess.Popen(
                [self.python, str(Path(__file__).resolve()), "--producer", self.peer_path,
                 self.target, str(self.format)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                text=True, bufsize=1)
            self.reply("PEER ready")
        self.child.stdin.write(f"{offset}\n")
        self.child.stdin.flush()
        self.reply("PEER updated")

    def end_producer(self):
        if self.child is not None:
            child, self.child = self.child, None
            try:
                if child.poll() is None:
                    child.stdin.write("quit\n")
                    child.stdin.flush()
                if child.wait(timeout=10) != 0:
                    raise RuntimeError("producer did not exit cleanly")
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                child.stdin.close()
                child.stdout.close()

    def stop(self):
        for kind in ("receiver", "sender"):
            if self.live.has_resources(self.scene, kind):
                self.operator(f"stop_{kind}")

    def next(self, phase):
        self.phase = phase
        self.deadline = time.perf_counter() + 1.0
        return 0.1

    def start(self):
        try:
            zip_path, peer_path, self.python = sys.argv[sys.argv.index("--") + 1:]
            if bpy.app.background:
                raise RuntimeError("foreground Blender required")
            self.peer_path = str(Path(peer_path).resolve())
            self.window = bpy.context.window_manager.windows[0]
            self.scene = self.window.scene
            self.workspace = self.window.workspace
            bpy.ops.preferences.addon_install(filepath=str(Path(zip_path).resolve()), overwrite=True)
            bpy.ops.preferences.addon_enable(module="nozzle_blender")
            from nozzle_blender import live, _nozzle_native
            self.live = live
            self.props = self.scene.nozzle_props
            self.format = _nozzle_native.get_format_constants()["RGBA8_UNORM"]
            self.target = f"blender-lifecycle-{os.getpid()}"
            self.props.receiver_target = self.target
            self.props.sender_name = f"blender-lifecycle-ui-{os.getpid()}"
            self.props.capture_width, self.props.capture_height = 65, 49
            self.operator("start_receiver")
            self.next("missing")
            bpy.app.timers.register(self.observe, first_interval=0.1)
        except Exception as error:
            traceback.print_exc()
            self.check("setup", False, str(error))
            self.finish()
        return None

    def observe(self):
        try:
            if time.perf_counter() < self.deadline:
                return 0.1
            if self.phase == "missing":
                self.check("waiting_before_producer", self.receiving(), self.props.last_error)
                self.operator("stop_receiver")
                self.check("pending_receiver_cancellable", not self.live.has_resources(self.scene, "receiver")
                           and not self.props.is_receiving and not bpy.app.timers.is_registered(self.live._tick)
                           and self.props.last_error == "", self.props.last_error)
                self.publish(1)
                return self.next("cancelled")
            if self.phase == "cancelled":
                self.check("cancelled_receiver_does_not_connect", self.session() is None
                           and bpy.data.images.get("__nozzle_received__") is None)
                self.end_producer()
                self.operator("start_receiver")
                self.publish(1)
                return self.next("appeared")
            if self.phase == "appeared":
                self.expect_pixels("late_producer_received", 1)
                self.check("late_producer_clears_wait_status", self.props.last_error == "", self.props.last_error)
                # Independent restart case even if the absent-producer case failed.
                self.stop()
                self.operator("start_receiver")
                return self.next("connected")
            if self.phase == "connected":
                self.expect_pixels("restart_baseline", 1)
                self.old_receiver = self.props.receiver_handle
                self.end_producer()
                return self.next("gone")
            if self.phase == "gone":
                self.check("waiting_after_producer_exit", self.receiving(), self.props.last_error)
                self.publish(2)
                return self.next("returned")
            if self.phase == "returned":
                self.expect_pixels("restarted_producer_received", 2)
                self.check("restarted_producer_clears_wait_status", self.props.last_error == "", self.props.last_error)
                self.check("same_receiver_after_restart", self.props.receiver_handle == self.old_receiver)
                self.stop()
                self.operator("start_receiver")
                self.operator("start_sender")
                return self.next("workspace_baseline")
            if self.phase == "workspace_baseline":
                self.expect_pixels("workspace_baseline_received", 2)
                session = self.session()
                self.old_receiver = self.props.receiver_handle
                self.old_sent = session.sent if session else 0
                self.old_screen = self.window.screen.as_pointer()
                self.window.workspace = next(w for w in bpy.data.workspaces if w != self.workspace)
                return self.next("switched")
            if self.phase == "switched":
                self.check("workspace_actually_changed", self.window.screen.as_pointer() != self.old_screen)
                session = self.session()
                self.old_sent = session.sent if session else 0
                self.publish(3)
                return self.next("workspace_updated")
            if self.phase == "workspace_updated":
                self.expect_pixels("receiver_survives_workspace", 3)
                self.check("workspace_receiver_identity", self.props.receiver_handle == self.old_receiver)
                session = self.session()
                progressed = bool(session and session.sender is not None and self.props.is_sending
                                  and session.sent > self.old_sent)
                sender_status = "sender" in self.props.last_error.lower()
                self.check("sender_not_silently_stopped", progressed or sender_status, self.props.last_error)
                self.old_sent = session.sent if session else 0
                self.window.workspace = self.workspace
                return self.next("workspace_restored")
            if self.phase == "workspace_restored":
                session = self.session()
                self.check("sender_resumes_original_viewport", bool(session and session.sender is not None
                           and self.props.is_sending and session.sent > self.old_sent), self.props.last_error)
                self.check("restored_viewport_clears_pause_status", self.props.last_error == "", self.props.last_error)
                self.stop()
                for area in self.window.screen.areas:
                    if area.type == "VIEW_3D":
                        area.type = "TEXT_EDITOR"
                area = next(a for a in self.window.screen.areas if a.type != "VIEW_3D"
                            and any(r.type == "WINDOW" for r in a.regions))
                self.operator("start_receiver", area)
                self.publish(4)
                return self.next("no_viewport")
            if self.phase == "no_viewport":
                self.expect_pixels("receiver_started_without_viewport", 4)
        except Exception as error:
            traceback.print_exc()
            self.check(self.phase, False, str(error))
        self.finish()
        return None

    def finish(self):
        for label, cleanup in (("live_cleanup", lambda: self.live.shutdown() if self.live else None),
                               ("child_cleanup", self.end_producer)):
            try:
                cleanup()
            except Exception as error:
                self.check(label, False, str(error))
        try:
            output = Path("artifacts/blender-live-lifecycle.json")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({"observations": self.observations, "failures": self.failures}, indent=2) + "\n")
        except Exception as error:
            self.check("persist_evidence", False, str(error))
        finally:
            try:
                print(f"BLENDER_LIFECYCLE_RESULT failures={len(self.failures)}", flush=True)
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                os._exit(1 if self.failures else 0)


probe = LifecycleProbe()
bpy.app.timers.register(probe.start, first_interval=1.0)
