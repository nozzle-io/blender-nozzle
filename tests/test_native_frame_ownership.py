from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "_nozzle_module.c"


def function_body(name: str) -> str:
    text = SOURCE.read_text()
    marker = f"static PyObject *{name}("
    start = text.index(marker)
    brace = text.index("{", start)
    depth = 0
    for idx in range(brace, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1:idx]
    raise AssertionError(f"function body not found: {name}")


class NativeFrameOwnershipTests(unittest.TestCase):
    def test_frame_handle_table_has_take_not_free_cleanup_semantics(self) -> None:
        text = SOURCE.read_text()
        self.assertIn("static NozzleFrame *take_frame_handle(int h)", text)
        self.assertNotIn("static void free_frame_handle", text)
        self.assertRegex(
            text,
            r"NozzleFrame \*frame = g_handles\.frames\[h\];\s*"
            r"g_handles\.frames\[h\] = NULL;\s*"
            r"return frame;",
        )

    def test_sender_acquire_handle_table_failure_discards_without_publishing(self) -> None:
        body = function_body("py_sender_acquire_writable_frame")
        failure_block = re.search(
            r"if \(fh < 0\) \{(?P<body>.*?)\n    \}", body, re.S
        )
        self.assertIsNotNone(failure_block)
        block = failure_block.group("body")
        self.assertIn("nozzle_sender_discard_frame", block)
        self.assertIn("nozzle_frame_release(frame)", block)
        self.assertNotIn("nozzle_sender_commit_frame", block)

    def test_sender_commit_consumes_handle_and_releases_wrapper(self) -> None:
        body = function_body("py_sender_commit_frame")
        self.assertIn("NozzleFrame *frame = take_frame_handle(frame_handle);", body)
        self.assertIn("nozzle_sender_commit_frame", body)
        self.assertIn("nozzle_frame_release(frame);", body)
        self.assertNotIn("g_handles.frames[frame_handle]", body)

    def test_receiver_release_consumes_handle_and_releases_wrapper(self) -> None:
        body = function_body("py_receiver_release_frame")
        self.assertIn("NozzleFrame *frame = take_frame_handle(frame_handle);", body)
        self.assertIn("nozzle_frame_release(frame);", body)
        self.assertNotIn("g_handles.frames[frame_handle]", body)


if __name__ == "__main__":
    unittest.main()
