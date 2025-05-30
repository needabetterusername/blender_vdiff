# blenddiff.py – zero-install .blend comparer (stable identity for Objects, hash-based for others)
# -----------------------------------------------------------------------------------
# Public API
#   diff_blend_files(path_a, path_b, *, id_prop=None)      -> dict
#   diff_current_vs_file(path_other, *, id_prop=None)      -> dict
#
# Identity strategy:
#   1) If id_prop is provided and datablock has that custom property, use it:
#        "Type:prop:<value>".
#   2) If datablock is an Object, use its mesh-data name and object type:
#        "Object:stable:<data_name>:<object_type>".  (Transforms don’t alter this.)
#   3) Otherwise, fall back to content hash:
#        "Type:hash:<digest>".  Names are diffed as a property change.
#
# CLI examples
#   blender --background -m blenddiff -- \
#       --fileA a.blend --fileB b.blend --out diff.json
#   blender --background -m blenddiff -- \
#       --fileA a.blend --fileB b.blend --idprop guid --stdout
# -----------------------------------------------------------------------------------

from __future__ import annotations
import bpy, sys, argparse, hashlib, json, logging, mathutils, numbers
from typing import Dict, Any

LOG = logging.getLogger("blenddiff")

# -----------------------------------------------------------------------------
# 1. Configuration
SKIP_PATHS = {
    # heavy payloads
    ".vertices", ".edges", ".loops", ".polygons",
    ".pixels",   ".tiles",
    # runtime-only / noise
    ".matrix_world", ".rna_type",
}
PRIMITIVE_TYPES = {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}

# -----------------------------------------------------------------------------
# 2. RNA serialisation helpers

def serialise_value(val):
    if isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, (mathutils.Vector, mathutils.Color, mathutils.Euler, mathutils.Quaternion)):
        return [float(x) for x in val]
    if isinstance(val, mathutils.Matrix):
        return [[float(c) for c in row] for row in val]
    if isinstance(val, (list, tuple)) and all(isinstance(x, numbers.Number) for x in val):
        return list(val)
    return str(val)


def walk_rna(rna_obj, base_path="") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
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
        if prop.type in PRIMITIVE_TYPES:
            out[path] = serialise_value(raw)
        elif prop.type == 'POINTER':
            if raw is None:
                out[path] = None
            elif isinstance(raw, bpy.types.ID):
                out[path] = f"{raw.__class__.__name__}:{raw.name_full}"
            else:
                out.update(walk_rna(raw, path))
        elif prop.is_collection:
            try:
                for idx, item in enumerate(raw):
                    subkey = getattr(item, "name", str(idx))
                    subpath = f"{path}[{subkey}]"
                    if isinstance(item, bpy.types.ID):
                        out[subpath] = f"{item.__class__.__name__}:{item.name_full}"
                    else:
                        out.update(walk_rna(item, subpath))
            except Exception as ex:
                out[path] = f"<error: {ex}>"
        else:
            out[path] = serialise_value(raw)
    return out

# -----------------------------------------------------------------------------
# 3. Identity & snapshot helpers

def make_identity_key(idb: bpy.types.ID, snap_block: Dict[str, Any], id_prop: str | None) -> str:
    """Return a stable key for this datablock, using chosen strategy."""
    # 1) explicit custom property
    if id_prop and id_prop in idb.keys():
        return f"{idb.__class__.__name__}:prop:{idb[id_prop]}"
    # 2) object by data name & type (transform‑proof)
    if isinstance(idb, bpy.types.Object):
        data_name = idb.data.name_full if idb.data else "NONE"
        return f"Object:stable:{data_name}:{idb.type}"
    # 3) fallback to content hash
    return f"{idb.__class__.__name__}:hash:{snap_block['hash']}"


def snap_datablock(idb: bpy.types.ID) -> Dict[str, Any]:
    """Return a dict with props + hash; **does not** attach attributes to the ID."""
    props = walk_rna(idb)
    props.setdefault("name", idb.name_full)
    hasher = hashlib.blake2s()
    for k in sorted(props):
        hasher.update(k.encode()); hasher.update(str(props[k]).encode())
    return {"type": idb.__class__.__name__, "props": props, "hash": hasher.hexdigest()}


