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
#class VDIFF_PG_Properties(PropertyGroup):
#    compare_filepath: StringProperty(
#        name="Compare File",
#        description="Path to the .blend file to compare with",
#        subtype='FILE_PATH',
#        default=""
#    )


#  helper: singular  ➜  plural
_PLURALS = {
    "workspace": "workspaces",
}

def _plural(attr: str) -> str:
    # Return the corresponding collection name inside bpy.data
    return _PLURALS.get(attr, attr + "s")

def get_screen_context(attrs=("scene", "workspace")):
    """Return a lightweight list describing the active datablocks for *attrs*."""
    win = bpy.context.window
    snapshot = []

    for attr in attrs:
        datablock = getattr(win, attr, None)
        if datablock is None:
            continue                                # skip if attr missing (rare)
        snapshot.append((attr, (datablock.name, _plural(attr))))
        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Stored screen context setting ({attr},({datablock.name},{_plural(attr)}))...')

    return snapshot

#@persistent
def set_screen_context(snapshot, win=None):
    """Apply *snapshot* (as produced by get_window_context) to *win*."""
    LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: After file reload, re-setting context...')
    win = win or bpy.context.window

    for attr, (name, collection) in snapshot:
        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: After file reload, re-setting {attr}, {name}, {collection} for window={win}...')
        datablock = getattr(bpy.data, collection, None)
        if datablock is None:
            continue
        datablock = datablock.get(name)
        if datablock is None:
            continue
        setattr(win, attr, datablock)


def top_level_excluded_collections(view_layer=None):
    """Return LayerCollections that are *explicitly* excluded and have no
    excluded ancestor i.e. the true “ancestor” exclusions."""
    if view_layer is None:
        view_layer = bpy.context.view_layer

    tops = []

    def visit(lc, parent_excluded=False):
        if lc.exclude:
            if not parent_excluded:
                tops.append(lc)        # first excluded in this branch
            parent_excluded = True     # descendants inherit exclusion
        for child in lc.children:
            visit(child, parent_excluded)

    visit(view_layer.layer_collection)
    return tops


