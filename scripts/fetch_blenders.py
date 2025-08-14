#!/usr/bin/env python
"""
fetch_blenders.py - download / register Blender versions listed in
`.blender-versions.yaml`.

Rules added in this revision
────────────────────────────
• A *.path* file is created **only after** the path has been verified:
  - For user-supplied paths we make sure it is a file (or a directory
    that actually contains an executable) before writing.
  - For downloaded builds we make sure the download finished and the
    executable can be located.
"""

from __future__ import annotations
import argparse, logging, pathlib, subprocess, sys, yaml

# ───────────────────────────── helpers

def _find_exe(root: pathlib.Path) -> pathlib.Path | None:
    """Return first Blender executable below *root* or None."""
    # macOS bundles
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
    """Write `<ver>.path` file **after** *exe* existence is confirmed."""
    if not exe.is_file():
        raise RuntimeError(f"{exe} is not a file - refusing to write .path")
    (cache / f"{ver}.path").write_text(str(exe.resolve()))
    print(f"✔ Blender {ver} → {exe}")


def _download_blender(ver: str, target_dir: pathlib.Path, force: bool) -> pathlib.Path:
    """
    Ensure *ver* is available under *target_dir* and return the path to
    the executable.  Raises on failure.
    """
    if force or not target_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)
        print(f"↓ Downloading Blender {ver} …")
        try:
            cp = subprocess.run(
                [sys.executable, "-m", "blender_downloader",
                 "-e", "-d", str(target_dir), "-b", "-q", ver],
                check=True,
                text=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            print("blender_downloader failed:")
            print("stdout:\n", e.stdout or "")
            print("stderr:\n", e.stderr or "")
            raise

        exe = pathlib.Path(cp.stdout.strip())
        if not exe.is_file():
            raise RuntimeError(f"Downloader returned non-file path: {exe}")
        return exe

    # Folder already exists → locate the exe inside it
    exe = _find_exe(target_dir)
    if exe is None:
        raise RuntimeError(
            f"Cached build for Blender {ver} in {target_dir} has no executable "
            "(delete the folder or use --force to redownload)."
        )
    return exe


# ───────────────────────────── main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=".cache/blender",
                    help="Directory for downloaded / registered builds")
    ap.add_argument("--force", action="store_true",
                    help="Re-download even if cached")
    args = ap.parse_args()

    root       = pathlib.Path(__file__).resolve().parents[1]
    cfg        = yaml.safe_load((root / ".blender-versions.yaml").read_text()) or {}
    cache_dir  = (root / args.cache).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    versions         = cfg.get("versions", [])
    local_exe_paths  = {v: pathlib.Path(p) for v, p in (cfg.get("paths") or {}).items()}

    for ver in versions:
        # 1️⃣ user-supplied path
        local_path = local_exe_paths.get(ver)
        if local_path:
            # Accept either the exe itself or a directory containing one
            exe = local_path if local_path.is_file() else _find_exe(local_path)
            if exe and exe.is_file():
                _store_path(ver, exe, cache_dir)
                continue
            print(f"⚠ Provided path for {ver} is invalid: {local_path}")

        # 2️⃣ pre-existing .path file (skip if --force)
        path_file = cache_dir / f"{ver}.path"
        if path_file.exists() and not args.force:
            exe = pathlib.Path(path_file.read_text().strip())
            if exe.is_file():        # still valid
                print(f"✔ Blender {ver} registered at {exe}")
                continue
            else:                    # stale → fall through to redownload
                print(f"Stale .path for {ver}, will redownload.")

        # 3️⃣ download & cache
        exe = _download_blender(ver, cache_dir / ver, args.force)
        _store_path(ver, exe, cache_dir)


if __name__ == "__main__":
    main()
