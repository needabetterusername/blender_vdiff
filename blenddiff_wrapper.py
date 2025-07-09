import sys
import argparse, logging
import subprocess
import json

from blenddiff import BlendDiffParser

LOG = logging.getLogger(__name__)
logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.DEBUG)

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
    ap.add_argument(_BLENDER_EXEC_LABEL, help="Path to the Blender executable.", required=True)
    ap.add_argument(
        "--wrapper-log-level",
        help="The log level for the wrapper.",
        required=False,
        choices=[name for name in logging._nameToLevel.keys() if name in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}]
    )

    args, argv = ap.parse_known_args() 
    LOG.debug(f"Got args: {args}")
    LOG.debug(f"Got argv: {argv}")

    if args.wrapper_log_level:
        LOG.setLevel(args.wrapper_log_level)

    bd_args = BlendDiffParser()
    try:
        bd_args.parse_args(argv)
    except Exception as e:
        LOG.error(f"Error parsing blenddiff arguments: {e}")
        sys.exit(1)

    result = run_diff(
        blender_exec = args.blender_exec,
        args = argv
    )

    # Output the result
    print(result)