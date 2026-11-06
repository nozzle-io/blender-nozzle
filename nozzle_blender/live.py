"""Own live callbacks and native handles independently of saved scene properties."""
import time

import bpy
from bpy.app.handlers import persistent

from . import _nozzle_native as native
from .engine import NozzleEngine

_sessions = {}
_interval = 0.05
_unavailable = {"sender_not_found", "sender_closed"}


class Session:
    def __init__(self, context):
        self.scene = context.scene
        self.key = self.scene.as_pointer()
        self.window = self.area = self.region = None
        self.engine = NozzleEngine()
        self.sender = self.receiver = None
        # Intent survives missing producers even when no native handle exists yet.
        self.receiver_name = None
        self.sender_error = self.receiver_error = ""
        self.draw_handle = None
        self.drawing = False
        self.next_send = 0.0
        self.sent = self.received = 0
        self.send_seconds = self.receive_seconds = 0.0
        self.pending_error = None

    def scene_valid(self):
        try:
            return self.scene in tuple(bpy.data.scenes)
        except ReferenceError:
            return False

    def viewport_valid(self):
        try:
            return (self.window is not None and self.area is not None and self.region is not None
                    and self.window in tuple(bpy.context.window_manager.windows)
                    and self.window.scene == self.scene
                    and self.area in tuple(self.window.screen.areas)
                    and self.area.type == "VIEW_3D"
                    and self.region in tuple(self.area.regions))
        except ReferenceError:
            return False

    def bind_viewport(self, context):
        area = context.area
        region = next((r for r in area.regions if r.type == "WINDOW"), None) if area else None
        if context.window is None or area is None or area.type != "VIEW_3D" or region is None:
            raise RuntimeError("a live 3D viewport window is required for sender")
        self.window, self.area, self.region = context.window, area, region

    def show_status(self):
        self.scene.nozzle_props.last_error = "; ".join(
            message for message in (self.sender_error, self.receiver_error) if message)

    def stop(self, kind):
        errors = []
        if kind == "receiver":
            self.receiver_name = None
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
        if (self.sender is None or self.drawing or self.pending_error is not None or self.sender_error
                or not self.viewport_valid()
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
    key = context.scene.as_pointer()
    if _sessions and key not in _sessions:
        raise RuntimeError("stop the existing live scene before starting another")
    session = _sessions.get(key)
    if session is None:
        session = Session(context)
        _sessions[key] = session
    if has_resources(context.scene, kind) and (kind == "receiver" or session.sender is not None):
        raise RuntimeError(f"{kind} is already running")
    # A failed cleanup still owns its callback/offscreen; never overwrite it.
    if kind == "sender" and (session.draw_handle is not None or session.engine._offscreen is not None):
        session.stop("sender")
    props = context.scene.nozzle_props
    try:
        name = props.sender_name if kind == "sender" else props.receiver_target.strip()
        setattr(session, f"{kind}_error", "")
        if kind == "sender":
            session.bind_viewport(context)
        else:
            session.receiver_name = name
        try:
            handle = getattr(native, f"create_{kind}")(name=name, app_name="Blender")
        except RuntimeError as error:
            if kind != "receiver" or str(error) not in _unavailable:
                raise
            handle = None
            session.receiver_error = f"receiver waiting: {error}"
        setattr(session, kind, handle)
        if kind == "sender":
            session.pending_error = None
            session.draw_handle = bpy.types.SpaceView3D.draw_handler_add(session.draw, (), "WINDOW", "POST_PIXEL")
        setattr(props, f"{kind}_handle", handle if handle is not None else -1)
        setattr(props, "is_sending" if kind == "sender" else "is_receiving", True)
        session.show_status()
        if not bpy.app.timers.is_registered(_tick):
            bpy.app.timers.register(_tick, first_interval=_interval)
        if kind == "sender":
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
    if not session.viewport_valid():
        session.sender_error = "sender paused: original viewport unavailable"
        session.show_status()
        return False
    session.area.tag_redraw()
    return True


def has_resources(scene, kind):
    session = _sessions.get(scene.as_pointer())
    return session is not None and (getattr(session, kind) is not None or
           (kind == "receiver" and session.receiver_name is not None) or
           (kind == "sender" and (session.draw_handle is not None or session.engine._offscreen is not None)))


def _remove_empty():
    for key, session in tuple(_sessions.items()):
        if (session.sender is None and session.receiver is None and session.receiver_name is None
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
        if not session.scene_valid():
            for error in _stop_both(session):
                print(f"NOZZLE_LIVE_ERROR cleanup: {error}", flush=True)
            continue
        props = session.scene.nozzle_props
        if session.sender is None and (session.draw_handle is not None or session.engine._offscreen is not None):
            try:
                session.stop("sender")
            except Exception as error:
                session.sender_error = f"sender cleanup: {error}"
                print(f"NOZZLE_LIVE_ERROR {session.sender_error}", flush=True)
        if session.pending_error is not None:
            session.sender_error = f"sender: {session.pending_error}"
            print(f"NOZZLE_LIVE_ERROR sender: {session.pending_error}", flush=True)
            try:
                session.stop("sender")
            except Exception as error:
                session.sender_error += f"; sender cleanup: {error}"
                print(f"NOZZLE_LIVE_ERROR {session.sender_error}", flush=True)
            session.pending_error = None
        if session.receiver_name is not None:
            try:
                if session.receiver is None:
                    session.receiver = native.create_receiver(name=session.receiver_name, app_name="Blender")
                    props.receiver_handle = session.receiver
                start_time = time.perf_counter()
                if session.engine.receive_to_image(None, session.receiver, timeout_ms=0):
                    session.received += 1
                    session.receive_seconds = time.perf_counter() - start_time
                    session.receiver_error = ""
            except Exception as error:
                if isinstance(error, RuntimeError) and str(error) in _unavailable:
                    session.receiver_error = f"receiver waiting: {error}"
                else:
                    session.receiver_error = f"receiver: {error}"
                    print(f"NOZZLE_LIVE_ERROR {session.receiver_error}", flush=True)
                    try:
                        session.stop("receiver")
                    except Exception as cleanup_error:
                        session.receiver_error += f"; receiver cleanup: {cleanup_error}"
                        print(f"NOZZLE_LIVE_ERROR {session.receiver_error}", flush=True)
        if session.sender is not None:
            if session.viewport_valid():
                if session.sender_error.startswith("sender paused:"):
                    session.sender_error = ""
                if not session.sender_error:
                    session.area.tag_redraw()
            elif not session.sender_error or session.sender_error.startswith("sender paused:"):
                # Retain intent and resources across temporary screen/scene changes.
                session.sender_error = "sender paused: original viewport unavailable"
        session.show_status()
    _remove_empty()
    return _interval if _sessions else None


@persistent
def shutdown(_unused=None):
    if bpy.app.timers.is_registered(reset_state):
        bpy.app.timers.unregister(reset_state)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    errors = []
    for session in tuple(_sessions.values()):
        errors.extend(_stop_both(session))
    _remove_empty()
    if errors:
        raise RuntimeError("live cleanup: " + "; ".join(errors))


def register():
    # Blender restricts bpy.data during addon registration.
    bpy.app.timers.register(reset_state, first_interval=0.0)
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
        reset_scene_state(scene)


def reset_scene_state(scene):
    if scene.as_pointer() not in _sessions:
        props = scene.nozzle_props
        props.sender_handle = props.receiver_handle = -1
        props.is_sending = props.is_receiving = False
        props.last_error = ""
