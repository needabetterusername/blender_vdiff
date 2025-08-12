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
# -----------------------------------------------------------------------------------
# CLI usage – **two launch modes**
#
# A) *Directly from Blender* (no Python wrapper):
#
#     blender --background --python blenddiff.py -- \
#         --file-original a.blend --file-modified b.blend --pretty-json --stdout
#
#     blender --background --python blenddiff.py -- \
#         --hash --hash-file a.blend --stdout
#
# B) *Wrapper convenience* (run this script with normal CPython and let it spawn Blender):
#
#     python blenddiff.py --blender-exec /path/to/Blender -- \
#         --file-original a.blend --file-modified b.blend --stdout
#
#     python blenddiff.py --blender-exec /path/to/Blender -- \
#         --hash --hash-file a.blend --stdout
#
#   The "--" separator is **mandatory** before the blenddiff‑specific arguments in *either* mode.
# -----------------------------------------------------------------------------------

from __future__ import annotations

import os, sys, logging, argparse, subprocess
import hashlib, json, numbers, inspect
from typing import Dict, Any

LOG = logging.getLogger(__name__)
logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)

USAGE = f"""This script must be run via Blender using:
  blender --background --python {os.path.basename(__file__)} -- [args]
NB: The extra '--' before [args] is mandatory. E.g. '... -- --arg1'
"""

# See if we are in Blender runtime
try:
    import bpy, mathutils
    _BLENDER = True
except ImportError:
    _BLENDER = False
    # We still want to allow use via import, so we don't exit here.
    # Error will only occur if the specific method is called.


