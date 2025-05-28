#!/usr/bin/env blender --background --python
"""
BlendDiff – generic, zero-install .blend comparator
---------------------------------------------------

CLI examples
============

1) Write diff to file:
   blender --background -m blenddiff -- --fileA a.blend --fileB b.blend --out diff.json

2) Stream diff JSON to stdout (for a parent process to capture):
   blender --background -m blenddiff -- --fileA a.blend --fileB b.blend --stdout

Import examples
===============

from blenddiff import diff_blend_files, diff_current_vs_file
diff = diff_blend_files("/path/old.blend", "/path/new.blend")

# inside Blender, compare current unsaved state with a reference file
diff2 = diff_current_vs_file("/path/new.blend")
"""

import bpy, sys, argparse, hashlib, json, logging, mathutils, numbers
from typing import Dict, Any

# -----------------------------------------------------------------------------
# Config
SKIP_PATHS = {
    ".vertices", ".edges", ".loops", ".polygons",
    ".pixels", ".tiles",
    ".matrix_world", ".rna_type",
}
PRIMITIVE_TYPES = {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}
LOG = logging.getLogger("blenddiff")


# -----------------------------------------------------------------------------
# Utility helpers
def id_key(idb: bpy.types.ID) -> str:
    return f"{idb.__class__.__name__}:{idb.name_full}"


def serialise_value(val) -> Any:
    """Convert RNA values to pure-Python primitives (no live mathutils)."""
    if isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, (mathutils.Vector, mathutils.Color,
                        mathutils.Euler, mathutils.Quaternion)):
        return [float(x) for x in val]
    if isinstance(val, mathutils.Matrix):
        return [[float(c) for c in row] for row in val]
    if isinstance(val, (list, tuple)) and all(isinstance(x, numbers.Number) for x in val):
        return list(val)
    return str(val)


def walk_rna(rna_obj, base_path="") -> Dict[str, Any]:
    """Recursively capture all relevant properties of `rna_obj`."""
    out = {}
    for prop in rna_obj.bl_rna.properties:
        if prop.is_readonly or prop.identifier == "rna_type":
            continue

        path = f"{base_path}.{prop.identifier}" if base_path else prop.identifier
        if any(path.endswith(skip) for skip in SKIP_PATHS):
            continue

        try:
            raw = getattr(rna_obj, prop.identifier)
        except Exception as ex:
            out[path] = f"<error: {ex}>"
            continue

        # primitives ---------------------------------------------------------
        if prop.type in PRIMITIVE_TYPES:
            out[path] = serialise_value(raw)
        # pointer ------------------------------------------------------------
        elif prop.type == 'POINTER':
            if raw is None:
                out[path] = None
            elif isinstance(raw, bpy.types.ID):
                out[path] = id_key(raw)
            else:
                out.update(walk_rna(raw, path))
        # collection ---------------------------------------------------------
        elif prop.is_collection:
            try:
                for idx, item in enumerate(raw):
                    subkey = getattr(item, "name", str(idx))
                    subpath = f"{path}[{subkey}]"
                    if isinstance(item, bpy.types.ID):
                        out[subpath] = id_key(item)
                    else:
                        out.update(walk_rna(item, subpath))
            except Exception as ex:
                out[path] = f"<error: {ex}>"
        else:
            out[path] = serialise_value(raw)
    return out


def snap_datablock(idb) -> Dict[str, Any]:
    props = walk_rna(idb)
    h = hashlib.blake2s()
    for k in sorted(props):
        h.update(k.encode()); h.update(str(props[k]).encode())
    return {"type": idb.__class__.__name__, "props": props, "hash": h.hexdigest()}


def snapshot_current_scene(ignore_linked=True) -> Dict[str, Any]:
    """Snapshot of *currently loaded* .blend."""
    snap = {}
    for collname in dir(bpy.data):
        coll = getattr(bpy.data, collname)
        if not hasattr(coll, "__iter__"):
            continue
        for idb in coll:
            if not hasattr(idb, "name_full"):
                continue
            if ignore_linked and getattr(idb, "library", None):
                continue
            snap[id_key(idb)] = snap_datablock(idb)
    return snap


def snapshot_file(filepath: str, ignore_linked=True) -> Dict[str, Any]:
    """Load file, snapshot, **return dict** – caller must ensure no side effects."""
    bpy.ops.wm.open_mainfile(filepath=filepath, load_ui=False)
    return snapshot_current_scene(ignore_linked)


def safe_cmp(a, b) -> bool:
    try:
        return a != b
    except Exception:
        return str(a) != str(b)


def diff_snapshots(snapA: Dict[str, Any], snapB: Dict[str, Any]) -> Dict[str, Any]:
    added = {k: {"type": snapB[k]["type"]} for k in snapB.keys() - snapA.keys()}
    removed = {k: {"type": snapA[k]["type"]} for k in snapA.keys() - snapB.keys()}
    changed = {}

    for k in snapA.keys() & snapB.keys():
        if snapA[k]["hash"] == snapB[k]["hash"]:
            continue
        diff = {}
        pa, pb = snapA[k]["props"], snapB[k]["props"]
        for p in pa.keys() | pb.keys():
            if safe_cmp(pa.get(p), pb.get(p)):
                diff[p] = {"A": pa.get(p), "B": pb.get(p)}
        if diff:
            changed[k] = diff
    return {"added": added, "removed": removed, "changed": changed}


# -----------------------------------------------------------------------------
# Public API
def diff_blend_files(path_a: str, path_b: str, ignore_linked=True) -> Dict[str, Any]:
    """
    Compare two .blend files in a **single Blender process**.

    Returns a dict that you can serialise to JSON or analyse further.
    """
    try:
        snapA = snapshot_file(path_a, ignore_linked)
        snapB = snapshot_file(path_b, ignore_linked)
        return diff_snapshots(snapA, snapB)
    except MemoryError:
        return {"error": "MemoryError", "stage": "snapshot"}


def diff_current_vs_file(path_other: str, ignore_linked=True) -> Dict[str, Any]:
    """
    Compare *currently open scene* with another .blend on disk.

    Useful from within an add-on when the user hasn’t saved yet.
    """
    try:
        snap_current = snapshot_current_scene(ignore_linked)
        snap_other   = snapshot_file(path_other, ignore_linked)
        return diff_snapshots(snap_current, snap_other)
    except MemoryError:
        return {"error": "MemoryError", "stage": "snapshot"}


# -----------------------------------------------------------------------------
# CLI front-end
def _parse_cli():
    argv = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="blenddiff")
    ap.add_argument("--fileA", required=True)
    ap.add_argument("--fileB", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--out", help="write diff JSON to file")
    g.add_argument("--stdout", action="store_true", help="print diff JSON to stdout")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap.parse_args(argv)


def _configure_logging(verbose: bool):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s"
    )


def _main_cli():
    args = _parse_cli()
    _configure_logging(args.verbose)
    LOG.info("Comparing %s ↔ %s", args.fileA, args.fileB)

    diff = diff_blend_files(args.fileA, args.fileB)

    payload = json.dumps(diff, indent=2)
    if args.stdout or not args.out:
        print(payload)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        LOG.info("Diff saved to %s", args.out)


# -----------------------------------------------------------------------------
# Entry-point when executed via “-m blenddiff”
if __name__ == "__main__":
    _main_cli()
