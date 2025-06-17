# blenddiff.py – grouped diff & authored‑hash utilities for .blend files
# -----------------------------------------------------------------------------------
# Public API
#   diff_blend_files(path_a, path_b, *, id_prop=None)      -> dict
#   diff_current_vs_other(path_other, *, reverse=False, id_prop=None) -> dict
#   hash_blend_file(path, *, id_prop=None)                 -> str
#   hash_current_file(*, id_prop=None)                     -> str
#
# Identity strategy (same for diff & hash):
#   1) If *id_prop* is provided and the datablock contains that custom property, use it:
#        "<Type>:prop:<value>".
#   2) If datablock is an *Object*, use its mesh‑data name and object type (stable identifier):
#        "Object:stable:<data_name>:<object_type>".  (Transforms don’t alter this.)
#   3) Otherwise, fall back to a content hash of the datablock’s serialised RNA:
#        "<Type>:hash:<digest>".  Names are treated as properties and diffed separately.
#
# CLI examples
#   # Diff two files and pretty‑print JSON to stdout
#   blender --background --python blenddiff.py -- \
#       --file-original a.blend --file-modified b.blend --pretty-json --stdout
#
#   # Diff two files using a custom GUID property and save to diff.json
#   blender --background --python blenddiff.py -- \
#       --file-original a.blend --file-modified b.blend --id-prop guid --file-out diff.json
#
#   # Generate a single authored‑hash for quick equality checks
#   blender --background --python blenddiff.py -- --hash-file a.blend --stdout
# -----------------------------------------------------------------------------------

from __future__ import annotations

import os, sys, logging, argparse, hashlib, json, numbers
from typing import Dict, Any

LOG = logging.getLogger(__name__)
logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)

USAGE = f"""This script must be run via Blender using:
  blender --background --python {os.path.basename(__file__)} -- [args]
NB: The extra '--' before [args] is mandatory. E.g. '... -- --arg1'
"""

try:
    import bpy, mathutils
except ImportError:
    print("ImportError: " + USAGE)
    sys.exit(1)

# -----------------------------------------------------------------------------
# 1. Configuration
SKIP_IDB_COLLS = {
    "batch_remove", "bl_rna", "filepath", "is_dirty", "is_saved", "orphans_purge",
    "rna_type", "temp_data", "user_map", "window_managers", "workspaces",
}

SKIP_RNA_PATHS = {
    # heavy payloads
    "vertices", "edges", "loops", "polygons",
    "pixels", "tiles",
    # runtime‑only / noisy
    "matrix_world", "rna_type",
}

PRIMITIVE_TYPES = {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}

_cache: Dict[str, Any] | None = None  # populated by diff_current_vs_other()

# -----------------------------------------------------------------------------
# 2. RNA serialisation helpers -------------------------------------------------


def _serialise(val):
    """Serialise common Blender types into JSON‑compatible primitives."""
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
# 3. Identity, snapshot & per‑block hash helpers ------------------------------


def _hash_datablock(idb: bpy.types.ID) -> Dict[str, Any]:
    props = _walk_rna(idb)
    props.setdefault("name", idb.name_full)
    h = hashlib.blake2s()
    for k in sorted(props):
        h.update(k.encode())
        h.update(str(props[k]).encode())
    return {"type": idb.__class__.__name__, "props": props, "hash": h.hexdigest()}


def _identity_key(idb: bpy.types.ID, block: Dict[str, Any], id_prop: str | None) -> str:
    if id_prop and id_prop in idb.keys():
        return f"{idb.__class__.__name__}:prop:{idb[id_prop]}"
    if isinstance(idb, bpy.types.Object):
        data_name = idb.data.name_full if idb.data else "NONE"
        return f"Object:stable:{data_name}:{idb.type}"
    return f"{idb.__class__.__name__}:hash:{block['hash']}"


def _snapshot_current(id_prop: str | None = None, *, ignore_linked=True):
    """Return a dict mapping *identity_key* ➜ block‑info for the *current* file."""
    snapshot: Dict[str, Any] = {}
    filtered_names = [n for n in dir(bpy.data)
                      if not n.startswith("__") and n not in SKIP_IDB_COLLS]
    for coll_name in filtered_names:
        collection = getattr(bpy.data, coll_name)
        if not hasattr(collection, "__iter__"):
            continue
        for idb in collection:
            if not hasattr(idb, "name_full"):
                continue
            if ignore_linked and getattr(idb, "library", None):
                continue
            block = _hash_datablock(idb)
            block.setdefault("bpy_path", coll_name)  # useful for grouping
            key = _identity_key(idb, block, id_prop)
            if key in snapshot:
                key = f"{key}:{idb.name_full}"  # fall‑back for accidental clashes
            snapshot[key] = block
    return snapshot


def _snapshot_file(path: str, id_prop: str | None = None, *, ignore_linked=True):
    """Load *path* (without UI) and snapshot it."""
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
    return _snapshot_current(id_prop, ignore_linked=ignore_linked)

# -----------------------------------------------------------------------------
# 4. FILE‑LEVEL AUTHORED HASH --------------------------------------------------


def _digest_from_snapshot(snapshot: Dict[str, Any]) -> str:
    """Collapse a snapshot into a single deterministic blake2s digest."""
    h = hashlib.blake2s()
    for key in sorted(snapshot):
        h.update(key.encode())
        h.update(snapshot[key]['hash'].encode())
    return h.hexdigest()