class BlendDiff():

    def __init__(self):
        self._cache: Dict[str, Any] | None = None  # populated by diff_current_vs_other()
        self._custom_policy: Dict[str, Any] | None = None


    # -----------------------------------------------------------------------------
    # 1. Configuration Policy -----------------------------------------------------
    # NOTE(!): Keep these alphabetically-ordered sets for consistent hashing

    PRIMITIVE_TYPES = {"BOOLEAN", "ENUM", "FLOAT", "INT", "STRING", }
    SKIP_IDB_COLLS = {
        "batch_remove",
        "bl_rna", 
        "filepath", 
        "is_dirty", 
        "is_saved", 
        "orphans_purge",
        "rna_type", 
        "temp_data", 
        "user_map", 
        "window_managers", 
        "workspaces",
    }
    SKIP_RNA_PATHS = { 
        # heavy payloads
        "edges",
        "loops", 
        "matrix_world", # runtime‑only / noisy
        "pixels", 
        "polygons",
        "rna_type", # runtime‑only / noisy 
        "tiles", 
        "vertices",  
    }

    def _get_policy_metadata_json(self) -> Dict[str, Any]:
        """Return a dict with the policy metadata, including a hash of the policy lists."""
        policy = {
            "primitive_types": sorted(self.PRIMITIVE_TYPES),
            "skip_idb_collections": sorted(self.SKIP_IDB_COLLS),
            "skip_rna_paths": sorted(self.SKIP_RNA_PATHS),
        }

        # Create a deterministic hash of the policy lists
        policy_str = json.dumps(policy, sort_keys=True, separators=(',', ':'))
        policy["policy_hash"] = hashlib.blake2s(policy_str.encode()).hexdigest()

        if self._custom_policy is not None:
            return policy
        else:
            return {"policy_hash": policy["policy_hash"]}
        
    def _get_codebase_hash(self) -> Dict[str, Any]:
        src = inspect.getsource(self.__class__)
        hash = hashlib.blake2s(src.encode('utf-8')).hexdigest()
        return {
            #"class_name": self.__class__.__name__,
            "codebase_hash": hash,
        }

    # -----------------------------------------------------------------------------
    # 2. RNA serialisation helpers ------------------------------------------------
    @classmethod
    def _serialise(cls, val):
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

    @classmethod
    def _walk_rna(cls, rna_obj, base="") -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for prop in rna_obj.bl_rna.properties:
            if prop.is_readonly or prop.identifier == "rna_type":
                continue
            path = f"{base}.{prop.identifier}" if base else prop.identifier
            if any(path.endswith(s) for s in cls.SKIP_RNA_PATHS):
                continue
            try:
                raw = getattr(rna_obj, prop.identifier)
            except Exception as ex:
                out[path] = f"<error:{ex}>"
                continue

            if prop.type in cls.PRIMITIVE_TYPES:
                out[path] = cls._serialise(raw)
            elif prop.type == 'POINTER':
                if raw is None:
                    out[path] = None
                elif isinstance(raw, bpy.types.ID):
                    out[path] = f"{raw.__class__.__name__}:{raw.name_full}"
                else:
                    out.update(cls._walk_rna(raw, path))
            elif prop.is_collection:
                try:
                    for idx, item in enumerate(raw):
                        subkey = getattr(item, "name", str(idx))
                        subpath = f"{path}[{subkey}]"
                        if isinstance(item, bpy.types.ID):
                            out[subpath] = f"{item.__class__.__name__}:{item.name_full}"
                        else:
                            out.update(cls._walk_rna(item, subpath))
                except Exception as ex:
                    out[path] = f"<error:{ex}>"
            else:
                out[path] = cls._serialise(raw)
        return out

    # -----------------------------------------------------------------------------
    # 3. Identity, snapshot & per‑block hash helpers ------------------------------

    @classmethod
    def _hash_datablock(cls, idb: bpy.types.ID) -> Dict[str, Any]:
        props = cls._walk_rna(idb)
        props.setdefault("name", idb.name_full)
        h = hashlib.blake2s()
        for k in sorted(props):
            h.update(k.encode())
            h.update(str(props[k]).encode())
        return {"type": idb.__class__.__name__, "props": props, "hash": h.hexdigest()}

    @classmethod
    def _identity_key(cls, idb: bpy.types.ID, block: Dict[str, Any], id_prop: str | None) -> str:
        if id_prop and id_prop in idb.keys():
            return f"{idb.__class__.__name__}:prop:{idb[id_prop]}"
        if isinstance(idb, bpy.types.Object):
            data_name = idb.data.name_full if idb.data else "NONE"
            return f"Object:stable:{data_name}:{idb.type}"
        return f"{idb.__class__.__name__}:hash:{block['hash']}"

    @classmethod
    def _snapshot_current(cls, id_prop: str | None = None, *, ignore_linked=True):
        """Return a dict mapping *identity_key* ➜ block‑info for the *current* file."""
        snapshot: Dict[str, Any] = {}
        filtered_names = [n for n in dir(bpy.data)
                        if not n.startswith("__") and n not in cls.SKIP_IDB_COLLS]
        for coll_name in filtered_names:
            collection = getattr(bpy.data, coll_name)
            if not hasattr(collection, "__iter__"):
                continue
            for idb in collection:
                if not hasattr(idb, "name_full"):
                    continue
                if ignore_linked and getattr(idb, "library", None):
                    continue
                block = cls._hash_datablock(idb)
                block.setdefault("bpy_path", coll_name)  # useful for grouping
                key = cls._identity_key(idb, block, id_prop)
                if key in snapshot:
                    key = f"{key}:{idb.name_full}"  # fall‑back for accidental clashes
                snapshot[key] = block
        return snapshot

    @classmethod
    def _snapshot_file(cls, path: str, id_prop: str | None = None, *, ignore_linked=True):
        """Load *path* (without UI) and snapshot it."""
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
        return cls._snapshot_current(id_prop, ignore_linked=ignore_linked)

    # -----------------------------------------------------------------------------
    # 4. FILE‑LEVEL AUTHORED HASH --------------------------------------------------

    @classmethod
    def _digest_from_snapshot(cls, snapshot: Dict[str, Any]) -> str:
        """Collapse a snapshot into a single deterministic blake2s digest."""
        h = hashlib.blake2s()
        for key in sorted(snapshot):
            h.update(key.encode())
            h.update(snapshot[key]['hash'].encode())
        return h.hexdigest()

    @classmethod
    def hash_blend_file(cls, path: str, *, id_prop: str | None = None) -> str:
        """Return a blake2s digest representing *authored* content of *path*."""
        snap = cls._snapshot_file(path, id_prop)
        return cls._digest_from_snapshot(snap)

    @classmethod
    def hash_current_file(cls,*, id_prop: str | None = None) -> str:
        """Return an authored‑hash for the *currently open* file."""
        snap = cls._snapshot_current(id_prop)
        return cls._digest_from_snapshot(snap)

    # -----------------------------------------------------------------------------
    # 4. Diff helpers --------------------------------------------------------------

    @classmethod
    def _safe_cmp(cls, a, b):
        try:
            return a != b
        except Exception:
            return str(a) != str(b)

    @classmethod
    def _diff_props(cls, pa, pb):
        out: Dict[str, Any] = {}
        for k in pa.keys() | pb.keys():
            if cls._safe_cmp(pa.get(k), pb.get(k)):
                out[k] = {"A": pa.get(k), "B": pb.get(k)}
        return out

    @classmethod
    def _group_by_type(cls, item_keys, src_dict, with_payload=False):
        groups: Dict[str, Dict[str, Any]] = {}
        for key in item_keys:
            block = src_dict[key]
            dtype = block.get("bpy_path", "Other")
            name = block["props"]["name"]
            groups.setdefault(dtype, {})[name] = block if with_payload else {}
        return groups

    @classmethod
    def _diff_snapshots(cls, snap_a, snap_b):
        added_keys = snap_b.keys() - snap_a.keys()
        removed_keys = snap_a.keys() - snap_b.keys()

        # changed ---------------------------------------------------------------
        changed: Dict[str, Dict[str, Any]] = {}
        for key in snap_a.keys() & snap_b.keys():
            if snap_a[key]["hash"] == snap_b[key]["hash"]:
                continue
            delta = cls._diff_props(snap_a[key]["props"], snap_b[key]["props"])
            if not delta:
                continue
            dtype = snap_b[key]["bpy_path"]
            name = snap_b[key]["props"]["name"]
            changed.setdefault(dtype, {})[name] = delta

        added = cls._group_by_type(added_keys, snap_b)
        removed = cls._group_by_type(removed_keys, snap_a)

        return {"added": added, "removed": removed, "changed": changed}

    # -----------------------------------------------------------------------------
    # 5. Public API ---------------------------------------------------------------

    @classmethod
    def diff_blend_files(cls, path_original: str, path_modified: str, *, id_prop: str | None = None):
        """Return diff‑dict between *path_original* and *path_modified*."""
        try:
            snap_orig = cls._snapshot_file(path_original, id_prop)
            snap_mod = cls._snapshot_file(path_modified, id_prop)
            return cls._diff_snapshots(snap_orig, snap_mod)
        except MemoryError:
            return {"error": "MemoryError", "stage": "snapshot"}

    def diff_current_vs_other(self, path_other: str, *, reverse: bool = False, id_prop: str | None = None):
        """Interactive diff: current file vs *path_other* (or reverse)."""

        try:
            current_fp = bpy.data.filepath
            snap_current = self.__class__._snapshot_current(id_prop)
            snap_other   = self.__class__._snapshot_file(path_other, id_prop)
            if current_fp:
                bpy.ops.wm.open_mainfile(filepath=current_fp, load_ui=False)
            self._cache = (self.__class__._diff_snapshots(snap_current, snap_other)
                    if not reverse else self.__class__._diff_snapshots(snap_other, snap_current))
            return self._cache
        except MemoryError:
            return {"error": "MemoryError", "stage": "snapshot"}

    def get_diff_cache(self):
        """Return the cached diff result from the last diff operation."""
        return self._cache

    def set_invalid_cache(self):
        """Invalidate the diff cache."""
        self._cache = None

