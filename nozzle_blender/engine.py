import bpy
import gpu
import struct
from array import array
from . import _nozzle_native

FORMAT = _nozzle_native.get_format_constants()
GL = _nozzle_native.get_gl_constants()
GL_TEXTURE_2D = GL["TEXTURE_2D"]

NOZZLE_FORMAT_RGBA8_UNORM = FORMAT["RGBA8_UNORM"]


def _image_pixels(result, semantic_format):
    data, width, height, storage_format, stride = result
    byte_formats = {FORMAT[key] for key in ("RGBA8_UNORM", "BGRA8_UNORM", "RGBA8_SRGB", "BGRA8_SRGB")}
    srgb_formats = {FORMAT["RGBA8_SRGB"], FORMAT["BGRA8_SRGB"]}
    if storage_format in byte_formats and semantic_format in byte_formats:
        pixel = struct.Struct("=4B")
    elif storage_format == semantic_format == FORMAT["RGBA16_FLOAT"]:
        pixel = struct.Struct("=4e")
    elif storage_format == semantic_format == FORMAT["RGBA32_FLOAT"]:
        pixel = struct.Struct("=4f")
    else:
        raise ValueError(f"unsupported image format: storage={storage_format}, semantic={semantic_format}")
    if width <= 0 or height <= 0 or stride < width * pixel.size or len(data) < (height-1)*stride + width*pixel.size:
        raise ValueError("invalid owned pixel layout")
    bgra = storage_format in {FORMAT["BGRA8_UNORM"], FORMAT["BGRA8_SRGB"]}
    values = array("f")
    # Native copy is top-down; Blender Image.pixels is bottom-up RGBA float.
    for y in range(height-1, -1, -1):
        for x in range(width):
            rgba = pixel.unpack_from(data, y*stride + x*pixel.size)
            if storage_format in byte_formats:
                rgba = [value / 255.0 for value in rgba]
                if bgra:
                    rgba[0], rgba[2] = rgba[2], rgba[0]
                if semantic_format in srgb_formats:
                    rgba[:3] = [v/12.92 if v <= 0.04045 else ((v+0.055)/1.055)**2.4 for v in rgba[:3]]
            values.extend(rgba)
    return values


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

            if result[1:3] != (w, h):
                raise ValueError("mapped and frame dimensions disagree")
            values = _image_pixels(result, info["semantic_format"])

            image_name = self._receive_image_name
            previous = bpy.data.images.get(image_name)
            replace = previous is None or tuple(previous.size) != (w, h) or not previous.is_float
            image = bpy.data.images.new(image_name, width=w, height=h, alpha=True, float_buffer=True) if replace else previous
            try:
                image.alpha_mode = "STRAIGHT"
                # Values are already linear (or untagged UNORM assumed linear).
                # The OCIO data role avoids a second color-space conversion.
                image.colorspace_settings.is_data = True
                image.pixels.foreach_set(values)
                image.update()
            except Exception:
                if replace:
                    bpy.data.images.remove(image)
                raise
            if replace:
                if previous is not None:
                    previous.user_remap(image)
                    bpy.data.images.remove(previous)
                image.name = image_name
            return True
        finally:
            _nozzle_native.receiver_release_frame(frame_handle)
