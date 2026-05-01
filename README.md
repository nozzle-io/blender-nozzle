# blender-nozzle

> This codebase is currently in its AI-slob prototyping phase: the code runs on momentum, vibes, and plausible intent.
> Proper debugging will be introduced once demand graduates from hypothetical to measurable.

Blender addon for GPU texture sharing via [nozzle](https://github.com/nozzle-io/nozzle).

Send Blender viewport renders or receive textures from other nozzle-compatible applications (openFrameworks, Max, etc.) in real-time.

## Status

Early development. Functional sender (GL texture publish) and receiver (pixel copy to Blender image).

## Requirements

- Blender 3.0+
- macOS 12+ / Windows 10+ / Linux
- CMake 3.20+
- C++17 compiler

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

This builds `_nozzle_native.so` (or `.pyd` on Windows) and copies it into `nozzle_blender/`.

## Installing in Blender

1. Build the native module (see above)
2. In Blender: Edit > Preferences > Add-ons > Install...
3. Select the `nozzle_blender/` directory (the folder containing `__init__.py`)
4. Enable the "Nozzle Texture Sharing" addon

## Usage

### Sender (Blender → other apps)

1. In the 3D viewport sidebar (N key), find the **Nozzle** tab
2. Set a sender name and resolution
3. Click **Start Sender**
4. Other nozzle receivers can now connect using that name

### Receiver (other apps → Blender)

1. Click **Refresh** to discover available senders
2. Select a sender and click **Start Receiver**
3. Received frames appear as `__nozzle_received__` image in Blender

## Architecture

```
nozzle_blender/         Blender addon (Python)
  ├── __init__.py       Registration, bl_info
  ├── operators.py      Send/Receive operators
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
