#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import glob

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.path.join(ROOT, "build")
BLENDER_PYTHON = os.environ.get("BLENDER_PYTHON_PATH", "")

def run(cmd, **kw):
    print(f"  $ {' '.join(cmd)}")
    subprocess.check_call(cmd, **kw)

def find_blender_python():
    if BLENDER_PYTHON:
        return BLENDER_PYTHON
    common = [
        "/Applications/Blender.app/Contents/Resources/python",
        "C:\\Program Files\\Blender Foundation\\Blender\\4.0\\python",
    ]
    for p in common:
        if os.path.isdir(p):
            return p
    return ""

def main():
    bp = find_blender_python()
    if not bp:
        print("Warning: BLENDER_PYTHON_PATH not set. Using system Python3.")

    os.makedirs(BUILD_DIR, exist_ok=True)

    if not os.path.exists(os.path.join(ROOT, "nozzle", "CMakeLists.txt")):
        print("Initializing nozzle submodule...")
        run(["git", "submodule", "update", "--init", "--recursive"], cwd=ROOT)

    print("Configuring...")
    cmake_args = ["cmake", ".."]
    if bp:
        cmake_args += [f"-DBLENDER_PYTHON_PATH={bp}"]
    cmake_args += ["-DCMAKE_OSX_DEPLOYMENT_TARGET=12.0"] if sys.platform == "darwin" else []
    run(cmake_args, cwd=BUILD_DIR)

    print("Building...")
    run(["cmake", "--build", ".", "--config", "Release"], cwd=BUILD_DIR)

    pattern = os.path.join(BUILD_DIR, "_nozzle_native*")
    matches = glob.glob(pattern)
    if not matches:
        pattern = os.path.join(BUILD_DIR, "Release", "_nozzle_native*")
        matches = glob.glob(pattern)

    for m in matches:
        dest = os.path.join(ROOT, "nozzle_blender", os.path.basename(m))
        shutil.copy2(m, dest)
        print(f"Copied {m} -> {dest}")

    print("Done.")

if __name__ == "__main__":
    main()
