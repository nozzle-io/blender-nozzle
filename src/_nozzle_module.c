#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <string.h>
#include "nozzle_module.h"

static nozzle_handle_store g_handles = {0};

// --- Handle management ---

static int alloc_sender_handle(NozzleSender *sender) {
    for (int i = 0; i < MAX_HANDLES; i++) {
        if (g_handles.senders[i] == NULL) {
            g_handles.senders[i] = sender;
            return i;
        }
    }
    return -1;
}

static int alloc_receiver_handle(NozzleReceiver *receiver) {
    for (int i = 0; i < MAX_HANDLES; i++) {
        if (g_handles.receivers[i] == NULL) {
            g_handles.receivers[i] = receiver;
            return i;
        }
    }
    return -1;
}

static int alloc_frame_handle(NozzleFrame *frame) {
    for (int i = 0; i < MAX_HANDLES; i++) {
        if (g_handles.frames[i] == NULL) {
            g_handles.frames[i] = frame;
            return i;
        }
    }
    return -1;
}

static void free_sender_handle(int h) {
    if (h >= 0 && h < MAX_HANDLES) {
        g_handles.senders[h] = NULL;
    }
}

static void free_receiver_handle(int h) {
    if (h >= 0 && h < MAX_HANDLES) {
        g_handles.receivers[h] = NULL;
    }
}

static void free_frame_handle(int h) {
    if (h >= 0 && h < MAX_HANDLES) {
        g_handles.frames[h] = NULL;
    }
}

// --- Error code to string ---

static const char *error_code_str(NozzleErrorCode code) {
    switch (code) {
        case NOZZLE_OK: return "ok";
        case NOZZLE_ERROR_UNKNOWN: return "unknown";
        case NOZZLE_ERROR_INVALID_ARGUMENT: return "invalid_argument";
        case NOZZLE_ERROR_UNSUPPORTED_BACKEND: return "unsupported_backend";
        case NOZZLE_ERROR_UNSUPPORTED_FORMAT: return "unsupported_format";
        case NOZZLE_ERROR_DEVICE_MISMATCH: return "device_mismatch";
        case NOZZLE_ERROR_RESOURCE_CREATION_FAILED: return "resource_creation_failed";
        case NOZZLE_ERROR_SHARED_HANDLE_FAILED: return "shared_handle_failed";
        case NOZZLE_ERROR_SENDER_NOT_FOUND: return "sender_not_found";
        case NOZZLE_ERROR_SENDER_CLOSED: return "sender_closed";
        case NOZZLE_ERROR_TIMEOUT: return "timeout";
        case NOZZLE_ERROR_BACKEND_ERROR: return "backend_error";
        default: return "unknown";
    }
}

static NozzleTextureFormat format_from_int(int fmt) {
    if (fmt >= NOZZLE_FORMAT_UNKNOWN && fmt <= NOZZLE_FORMAT_DEPTH32_FLOAT) {
        return (NozzleTextureFormat)fmt;
    }
    return NOZZLE_FORMAT_RGBA8_UNORM;
}

// --- Helper: raise NozzleError or return None on OK ---

static PyObject *check_error(NozzleErrorCode code) {
    if (code == NOZZLE_OK) {
        Py_RETURN_NONE;
    }
    PyErr_SetString(PyExc_RuntimeError, error_code_str(code));
    return NULL;
}

// --- Python functions ---

static PyObject *py_create_sender(PyObject *self, PyObject *args, PyObject *kwargs) {
    (void)self;
    const char *name = NULL;
    const char *app_name = "Blender";
    uint32_t ring_size = 3;

    static char *kwlist[] = {"name", "app_name", "ring_size", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "s|sI", kwlist,
                                     &name, &app_name, &ring_size)) {
        return NULL;
    }

    NozzleSenderDesc desc = {0};
    desc.name = name;
    desc.application_name = app_name;
    desc.ring_buffer_size = ring_size;
    desc.allow_format_fallback = 1;

    NozzleSender *sender = NULL;
    NozzleErrorCode err = nozzle_sender_create(&desc, &sender);
    if (err != NOZZLE_OK) {
        PyErr_SetString(PyExc_RuntimeError, error_code_str(err));
        return NULL;
    }

    int h = alloc_sender_handle(sender);
    if (h < 0) {
        nozzle_sender_destroy(sender);
        PyErr_SetString(PyExc_RuntimeError, "too many sender handles");
        return NULL;
    }

    return PyLong_FromLong(h);
}

