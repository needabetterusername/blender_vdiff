#!/usr/bin/env python
"""
Fetch or register Blender executables listed in `.blender-versions.yaml`.

Expected YAML schema
-----------
versions:            # mandatory
  - "3.6.15"
  - "4.2.0"
paths:               # optional - personal installs
  "3.6.15": "/Applications/Blender-3.6.app/Contents/MacOS/Blender"
  "4.2.0":  "C:/Program Files/Blender 4.2/blender.exe"
"""
from __future__ import annotations

import sys
import logging

import argparse
import pathlib
import subprocess
import yaml
import yaml.parser

# ───────────────────────────────────────────────────────── helpers

def _find_exe(root: pathlib.Path) -> pathlib.Path | None:
    """
    Locate a Blender executable under *root*:

    • macOS: **/*.app/Contents/MacOS/Blender
    • Linux: **/blender
    • Win:   **/blender.exe
    Returns first match or None.
    """
    # macOS bundle
    for p in root.glob("**/*.app/Contents/MacOS/Blender"):
        if p.is_file():
            return p

    # Linux / Windows
    for pattern in ("**/blender", "**/blender.exe", "**/Blender"):
        for p in root.glob(pattern):
            if p.is_file():
                return p
    return None


def _store_path(ver: str, exe: pathlib.Path, cache: pathlib.Path) -> None:
    """ Write a .path file which has the name being the version and the content being the path string. """
    (cache / f"{ver}.path").write_text(str(exe.resolve()))
    print(f"✔ Blender {ver} → {exe}")


def _download_blender(ver: str, target: pathlib.Path, force: bool) -> pathlib.Path:

    exe_path = None
    print(f"_download_blender: Types - ver: {type(ver)}, target: {type(target)}, force: {type(force)} ")

    if (not target.exists()) or force:
        target.mkdir(parents=True, exist_ok=True)
        # Request blender_downloader to download the specified version
        # and rely on its cache.
        print(f"↓ Downloading Blender {ver} …")
        try:
            cp = subprocess.run(
                [sys.executable, "-m", "blender_downloader",
                "-e", "-d", str(target), "-b", "-q", ver],   # version LAST
                check=True,
                capture_output=True,
                text=True,
            )
            exe_path = pathlib.Path(cp.stdout.strip())
        except subprocess.CalledProcessError as e:
            # Forward whatever blender_downloader wrote
            print("blender_downloader failed:")
            print("stdout:\n", e.stdout or "")
            print("stderr:\n", e.stderr or "")
            raise 

    else:
        exe_path = target

    return exe_path




# ─────────────────────────────────────────────────────────── main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=".cache/blender",
                    help="Directory for downloaded / registered builds")
    ap.add_argument("--force", action="store_true",
                    help="Re-download even if cached")
    args = ap.parse_args()

    root  = pathlib.Path(__file__).resolve().parents[1]
    cfg   = yaml.safe_load((root / ".blender-versions.yaml").read_text())
    # The cache is used to store path information in files. However it is also the 
    # default location for downloaded binaries.
    cache_dir = (root / args.cache).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Read from config the required versions and any local executables
    try:
        versions = cfg.get("versions", [])
    except yaml.parser.PathError as e:
        print(f"Error loading config: {e}")
        exit(1)
    local_exe_paths = {v: pathlib.Path(p) for v, p in (cfg.get("paths") or {}).items()}

    for ver in versions:
        # 1️⃣ Store and user-supplied paths
        local_path = local_exe_paths.get(ver)
        if local_path and local_path.exists():
            _store_path(ver, local_path, cache_dir)
            print(f"Will store local path ({local_path}) for version {ver}")
            continue
        else:
            print(f"Did not get a valid local path {local_path} for version {ver}")

        # 2️⃣ existing .path file
        path_file = cache_dir / f"{ver}.path"
        if path_file.exists() and not args.force:
            print(f"✔ Blender {ver} registered at {path_file.read_text().strip()}")
            continue

        # 3️⃣ download & cache
        print(f"Attempting to download Blender version {ver} to {cache_dir / ver }, with force set to {args.force}.")
        local_path = _download_blender(ver, cache_dir / ver , args.force)
        _store_path(ver, local_path, cache_dir)


if __name__ == "__main__":
    main()
