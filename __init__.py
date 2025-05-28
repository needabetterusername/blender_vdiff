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

import bpy
import os
from bpy.props import StringProperty
from bpy.types import AddonPreferences, Panel, Operator, PropertyGroup
from bpy.app.handlers import persistent

# Ensure blenddiff.py is in the same folder as this __init__.py
from . import blenddiff

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
    bl_label = "BlendDiff"
    bl_idname = "BLENDDIFF_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlendDiff'

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager

        layout.prop(wm, "compare_filepath")
        layout.operator("blenddiff.compare")

# --- Operator to run diff ---
class BLENDDIFF_OT_Compare(Operator):
    bl_idname = "blenddiff.compare"
    bl_label = "Compare and Highlight"
    bl_description = "Compare current scene with the selected .blend file"

    def execute(self, context):
        wm = context.window_manager
        compare_filepath = wm.compare_filepath

        # Must be saved and clean
        if not bpy.data.filepath:
            self.report({'ERROR'}, "Please save the current file before comparing.")
            return {'CANCELLED'}

        if bpy.data.is_dirty:
            self.report({'ERROR'}, "Please save or revert changes before comparing.")
            return {'CANCELLED'}

        # Resolve relative path using the current file's directory as base
        base_path = os.path.dirname(bpy.data.filepath)
        rel_path = wm.compare_filepath
        if rel_path.startswith("//"):
            rel_path = rel_path[2:]
        target = os.path.abspath(os.path.join(base_path, rel_path))

        if not os.path.exists(target):
            self.report({'ERROR'}, f"File does not exist: {target}")
            return {'CANCELLED'}

        diff = blenddiff.diff_current_vs_file(target)

        if "error" in diff:
            self.report({'ERROR'}, f"Diff failed: {diff['error']} ({diff.get('stage')})")
            return {'CANCELLED'}

        # Deselect all
        bpy.ops.object.select_all(action='DESELECT')

        # Select changed objects (only top-level objects)
        for idstr in diff.get("changed", {}):
            if idstr.startswith("Object:"):
                name = idstr.split(":", 1)[1]
                obj = bpy.data.objects.get(name)
                if obj:
                    obj.select_set(True)

        self.report({'INFO'}, f"{len(diff.get('changed', {}))} items changed.")
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