static PyObject *py_destroy_sender(PyObject *self, PyObject *args) {
    (void)self;
    int handle;
    if (!PyArg_ParseTuple(args, "i", &handle)) return NULL;

    if (handle < 0 || handle >= MAX_HANDLES || g_handles.senders[handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid sender handle");
        return NULL;
    }

    nozzle_sender_destroy(g_handles.senders[handle]);
    free_sender_handle(handle);
    Py_RETURN_NONE;
}

static PyObject *py_sender_acquire_writable_frame(PyObject *self, PyObject *args) {
    (void)self;
    int handle;
    uint32_t width, height;
    int fmt_int;

    if (!PyArg_ParseTuple(args, "iIIi", &handle, &width, &height, &fmt_int)) return NULL;

    if (handle < 0 || handle >= MAX_HANDLES || g_handles.senders[handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid sender handle");
        return NULL;
    }

    NozzleFrame *frame = NULL;
    NozzleErrorCode err = nozzle_sender_acquire_writable_frame(
        g_handles.senders[handle], width, height, format_from_int(fmt_int), &frame);
    if (err != NOZZLE_OK) {
        return check_error(err);
    }

    int fh = alloc_frame_handle(frame);
    if (fh < 0) {
        nozzle_sender_commit_frame(g_handles.senders[handle], frame);
        PyErr_SetString(PyExc_RuntimeError, "too many frame handles");
        return NULL;
    }

    return PyLong_FromLong(fh);
}

static PyObject *py_sender_commit_frame(PyObject *self, PyObject *args) {
    (void)self;
    int sender_handle, frame_handle;
    if (!PyArg_ParseTuple(args, "ii", &sender_handle, &frame_handle)) return NULL;

    if (sender_handle < 0 || sender_handle >= MAX_HANDLES ||
        g_handles.senders[sender_handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid sender handle");
        return NULL;
    }
    if (frame_handle < 0 || frame_handle >= MAX_HANDLES ||
        g_handles.frames[frame_handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid frame handle");
        return NULL;
    }

    NozzleErrorCode err = nozzle_sender_commit_frame(
        g_handles.senders[sender_handle], g_handles.frames[frame_handle]);
    free_frame_handle(frame_handle);
    return check_error(err);
}

static PyObject *py_sender_publish_gl_texture(PyObject *self, PyObject *args) {
    (void)self;
    int handle;
    uint32_t gl_tex, gl_target, width, height;
    int fmt_int;

    if (!PyArg_ParseTuple(args, "iIIIIi", &handle, &gl_tex, &gl_target,
                          &width, &height, &fmt_int)) return NULL;

    if (handle < 0 || handle >= MAX_HANDLES || g_handles.senders[handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid sender handle");
        return NULL;
    }

    NozzleErrorCode err = nozzle_sender_publish_gl_texture(
        g_handles.senders[handle], gl_tex, gl_target, width, height,
        format_from_int(fmt_int));
    return check_error(err);
}

static PyObject *py_create_receiver(PyObject *self, PyObject *args, PyObject *kwargs) {
    (void)self;
    const char *name = NULL;
    const char *app_name = "Blender";

    static char *kwlist[] = {"name", "app_name", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "s|s", kwlist, &name, &app_name)) {
        return NULL;
    }

    NozzleReceiverDesc desc = {0};
    desc.name = name;
    desc.application_name = app_name;
    desc.receive_mode = NOZZLE_RECEIVE_LATEST_ONLY;

    NozzleReceiver *receiver = NULL;
    NozzleErrorCode err = nozzle_receiver_create(&desc, &receiver);
    if (err != NOZZLE_OK) {
        PyErr_SetString(PyExc_RuntimeError, error_code_str(err));
        return NULL;
    }

    int h = alloc_receiver_handle(receiver);
    if (h < 0) {
        nozzle_receiver_destroy(receiver);
        PyErr_SetString(PyExc_RuntimeError, "too many receiver handles");
        return NULL;
    }

    return PyLong_FromLong(h);
}

static PyObject *py_destroy_receiver(PyObject *self, PyObject *args) {
    (void)self;
    int handle;
    if (!PyArg_ParseTuple(args, "i", &handle)) return NULL;

    if (handle < 0 || handle >= MAX_HANDLES || g_handles.receivers[handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid receiver handle");
        return NULL;
    }

    nozzle_receiver_destroy(g_handles.receivers[handle]);
    free_receiver_handle(handle);
    Py_RETURN_NONE;
}

static PyObject *py_receiver_acquire_frame(PyObject *self, PyObject *args) {
    (void)self;
    int handle;
    uint64_t timeout_ms = 100;

    if (!PyArg_ParseTuple(args, "i|K", &handle, &timeout_ms)) return NULL;

    if (handle < 0 || handle >= MAX_HANDLES || g_handles.receivers[handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid receiver handle");
        return NULL;
    }

    NozzleAcquireDesc desc = {0};
    desc.timeout_ms = timeout_ms;

    NozzleFrame *frame = NULL;
    NozzleErrorCode err = nozzle_receiver_acquire_frame(
        g_handles.receivers[handle], &desc, &frame);
    if (err != NOZZLE_OK) {
        return check_error(err);
    }

    int fh = alloc_frame_handle(frame);
    if (fh < 0) {
        nozzle_frame_release(frame);
        PyErr_SetString(PyExc_RuntimeError, "too many frame handles");
        return NULL;
    }

    return PyLong_FromLong(fh);
}

static PyObject *py_receiver_release_frame(PyObject *self, PyObject *args) {
    (void)self;
    int frame_handle;
    if (!PyArg_ParseTuple(args, "i", &frame_handle)) return NULL;

    if (frame_handle < 0 || frame_handle >= MAX_HANDLES ||
        g_handles.frames[frame_handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid frame handle");
        return NULL;
    }

    nozzle_frame_release(g_handles.frames[frame_handle]);
    free_frame_handle(frame_handle);
    Py_RETURN_NONE;
}

static PyObject *py_frame_get_info(PyObject *self, PyObject *args) {
    (void)self;
    int frame_handle;
    if (!PyArg_ParseTuple(args, "i", &frame_handle)) return NULL;

    if (frame_handle < 0 || frame_handle >= MAX_HANDLES ||
        g_handles.frames[frame_handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid frame handle");
        return NULL;
    }

    NozzleFrameInfo info = {0};
    NozzleErrorCode err = nozzle_frame_get_info(g_handles.frames[frame_handle], &info);
    if (err != NOZZLE_OK) {
        return check_error(err);
    }

    return Py_BuildValue("{sK sK sI sI si sI}",
        "frame_index", info.frame_index,
        "timestamp_ns", info.timestamp_ns,
        "width", info.width,
        "height", info.height,
        "format", (int)info.format,
        "dropped_frame_count", info.dropped_frame_count);
}

static PyObject *py_frame_lock_pixels(PyObject *self, PyObject *args) {
    (void)self;
    int frame_handle;
    if (!PyArg_ParseTuple(args, "i", &frame_handle)) return NULL;

    if (frame_handle < 0 || frame_handle >= MAX_HANDLES ||
        g_handles.frames[frame_handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid frame handle");
        return NULL;
    }

    NozzleMappedPixels pixels = {0};
    NozzleErrorCode err = nozzle_frame_lock_pixels_with_origin(
        g_handles.frames[frame_handle], NOZZLE_ORIGIN_TOP_LEFT, &pixels);
    if (err != NOZZLE_OK) {
        return check_error(err);
    }

    Py_ssize_t total = (Py_ssize_t)pixels.row_stride_bytes * pixels.height;
    PyObject *bytes = PyBytes_FromStringAndSize((const char *)pixels.data, total);
    if (!bytes) {
        nozzle_frame_unlock_pixels(g_handles.frames[frame_handle]);
        return NULL;
    }

    nozzle_frame_unlock_pixels(g_handles.frames[frame_handle]);

    return Py_BuildValue("(N I I i I)",
        bytes,
        pixels.width,
        pixels.height,
        (int)pixels.format,
        pixels.row_stride_bytes);
}

static PyObject *py_frame_lock_writable_pixels(PyObject *self, PyObject *args) {
    (void)self;
    int frame_handle;
    if (!PyArg_ParseTuple(args, "i", &frame_handle)) return NULL;

    if (frame_handle < 0 || frame_handle >= MAX_HANDLES ||
        g_handles.frames[frame_handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid frame handle");
        return NULL;
    }

    NozzleMappedPixels pixels = {0};
    NozzleErrorCode err = nozzle_frame_lock_writable_pixels_with_origin(
        g_handles.frames[frame_handle], NOZZLE_ORIGIN_TOP_LEFT, &pixels);
    if (err != NOZZLE_OK) {
        return check_error(err);
    }

    Py_ssize_t total = (Py_ssize_t)pixels.row_stride_bytes * pixels.height;
    PyObject *bytes = PyBytes_FromStringAndSize((const char *)pixels.data, total);
    if (!bytes) {
        nozzle_frame_unlock_writable_pixels(g_handles.frames[frame_handle]);
        return NULL;
    }

    nozzle_frame_unlock_writable_pixels(g_handles.frames[frame_handle]);

    return Py_BuildValue("(N I I i I)",
        bytes,
        pixels.width,
        pixels.height,
        (int)pixels.format,
        pixels.row_stride_bytes);
}

static PyObject *py_frame_copy_to_gl_texture(PyObject *self, PyObject *args) {
    (void)self;
    int frame_handle;
    uint32_t gl_tex, gl_target, width, height;
    int fmt_int;

    if (!PyArg_ParseTuple(args, "iIIIIi", &frame_handle, &gl_tex, &gl_target,
                          &width, &height, &fmt_int)) return NULL;

    if (frame_handle < 0 || frame_handle >= MAX_HANDLES ||
        g_handles.frames[frame_handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid frame handle");
        return NULL;
    }

    NozzleErrorCode err = nozzle_frame_copy_to_gl_texture(
        g_handles.frames[frame_handle], gl_tex, gl_target, width, height,
        format_from_int(fmt_int));
    return check_error(err);
}

static PyObject *py_enumerate_senders(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;

    NozzleSenderInfoArray arr = {0};
    NozzleErrorCode err = nozzle_enumerate_senders(&arr);
    if (err != NOZZLE_OK) {
        return check_error(err);
    }

    PyObject *list = PyList_New(0);
    if (!list) {
        nozzle_free_sender_info_array(&arr);
        return NULL;
    }

    for (uint32_t i = 0; i < arr.count; i++) {
        NozzleSenderInfo *info = &arr.items[i];
        PyObject *dict = Py_BuildValue("{s s s s s i}",
            "name", info->name ? info->name : "",
            "application_name", info->application_name ? info->application_name : "",
            "id", info->id ? info->id : "",
            "backend", (int)info->backend);
        if (dict) {
            PyList_Append(list, dict);
            Py_DECREF(dict);
        }
    }

    nozzle_free_sender_info_array(&arr);
    return list;
}

static PyObject *py_receiver_get_connected_info(PyObject *self, PyObject *args) {
    (void)self;
    int handle;
    if (!PyArg_ParseTuple(args, "i", &handle)) return NULL;

    if (handle < 0 || handle >= MAX_HANDLES || g_handles.receivers[handle] == NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid receiver handle");
        return NULL;
    }

    NozzleConnectedSenderInfo info = {0};
    NozzleErrorCode err = nozzle_receiver_get_connected_info(
        g_handles.receivers[handle], &info);
    if (err != NOZZLE_OK) {
        return check_error(err);
    }

    return Py_BuildValue("{s s s s s I s I s I si sK sK sd}",
        "name", info.name ? info.name : "",
        "application_name", info.application_name ? info.application_name : "",
        "id", info.id ? info.id : "",
        "backend", (int)info.backend,
        "width", info.width,
        "height", info.height,
        "format", (int)info.format,
        "estimated_fps", info.estimated_fps,
        "frame_counter", info.frame_counter,
        "last_update_time_ns", info.last_update_time_ns);
}

// --- Format constants ---

static PyObject *py_get_format_constants(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    return Py_BuildValue("{s i s i s i s i s i s i s i s i s i s i s i s i s i s i s i s i s i}",
        "UNKNOWN", (int)NOZZLE_FORMAT_UNKNOWN,
        "R8_UNORM", (int)NOZZLE_FORMAT_R8_UNORM,
        "RG8_UNORM", (int)NOZZLE_FORMAT_RG8_UNORM,
        "RGBA8_UNORM", (int)NOZZLE_FORMAT_RGBA8_UNORM,
        "BGRA8_UNORM", (int)NOZZLE_FORMAT_BGRA8_UNORM,
        "RGBA8_SRGB", (int)NOZZLE_FORMAT_RGBA8_SRGB,
        "BGRA8_SRGB", (int)NOZZLE_FORMAT_BGRA8_SRGB,
        "R16_UNORM", (int)NOZZLE_FORMAT_R16_UNORM,
        "RG16_UNORM", (int)NOZZLE_FORMAT_RG16_UNORM,
        "RGBA16_UNORM", (int)NOZZLE_FORMAT_RGBA16_UNORM,
        "R16_FLOAT", (int)NOZZLE_FORMAT_R16_FLOAT,
        "RG16_FLOAT", (int)NOZZLE_FORMAT_RG16_FLOAT,
        "RGBA16_FLOAT", (int)NOZZLE_FORMAT_RGBA16_FLOAT,
        "R32_FLOAT", (int)NOZZLE_FORMAT_R32_FLOAT,
        "RG32_FLOAT", (int)NOZZLE_FORMAT_RG32_FLOAT,
        "RGBA32_FLOAT", (int)NOZZLE_FORMAT_RGBA32_FLOAT,
        "R32_UINT", (int)NOZZLE_FORMAT_R32_UINT,
        "RGBA32_UINT", (int)NOZZLE_FORMAT_RGBA32_UINT,
        "DEPTH32_FLOAT", (int)NOZZLE_FORMAT_DEPTH32_FLOAT);
}

// --- GL target constants ---

static PyObject *py_get_gl_constants(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    // GL_TEXTURE_2D = 0x0DE1
    return Py_BuildValue("{s i}", "TEXTURE_2D", 0x0DE1);
}

// --- Module method table ---

static PyMethodDef nozzle_methods[] = {
    {"create_sender", (PyCFunction)py_create_sender,
     METH_VARARGS | METH_KEYWORDS, "Create a nozzle sender. Returns handle."},
    {"destroy_sender", py_destroy_sender,
     METH_VARARGS, "Destroy a nozzle sender by handle."},
    {"sender_acquire_writable_frame", py_sender_acquire_writable_frame,
     METH_VARARGS, "Acquire a writable frame from sender. Returns frame handle."},
    {"sender_commit_frame", py_sender_commit_frame,
     METH_VARARGS, "Commit a frame to the sender."},
    {"sender_publish_gl_texture", py_sender_publish_gl_texture,
     METH_VARARGS, "Publish a GL texture directly via sender."},
    {"create_receiver", (PyCFunction)py_create_receiver,
     METH_VARARGS | METH_KEYWORDS, "Create a nozzle receiver. Returns handle."},
    {"destroy_receiver", py_destroy_receiver,
     METH_VARARGS, "Destroy a nozzle receiver by handle."},
    {"receiver_acquire_frame", py_receiver_acquire_frame,
     METH_VARARGS, "Acquire a frame from receiver. Returns frame handle."},
    {"receiver_release_frame", py_receiver_release_frame,
     METH_VARARGS, "Release a frame."},
    {"frame_get_info", py_frame_get_info,
     METH_VARARGS, "Get frame info dict."},
    {"frame_lock_pixels", py_frame_lock_pixels,
     METH_VARARGS, "Lock frame pixels, returns (bytes, w, h, format, row_bytes)."},
    {"frame_lock_writable_pixels", py_frame_lock_writable_pixels,
     METH_VARARGS, "Lock frame writable pixels, returns (bytes, w, h, format, row_bytes)."},
    {"frame_copy_to_gl_texture", py_frame_copy_to_gl_texture,
     METH_VARARGS, "Copy frame to a GL texture."},
    {"enumerate_senders", py_enumerate_senders,
     METH_NOARGS, "Enumerate available senders."},
    {"receiver_get_connected_info", py_receiver_get_connected_info,
     METH_VARARGS, "Get connected sender info."},
    {"get_format_constants", py_get_format_constants,
     METH_NOARGS, "Get dict of format constants."},
    {"get_gl_constants", py_get_gl_constants,
     METH_NOARGS, "Get dict of GL constants."},
    {NULL, NULL, 0, NULL}
};

// --- Module definition ---

static struct PyModuleDef nozzle_module = {
    PyModuleDef_HEAD_INIT,
    "_nozzle_native",
    "Python C extension wrapping the nozzle texture sharing C ABI.",
    -1,
    nozzle_methods
};

PyMODINIT_FUNC PyInit__nozzle_native(void) {
    memset(&g_handles, 0, sizeof(g_handles));
    return PyModule_Create(&nozzle_module);
}