def objects_under_excluded(view_layer=None, unique=True):
    """
    Return a list of all objects that belong to the *top-level* excluded
    LayerCollections of the given view-layer (recursively through children).

    Parameters
    ----------
    view_layer : bpy.types.ViewLayer or None
        Defaults to the active context view-layer.
    unique : bool
        If True (default) de-duplicates objects that live in multiple collections.

    Returns
    -------
    list[bpy.types.Object]
    """
    if view_layer is None:
        view_layer = bpy.context.view_layer

    tops = top_level_excluded_collections(view_layer)

    result = []
    seen   = set()       # used only when unique=True

    def walk_coll(coll):
        # 1) objects directly in this collection
        for obj in coll.objects:
            if not unique or obj.name not in seen:
                result.append(obj)
                seen.add(obj.name)
        # 2) recurse into child collections
        for child in coll.children:
            walk_coll(child)

    for lc in tops:
        walk_coll(lc.collection)

    return result


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
        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Dialog requested.')

        # Prepare list text
        try:
            items = json.loads(self.items_json)
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: items_json = {json.dumps(self.items_json)}')
        except Exception:
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Exception while loading JSON for display.')
            items = []
        self.items_text = "\n".join(str(i) for i in items)

        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Calling _suppress_cancel_label()')
        _suppress_cancel_label()  # hide “Cancel” *before* dialog is drawn
        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: _suppress_cancel_label() ended.')

        # Blender 4.x
        if hasattr(bpy.types.UILayout, "template_popup_confirm"):
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: mindow_manager available = {context.window_manager!=None}')
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


    # Here we prep for the execution
    def invoke(self, context, event):
        wm = context.window_manager

        # Ensure current file is saved & clean
        if not bpy.data.filepath:
            self.report({'ERROR'}, "Please save the current file before comparing.")
            return {'CANCELLED'}
        #if bpy.data.is_dirty:
        #    self.report({'ERROR'}, "Please save or revert changes before comparing.")
        #    return {'CANCELLED'}

        target = self._validate_path(wm.compare_filepath)
        if not target:
            return {'CANCELLED'}

        # Save screen context state for restore after file change
        snap = get_screen_context()

        # --- NOTE ---
        # The following function will change the main file. The effects of 
        # this and their required handling are explained below.
        #
        # - Python env is rebuilt (lost) and running operators and timers are
        #   cancelled.
        #   -> Subsequent execution requires registration of a @Persistent 
        #      decorated handler via load_post(). The handler will then be 
        #      able to pick up execution after the reload.
        #
        # - UI context is rebuilt and during that time will appear None. Therefore
        #   calls th eUI context will fail during that time.
        #   -> A timer bpy.app.timers.register() CAN be used to wait on the next UI tick,
        #      but it woudl NOT survive a subsequent/intervening reload.
        #   -> As above, use of load post will guarantee a working environment

        diff = blenddiff.diff_current_vs_other(target)

        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Got JSON diff:\n{json.dumps(diff, indent=2)}')
        if "error" in diff:
            self.report({'ERROR'}, f"Diff failed: {diff['error']} ({diff.get('stage')})")
            return {'CANCELLED'}

        # Restore Context
        def _restore_context():
            
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: After file reload, inspecting context...')

            if (not hasattr(bpy,"context")) or bpy.context is None:
                return 0.1
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: After file reload, got context...')

            if (not hasattr(bpy.context,"window")) or bpy.context.window is None:
                return 0.1
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: After file reload, got window...')

            win = bpy.context.window
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: After file reload, snap={snap}')
            set_screen_context(snap, win)

            return None  # unregister the timer

        # Restore the screen context state saved before file change.
        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: After file reload, bpy.context.window={bpy.context.window}')
        bpy.app.timers.register(_restore_context, first_interval=0.0)
        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: After file reload, context re-set.')

        # ------------------------------------------------------------------
        # Create report
        changed = diff.get("changed", {})
        added = diff.get("added",{})
        removed = diff.get("removed", {})

        # Report findings
        self.report({'INFO'}, f"Found a total of {len(changed)} changed, {len(added)} added, and {len(removed)} removed items in the file.")

        # ------------------------------------------------------------------
        # Highlight current scene's objects
        bpy.ops.object.select_all(action='DESELECT')

        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Current scene is {context.scene}.')
        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Current view_layer {context.view_layer}.')

        # Only objects can be selected
        changed_obj_names = changed.get("objects",{})
        added_obj_names   = added.get("objects",{})

        # Pare collections disabled in view layer
        candidate_objects = [object for object in context.scene.objects if object not in objects_under_excluded(context.view_layer)]
        for object in objects_under_excluded(context.view_layer):
            LOG.debug(f"Excluded object: {object.name}")
        for object in candidate_objects:
            LOG.debug(f"Candidate object: {object.name}")
        target_objects = [object for object in candidate_objects if object.name in (list(changed_obj_names.keys()) + list(added_obj_names.keys()))]
        for object in target_objects:
            LOG.debug(f"Target object: {object.name}")

        disabled_objects = []
        for object in target_objects:
            if getattr(object,"hide_select",False):
                disabled_objects.append(object)
            else:
                object.select_set(True)
    
        if disabled_objects:
            object_names = [object.name for object in disabled_objects]

            def _show_popup():
                LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Calling disabled items report dialog...')
                bpy.ops.okonly.dialog('INVOKE_DEFAULT', items_json=json.dumps(object_names))
                LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Reporting disabled objects... dialog call completed.')

                return None          # unregister the timer

            self.report({'WARNING'}, f"{len(object_names)} objects are disabled and could not be selected in the viewport.")
            bpy.app.timers.register(_show_popup, first_interval=0.0)

        return {'FINISHED'}

    def execute(self, context):
        # Optional: if you need to support 'Run' via search menu
        return self.invoke(context, None)

# --- Register ---
classes = (
    #VDIFF_PG_Properties,
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
