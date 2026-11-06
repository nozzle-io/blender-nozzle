import bpy
import gpu
import numpy as np
from . import _nozzle_native

FORMAT = _nozzle_native.get_format_constants()
_SRGB = np.array([v/255/12.92 if v <= 10 else ((v/255+0.055)/1.055)**2.4
                  for v in range(256)], dtype=np.float32)
_ALPHA = np.arange(256, dtype=np.float32) / 255.0


def _image_pixels(result, semantic_format):
    data, width, height, storage_format, stride = result
    byte_formats = {FORMAT[key] for key in ("RGBA8_UNORM", "BGRA8_UNORM", "RGBA8_SRGB", "BGRA8_SRGB")}
    if storage_format in byte_formats and semantic_format in byte_formats:
        dtype = np.dtype("u1")
    elif storage_format == semantic_format == FORMAT["RGBA16_FLOAT"]:
        dtype = np.dtype("=f2")
    elif storage_format == semantic_format == FORMAT["RGBA32_FLOAT"]:
        dtype = np.dtype("=f4")
    else:
        raise ValueError(f"unsupported image format: storage={storage_format}, semantic={semantic_format}")
    pixel_size = 4 * dtype.itemsize
    if width <= 0 or height <= 0 or stride < width * pixel_size or len(data) < (height-1)*stride + width*pixel_size:
        raise ValueError("invalid owned pixel layout")
    bgra = storage_format in {FORMAT["BGRA8_UNORM"], FORMAT["BGRA8_SRGB"]}
    # The view skips row padding; the result is owned, packed bottom-up RGBA.
    source = np.ndarray((height, width, 4), dtype=dtype, buffer=data,
                        strides=(stride, pixel_size, dtype.itemsize))[::-1]
    values = np.empty((height, width, 4), dtype=np.float32)
    if storage_format in byte_formats:
        for dst, src in enumerate((2, 1, 0) if bgra else (0, 1, 2)):
            values[:, :, dst] = _SRGB[source[:, :, src]]
        values[:, :, 3] = _ALPHA[source[:, :, 3]]
    else:
        np.copyto(values, source)
    return values.reshape(-1)


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
        self._offscreen = gpu.types.GPUOffScreen(width, height, format="RGBA32F")

    def _free_offscreen(self):
        if self._offscreen:
            self._offscreen.free()
            self._offscreen = None

    def capture_and_send(self, context, sender_handle, width, height):
        self._ensure_offscreen(width, height)

        self._offscreen.draw_view3d(
            context.scene,
            context.view_layer,
            context.space_data,
            context.region,
            context.region_data.view_matrix,
            context.region_data.window_matrix,
            do_color_management=False,
        )
        pixels = self._offscreen.texture_color.read()
        values = np.asarray(pixels, dtype=np.float32)
        _nozzle_native.sender_publish_rgba32f(sender_handle, width, height, values.tobytes())
        return True

    def receive_to_image(self, context, receiver_handle, timeout_ms=100):
        frame_handle = _nozzle_native.receiver_acquire_frame(receiver_handle, timeout_ms)
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
            reference = bpy.data.images.new("__nozzle_float_role__", width=1, height=1, float_buffer=True)
            try:
                default_float_space = reference.colorspace_settings.name
            finally:
                bpy.data.images.remove(reference)
            replace = (previous is None or tuple(previous.size) != (w, h) or not previous.is_float
                       or previous.colorspace_settings.name != default_float_space)
            image = bpy.data.images.new(image_name, width=w, height=h, alpha=True, float_buffer=True) if replace else previous
            try:
                image.alpha_mode = "STRAIGHT"
                # Keep Blender's default float role. Tagging color as data would
                # bypass display color management as well as input conversion.
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
