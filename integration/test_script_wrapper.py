# integration/test_module_mode.py
import sys, pathlib, subprocess, json, pathlib
import pytest

DATA   = pathlib.Path(__file__).parent / "test-cases"   # add .blend files later
BASELINE_FILE_PATH_TC1 = str(DATA / "1" / "baseline.blend")
MODIFIED_FILE_PATH_TC1 = str(DATA / "1" / "modified.blend")
DIFF_CHECK_FILE_PATH_TC1 = str(DATA / "1" / "diff.json")

SCRIPT = pathlib.Path(__file__).parents[1] / "src" / "vdiff_core" / "blenddiff.py"

pytestmark = [pytest.mark.integration, pytest.mark.blender]   # ▶ tagged for the plugin

def run_blender_script(blender_executable, opts):
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--blender-exec", str(blender_executable),
    ] + opts
    return subprocess.run(cmd, capture_output=True, text=True)

@pytest.mark.integration
def test_script_mode_diff_stdout(blender_executable):
    opts = [
        "--diff",
        "--file-original", BASELINE_FILE_PATH_TC1,
        "--file-modified", MODIFIED_FILE_PATH_TC1,
        "--stdout",
    ]
    cp = run_blender_script(blender_executable, opts)

    assert cp.returncode == 0, cp.stderr
    with pathlib.Path(DIFF_CHECK_FILE_PATH_TC1).open(encoding="utf-8") as f:
        assert json.load(f) == json.loads(cp.stdout), f"Unexpected output: {cp.stdout.strip()}"
