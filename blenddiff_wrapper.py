import sys
import argparse, logging
import subprocess
import json


logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)
LOG = logging.getLogger(__name__)


def run_diff(file_original: str, file_modified: str, *, id_prop: str | None = None, blender_exec: str = "/Applications/Blender.app/Contents/MacOS/Blender"):
    cmd = [
        blender_exec,
        "--background",
        "--python", "blenddiff.py",
        "--",
        "--file-original", file_original,
        "--file-modified", file_modified,
        "--stdout"
    ]
    if id_prop:
        cmd.extend(["--id-prop", id_prop])
    
    LOG.debug("Running command: %s", " ".join(cmd))

    # Run blender and capture output
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )
    
    # Find the JSON output - it starts with '{'
    output_lines = result.stdout.splitlines()
    LOG.debug("Blender output: %s", output_lines)
    json_output = next(line for line in output_lines if line.startswith('{'))
    
    return json.loads(json_output)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Wrapper for running blenddiff via Blender.")
    ap.add_argument("--file-original", required=True, help="Path to the original .blend file.")
    ap.add_argument("--file-modified", required=True, help="Path to the modified .blend file.")
    ap.add_argument("--id-prop", help="Optional ID property to use for datablock identity.")
    ap.add_argument("--blender-exec", help="Path to the Blender executable.", required=True)
    ap.add_argument("--pretty-json", action="store_true", help="Output JSON in pretty format.")
    ap.add_argument("--debug", action="store_true", help="Enable debug logging to stdout.")
    args = ap.parse_args()

    # Set logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    diff = run_diff(
        file_original=args.file_original,
        file_modified=args.file_modified,
        id_prop=args.id_prop,
        blender_exec=args.blender_exec
    )

    # Output the result
    if args.pretty_json:
        print(json.dumps(diff, indent=2), flush=True)  # Pretty-print JSON output
    else:
        print(json.dumps(diff, separators=(',', ':')), flush=True)  # Compact JSON output