def hash_blend_file(path: str, *, id_prop: str | None = None) -> str:
    """Return a blake2s digest representing *authored* content of *path*."""
    snap = _snapshot_file(path, id_prop)
    return _digest_from_snapshot(snap)


def hash_current_file(*, id_prop: str | None = None) -> str:
    """Return an authored‑hash for the *currently open* file."""
    snap = _snapshot_current(id_prop)
    return _digest_from_snapshot(snap)

# -----------------------------------------------------------------------------
# 5. Diff helpers --------------------------------------------------------------


def _safe_cmp(a, b):
    try:
        return a != b
    except Exception:
        return str(a) != str(b)


def _diff_props(pa, pb):
    out: Dict[str, Any] = {}
    for k in pa.keys() | pb.keys():
        if _safe_cmp(pa.get(k), pb.get(k)):
            out[k] = {"A": pa.get(k), "B": pb.get(k)}
    return out


def _group_by_type(item_keys, src_dict, with_payload=False):
    groups: Dict[str, Dict[str, Any]] = {}
    for key in item_keys:
        block = src_dict[key]
        dtype = block.get("bpy_path", "Other")
        name = block["props"]["name"]
        groups.setdefault(dtype, {})[name] = block if with_payload else {}
    return groups


def _diff_snapshots(snap_a, snap_b):
    added_keys = snap_b.keys() - snap_a.keys()
    removed_keys = snap_a.keys() - snap_b.keys()

    # changed ---------------------------------------------------------------
    changed: Dict[str, Dict[str, Any]] = {}
    for key in snap_a.keys() & snap_b.keys():
        if snap_a[key]["hash"] == snap_b[key]["hash"]:
            continue
        delta = _diff_props(snap_a[key]["props"], snap_b[key]["props"])
        if not delta:
            continue
        dtype = snap_b[key]["bpy_path"]
        name = snap_b[key]["props"]["name"]
        changed.setdefault(dtype, {})[name] = delta

    added = _group_by_type(added_keys, snap_b)
    removed = _group_by_type(removed_keys, snap_a)

    return {"added": added, "removed": removed, "changed": changed}

# -----------------------------------------------------------------------------
# 6. Public API ---------------------------------------------------------------


def diff_blend_files(path_original: str, path_modified: str, *, id_prop: str | None = None):
    """Return diff‑dict between *path_original* and *path_modified*."""
    try:
        snap_orig = _snapshot_file(path_original, id_prop)
        snap_mod = _snapshot_file(path_modified, id_prop)
        return _diff_snapshots(snap_orig, snap_mod)
    except MemoryError:
        return {"error": "MemoryError", "stage": "snapshot"}


def diff_current_vs_other(path_other: str, *, reverse: bool = False, id_prop: str | None = None):
    """Interactive diff: current file vs *path_other* (or reverse)."""
    global _cache
    try:
        current_fp = bpy.data.filepath
        snap_current = _snapshot_current(id_prop)
        snap_other = _snapshot_file(path_other, id_prop)
        if current_fp:
            bpy.ops.wm.open_mainfile(filepath=current_fp, load_ui=False)
        _cache = (_diff_snapshots(snap_current, snap_other)
                  if not reverse else _diff_snapshots(snap_other, snap_current))
        return _cache
    except MemoryError:
        return {"error": "MemoryError", "stage": "snapshot"}


def get_diff_cache():
    return _cache

# -----------------------------------------------------------------------------
# 7. CLI entry‑point -----------------------------------------------------------


def _cli():
    if "--" not in sys.argv:
        return
    argv = sys.argv[sys.argv.index("--") + 1:]
    ap = argparse.ArgumentParser(description="BlendDiff & authored‑hash utility")

    # Operation selection ----------------------------------------------------
    ap.add_argument("--hash-file", help="Generate an authored‑hash for a single .blend file")
    ap.add_argument("--file-original", help="Original .blend file for diff")
    ap.add_argument("--file-modified", help="Modified .blend file for diff")

    # Shared options ---------------------------------------------------------
    ap.add_argument("--id-prop", help="Custom property used for stable identity")

    out_grp = ap.add_mutually_exclusive_group()
    out_grp.add_argument("--file-out", help="Save output JSON to file")
    out_grp.add_argument("--stdout", action="store_true", help="Print output to stdout")

    ap.add_argument("--pretty-json", action="store_true", help="Pretty‑print JSON (indent=2)")
    ap.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    args = ap.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # HASH mode --------------------------------------------------------------
    if args.hash_file:
        digest = hash_blend_file(args.hash_file, id_prop=args.id_prop)
        payload = {"hash": digest}
    # DIFF mode --------------------------------------------------------------
    else:
        if not (args.file_original and args.file_modified):
            ap.error("--file-original and --file-modified are required when not using --hash-file")
        payload = diff_blend_files(args.file_original, args.file_modified, id_prop=args.id_prop)

    # Serialise & output -----------------------------------------------------
    if args.pretty_json:
        payload_str = json.dumps(payload, indent=2)
    else:
        payload_str = json.dumps(payload, separators=(',', ':'))

    if args.stdout or not args.file_out:
        print(payload_str, flush=True)
    if args.file_out:
        with open(args.file_out, "w", encoding="utf-8") as fh:
            fh.write(payload_str)
        LOG.info("Output saved to %s", args.file_out)


if __name__ == "__main__":
    _cli()
