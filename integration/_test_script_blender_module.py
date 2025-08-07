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

STD_OUT_CHECK = "{'added': {'scenes': {'Scene.001': {}, 'Scene': {}}, 'objects': {'Icosphere': {}}, 'texts': {'script.py': {}}, 'meshes': {'Icosphere': {}, 'Cube': {}}, 'collections': {'LCA': {}}}, 'removed': {'meshes': {'Cube': {}}, 'scenes': {'Scene': {}}, 'materials': {'Material': {}}}, 'changed': {'objects': {'Camera': {'matrix_local': {'A': [[0.6859206557273865, -0.32401347160339355, 0.6515582203865051, 7.358891487121582], [0.7276763319969177, 0.305420845746994, -0.6141703724861145, -6.925790786743164], [0.0, 0.8953956365585327, 0.44527140259742737, 4.958309173583984], [0.0, 0.0, 0.0, 1.0]], 'B': [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]}}, 'Cube': {'empty_display_type': {'A': 'ARROWS', 'B': 'PLAIN_AXES'}, 'lock_rotations_4d': {'A': False, 'B': True}, 'matrix_local': {'A': [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 1.5], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]], 'B': [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]}, 'active_material': {'A': 'Material:Material', 'B': None}}, 'Cube.001': {'scale': {'A': [1.0, 1.0, 1.0], 'B': [1.5, 1.5, 1.5]}, 'matrix_basis': {'A': [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, -1.5], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]], 'B': [[1.5, 0.0, 0.0, 0.0], [0.0, 1.5, 0.0, -1.5], [0.0, 0.0, 1.5, 0.0], [0.0, 0.0, 0.0, 1.0]]}, 'hide_select': {'A': False, 'B': True}, 'matrix_local': {'A': [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, -1.5], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]], 'B': [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]}}, 'Light': {'matrix_local': {'A': [[-0.29086464643478394, -0.7711008191108704, 0.5663931965827942, 4.076245307922363], [0.9551711678504944, -0.1998833566904068, 0.21839119493961334, 1.0054539442062378], [-0.05518905818462372, 0.6045247316360474, 0.7946722507476807, 5.903861999511719], [0.0, 0.0, 0.0, 1.0]], 'B': [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]}}}}}"