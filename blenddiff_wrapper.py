import sys
import argparse, logging
import subprocess
import json


logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)
LOG = logging.getLogger(__name__)


def run_diff(blender_exec: str, args: list[str]):
    cmd = [
        blender_exec,
        "--background",
        "--python", "blenddiff.py",
        "--"
    ]
    cmd += args
    LOG.debug(f"Running command: {cmd}")

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

    _BLENDER_EXEC_LABEL = "--blender-exec"

    ap = argparse.ArgumentParser(description="Wrapper for running blenddiff via Blender.")
    # ap.add_argument("--file-original", required=True, help="Path to the original .blend file.")
    # ap.add_argument("--file-modified", required=True, help="Path to the modified .blend file.")
    # ap.add_argument("--id-prop", help="Optional ID property to use for datablock identity.")
    ap.add_argument(_BLENDER_EXEC_LABEL, help="Path to the Blender executable.", required=True)
    # ap.add_argument("--pretty-json", action="store_true", help="Output JSON in pretty format.")
    # ap.add_argument("--debug", action="store_true", help="Enable debug logging to stdout.")

    args, argv = ap.parse_known_args() 

    result = run_diff(
        blender_exec = args.blender_exec,
        args = argv
    )

    # Output the result
    print(result)