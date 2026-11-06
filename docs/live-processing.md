# Live Processing Evidence

The foreground probe runs real operators from the installed ZIP in Blender
4.3.2 on macOS Metal. Its baseline run at head
`a3776ba4a70abfc100f707dffe6c107bf29823e3` reproduced the sender Timer/IntProperty
failure and missing automatic receive, while manual native receive passed:
https://github.com/nozzle-io/blender-nozzle/actions/runs/42185667847/job/127785281110

The repaired probe requires:

- Two frames from one independent native producer to update the same Blender
  image through the live callback, without manually driving the receive engine.
- Multiple viewport captures, finite nonconstant received pixels, and changed
  received content when the real scene cube is hidden.
- Stop, a subsequent event-loop interval without image changes, restart, and
  addon disable with an allocated offscreen. Callback/session ownership and
  native handle invalidation are checked separately.
- Exact synthetic RGBA32F low-mantissa/HDR/negative values and bottom-left rows
  through the native publish function, observed by independent C API code.
- The existing receive pixel matrix, including half/float precision, byte sRGB
  policy, image reuse, padding, orientation and rejection preserving old pixels.
- A separate foreground lifecycle probe starts a receiver before a child-process
  producer exists, cancels pending reception, receives after a late start, and
  reconnects to a replacement producer PID without replacing the receiver handle.
- Actual workspace switching preserves reception. Sending either progresses or
  reports its pause; returning to the original viewport resumes capture. A
  receiver also starts with no VIEW_3D areas in the current screen.

The lifecycle cases failed against the unmodified implementation at
`16929118ad935d63319a98489c8e76182e0b76c2` (11 assertions, exit 1):
https://github.com/nozzle-io/blender-nozzle/actions/runs/42193344468/job/127810441240
This is negative evidence, not a claim that the subsequent repair passes.

## Development Release Note

The repaired viewport sender publishes scene-linear RGBA32F instead of its former
RGBA8 declaration. Receiving applications must support float32. The old live
sender failed before producing frames, but the format change is still explicit;
there is no precision-reducing fallback. This change is not an official release.

The viewport test is not an exact render-color/orientation/HDR oracle. The
synthetic native-publisher check bypasses viewport rendering/readback. Window
loss, file-load cleanup and exceptional cleanup failures are source-reviewed
paths, not established by the ordinary stop/restart/disable run. No Windows or
Linux live viewport proof is claimed from their package-import checks.

Timing reports distinguish conversion-only, native acquire/map/copy/conversion/
Blender image update (excluding discovery), and viewport capture/readback/publish.
Three 1920x1080 samples are recorded without a hardware-dependent pass threshold.
These measurements are not sustained frame-rate guarantees. Timeout zero avoids
waiting for a new frame but core GPU synchronization and pixel mapping may block.

Reference implementation for the viewport draw callback:
https://github.com/blender/blender/blob/v4.3.2/doc/python_api/examples/gpu.9.py
