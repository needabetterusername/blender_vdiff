# Adds src/ to sys.path so imports work even without PYTHONPATH
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC  = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))