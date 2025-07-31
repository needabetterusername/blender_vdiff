# integration/test_script_mode.py
import subprocess, json, pathlib, pytest

DATA   = pathlib.Path(__file__).parent / "data"   # add .blend files later
SCRIPT = pathlib.Path(__file__).parents[1] / "src" / "vdiff_core" / "blendiff.py"

@pytest.mark.integration
def test_script_mode(blender_executable, tmp_path):
    out = tmp_path / "result.json"
    subprocess.check_call([
        blender_executable,
        "--background", "--factory-startup",
        "--python", str(SCRIPT), "--",
        "--a", "dummyA.blend", "--b", "dummyB.blend", "--out", str(out),
    ])
    # stub assertion until real logic is in place
    assert True
