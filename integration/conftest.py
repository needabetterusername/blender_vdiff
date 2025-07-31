"""
Auto-discover Blender executables cached in .cache/blender/<ver>.path
(written by scripts/fetch_blenders.py).  Provides the fixture
`blender_executable`, parametrised over every version found.
"""
import os, tempfile, sys, pathlib, yaml
import pytest

# --- guarantee clean prefs & scripts -----------------
os.environ["BLENDER_USER_CONFIG"]   = tempfile.mkdtemp()
os.environ["BLENDER_USER_SCRIPTS"]  = tempfile.mkdtemp()

# --- make src/ importable ----------------------------
ROOT   = pathlib.Path(__file__).resolve().parents[1]
SRC    = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# --- prep Blender executables ----------------------
CACHE  = ROOT / ".cache" / "blender"
CFG    = yaml.safe_load((ROOT / ".blender-versions.yaml").read_text())
VERS    = CFG.get("versions", [])


def _executables():
    for ver in VERS:
        p = CACHE / f"{ver}.path"
        if p.exists():
            yield ver, p.read_text().strip()

EXECES = dict(_executables())

def pytest_generate_tests(metafunc):
    if "blender_executable" in metafunc.fixturenames:
        if EXECES:
            ids, paths = zip(*EXECES.items())
            metafunc.parametrize("blender_executable", paths, ids=ids)
        else:
            metafunc.parametrize(
                "blender_executable", [],
                marks=pytest.mark.skip("No Blender executables cached"))
