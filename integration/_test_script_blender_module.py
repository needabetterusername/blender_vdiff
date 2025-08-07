# integration/test_script_mode.py
import subprocess, json, pathlib, pytest

DATA   = pathlib.Path(__file__) / "test-cases"   # add .blend files later
SCRIPT = pathlib.Path(__file__).parents[1] / "src" / "vdiff_core" / "blendiff.py"

def run_blender_script(blender, opts):
    cmd = [
        blender,
        "--background",
        "--factory-startup",  # keep clean prefs
        "--python", str(SCRIPT),
        "--",                 # everything after goes to blendiff.py
    ] + opts
    return subprocess.run(cmd, capture_output=True, text=True)

@pytest.mark.integration
def test_script_mode_diff_json(blender_executable, tmp_path):
    out_json_path = tmp_path / "result.json"

    opts = [
        "--diff",
        "--file-original", str(DATA/"baseline.blend"),
        "--file-modified", str(DATA/"changed.blend"),
        "--file-out", str(out_json_path),
    ]
    cp = run_blender_script(blender_executable, opts)

    assert cp.returncode == 0, cp.stderr
    assert out_json_path.is_file(), f"Missing output file."

    data = json.loads(out_json_path.read_text())
    assert data["changed_object_count"] == 1
