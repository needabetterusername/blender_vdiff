# json-diff.py  --  run with:
# blender --background --python json-diff.py -- --fileA a.blend --fileB b.blend

import os, bpy, sys, argparse, hashlib, json

# ---------------------------------------------------------------------------
def parse_cli():
    # Blender puts your extra args *after* the first “--”
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []                         # user forgot "--", avoid crash

    ap = argparse.ArgumentParser(description="Diff two .blend files")
    ap.add_argument("--fileA", required=True, help="first blend file")
    ap.add_argument("--fileB", required=True, help="second blend file")
    ap.add_argument("--out",   default="diff.json", help="output JSON diff")
    return ap.parse_args(argv)

# ---------------------------------------------------------------------------
IGNORE_RNA = {".matrix_world"}           # expand later

def should_skip(prop):
    return (prop.is_readonly or f".{prop.identifier}" in IGNORE_RNA)

def id_key(id):
    return f"{id.__class__.__name__}:{id.name_full}"

def hash_id(id):
    h = hashlib.blake2s()                # 256-bit digest, no external deps
    for prop in id.bl_rna.properties:
        if should_skip(prop):
            continue
        val = getattr(id, prop.identifier)
        try:
            payload = json.dumps(val, default=str, sort_keys=True)
        except TypeError:
            payload = str(val)
        h.update(payload.encode("utf-8"))
    return h.hexdigest()

def snapshot():
    snap = {}
    for collection_name in dir(bpy.data):
        if collection_name.startswith("__"):  # skip dunder methods
            continue

        collection = getattr(bpy.data, collection_name)

        # skip non-collections and attributes that don't behave like lists
        if not hasattr(collection, "__iter__"):
            continue

        try:
            for id in collection:
                # Ensure this is a real ID datablock (e.g. Object, Material)
                if not hasattr(id, "name_full"):
                    continue
                if hasattr(id, "library") and id.library:
                    continue
                snap[id_key(id)] = hash_id(id)
        except TypeError:
            # Some bpy.data entries like .objects['name'] are dict-like; skip
            continue

    return snap


def load_and_snap(path):
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
    return snapshot()

# ---------------------------------------------------------------------------
def main():
    args = parse_cli()

    print(f"📁 Working dir: {os.getcwd()}")
    print(f'args: {args}')

    snapA = load_and_snap(args.fileA)
    snapB = load_and_snap(args.fileB)

    added   = {k: snapB[k] for k in snapB.keys() - snapA.keys()}
    removed = {k: snapA[k] for k in snapA.keys() - snapB.keys()}
    changed = {k: {"A": snapA[k], "B": snapB[k]}
               for k in snapA.keys() & snapB.keys()
               if snapA[k] != snapB[k]}

    if (args.out):
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"added": added, "removed": removed, "changed": changed},
                    fh, indent=2)
        print(f"✔ Diff written to {args.out}")
    else:
        print(json.dump({"added": added, "removed": removed, "changed": changed},indent=2))

if __name__ == "__main__":
    main()
