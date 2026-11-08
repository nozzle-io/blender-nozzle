# blender-nozzle

> This codebase is currently in its AI-slob prototyping phase: the code runs on momentum, vibes, and plausible intent.
> Proper debugging will be introduced once demand graduates from hypothetical to measurable.

Blender addon for GPU texture sharing via [nozzle](https://github.com/nozzle-io/nozzle).

Send Blender viewport renders or receive textures from other nozzle-compatible applications (openFrameworks, Max, etc.).

## Disclaimer / Notice

This library is currently a work in progress and contains many incomplete features and unverified implementations.
Although it may appear usable at first glance, it may not function correctly.

Please use it with the understanding that no guarantees are made regarding its behavior, and perform debugging, validation, and review as needed.
If you encounter problems, please do not become angry; instead, contributions in the form of Issues or Pull Requests would be greatly appreciated.

## Status

Early development. Live processing uses the Blender UI thread. The sender captures
scene-linear RGBA32F in a viewport draw callback, reads it back to the CPU, then
publishes through nozzle. It does not pass a Metal texture as an OpenGL ID and is
not a zero-copy sender. The receiver copies pixels into a float Blender image.
The foreground CI probe targets Blender 4.3.2 on macOS Metal; builds/imports on
other platforms do not establish live viewport interoperability.
The probe checks changing viewport content and float32 transport, not an exact
rendered-color/orientation oracle. Exact low-mantissa and row-orientation checks
cover synthetic float bytes through the native publisher; they do not prove the
renderer itself preserves arbitrary HDR values. See [live evidence](docs/live-processing.md).

## Requirements

- Blender with its bundled NumPy (the packaged CI target is Blender 4.3.2)
- macOS 12+ / Windows 10+ / Linux
- The published Linux x64 build targets **x86-64-v2** (no AVX, AVX2, FMA or F16C). CI sets this explicitly and verifies the binary with `nozzle/scripts/check_x86_64_isa.sh`.
- CMake 3.20+
- C++17 compiler

## Native Metadata

The native `receiver_get_connected_info` function copies an owned C snapshot into
the existing Python dictionary schema and types. Metadata is last-known state,
not a connection predicate or fresh liveness check. Returned strings and values
survive later calls and handle closure; this does not add concurrent handle
destruction safety. The existing macOS native metadata contract checks this
ownership using the built extension and real Metal peer, including UTF-8 names.
It is separate from Blender UI/pixel validation and does not establish runtime
receiver behavior on Windows or Linux.

## Building

The native extension wraps the nozzle C ABI into a Python module.

```bash
# Clone with submodule
git clone --recurse-submodules git@github.com:nozzle-io/blender-nozzle.git
cd blender-nozzle

# Build (set BLENDER_PYTHON_PATH to Blender's bundled Python for compatibility)
export BLENDER_PYTHON_PATH=/Applications/Blender.app/Contents/Resources/python
python3 build.py
```

This builds `_nozzle_native` with the active Python ABI suffix (for example
`_nozzle_native.cpython-313-darwin.so` on macOS or a `.pyd` on Windows) and
copies it into `nozzle_blender/`.

## Installing in Blender

1. Build the native module (see above)
2. In Blender: Edit > Preferences > Add-ons > Install...
3. Select a zip whose top-level entry is the `nozzle_blender/` directory
   containing `__init__.py`, the Python files, and the built `_nozzle_native`
   extension
4. Enable the "Nozzle Texture Sharing" addon

The native extension is platform- and Python-ABI-specific. A package built
against one Blender-bundled Python ABI is not a generic source zip for every
Blender version/platform.

## Usage

### Sender (Blender → other apps)

1. In the 3D viewport sidebar (N key), find the **Nozzle** tab
2. Set a sender name and resolution
3. Click **Start Sender**
4. Other nozzle receivers can now connect using that name

Capture is requested at most every 50 ms after the preceding capture completes,
not at a guaranteed frame rate. If the original viewport is unavailable after a
workspace/screen/scene change, sending pauses with a visible status and resumes
when that viewport returns. Receiving continues independently of the viewport.
Stop explicitly if the original viewport will not return. Both stop buttons,
loading another file and disabling the addon release live callbacks/handles.
Fatal errors stop the affected direction and remain visible in the sidebar and in
`NOZZLE_LIVE_ERROR` console output. Sender pixel format is RGBA32F, with no
precision-reducing fallback; receiving applications must support it.

### Receiver (other apps → Blender)

1. Click **Refresh** to discover available senders
2. Select a sender and click **Start Receiver**
3. Received frames appear as `__nozzle_received__` image in Blender

The receiver may also be started before its named sender exists. It keeps
waiting until the producer appears and reconnects after the producer process
exits and restarts. Stop Receiver cancels waiting as well as active reception.
Missing/closed sender states clear when frames return; other errors remain fatal.
The live receiver requires a scene, not a 3D viewport. Same-process destruction
and recreation of a producer remains subject to core issue #184; this is not
covered by the child-process restart guarantee.

Live acquisition does not wait for a new frame (timeout=0); a timeout leaves the
previous image unchanged. GPU synchronization and pixel mapping can still block;
this is not a hard real-time/nonblocking guarantee. One scene can own a live
sender/receiver pair at a time because the output image has a shared name.
The native `receiver_acquire_frame` now returns `None` on timeout; other failures
still raise an error. Continuous conversion uses bundled NumPy without installing
packages. It maintains stride, channel order and float precision; this does not
remove native readback/copy or Blender image-update costs. Measured conversion
and full receive timings are recorded separately by the foreground CI probe.

The receive image uses a float buffer: RGBA8/BGRA8 color is normalized and
decoded from sRGB, and RGBA16F/RGBA32F retain negative/HDR values without byte quantization.
Other image layouts are rejected. Native storage channel order and padding are
handled before assigning bottom-up RGBA values to Blender.

Untagged 8-bit UNORM is assumed sRGB-encoded, as are explicitly tagged sRGB
inputs. RGB is decoded once, while alpha is only normalized and float input
remains linear. This is a receive-image policy, not a change to core format
semantics: truly linear 8-bit content cannot be detected automatically and will
be decoded under this assumption. The image keeps Blender's default float color
space from the active OCIO configuration, not Non-Color/data. Native Metal sRGB transport is not
supported by the pinned core, even though the CPU conversion understands it.

Existing byte, wrong-size or wrong-color-space images are replaced with default
float images while retaining their material/editor users; for 8-bit inputs this costs
approximately four times the image pixel-buffer memory. Received images are
live buffers and are no longer automatically packed into a file every frame.
See [receive evidence boundaries](docs/receive-pixel-peer.md) for the real-Blender
native-peer CI coverage; build/import success on another platform is not pixel
interoperability evidence.

## Architecture

```
nozzle_blender/         Blender addon (Python)
  ├── __init__.py       Registration, bl_info
  ├── operators.py      Send/Receive operators
  ├── live.py           Callback and native handle ownership
  ├── properties.py     Addon properties
  ├── panels.py         UI panels (View3D sidebar)
  └── engine.py         Texture capture & receive logic

src/
  └── _nozzle_module.c  Python C extension wrapping nozzle C ABI

nozzle/                 git submodule (nozzle library)
```

The addon uses a two-layer approach:
1. **Native C extension** (`_nozzle_native`): wraps the nozzle C ABI, manages handles
2. **Blender addon** (Python): uses `gpu.offscreen` for capture, `bpy.data.images` for display

## License

MIT

Third-party dependencies:

- [nozzle](https://github.com/nozzle-io/nozzle) — MIT
