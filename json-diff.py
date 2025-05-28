#!/usr/bin/env blender --background --python
# Usage:
# blender --background --python json-diff.py -- --fileA a.blend --fileB b.blend --out diff.json [-v]

import bpy, sys, argparse, hashlib, json, logging
import mathutils, numbers

# ---------------------------------------------------------------------------
# Configuration
SKIP_PATHS = {
    # Heavy mesh & image payloads we do NOT want to load or trigger
    ".vertices", ".edges", ".loops", ".polygons",
    ".pixels", ".tiles",
    # Runtime-only or noisy
    ".matrix_world", ".rna_type",
}

PRIMITIVE_TYPES = {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}

LOG = logging.getLogger("blendediff")

# ---------------------------------------------------------------------------
def parse_cli():
    argv = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--fileA", required=True)
    ap.add_argument("--fileB", required=True)
    ap.add_argument("--out", default="diff.json")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s"
    )
    return args

def id_key(id):
    return f"{id.__class__.__name__}:{id.name_full}"

# ---------------------------------------------------------------------------
# Safe serialisation for RNA values (prevents segfaults after switch)
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

# ---------------------------------------------------------------------------
# Walk every reachable RNA path in the datablock
def walk_rna(rna_obj, base_path=""):
    out = {}
    rna = rna_obj.bl_rna
    for prop in rna.properties:
        if prop.is_readonly or prop.identifier == "rna_type":
            continue

        full_path = f"{base_path}.{prop.identifier}" if base_path else prop.identifier
        if any(full_path.endswith(skip) for skip in SKIP_PATHS):
            continue

        try:
            val_raw = getattr(rna_obj, prop.identifier)
        except ReferenceError:
            out[full_path] = "<invalid>"
            LOG.debug("REFERROR: %s", full_path)
            continue
        except Exception as ex:
            out[full_path] = f"<error: {ex}>"
            LOG.debug("EXCEPTION: %s → %s", full_path, ex)
            continue

        # Primitives
        if prop.type in PRIMITIVE_TYPES:
            out[full_path] = serialise_value(val_raw)
            LOG.debug("    %s -> %s", full_path, type(val_raw).__name__)

        # Pointer (ID or struct)
        elif prop.type == 'POINTER':
            if val_raw is None:
                out[full_path] = None
            elif isinstance(val_raw, bpy.types.ID):
                out[full_path] = id_key(val_raw)
            else:
                out.update(walk_rna(val_raw, full_path))

        # Collection
        elif prop.is_collection:
            try:
                for i, item in enumerate(val_raw):
                    subkey = getattr(item, "name", str(i))
                    subpath = f"{full_path}[{subkey}]"
                    if isinstance(item, bpy.types.ID):
                        out[subpath] = id_key(item)
                    else:
                        out.update(walk_rna(item, subpath))
            except Exception as ex:
                out[full_path] = f"<error: {ex}>"
                LOG.debug("COLLECTION ERROR: %s → %s", full_path, ex)

        else:
            out[full_path] = serialise_value(val_raw)

    return out

# ---------------------------------------------------------------------------
def snap_datablock(id):
    props = walk_rna(id)
    hasher = hashlib.blake2s()
    for k in sorted(props):
        hasher.update(k.encode()); hasher.update(str(props[k]).encode())
    return {"type": id.__class__.__name__, "props": props, "hash": hasher.hexdigest()}

def snapshot_file(filepath):
    bpy.ops.wm.open_mainfile(filepath=filepath, load_ui=False)
    snap = {}
    for coll_name in dir(bpy.data):
        coll = getattr(bpy.data, coll_name)
        if not hasattr(coll, "__iter__"):
            continue
        for id in coll:
            if not hasattr(id, "name_full"):
                continue
            if getattr(id, "library", None):  # skip linked libraries
                continue
            snap[id_key(id)] = snap_datablock(id)
    return snap

# ---------------------------------------------------------------------------
def safe_cmp(a, b):
    try:
        return a != b
    except Exception as ex:
        LOG.debug("Compare error (%s vs %s): %s", type(a), type(b), ex)
        return str(a) != str(b)

def diff_props(pa, pb):
    diff = {}
    for k in pa.keys() | pb.keys():
        if safe_cmp(pa.get(k), pb.get(k)):
            diff[k] = {"A": pa.get(k), "B": pb.get(k)}
    return diff

# ---------------------------------------------------------------------------
def main():
    args = parse_cli()

    LOG.info("🔍 Reading A: %s", args.fileA)
    snapA = snapshot_file(args.fileA)

    LOG.info("🔍 Reading B: %s", args.fileB)
    snapB = snapshot_file(args.fileB)

    added = {k: {"type": snapB[k]["type"]} for k in snapB.keys() - snapA.keys()}
    removed = {k: {"type": snapA[k]["type"]} for k in snapA.keys() - snapB.keys()}
    changed = {}

    LOG.info("🔍 Comparing %d common IDs", len(snapA.keys() & snapB.keys()))
    for k in snapA.keys() & snapB.keys():
        if snapA[k]["hash"] != snapB[k]["hash"]:
            diff = diff_props(snapA[k]["props"], snapB[k]["props"])
            if diff:
                changed[k] = diff

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"added": added, "removed": removed, "changed": changed}, fh, indent=2)

    LOG.info("✔ Diff complete: %s", args.out)

if __name__ == "__main__":
    main()


