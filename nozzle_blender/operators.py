import bpy
from . import _nozzle_native
from .engine import NozzleEngine

engine = NozzleEngine()

FORMAT = _nozzle_native.get_format_constants()
GL = _nozzle_native.get_gl_constants()
GL_TEXTURE_2D = GL["TEXTURE_2D"]


class NOZZLE_OT_start_sender(bpy.types.Operator):
    bl_idname = "nozzle.start_sender"
    bl_label = "Start Sender"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.nozzle_props
        if props.sender_handle >= 0:
            return {"CANCELLED"}

        handle = _nozzle_native.create_sender(
            name=props.sender_name,
            app_name="Blender",
            ring_size=3,
        )
        props.sender_handle = handle
        props.is_sending = True

        if context.area:
            props.timer_handle = context.window_manager.event_timer_add(0.05, window=context.window)

        self.report({"INFO"}, f"Nozzle sender started: {props.sender_name}")
        return {"FINISHED"}


class NOZZLE_OT_stop_sender(bpy.types.Operator):
    bl_idname = "nozzle.stop_sender"
    bl_label = "Stop Sender"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.nozzle_props
        if props.sender_handle < 0:
            return {"CANCELLED"}

        if props.timer_handle >= 0:
            context.window_manager.event_timer_remove(
                context.window_manager.timers[props.timer_handle]
            )
            props.timer_handle = -1

        _nozzle_native.destroy_sender(props.sender_handle)
        props.sender_handle = -1
        props.is_sending = False
        engine.stop_sender()

        self.report({"INFO"}, "Nozzle sender stopped")
        return {"FINISHED"}


class NOZZLE_OT_send_viewport(bpy.types.Operator):
    bl_idname = "nozzle.send_viewport"
    bl_label = "Send Viewport (Single Frame)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.nozzle_props
        if props.sender_handle < 0:
            self.report({"ERROR"}, "Start sender first")
            return {"CANCELLED"}

        result = engine.capture_and_send(
            context,
            props.sender_handle,
            props.capture_width,
            props.capture_height,
        )
        if result:
            self.report({"INFO"}, "Frame sent")
        else:
            self.report({"WARNING"}, "Failed to send frame")
        return {"FINISHED"}


class NOZZLE_OT_receive_texture(bpy.types.Operator):
    bl_idname = "nozzle.receive_texture"
    bl_label = "Receive Texture (Single Frame)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.nozzle_props
        if props.receiver_handle < 0:
            self.report({"ERROR"}, "Start receiver first")
            return {"CANCELLED"}

        result = engine.receive_to_image(context, props.receiver_handle)
        if result:
            self.report({"INFO"}, "Frame received")
        else:
            self.report({"WARNING"}, "No new frame available")
        return {"FINISHED"}


class NOZZLE_OT_start_receiver(bpy.types.Operator):
    bl_idname = "nozzle.start_receiver"
    bl_label = "Start Receiver"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.nozzle_props
        if props.receiver_handle >= 0:
            return {"CANCELLED"}

        target = props.receiver_target.strip()
        if not target:
            self.report({"ERROR"}, "Set a sender name to connect to")
            return {"CANCELLED"}

        handle = _nozzle_native.create_receiver(
            name=target,
            app_name="Blender",
        )
        props.receiver_handle = handle
        props.is_receiving = True

        self.report({"INFO"}, f"Nozzle receiver started: {target}")
        return {"FINISHED"}


class NOZZLE_OT_stop_receiver(bpy.types.Operator):
    bl_idname = "nozzle.stop_receiver"
    bl_label = "Stop Receiver"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.nozzle_props
        if props.receiver_handle < 0:
            return {"CANCELLED"}

        _nozzle_native.destroy_receiver(props.receiver_handle)
        props.receiver_handle = -1
        props.is_receiving = False
        engine.stop_receiver()

        self.report({"INFO"}, "Nozzle receiver stopped")
        return {"FINISHED"}


class NOZZLE_OT_refresh_senders(bpy.types.Operator):
    bl_idname = "nozzle.refresh_senders"
    bl_label = "Refresh Senders"
    bl_options = {"REGISTER"}

    def execute(self, context):
        context.scene.nozzle_props.available_senders
        return {"FINISHED"}


class NOZZLE_OT_timer_tick(bpy.types.Operator):
    bl_idname = "nozzle.timer_tick"
    bl_label = "Nozzle Timer"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        props = context.scene.nozzle_props
        if props.is_sending and props.sender_handle >= 0:
            engine.capture_and_send(
                context,
                props.sender_handle,
                props.capture_width,
                props.capture_height,
            )
        if props.is_receiving and props.receiver_handle >= 0:
            engine.receive_to_image(context, props.receiver_handle)
        return {"FINISHED"}


classes = (
    NOZZLE_OT_start_sender,
    NOZZLE_OT_stop_sender,
    NOZZLE_OT_send_viewport,
    NOZZLE_OT_receive_texture,
    NOZZLE_OT_start_receiver,
    NOZZLE_OT_stop_receiver,
    NOZZLE_OT_refresh_senders,
    NOZZLE_OT_timer_tick,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
