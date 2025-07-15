import sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]   # repo root
SRC  = ROOT / "src"

# Only prepend once; keep it as str
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
