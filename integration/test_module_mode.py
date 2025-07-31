# integration/test_module_mode.py
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.blender]   # ▶ tagged for the plugin

def test_module_mode_inside_blender(blender_executable):
    # Real logic later – for now just assert True inside Blender
    assert True
