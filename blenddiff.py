# blenddiff.py – grouped diff by datablock type
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

def _serialise(val):
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


def _walk_rna(rna_obj, base="") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for prop in rna_obj.bl_rna.properties:
        if prop.is_readonly or prop.identifier == "rna_type":
            continue
        path = f"{base}.{prop.identifier}" if base else prop.identifier
        if any(path.endswith(s) for s in SKIP_PATHS):
            continue
        try:
            raw = getattr(rna_obj, prop.identifier)
        except Exception as ex:
            out[path] = f"<error:{ex}>"
            continue

        if prop.type in PRIMITIVE_TYPES:
            out[path] = _serialise(raw)
        elif prop.type == 'POINTER':
            if raw is None:
                out[path] = None
            elif isinstance(raw, bpy.types.ID):
                out[path] = f"{raw.__class__.__name__}:{raw.name_full}"
            else:
                out.update(_walk_rna(raw, path))
        elif prop.is_collection:
            try:
                for idx, item in enumerate(raw):
                    subkey = getattr(item, "name", str(idx))
                    subpath = f"{path}[{subkey}]"
                    if isinstance(item, bpy.types.ID):
                        out[subpath] = f"{item.__class__.__name__}:{item.name_full}"
                    else:
                        out.update(_walk_rna(item, subpath))
            except Exception as ex:
                out[path] = f"<error:{ex}>"
        else:
            out[path] = _serialise(raw)
    return out

# -----------------------------------------------------------------------------
# 3. Identity & snapshot helpers

def _snap_datablock(idb: bpy.types.ID) -> Dict[str, Any]:
    props = _walk_rna(idb)
    props.setdefault("name", idb.name_full)
    h = hashlib.blake2s()
    for k in sorted(props):
        h.update(k.encode()); h.update(str(props[k]).encode())
    return {"type": idb.__class__.__name__, "props": props, "hash": h.hexdigest()}


def _identity_key(idb: bpy.types.ID, block: Dict[str, Any], id_prop: str | None) -> str:
    if id_prop and id_prop in idb.keys():
        return f"{idb.__class__.__name__}:prop:{idb[id_prop]}"
    if isinstance(idb, bpy.types.Object):
        data_name = idb.data.name_full if idb.data else "NONE"
        return f"Object:stable:{data_name}:{idb.type}"
    return f"{idb.__class__.__name__}:hash:{block['hash']}"


def _snapshot_current(id_prop: str | None = None, *, ignore_linked=True):
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
            block = _snap_datablock(idb)
            key = _identity_key(idb, block, id_prop)
            if key in snap:
                key = f"{key}:{idb.name_full}"
            snap[key] = block
    return snap


def _snapshot_file(path: str, id_prop: str | None = None, *, ignore_linked=True):
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
    return _snapshot_current(id_prop, ignore_linked=ignore_linked)

# -----------------------------------------------------------------------------
# 3. Diff helpers

def _safe_cmp(a, b):
    try:
        return a != b
    except Exception:
        return str(a) != str(b)


def _diff_props(a, b):
    diff: Dict[str, Any] = {}
    for k in a.keys() | b.keys():
        if _safe_cmp(a.get(k), b.get(k)):
            diff[k] = {"A": a.get(k), "B": b.get(k)}
    return diff


def _group_by_type(entries: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for key, block in entries.items():
        t = block["type"]
        grouped.setdefault(t, {})[key] = block
    return grouped


def _diff_snap(a, b):
    added_raw   = {k: b[k] for k in b.keys() - a.keys()}
    removed_raw = {k: a[k] for k in a.keys() - b.keys()}
    changed_raw: Dict[str, Any] = {}

    for k in a.keys() & b.keys():
        if a[k]["hash"] == b[k]["hash"]:
            continue
        pd = _diff_props(a[k]["props"], b[k]["props"])
        if pd:
            changed_raw[k] = {"type": a[k]["type"], "props": pd}

    return {
        "added":   _group_by_type(added_raw),
        "removed": _group_by_type(removed_raw),
        "changed": _group_by_type(changed_raw),
    }

# -----------------------------------------------------------------------------
# 4. Public API

def diff_blend_files(path_a: str, path_b: str, *, id_prop: str | None = None):
    try:
        sA = _snapshot_file(path_a, id_prop)
        sB = _snapshot_file(path_b, id_prop)
        return _diff_snap(sA, sB)
    except MemoryError:
        return {"error": "MemoryError", "stage": "snapshot"}


def diff_current_vs_file(path: str, *, id_prop: str | None = None):
    try:
        orig = bpy.data.filepath
        sCur = _snapshot_current(id_prop)
        sNew = _snapshot_file(path, id_prop)
        if orig:
            bpy.ops.wm.open_mainfile(filepath=orig, load_ui=False)
        return _diff_snap(sCur, sNew)
    except MemoryError:
        return {"error": "MemoryError", "stage": "snapshot"}

# -----------------------------------------------------------------------------
# 5. CLI entry‑point

def _cli():
    if "--" not in sys.argv:
        return
    argv = sys.argv[sys.argv.index("--") + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--fileA", required=True)
    ap.add_argument("--fileB", required=True)
    ap.add_argument("--idprop")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--out")
    grp.add_argument("--stdout", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s: %(message)s")

    diff = diff_blend_files(args.fileA, args.fileB, id_prop=args.idprop)
    payload = json.dumps(diff, indent=2)
    if args.stdout or not args.out:
        print(payload)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        LOG.info("Diff saved to %s", args.out)


if __name__ == "__main__":
    _cli()
