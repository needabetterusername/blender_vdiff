# integration/test_script_mode.py
import pathlib, uuid, subprocess, json
import pytest

DATA   = pathlib.Path(__file__).parent / "test-cases"   # add .blend files later
SCRIPT = pathlib.Path(__file__).parents[1] / "addons" / "blender_vdiff" / "src" / "blenddiff.py"

BASELINE_FILE_PATH_TC1 = str(DATA / "1" / "baseline.blend")
MODIFIED_FILE_PATH_TC1 = str(DATA / "1" / "modified.blend")

HASH_CHECK_FILE_PATH_TC1_MODIFIED = str(DATA / "1" / "hash-modified.json")
DIFF_CHECK_FILE_PATH_TC1 = str(DATA / "1" / "diff.json")

pytestmark = [pytest.mark.integration, pytest.mark.blender]   # ▶ tagged for the plugin

def run_blender_script(blender_executable, opts):
    cmd = [
        blender_executable,
        "--background",
        "--factory-startup",  # keep clean prefs
        "--python", str(SCRIPT),
        "--",                 # everything after goes to blendiff.py
    ] + opts
    return subprocess.run(cmd, capture_output=True, text=True)

def _extract_first_json(text: str):
    start = text.find('{')
    if start == -1:
        raise ValueError("No JSON object found in subprocess output")

    decoder = json.JSONDecoder()
    obj, end = decoder.raw_decode(text[start:])   # parse first JSON object
    return obj

###################################################################
## HASH
###################################################################
@pytest.mark.xfail(strict=False, reason="Currently not supported across Blender versions.")
@pytest.mark.integration
def test_blender_script_mode_hash_stdout(blender_executable):
    opts = [
        "--hash",
        "--hash-file", MODIFIED_FILE_PATH_TC1,
        "--stdout",
    ]
    cp = run_blender_script(blender_executable, opts)

    assert cp.returncode == 0, cp.stderr
    output = cp.stdout
    json_output = _extract_first_json(output)
    with pathlib.Path(HASH_CHECK_FILE_PATH_TC1_MODIFIED).open(encoding="utf-8") as f:
        assert json.load(f) == json_output, f"Unexpected output: {json_output}"

@pytest.mark.xfail(strict=False, reason="Currently not supported across Blender versions.")
@pytest.mark.integration
def test_blender_script_mode_hash_file_out(blender_executable, tmp_path):

    out_json_path = tmp_path / f"{uuid.uuid4().hex}.json"

    opts = [
        "--hash",
        "--hash-file", MODIFIED_FILE_PATH_TC1,
        "--file-out", out_json_path,
    ]
    cp = run_blender_script(blender_executable, opts)

    assert cp.returncode == 0, cp.stderr
    with pathlib.Path(HASH_CHECK_FILE_PATH_TC1_MODIFIED).open(encoding="utf-8") as truth_file:
        with out_json_path.open(encoding="utf-8") as output_file:
            assert json.load(truth_file) == json.load(output_file), f"Unexpected output: {output_file.read().strip()}"

###################################################################
## DIFF
###################################################################
@pytest.mark.integration
def test_blender_script_mode_diff_stdout(blender_executable):
    opts = [
        "--diff",
        "--file-original", BASELINE_FILE_PATH_TC1,
        "--file-modified", MODIFIED_FILE_PATH_TC1,
        "--stdout",
    ]
    cp = run_blender_script(blender_executable, opts)

    assert cp.returncode == 0, cp.stderr
    output = cp.stdout
    json_output = _extract_first_json(output)
    with pathlib.Path(DIFF_CHECK_FILE_PATH_TC1).open(encoding="utf-8") as f:
        assert json.load(f) == json_output, f"Unexpected output: {json_output}"


@pytest.mark.integration
def test_blender_script_mode_diff_file_out(blender_executable, tmp_path):

    out_json_path = tmp_path / f"{uuid.uuid4().hex}.json"

    opts = [
        "--diff",
        "--file-original", BASELINE_FILE_PATH_TC1,
        "--file-modified", MODIFIED_FILE_PATH_TC1,
        "--file-out", out_json_path,
    ]
    cp = run_blender_script(blender_executable, opts)

    assert cp.returncode == 0, cp.stderr
    with pathlib.Path(DIFF_CHECK_FILE_PATH_TC1).open(encoding="utf-8") as truth_file:
        with out_json_path.open(encoding="utf-8") as output_file:
            assert json.load(truth_file) == json.load(output_file), f"Unexpected output: {output_file.read().strip()}"
