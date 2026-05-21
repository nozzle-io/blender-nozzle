import bpy
import gpu
from . import _nozzle_native

FORMAT = _nozzle_native.get_format_constants()
GL = _nozzle_native.get_gl_constants()
GL_TEXTURE_2D = GL["TEXTURE_2D"]

NOZZLE_FORMAT_RGBA8_UNORM = FORMAT["RGBA8_UNORM"]


class NozzleEngine:
    def __init__(self):
        self._offscreen = None
        self._receive_image_name = "__nozzle_received__"

    def stop_sender(self):
        self._free_offscreen()

    def stop_receiver(self):
        pass

    def _ensure_offscreen(self, width, height):
        if self._offscreen and self._offscreen.width == width and self._offscreen.height == height:
            return
        self._free_offscreen()
        self._offscreen = gpu.types.GPUOffScreen(width, height)

    def _free_offscreen(self):
        if self._offscreen:
            self._offscreen.free()
            self._offscreen = None

    def capture_and_send(self, context, sender_handle, width, height):
        self._ensure_offscreen(width, height)

        view_layer = context.view_layer
        self._offscreen.draw_view3d(
            context.scene,
            context.region,
            context.space_data,
            view_layer,
            context.depsgraph,
        )

        color_tex = self._offscreen.color_texture
        _nozzle_native.sender_publish_gl_texture(
            sender_handle,
            color_tex,
            GL_TEXTURE_2D,
            width,
            height,
            NOZZLE_FORMAT_RGBA8_UNORM,
        )
        return True

    def receive_to_image(self, context, receiver_handle):
        frame_handle = _nozzle_native.receiver_acquire_frame(receiver_handle, 100)
        if frame_handle is None:
            return False

        try:
            info = _nozzle_native.frame_get_info(frame_handle)
            w = info["width"]
            h = info["height"]

            result = _nozzle_native.frame_lock_pixels(frame_handle)
            if result is None:
                return False

            pixel_bytes, _, _, fmt, _ = result

            image_name = self._receive_image_name
            if image_name in bpy.data.images:
                image = bpy.data.images[image_name]
                if image.size[0] != w or image.size[1] != h:
                    bpy.data.images.remove(image)
                    image = None
            else:
                image = None

            if image is None:
                image = bpy.data.images.new(image_name, width=w, height=h)

            image.pixels = list(pixel_bytes)
            image.pack()
            return True
        finally:
            _nozzle_native.receiver_release_frame(frame_handle)

        return False