def snapshot_current(id_prop: str | None = None, *, ignore_linked=True) -> Dict[str, Any]:
    snap: Dict[str, Any] = {}
    for coll_name in dir(bpy.data):
        coll = getattr(bpy.data, coll_name)
        if not hasattr(coll, "__iter__"):
            continue
        for idb in coll:
            if not hasattr(idb, "name_full"):
                continue
            if ignore_linked and getattr(idb, "library", None):
                continue
            block = snap_datablock(idb)
            key   = make_identity_key(idb, block, id_prop)
            # disambiguate accidental duplicates
            if key in snap:
                key = f"{key}:{idb.name_full}"
            snap[key] = block
    return snap


def snapshot_file(path: str, id_prop: str | None = None, *, ignore_linked=True):
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
    return snapshot_current(id_prop, ignore_linked=ignore_linked)

# -----------------------------------------------------------------------------
# 4. Diff helpers(id_prop: str | None = None, *, ignore_linked=True) -> Dict[str, Any]:
    snap: Dict[str, Any] = {}
    for coll_name in dir(bpy.data):
        coll = getattr(bpy.data, coll_name)
        if not hasattr(coll, "__iter__"):
            continue
        for idb in coll:
            if not hasattr(idb, "name_full"):
                continue
            if ignore_linked and getattr(idb, "library", None):
                continue
            snap_block = snap_datablock(idb)
            key = identity_key(idb, id_prop)
            # disambiguate duplicates
            if key in snap:
                key = f"{key}:{idb.name_full}"
            snap[key] = snap_block
    return snap


def snapshot_file(path: str, id_prop: str | None = None, *, ignore_linked=True):
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
    return snapshot_current(id_prop, ignore_linked=ignore_linked)

# -----------------------------------------------------------------------------
# 4. Diff helpers

def safe_cmp(a, b):
    try: return a != b
    except: return str(a) != str(b)


def diff_props(props_a, props_b):
    diff: Dict[str, Any] = {}
    for k in props_a.keys() | props_b.keys():
        if safe_cmp(props_a.get(k), props_b.get(k)):
            diff[k] = {"A": props_a.get(k), "B": props_b.get(k)}
    return diff


def diff_snapshots(snap_a, snap_b):
    added   = {k: snap_b[k] for k in snap_b.keys() - snap_a.keys()}
    removed = {k: snap_a[k] for k in snap_a.keys() - snap_b.keys()}
    changed: Dict[str, Any] = {}
    for k in snap_a.keys() & snap_b.keys():
        if snap_a[k]["hash"] == snap_b[k]["hash"]:
            continue
        pd = diff_props(snap_a[k]["props"], snap_b[k]["props"])
        if pd: changed[k] = pd
    return {"added": added, "removed": removed, "changed": changed}

# -----------------------------------------------------------------------------
# 5. Public API

def diff_blend_files(path_a: str, path_b: str, *, id_prop: str | None = None) -> Dict[str, Any]:
    try:
        snap_a = snapshot_file(path_a, id_prop)
        snap_b = snapshot_file(path_b, id_prop)
        return diff_snapshots(snap_a, snap_b)
    except MemoryError:
        return {"error": "MemoryError", "stage": "snapshot"}

def diff_current_vs_file(path_other: str, *, id_prop: str | None = None) -> Dict[str, Any]:
    try:
        orig = bpy.data.filepath
        snap_cur = snapshot_current(id_prop)
        snap_new = snapshot_file(path_other, id_prop)
        if orig: bpy.ops.wm.open_mainfile(filepath=orig, load_ui=False)
        return diff_snapshots(snap_cur, snap_new)
    except MemoryError:
        return {"error": "MemoryError", "stage": "snapshot"}

# -----------------------------------------------------------------------------
# 6. CLI entry-point

def _parse_cli():
    argv = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="blenddiff")
    ap.add_argument("--fileA", required=True)
    ap.add_argument("--fileB", required=True)
    ap.add_argument("--idprop", help="custom property for identity")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--out", help="write diff JSON to file")
    grp.add_argument("--stdout", action="store_true")
    ap.add_argument("-v","--verbose",action="store_true")
    return ap.parse_args(argv)

def _config_logging(v): logging.basicConfig(level=logging.DEBUG if v else logging.INFO, format="%(levelname)s: %(message)s")

def _main_cli():
    args = _parse_cli(); _config_logging(args.verbose)
    diff = diff_blend_files(args.fileA, args.fileB, id_prop=args.idprop)
    out = json.dumps(diff, indent=2)
    if args.stdout or not args.out: print(out)
    if args.out:
        open(args.out,'w',encoding='utf-8').write(out)
        LOG.info("Diff saved to %s",args.out)

if __name__ == "__main__": _main_cli()
