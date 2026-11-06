"""Own live callbacks and native handles independently of saved scene properties."""
import time

import bpy
from bpy.app.handlers import persistent

from . import _nozzle_native as native
from .engine import NozzleEngine

_sessions = {}
_interval = 0.05


class Session:
    def __init__(self, context):
        self.scene = context.scene
        self.key = self.scene.as_pointer()
        self.window = context.window
        self.area = context.area
        self.region = next((r for r in self.area.regions if r.type == "WINDOW"), None)
        if self.window is None or self.area.type != "VIEW_3D" or self.region is None:
            raise RuntimeError("a live 3D viewport window is required")
        self.engine = NozzleEngine()
        self.sender = self.receiver = None
        self.draw_handle = None
        self.drawing = False
        self.next_send = 0.0
        self.sent = self.received = 0
        self.send_seconds = self.receive_seconds = 0.0
        self.pending_error = None

    def valid(self):
        try:
            return (self.window in tuple(bpy.context.window_manager.windows)
                    and self.window.scene == self.scene
                    and self.area in tuple(self.window.screen.areas)
                    and self.area.type == "VIEW_3D"
                    and self.region in tuple(self.area.regions))
        except ReferenceError:
            return False

    def stop(self, kind):
        try:
            if kind == "sender":
                if self.draw_handle is not None:
                    bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, "WINDOW")
                    self.draw_handle = None
                self.engine.stop_sender()
        finally:
            handle = getattr(self, kind)
            if handle is not None:
                getattr(native, f"destroy_{kind}")(handle)
                setattr(self, kind, None)
            try:
                props = self.scene.nozzle_props
                setattr(props, f"{kind}_handle", -1)
                setattr(props, "is_sending" if kind == "sender" else "is_receiving", False)
            except ReferenceError:
                pass

    def draw(self):
        if (self.sender is None or self.drawing or self.pending_error is not None
                or bpy.context.window != self.window or bpy.context.area != self.area
                or bpy.context.region != self.region or time.perf_counter() < self.next_send):
            return
        self.drawing = True
        start = time.perf_counter()
        try:
            props = self.scene.nozzle_props
            self.engine.capture_and_send(bpy.context, self.sender, props.capture_width, props.capture_height)
            self.sent += 1
            self.send_seconds = time.perf_counter() - start
        except Exception as error:
            # Remove/free drawing resources in the next timer, not inside their callback.
            self.pending_error = str(error)
        finally:
            self.next_send = time.perf_counter() + _interval
            self.drawing = False


def start(context, kind):
    if context.area is None or context.window is None:
        raise RuntimeError("a live 3D viewport window is required")
    key = context.scene.as_pointer()
    session = _sessions.get(key)
    if session is None:
        session = Session(context)
        _sessions[key] = session
    if getattr(session, kind) is not None:
        raise RuntimeError(f"{kind} is already running")
    props = context.scene.nozzle_props
    try:
        name = props.sender_name if kind == "sender" else props.receiver_target.strip()
        handle = getattr(native, f"create_{kind}")(name=name, app_name="Blender")
        setattr(session, kind, handle)
        if kind == "sender":
            session.pending_error = None
            session.draw_handle = bpy.types.SpaceView3D.draw_handler_add(session.draw, (), "WINDOW", "POST_PIXEL")
        setattr(props, f"{kind}_handle", handle)
        setattr(props, "is_sending" if kind == "sender" else "is_receiving", True)
        props.last_error = ""
        if not bpy.app.timers.is_registered(_tick):
            bpy.app.timers.register(_tick, first_interval=_interval)
        session.area.tag_redraw()
    except Exception:
        session.stop(kind)
        _remove_empty()
        raise
    return session


def stop(scene, kind):
    session = _sessions.get(scene.as_pointer())
    if session is not None:
        session.stop(kind)
    _remove_empty()
    if not _sessions and bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)


def request_capture(scene):
    session = _sessions.get(scene.as_pointer())
    if session is None or session.sender is None:
        return False
    session.next_send = 0.0
    session.area.tag_redraw()
    return True


def _remove_empty():
    for key, session in tuple(_sessions.items()):
        if session.sender is None and session.receiver is None:
            del _sessions[key]


def _tick():
    for session in tuple(_sessions.values()):
        if not session.valid():
            session.stop("sender")
            session.stop("receiver")
            continue
        props = session.scene.nozzle_props
        if session.pending_error is not None:
            props.last_error = session.pending_error
            print(f"NOZZLE_LIVE_ERROR sender: {session.pending_error}", flush=True)
            session.stop("sender")
            session.pending_error = None
        if session.receiver is not None:
            try:
                start_time = time.perf_counter()
                if session.engine.receive_to_image(None, session.receiver, timeout_ms=0):
                    session.received += 1
                    session.receive_seconds = time.perf_counter() - start_time
            except Exception as error:
                props.last_error = str(error)
                print(f"NOZZLE_LIVE_ERROR receiver: {error}", flush=True)
                session.stop("receiver")
        session.area.tag_redraw()
    _remove_empty()
    return _interval if _sessions else None


@persistent
def shutdown(_unused=None):
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for session in tuple(_sessions.values()):
        session.stop("sender")
        session.stop("receiver")
    _sessions.clear()


def register():
    if shutdown not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(shutdown)


def unregister():
    shutdown()
    if shutdown in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(shutdown)
