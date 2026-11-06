import bpy
from . import live
from .engine import NozzleEngine

engine = NozzleEngine()

class NOZZLE_OT_start_sender(bpy.types.Operator):
    bl_idname = "nozzle.start_sender"
    bl_label = "Start Sender"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.nozzle_props
        if props.sender_handle >= 0:
            return {"CANCELLED"}

        try:
            live.start(context, "sender")
        except Exception as error:
            props.last_error = str(error)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        self.report({"INFO"}, f"Nozzle sender started: {props.sender_name}")
        return {"FINISHED"}


class NOZZLE_OT_stop_sender(bpy.types.Operator):
    bl_idname = "nozzle.stop_sender"
    bl_label = "Stop Sender"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.nozzle_props
        if not live.has_resources(context.scene, "sender"):
            return {"CANCELLED"}

        live.stop(context.scene, "sender")

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

        # Capture must run in the viewport draw context, not the sidebar operator.
        if not live.request_capture(context.scene):
            return {"CANCELLED"}
        self.report({"INFO"}, "Viewport capture requested")
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

        try:
            result = engine.receive_to_image(context, props.receiver_handle)
        except (ValueError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
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

        try:
            live.start(context, "receiver")
        except Exception as error:
            props.last_error = str(error)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        self.report({"INFO"}, f"Nozzle receiver started: {target}")
        return {"FINISHED"}


class NOZZLE_OT_stop_receiver(bpy.types.Operator):
    bl_idname = "nozzle.stop_receiver"
    bl_label = "Stop Receiver"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.nozzle_props
        if not live.has_resources(context.scene, "receiver"):
            return {"CANCELLED"}

        live.stop(context.scene, "receiver")

        self.report({"INFO"}, "Nozzle receiver stopped")
        return {"FINISHED"}


class NOZZLE_OT_refresh_senders(bpy.types.Operator):
    bl_idname = "nozzle.refresh_senders"
    bl_label = "Refresh Senders"
    bl_options = {"REGISTER"}

    def execute(self, context):
        context.scene.nozzle_props.available_senders
        return {"FINISHED"}


classes = (
    NOZZLE_OT_start_sender,
    NOZZLE_OT_stop_sender,
    NOZZLE_OT_send_viewport,
    NOZZLE_OT_receive_texture,
    NOZZLE_OT_start_receiver,
    NOZZLE_OT_stop_receiver,
    NOZZLE_OT_refresh_senders,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    live.register()


def unregister():
    live.unregister()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
