import bpy


class NOZZLE_PT_sender(bpy.types.Panel):
    bl_label = "Nozzle Sender"
    bl_idname = "NOZZLE_PT_sender"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Nozzle"

    def draw(self, context):
        layout = self.layout
        props = context.scene.nozzle_props

        if props.is_sending:
            layout.prop(props, "sender_name")
            row = layout.row()
            row.prop(props, "capture_width")
            row.prop(props, "capture_height")
            layout.operator("nozzle.send_viewport", text="Send Single Frame")
            layout.operator("nozzle.stop_sender", text="Stop Sender", icon="X")
        else:
            layout.prop(props, "sender_name")
            row = layout.row()
            row.prop(props, "capture_width")
            row.prop(props, "capture_height")
            layout.operator("nozzle.start_sender", text="Start Sender")


class NOZZLE_PT_receiver(bpy.types.Panel):
    bl_label = "Nozzle Receiver"
    bl_idname = "NOZZLE_PT_receiver"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Nozzle"

    def draw(self, context):
        layout = self.layout
        props = context.scene.nozzle_props

        if props.is_receiving:
            layout.label(text=f"Connected to: {props.receiver_target}")
            layout.operator("nozzle.receive_texture", text="Receive Single Frame")
            layout.operator("nozzle.stop_receiver", text="Stop Receiver", icon="X")
        else:
            layout.prop(props, "available_senders")
            layout.prop(props, "receiver_target")
            row = layout.row()
            row.operator("nozzle.refresh_senders", text="Refresh")
            row.operator("nozzle.start_receiver", text="Start Receiver")


classes = (NOZZLE_PT_sender, NOZZLE_PT_receiver)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
