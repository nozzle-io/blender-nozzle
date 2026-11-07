"""Exercise real live callbacks with deterministic host stubs, not GPU contention."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class StubEngine:
    def __init__(self):
        self._offscreen = object()
        self.capture_and_send = mock.Mock()
        self.stop_sender = mock.Mock(side_effect=self.release_offscreen)

    def release_offscreen(self):
        self._offscreen = None


class LiveSenderLifecycleTests(unittest.TestCase):
    def setUp(self):
        package = ModuleType("busy_ring_addon")
        package.__path__ = [str(ROOT / "nozzle_blender")]
        native = ModuleType("busy_ring_addon._nozzle_native")
        native.ERROR_TIMEOUT = 10
        native.destroy_sender = mock.Mock()
        engine = ModuleType("busy_ring_addon.engine")
        engine.NozzleEngine = StubEngine
        bpy = ModuleType("bpy")
        app = ModuleType("bpy.app")
        handlers = ModuleType("bpy.app.handlers")
        handlers.persistent = lambda callback: callback
        app.handlers = handlers
        bpy.app = app
        bpy.types = SimpleNamespace(SpaceView3D=SimpleNamespace(draw_handler_remove=mock.Mock()))
        modules = {
            "busy_ring_addon": package,
            "busy_ring_addon._nozzle_native": native,
            "busy_ring_addon.engine": engine,
            "bpy": bpy,
            "bpy.app": app,
            "bpy.app.handlers": handlers,
        }
        self.enterContext(mock.patch.dict(sys.modules, modules))
        spec = importlib.util.spec_from_file_location(
            "busy_ring_addon.live", ROOT / "nozzle_blender" / "live.py")
        self.live = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.live
        spec.loader.exec_module(self.live)
        self.clock = self.enterContext(mock.patch.object(self.live.time, "perf_counter", return_value=10.0))

        props = SimpleNamespace(capture_width=4, capture_height=3, sender_handle=41,
                                receiver_handle=-1, is_sending=True, is_receiving=False, last_error="")
        scene = SimpleNamespace(as_pointer=lambda: 7, nozzle_props=props)
        region = SimpleNamespace(type="WINDOW")
        area = SimpleNamespace(type="VIEW_3D", regions=[region], tag_redraw=mock.Mock())
        window = SimpleNamespace(scene=scene, screen=SimpleNamespace(areas=[area]))
        context = SimpleNamespace(scene=scene, window=window, area=area, region=region,
                                  window_manager=SimpleNamespace(windows=[window]))
        bpy.context = context
        bpy.data = SimpleNamespace(scenes=[scene])
        self.native, self.bpy, self.props, self.area = native, bpy, props, area
        self.session = self.live.Session(context)
        self.session.bind_viewport(context)
        self.session.sender = 41
        self.session.draw_handle = object()
        self.draw_handle = self.session.draw_handle
        self.session.sent = 3
        self.live._sessions[self.session.key] = self.session

    def assert_attempt_finished(self):
        self.assertEqual(self.session.sent, 3)
        self.assertFalse(self.session.drawing)
        self.assertAlmostEqual(self.session.next_send, 10.0 + self.live._interval)
        self.session.engine.capture_and_send.assert_called_once()

    def test_coded_timeout_preserves_session_then_recovers_after_pacing(self):
        error = RuntimeError("timeout")
        error.code = self.native.ERROR_TIMEOUT
        self.session.engine.capture_and_send.side_effect = [error, None]
        self.session.draw()
        self.assert_attempt_finished()
        self.assertIsNone(self.session.pending_error)
        self.assertEqual(self.live._tick(), self.live._interval)
        self.assertIs(self.live._sessions[self.session.key], self.session)
        self.assertEqual(self.session.sender, 41)
        self.assertIs(self.session.draw_handle, self.draw_handle)
        self.assertIsNotNone(self.session.engine._offscreen)
        self.assertTrue(self.props.is_sending)
        self.assertEqual(self.props.last_error, "")
        self.area.tag_redraw.assert_called_once()
        self.native.destroy_sender.assert_not_called()
        self.session.engine.stop_sender.assert_not_called()
        self.bpy.types.SpaceView3D.draw_handler_remove.assert_not_called()

        self.clock.return_value = self.session.next_send - 0.001
        self.session.draw()
        self.session.engine.capture_and_send.assert_called_once()
        self.clock.return_value = 10.1
        self.session.draw()
        self.assertEqual(self.session.sent, 4)
        self.assertEqual(self.session.engine.capture_and_send.call_count, 2)
        self.assertFalse(self.session.drawing)
        self.assertAlmostEqual(self.session.next_send, 10.1 + self.live._interval)
        self.assertIsNone(self.session.pending_error)
        self.assertEqual(self.live._tick(), self.live._interval)
        self.native.destroy_sender.assert_not_called()

    def assert_deferred_fatal_cleanup(self, error):
        self.session.engine.capture_and_send.side_effect = error
        self.session.draw()
        self.assert_attempt_finished()
        self.assertEqual(self.session.pending_error, str(error))
        self.assertEqual(self.session.sender, 41)
        self.native.destroy_sender.assert_not_called()
        self.clock.return_value = 11.0
        self.session.draw()
        self.session.engine.capture_and_send.assert_called_once()

        self.assertIsNone(self.live._tick())
        self.native.destroy_sender.assert_called_once_with(41)
        self.session.engine.stop_sender.assert_called_once()
        self.bpy.types.SpaceView3D.draw_handler_remove.assert_called_once_with(self.draw_handle, "WINDOW")
        self.assertIsNone(self.session.sender)
        self.assertIsNone(self.session.draw_handle)
        self.assertIsNone(self.session.engine._offscreen)
        self.assertIsNone(self.session.pending_error)
        self.assertEqual(self.session.sent, 3)
        self.assertFalse(self.props.is_sending)
        self.assertEqual(self.props.sender_handle, -1)
        self.assertEqual(self.props.last_error, f"sender: {error}")
        self.assertNotIn(self.session.key, self.live._sessions)
        self.assertIsNone(self.live._tick())
        self.native.destroy_sender.assert_called_once()
        self.session.engine.stop_sender.assert_called_once()

    def test_uncoded_timeout_message_remains_fatal(self):
        self.assert_deferred_fatal_cleanup(RuntimeError("timeout"))

    def test_non_timeout_code_remains_fatal_even_with_timeout_message(self):
        error = RuntimeError("timeout")
        error.code = 11
        self.assert_deferred_fatal_cleanup(error)

    def test_other_exception_remains_fatal_even_with_timeout_code(self):
        error = ValueError("invalid pixels")
        error.code = self.native.ERROR_TIMEOUT
        self.assert_deferred_fatal_cleanup(error)

    def test_older_native_module_without_code_constant_remains_fatal(self):
        with mock.patch.object(self.live, "_timeout_code", None):
            error = RuntimeError("timeout")
            error.code = self.native.ERROR_TIMEOUT
            self.assert_deferred_fatal_cleanup(error)


if __name__ == "__main__":
    unittest.main()
