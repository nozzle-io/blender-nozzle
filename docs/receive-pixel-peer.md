# Blender receive pixel evidence

The macOS Blender package CI job runs `tests/blender_receive_peer.py` inside
Blender 4.3.2, using the packaged production addon. A separate test-only native
library publishes through the C API and writes actual Metal storage lanes. It
does not use the Blender sender or the Blender pixel conversion.

The oracle reads actual `bpy.data.images` pixels after the production
`NozzleEngine.receive_to_image` call. It compares all pixels, including alpha,
bottom-up image row placement, negative/HDR float values and low mantissa bits.
Widths 65 and 641 exercise native padding; width 64 isolates numeric range from
row padding. The peer writes through top-left and bottom-left views. This tests
both producer mapping origins, not two distinct receiver storage origins.

Setup failures, missing Metal, exceptions and pixel mismatches fail CI, with no
SKIP or swallowed exit status. `--python-exit-code 1` makes Blender propagate
script exceptions. These are same-process native-peer CPU image tests, not
viewport screenshots, cross-process or GL sender tests. Windows/Linux package
jobs remain build/import evidence, not equivalent pixel evidence. Test peer
libraries and scripts are not part of addon zip contents.

Blender's `Image.pixels` accepts floats, not raw byte components. Primary source:
https://github.com/blender/blender/blob/v4.3.2/source/blender/makesrna/intern/rna_image.cc#L656
