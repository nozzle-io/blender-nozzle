import bpy


class NozzleProperties(bpy.types.PropertyGroup):
    sender_name: bpy.props.StringProperty(
        name="Sender Name",
        default="Blender",
        description="Name published to the nozzle discovery system",
    )

    receiver_target: bpy.props.StringProperty(
        name="Sender Name",
        default="",
        description="Name of the nozzle sender to receive from",
    )

    capture_width: bpy.props.IntProperty(
        name="Width",
        default=1920,
        min=1,
        max=8192,
    )

    capture_height: bpy.props.IntProperty(
        name="Height",
        default=1080,
        min=1,
        max=8192,
    )

    is_sending: bpy.props.BoolProperty(
        name="Sending",
        default=False,
        options={"HIDDEN"},
    )

    is_receiving: bpy.props.BoolProperty(
        name="Receiving",
        default=False,
        options={"HIDDEN"},
    )

    sender_handle: bpy.props.IntProperty(
        default=-1,
        options={"HIDDEN"},
    )

    receiver_handle: bpy.props.IntProperty(
        default=-1,
        options={"HIDDEN"},
    )

    timer_handle: bpy.props.IntProperty(
        default=-1,
        options={"HIDDEN"},
    )

    available_senders: bpy.props.EnumProperty(
        name="Available Senders",
        description="Discovered nozzle senders",
        items=lambda self, context: _enum_senders(self),
    )


def _enum_senders(props):
    items = []
    try:
        from . import _nozzle_native
        senders = _nozzle_native.enumerate_senders()
        for s in senders:
            name = s.get("name", "")
            app = s.get("application_name", "")
            label = f"{name} ({app})" if app else name
            items.append((name, label, f"Sender from {app}"))
    except Exception:
        pass
    if not items:
        items.append(("__none__", "No senders found", ""))
    return items


classes = (NozzleProperties,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.nozzle_props = bpy.props.PointerProperty(type=NozzleProperties)


def unregister():
    del bpy.types.Scene.nozzle_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