# -----------------------------------------------------------------------------
# Arg parser class ---------------------------------------------------------
class BlendDiffArgParser(argparse.ArgumentParser):

    """Reusable argument parser for blenddiff CLI and wrappers."""
    def __init__(self):
        super().__init__()

        # Operation selection
        op_grp = self.add_argument_group("op-mode", "Operation mode")
        ex_grp = op_grp.add_mutually_exclusive_group(required=True)
        ex_grp.add_argument("--hash", action="store_true", help="Generate an authored‑hash for a single .blend file")
        ex_grp.add_argument("--diff", action="store_true", help="Diff two .blend files")

        hash_grp = self.add_argument_group("hash-mode", "Hash mode")
        hash_grp.add_argument("--hash-file", help="Generate an authored‑hash for a single .blend file")

        diff_grp = self.add_argument_group("diff-mode", "Diff mode")
        diff_grp.add_argument("--file-original", help="Original .blend file for diff")
        diff_grp.add_argument("--file-modified", help="Modified .blend file for diff")

        # Shared options
        self.add_argument("--id-prop", help="Custom property used for stable identity", required=False)
        self.add_argument("--no-factory-startup", action="store_true", help="Don't use factory startup option (not recommended)", required=False)

        out_grp = self.add_mutually_exclusive_group(required=True)
        out_grp.add_argument("--file-out", help="Save output JSON to file", required=False)
        out_grp.add_argument("--stdout", action="store_true", help="Print output to stdout", required=False)

        self.add_argument("--pretty-json", action="store_true", help="Pretty‑print JSON (indent=2)")
        self.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    def parse_args(self, args=None, namespace=None):
        args = super().parse_args(args, namespace)
        # Custom required logic
        if args.hash:
            if not args.hash_file:
                self.error("Must provide --hash-file when using --hash")
        elif args.diff:
            if not (args.file_original and args.file_modified):
                self.error("Must provide both --file-original and --file-modified when using --diff")
        else:
            self.error("Must specify either --hash or --diff")
        if not (args.file_out or args.stdout):
            self.error("One of --file-out or --stdout is required")
        return args


