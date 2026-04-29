bl_info = {
    "name": "Nozzle Texture Sharing",
    "author": "nozzle-io",
    "version": (0, 1, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Nozzle",
    "description": "Share GPU textures with other applications via nozzle",
    "category": "Render",
}

import bpy
from . import properties, operators, panels, engine


def register():
    properties.register()
    operators.register()
    panels.register()


def unregister():
    panels.unregister()
    operators.unregister()
    properties.unregister()
