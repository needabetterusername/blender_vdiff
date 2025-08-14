#!/usr/bin/env python
"""
run_tests.py – wrapper that spins up a clean Blender env and then calls pytest
"""

from __future__ import annotations
import argparse, os, subprocess, sys, tempfile, shutil, pathlib

ROOT = pathlib.Path(__file__).resolve().parent
SRC  = ROOT / "src"

def _temp_dir() -> str:
    return tempfile.mkdtemp(prefix="bvt_")



def _prep_blender(blender_exe: str) -> None:
    # 1 – ask pytest-blender for the interpreter path
    py_path_str = subprocess.check_output(
        [sys.executable, "-m", "pytest_blender", "--blender-executable", blender_exe],
        text=True,
    ).strip()
    py_path = pathlib.Path(py_path_str)

    # 2 – bootstrap pip + pytest inside Blender’s Python
    subprocess.check_call([py_path, "-m", "ensurepip", "--upgrade"])
    subprocess.check_call([py_path, "-m", "pip", "install", "-q", "pytest", "pyyaml"])
    print(f"✔ Bundled Python ready: {py_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", help="Path to Blender executable")
    args, extra_pytest = parser.parse_known_args()

    # clean prefs/add-ons
    os.environ["BLENDER_USER_CONFIG"]  = _temp_dir()
    os.environ["BLENDER_USER_SCRIPTS"] = _temp_dir()

    cmd: list[str] = [sys.executable, "-m", "pytest", "-c", "pytest.ini"]

    if args.blender:
        _prep_blender(args.blender)
        cmd += ["--blender-executable", args.blender]
    else:
        cmd += ["-p", "no:pytest-blender"]         # disable plugin

    cmd += extra_pytest
    print("▶ Running:", " ".join(cmd))
    try:
        subprocess.check_call(cmd, cwd=ROOT)
    finally:
        shutil.rmtree(os.environ["BLENDER_USER_CONFIG"],  ignore_errors=True)
        shutil.rmtree(os.environ["BLENDER_USER_SCRIPTS"], ignore_errors=True)

if __name__ == "__main__":
    main()

