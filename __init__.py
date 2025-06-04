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
from bpy.app import translations
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


# --- UI Dialog ---
# helper: temporarily hide the word “Cancel”
_cancel_overridden = False

def _suppress_cancel_label():
    """Override UI translation so the Cancel button’s label is empty."""
    global _cancel_overridden
    if _cancel_overridden:
        return
    # In the default UI context (“*”) replace “Cancel” with “”.
    translations.register(__name__, {"*": {"Cancel": ""}})
    _cancel_overridden = True

def _restore_cancel_label():
    global _cancel_overridden
    if not _cancel_overridden:
        return
    translations.unregister(__name__)
    _cancel_overridden = False

# dummy OK operator
class VDIFF_OT_confirm(bpy.types.Operator):
    bl_idname  = "okonly.confirm"
    bl_label   = "OK"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        _restore_cancel_label()           # put the UI back to normal
        return {'FINISHED'}

# dialog operator
class VDIFF_OT_dialog(bpy.types.Operator):
    """Persistent dialog that shows a list of strings and just an OK button"""
    bl_idname  = "okonly.dialog"
    bl_label   = "Warning - Disabled objects found in scene"
    bl_options = {'INTERNAL'}

    items_json: bpy.props.StringProperty(default="[]")
    items_text: bpy.props.StringProperty()   # read-only display

    def invoke(self, context, event):
        # Prepare list text
        try:
            items = json.loads(self.items_json)
        except Exception:
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Exception while loading JSON for display.')
            items = []
        self.items_text = "\n".join(str(i) for i in items)

        _suppress_cancel_label()  # hide “Cancel” *before* dialog is drawn

        # Blender 4.x
        if hasattr(bpy.types.UILayout, "template_popup_confirm"):
            return context.window_manager.invoke_props_dialog(self, width=420)
        # <= 3.6 LTS: fall back to regular props dialog
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"The below scene items are disabled and cannot be highlighted.")
        col = layout.column()
        col.enabled = False                       # read-only
        col.prop(self, "items_text", text="")    # multi-line, scrollable

        if hasattr(layout, "template_popup_confirm"):
            # Blender 4.x – override footer with our single OK button
            layout.template_popup_confirm(
                operator=VDIFF_OT_confirm.bl_idname,
                text="OK",
                cancel_text="",                   # <- no Cancel button
            )
        else:
            # Blender 3.x – draw our own OK button; Cancel label is blank
            row = layout.row()
            row.operator(VDIFF_OT_confirm.bl_idname, text="OK").default = True

    def execute(self, context):
        return {'FINISHED'}

    # Make pressing Esc cleanly restore the translation override
    def cancel(self, context):
        _restore_cancel_label()


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

        disabled_objects = []
        for object_name in selectable:
            object = context.scene.objects.get(object_name,None)
            if object:
                if getattr(object,"hide_select",False):
                    disabled_objects.append(object)
                else:
                    object.select_set(True)

        if disabled_objects:
            object_names = [object.name for object in disabled_objects]

            def _show_popup():
                #bpy.ops.wm.long_warning_popup('INVOKE_DEFAULT', items_json=data)
                bpy.ops.okonly.dialog('INVOKE_DEFAULT', items_json=json.dumps(object_names))
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
    VDIFF_OT_confirm,
    VDIFF_OT_dialog,
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
