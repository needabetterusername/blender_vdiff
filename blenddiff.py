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

import os, sys
USAGE = f"""This script must be run via Blender using:
  blender --background --python {os.path.basename(__file__)} -- [args]
NB: The extra \'--\' before [args] is mandatory. E.g. \"... -- --arg1\"
"""
try:
    import bpy, mathutils
except ImportError:
    print("ImportError: " + USAGE)
    sys.exit(1)

import argparse, hashlib, json, logging, numbers
from typing import Dict, Any

LOG = logging.getLogger("blenddiff")

_cache = None

# -----------------------------------------------------------------------------
# 1. Configuration
SKIP_IDB_COLLS = {"batch_remove", "bl_rna", "filepath", "is_dirty", "is_saved", "orphans_purge", "rna_type", "temp_data", "user_map", "window_managers", "workspaces"}

SKIP_RNA_PATHS = {
    # heavy payloads
    "vertices", "edges", "loops", "polygons",
    "pixels",   "tiles",
    # runtime-only / noise
    "matrix_world", "rna_type",
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
        if any(path.endswith(s) for s in SKIP_RNA_PATHS):
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

def _hash_datablock(idb: bpy.types.ID) -> Dict[str, Any]:
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
    # Create a dict of hashes for non-excluded datablocks in the currently open file.
    snapshot: Dict[str, Any] = {}
    filtered_names = [coll_name for coll_name in dir(bpy.data) if not coll_name.startswith("__") and not coll_name in SKIP_IDB_COLLS]
    for coll_name in filtered_names:
        collection = getattr(bpy.data, coll_name)
        if not hasattr(collection, "__iter__"): # Only use iterable collections
            LOG.debug(f'Snapshot: Skipping bpy.data.{coll_name}')
            continue
        for idb in collection: # Only use named and unliked internal data blocks 
            LOG.debug(f'Snapshot: Inspecting bpy.data.{coll_name}')
            if not hasattr(idb, "name_full"):
                LOG.debug(f'Snapshot: Rejected block {idb} in bpy.data.{coll_name} with no \'name_full\' attribute.')
                continue
            if ignore_linked and getattr(idb, "library", None):
                LOG.debug(f'Snapshot: Rejected block {idb} in bpy.data.{coll_name} with \'library\' attribute.')
                continue
            LOG.debug(f'Snapshot: Including block {idb.name_full} in bpy.data.{coll_name}')
            block_hash = _hash_datablock(idb)
            block_hash.setdefault("bpy_path", coll_name)
            key = _identity_key(idb, block_hash, id_prop)
            if key in snapshot:
                key = f"{key}:{idb.name_full}"
            snapshot[key] = block_hash
    return snapshot


def _snapshot_file(path: str, id_prop: str | None = None, *, ignore_linked=True):
    # Load the target file as the current file then snapshot.
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
    return _snapshot_current(id_prop, ignore_linked=ignore_linked)

# -----------------------------------------------------------------------------
# 3. Diff helpers 

def _safe_cmp(a, b):
    try:
        return a != b
    except Exception:
        return str(a) != str(b)


def _diff_props(pa, pb):
    # Return a dict of property deltas.
    out: Dict[str, Any] = {}
    for k in pa.keys() | pb.keys():
        if _safe_cmp(pa.get(k), pb.get(k)):
            out[k] = {"A": pa.get(k), "B": pb.get(k)}
    return out


def _group_by_type(item_dict, src_dict, with_payload=False):
    #
    # Turn an iterable of identity-keys into:
    #     { "Object": { "Cube": payload_or_{} , ... } , ... }
    #
    groups: Dict[str, Dict[str, Any]] = {}
    for key in item_dict:
        block = src_dict[key]
        #type  = block["type"] #Change this to use collection name
        type = block.get("bpy_path","Other")
        name  = block["props"]["name"]
        groups.setdefault(type, {})[name] = block if with_payload else {}
    return groups


def _diff_snapshots(snap_original, snap_modified):
    # 
    # Produce a dict grouped by datablock type, e.g.
    # 
    #   "added":   { "Object": { "Cube.001": {} }, ... }
    #   "changed": { "Object": { "Cube": {<prop-diff>} } }
    #   "removed": { "Object": { "Cube": {<prop-diff>} } }
    # 
    added_keys   = snap_modified.keys() - snap_original.keys()
    removed_keys = snap_original.keys() - snap_modified.keys()

    # changed ---------------------------------------------------------------
    changed: Dict[str, Dict[str, Any]] = {}
    for key in snap_original.keys() & snap_modified.keys():
        if snap_original[key]["hash"] == snap_modified[key]["hash"]:
            continue
        delta = _diff_props(snap_original[key]["props"], snap_modified[key]["props"])
        if not delta:
            continue
        #type  = snap_b[key]["type"]
        type = snap_modified[key]["bpy_path"]
        name = snap_modified[key]["props"]["name"]
        changed.setdefault(type, {})[name] = delta

    added   = _group_by_type(added_keys,   snap_modified, with_payload=False)
    removed = _group_by_type(removed_keys, snap_original, with_payload=False)

    return {"added": added, "removed": removed, "changed": changed}


# -----------------------------------------------------------------------------
# 4. Public API

def diff_blend_files(path_original: str, path_modified: str, *, id_prop: str | None = None):
    """
    This method is intended to be run in the background from the CLI or as a subprocess.

    Run a diff against authored objects in the provided two files.
    """
    try:
        snap_orig = _snapshot_file(path_original, id_prop)
        snap_mod = _snapshot_file(path_modified, id_prop)
        return _diff_snapshots(snap_orig, snap_mod)
    except MemoryError:
        return {"error": "MemoryError", "stage": "snapshot"}


def diff_current_vs_original(path_other: str, *, reverse: bool = False, id_prop: str | None = None):
    """
    This method is intended to be run interactively from the Blender addon's UI.

    Run a diff against authored objects in the currently open modified file
    against the provided original file.

    Setting reverse=True will reverse the direction of the diff. I.e. current file
    will be treated as the original and the other file will be treated as the modified one.
    """
    try:
        _cache = None
        current_filepath = bpy.data.filepath
        snap_current = _snapshot_current(id_prop)
        snap_other   = _snapshot_file(path_other, id_prop)
        if current_filepath:
            bpy.ops.wm.open_mainfile(filepath=current_filepath, load_ui=False)
        _cache = _diff_snapshots(snap_current, snap_other) if not reverse else _diff_snapshots(snap_other, snap_current)
        return _cache
    except MemoryError:
        return {"error": "MemoryError", "stage": "snapshot"}
    
def get_diff_cache():
    return _cache

# -----------------------------------------------------------------------------
# 5. CLI entry‑point

def _cli():
    if "--" not in sys.argv:
        return
    argv = sys.argv[sys.argv.index("--") + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--file-original", required=True)
    ap.add_argument("--file-modified", required=True)
    ap.add_argument("--id-prop")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--out")
    grp.add_argument("--stdout", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s: %(message)s")

    diff = diff_blend_files(args.file_original, args.file_modified, id_prop=args.id_prop)
    payload = json.dumps(diff, indent=2)
    if args.stdout or not args.out:
        print(payload)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        LOG.info("Diff saved to %s", args.out)


if __name__ == "__main__":
    _cli()
