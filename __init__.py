bl_info = {
    "name": "vDiff",
    "author": "Chris Margach",
    "description": "",
    "blender": (2, 80, 0),
    "version": (0, 0, 1),
    "category": "System",
    "location": "Viewport -> Navigator -> ",
    "description": "Compare current scene to another .blend file and highlight new and modified objects.",
    "warning": "",
    "doc_url": "",
    "tracker_url": ""
}

import os, sys, logging, json

import bpy
from bpy.props import StringProperty
from bpy.types import AddonPreferences, Panel, Operator, PropertyGroup
from bpy.app.handlers import persistent

from . import blenddiff

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG,
                    format="%(levelname)s: %(message)s")

@persistent
def update_compare_filepath(dummy):
    wm = bpy.context.window_manager
    if bpy.data.filepath:
        wm.compare_filepath = os.path.join(os.path.dirname(bpy.data.filepath), '')
    else:
        wm.compare_filepath = ""

# --- Storage for file path ---
class BlendDiffProperties(PropertyGroup):
    compare_filepath: StringProperty(
        name="Compare File",
        description="Path to the .blend file to compare with",
        subtype='FILE_PATH',
        default=""
    )

# --- UI Panel ---
class BLENDDIFF_PT_MainPanel(Panel):
    bl_label       = "Blender vDiff"
    bl_idname      = "BLENDDIFF_PT_main"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'vDiff'

    def draw(self, context):
        layout = self.layout
        wm     = context.window_manager

        layout.prop(wm, "compare_filepath", text="File to compare")
        layout.operator("blenddiff.compare", icon='VIEWZOOM')


# --- Operator to run diff ---
class BLENDDIFF_OT_Compare(Operator):
    bl_idname      = "blenddiff.compare"
    bl_label       = "Compare & Highlight"
    bl_description = "Compare current .blend with selected file and select changed objects"

    def _validate_path(self, compare_path) -> str | None:
        """Return absolute path or None and set self.report error."""
        if not compare_path:
            self.report({'ERROR'}, "No comparison file selected.")
            return None

        # Resolve relative path ("//") against current file
        base = os.path.dirname(bpy.data.filepath)
        if compare_path.startswith("//"):
            compare_path = compare_path[2:]
        abs_path = os.path.abspath(os.path.join(base, compare_path))

        if not abs_path.lower().endswith(".blend"):
            self.report({'ERROR'}, "Selected file is not a .blend file.")
            return None
        if not os.path.isfile(abs_path):
            self.report({'ERROR'}, f"File does not exist:\n{abs_path}")
            return None
        return abs_path

    def execute(self, context):
        wm = context.window_manager

        # Ensure current file is saved & clean
        if not bpy.data.filepath:
            self.report({'ERROR'}, "Please save the current file before comparing.")
            return {'CANCELLED'}
        if bpy.data.is_dirty:
            self.report({'ERROR'}, "Please save or revert changes before comparing.")
            return {'CANCELLED'}

        target = self._validate_path(wm.compare_filepath)
        if not target:
            return {'CANCELLED'}

        diff = blenddiff.diff_current_vs_other(target)
        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Got JSON diff:\n{json.dumps(diff, indent=2)}')
        if "error" in diff:
            self.report({'ERROR'}, f"Diff failed: {diff['error']} ({diff.get('stage')})")
            return {'CANCELLED'}

        # ------------------------------------------------------------------
        # Highlight changed objects
        bpy.ops.object.select_all(action='DESELECT')

        obj_changes = diff.get("changed", {}).get("Object", {})
        for obj_name in obj_changes.keys():
            obj = bpy.data.objects.get(obj_name)
            if obj:
                obj.select_set(True)

        #self.report({'INFO'}, f"{len(obj_changes)} object(s) changed.")
        return {'FINISHED'}

# --- Register ---
classes = (
    BlendDiffProperties,
    BLENDDIFF_PT_MainPanel,
    BLENDDIFF_OT_Compare,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.WindowManager.compare_filepath = StringProperty(
      name="Compare File",
      description="Path to the .blend file to compare with",
      subtype='FILE_PATH',
      default=""
    )

    bpy.app.handlers.load_post.append(update_compare_filepath)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.compare_filepath
    bpy.app.handlers.load_post.remove(update_compare_filepath)

if __name__ == "__main__":
    register()
