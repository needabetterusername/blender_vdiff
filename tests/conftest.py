# This file is part of the Blender VDiff project.
# Used to set up the Python path for unit tests.

# Adds src/ to sys.path so imports work even without PYTHONPATH

import sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC  = ROOT / "src"

# Only prepend once; keep it as str
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))