def _run_directly_from_args():
    """Run blenddiff from the command line arguments as received through Blender."""
    argv = sys.argv[sys.argv.index("--") + 1:]
    args = BlendDiffArgParser().parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    blend_diff = BlendDiff()

    # HASH mode
    if args.hash_file:
        digest = blend_diff.hash_blend_file(args.hash_file, id_prop=args.id_prop)
        payload = {"file_hash": digest}
        metadata = {**blend_diff._get_policy_metadata_json(), **blend_diff._get_codebase_hash()}
        payload["metadata"] = metadata
    # DIFF mode
    else:
        if not (args.file_original and args.file_modified):
            BlendDiffArgParser().parser.error("--file-original and --file-modified are required when not using --hash-file")
        payload = blend_diff.diff_blend_files(args.file_original, args.file_modified, id_prop=args.id_prop)

    # Serialise & output
    if args.pretty_json:
        payload_str = json.dumps(payload, indent=2)
    else:
        payload_str = json.dumps(payload, separators=(',', ':'))

    if args.stdout:
        print(payload_str, flush=True)
    if args.file_out:
        LOG.info("Saving JSON output to %s", args.file_out)
        with open(args.file_out, "w", encoding="utf-8") as fh:
            fh.write(payload_str)
        LOG.info("JSON output saved.")

def _extract_first_json(text: str):
    start = text.find('{')
    if start == -1:
        raise ValueError("No JSON object found in subprocess output")

    decoder = json.JSONDecoder()
    obj, end = decoder.raw_decode(text[start:])   # parse first JSON object
    return obj

# Re-run via wrapper
def _run_from_wrapper():

    _BLENDER_EXEC_LABEL = "--blender-exec" # FLAG name for the Blender executable

    wrap_ap = argparse.ArgumentParser(description="Wrapper for running blenddiff via Blender CLI.")
    wrap_ap.add_argument(_BLENDER_EXEC_LABEL, help="Path to the Blender executable.", required=True)
    wrap_ap.add_argument(
        "--wrapper-log-level",
        help="The log level for the wrapper.",
        required=False,
        choices=[name for name in logging._nameToLevel.keys() if name in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}]
    )
    args, argv = wrap_ap.parse_known_args() 

    if args.wrapper_log_level:
        LOG.setLevel(args.wrapper_log_level)

    LOG.debug(f"Wrapper got args: {args}")
    LOG.debug(f"Wrapper got argv: {argv}")

    bd_ap = BlendDiffArgParser()
    blender_args = None
    try:
        blender_args = bd_ap.parse_args(argv)
        LOG.debug("Parsed blenddiff arguments successfully.")
    except Exception as e:
        LOG.error(f"Error parsing blenddiff arguments:\n{e}")
        sys.exit(1)

    try:
        script_path = os.path.abspath(__file__)
        cmd = [
            args.blender_exec,
            "--background"]
        
        if not blender_args.no_factory_startup:
            cmd += ["--factory-startup"]

        cmd += ["--factory-startup",  # start with factory settings
            "--python", script_path,
            "--"
        ]

        cmd += argv

        # Run blender and capture output
        LOG.debug(f"Running command: {cmd}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )
        LOG.debug(f"Blender command completed with return code {result.returncode}.")
        LOG.debug(f"Blender stdout: {result.stdout}")
        LOG.debug(f"Blender stderr: {result.stderr}")        

    except Exception as e:
        LOG.error(f"Error running wrapper:\n{e}")
        sys.exit(1)

    if not blender_args.file_out: # if we are not writing to a file, we expect JSON output on stdout
        # Find the JSON output - it starts with '{'
        output_lines = result.stdout.splitlines()
        LOG.debug("Blender output: %s", output_lines)
        #json_output = json.loads(next(line for line in output_lines if line.startswith('{')))
        json_output = _extract_first_json(result.stdout)
        LOG.debug("Captured JSON output: %s", json_output)

        if blender_args.pretty_json:
            json_output = json.dumps(json_output, indent=2)
        else:
            json_output = json.dumps(json_output, separators=(',', ':'))

        print(json_output, flush=True)  # Print the JSON output to stdout

    else:
        # If we are writing to a file, we expect the output to be in the file
        if not os.path.exists(blender_args.file_out):
            LOG.error(f"Output file could not be written.")
            sys.exit(1)
        else:
            print(f"Output written to {blender_args.file_out}", flush=True)


# -----------------------------------------------------------------------------
# 1. Arg parsing ---------------------------------------------------------
if __name__ == "__main__": # bl_ext.{...} when running under blender
    
    if _BLENDER and "--" in sys.argv:
        # Run in Blender
        LOG.debug("Running in Blender directly.")
        _run_directly_from_args()
    else:
        if (not _BLENDER and "--" not in sys.argv) :
            # Run via wrapper
            LOG.debug("Running from wrapper.")
            _run_from_wrapper()
        else:
            # Invalid usage
            LOG.error(USAGE)
            if (_BLENDER):
                LOG.warning("\'bpy\' and \'mathutil\' modules were importable. If you ran directly from python, this might be caused by a third-party package such as \'fake-bpy-module\'. Please remove them from the path (or use a venv) and try again.")
            sys.exit(1)