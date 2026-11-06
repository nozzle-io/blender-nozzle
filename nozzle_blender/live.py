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
        errors = []
        if kind == "sender":
            if self.draw_handle is not None:
                try:
                    bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, "WINDOW")
                    self.draw_handle = None
                except Exception as error:
                    errors.append(str(error))
            try:
                self.engine.stop_sender()
            except Exception as error:
                errors.append(str(error))
        handle = getattr(self, kind)
        if handle is not None:
            try:
                getattr(native, f"destroy_{kind}")(handle)
                setattr(self, kind, None)
            except Exception as error:
                errors.append(str(error))
        try:
            props = self.scene.nozzle_props
            remaining = getattr(self, kind)
            setattr(props, f"{kind}_handle", remaining if remaining is not None else -1)
            setattr(props, "is_sending" if kind == "sender" else "is_receiving", remaining is not None)
        except ReferenceError:
            pass
        if errors:
            raise RuntimeError("; ".join(errors))

    def draw(self):
        if (self.sender is None or self.drawing or self.pending_error is not None or not self.valid()
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
    if _sessions and key not in _sessions:
        raise RuntimeError("stop the existing live scene before starting another")
    session = _sessions.get(key)
    if session is None:
        session = Session(context)
        _sessions[key] = session
    if getattr(session, kind) is not None:
        raise RuntimeError(f"{kind} is already running")
    # A failed cleanup still owns its callback/offscreen; never overwrite it.
    if kind == "sender" and (session.draw_handle is not None or session.engine._offscreen is not None):
        session.stop("sender")
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
    try:
        if session is not None:
            session.stop(kind)
    finally:
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


def has_resources(scene, kind):
    session = _sessions.get(scene.as_pointer())
    return session is not None and (getattr(session, kind) is not None or
           (kind == "sender" and (session.draw_handle is not None or session.engine._offscreen is not None)))


def _remove_empty():
    for key, session in tuple(_sessions.items()):
        if (session.sender is None and session.receiver is None
                and session.draw_handle is None and session.engine._offscreen is None):
            del _sessions[key]


def _stop_both(session):
    errors = []
    for kind in ("sender", "receiver"):
        try:
            session.stop(kind)
        except Exception as error:
            errors.append(str(error))
    return errors


def _tick():
    for session in tuple(_sessions.values()):
        if not session.valid():
            for error in _stop_both(session):
                print(f"NOZZLE_LIVE_ERROR cleanup: {error}", flush=True)
            continue
        props = session.scene.nozzle_props
        if session.sender is None and (session.draw_handle is not None or session.engine._offscreen is not None):
            try:
                session.stop("sender")
            except Exception as error:
                props.last_error = f"sender cleanup: {error}"
                print(f"NOZZLE_LIVE_ERROR {props.last_error}", flush=True)
        if session.pending_error is not None:
            props.last_error = session.pending_error
            print(f"NOZZLE_LIVE_ERROR sender: {session.pending_error}", flush=True)
            try:
                session.stop("sender")
            except Exception as error:
                props.last_error = f"sender cleanup: {error}"
                print(f"NOZZLE_LIVE_ERROR {props.last_error}", flush=True)
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
                try:
                    session.stop("receiver")
                except Exception as cleanup_error:
                    props.last_error = f"receiver cleanup: {cleanup_error}"
                    print(f"NOZZLE_LIVE_ERROR {props.last_error}", flush=True)
        session.area.tag_redraw()
    _remove_empty()
    return _interval if _sessions else None


@persistent
def shutdown(_unused=None):
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    errors = []
    for session in tuple(_sessions.values()):
        errors.extend(_stop_both(session))
    _remove_empty()
    if errors:
        raise RuntimeError("live cleanup: " + "; ".join(errors))


def register():
    reset_state()
    if shutdown not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(shutdown)
    if reset_state not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(reset_state)


def unregister():
    shutdown()
    if shutdown in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(shutdown)
    if reset_state in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(reset_state)


@persistent
def reset_state(_unused=None):
    # Older .blend files may contain saved native IDs, which are not resources.
    for scene in bpy.data.scenes:
        props = scene.nozzle_props
        props.sender_handle = props.receiver_handle = -1
        props.is_sending = props.is_receiving = False
        props.last_error = ""
