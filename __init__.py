bl_info = {
    "name": "vDiff",
    "author": "Chris Margach",
    "description": "Visual Diff for Blender",
    "blender": (2, 80, 0),
    "version": (0, 0, 1),
    "category": "System",
    "location": "Viewport -> Navigator -> vDiff",
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
class VDIFF_PG_Properties(PropertyGroup):
    compare_filepath: StringProperty(
        name="Compare File",
        description="Path to the .blend file to compare with",
        subtype='FILE_PATH',
        default=""
    )



# --- UI Panel ---
class VDIFF_PT_MainPanel(Panel):
    bl_label       = "Blender vDiff"
    bl_idname      = "VDIFF_PT_main"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'vDiff'

    def draw(self, context):
        layout = self.layout
        wm     = context.window_manager

        layout.prop(wm, "compare_filepath", text="File to compare")
        layout.operator("vdiff.compare", icon='VIEWZOOM')

# --- UI Panel ---
class VDIFF_OT_warning_popup(bpy.types.Operator):
    bl_idname = "wm.long_warning_popup"
    bl_label = "Disabled Items Warning"
    bl_description = "Some items are disabled. Review the list below."

    items_json: bpy.props.StringProperty() #JSON

    def draw(self, context):
        layout = self.layout
        items = json.loads(self.items_json)
        box = layout.box()
        col = box.column()

        # Scrollable area: limit visible height
        row = col.row()
        row.label(text=f"{len(items)} items disabled:")

        scroll = col.column()
        scroll.scale_y = 5  # ← increases visible scroll area
        for item in items:
            scroll.label(text=item)

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=400)

    def execute(self, context):
        return {'FINISHED'}


# --- Operator to run diff ---
class VDIFF_OT_Compare(Operator):
    bl_idname      = "vdiff.compare"
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

    def invoke(self, context, event):
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
        # Create report
        changed = diff.get("changed", {})
        added = diff.get("added",{})

        # ------------------------------------------------------------------
        # Highlight scene objects
        bpy.ops.object.select_all(action='DESELECT')

        # Only objects can be selected
        changed_selectable = changed.get("objects",{})
        added_selectable = added.get("objects",{})
        selectable = list(changed_selectable.keys()) + list(added_selectable.keys())

        select_disabled = []
        for object_name in selectable:
            object = context.scene.objects.get(object_name,None)
            if object:
                if getattr(object,"hide_select",False):
                    select_disabled.append(object.name)
                else:
                    object.select_set(True)

        if select_disabled:
            data = json.dumps(select_disabled)

            def _show_popup():
                bpy.ops.wm.long_warning_popup('INVOKE_DEFAULT', items_json=data)
                return None          # unregister the timer

            bpy.app.timers.register(_show_popup, first_interval=0.0)

        #self.report({'INFO'}, f"{len(obj_changes)} object(s) changed.")
        return {'FINISHED'}

    def execute(self, context):
        # Optional: if you need to support 'Run' via search menu
        return self.invoke(context, None)

# --- Register ---
classes = (
    VDIFF_PG_Properties,
    VDIFF_PT_MainPanel,
    VDIFF_OT_warning_popup,
    VDIFF_OT_Compare,
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
