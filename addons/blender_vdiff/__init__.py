bl_info = {
    "name": "vDiff",
    "author": "Chris Margach",
    "description": "Visual Diff for Blender",
    "blender": (4, 00, 0),
    "version": (0, 0, 1),
    "category": "System",
    "location": "3D Viewport -> Sidebar-> vDiff",
    "description": "Compare current scene to another .blend file and highlight new and modified objects.",
    "warning": "",
    "doc_url": "",
    "tracker_url": ""
}

import os, sys, logging, pathlib, json

from typing import Generator

import bpy
from bpy.props import StringProperty, CollectionProperty
from bpy.types import AddonPreferences, Panel, Operator, OperatorFileListElement, PropertyGroup, Window
from bpy.app import translations, timers
from bpy.app.handlers import persistent

from bpy_extras.io_utils import ImportHelper

from .src.blenddiff import BlendDiff

# Configure root logging once, replacing any prior handlers.
log_dir = pathlib.Path(bpy.utils.user_resource('CONFIG', path='vdiff_logs', create=True))
log_file = log_dir / "vdiff.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),             # terminal / system console
        logging.FileHandler(log_file, encoding="utf-8")  # always available
    ],
    force=True 
)

LOG = logging.getLogger(__name__)


#######################
# --- Preferences --- #
#######################

def _addon_key():
    key = __package__ or __name__.split('.')[0]
    LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Add-on key is {key}')

    return key

def _prefs():
    ad = bpy.context.preferences.addons.get(_addon_key())
    LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Add-on preferences: {ad}')
    return getattr(ad, "preferences", None)


class VDIFF_Preferences(AddonPreferences):
    bl_idname = _addon_key()

    sticky_compare_path: StringProperty(
        name="Last compare .blend",
        subtype='FILE_PATH',
        default="",
    )

    def draw(self, context):
        self.layout.prop(self, "sticky_compare_path")


def _save_prefs_once():
    try:
        bpy.ops.wm.save_userpref()
    except Exception:
        pass
    return None  # stop the timer


def _on_compare_path_update(self, context):
# Called when the compare_filepath StringProperty object is updated.
    p = _prefs()
    if p:
        p.sticky_compare_path = self.compare_filepath
        # Debounce: save a moment later so we don't call ops inside RNA update
        timers.register(_save_prefs_once, first_interval=0.2)
        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Updated sticky_compare_path to \'{p.sticky_compare_path}\'')
    else:
        LOG.warning(f'{__name__}.{sys._getframe(0).f_code.co_name}: No preferences found, cannot update sticky_compare_path.')


@persistent
def _restore_compare_after_load(_):
    """Update the volatile compare_filepath WM property after loading a .blend file."""
    p = _prefs()
    if p and p.sticky_compare_path:
        bpy.context.window_manager.compare_filepath = p.sticky_compare_path
    else:
        #Use the current (if available) file's location as default
        bpy.context.window_manager.compare_filepath = os.path.join(os.path.dirname(bpy.data.filepath), '')


############################
# --- Helper functions --- #
############################
_PLURALS = {
    "mesh": "meshes",
}

def _plural(attr: str) -> str:
    # Return the corresponding collection name inside bpy.data
    return _PLURALS.get(attr, attr + "s")


def is_main_window(win:Window) -> bool:
    """Check if the given window is the main Blender window."""
    # A "main window" is one that has a menu bar and screen selection bar. 
    # However, these cannot be inspected arbitrarily from python, so we use a heuristic: 
    #  > check the name of the screen. 
    # For non-main windows, the screen is not one of the default ones, but a temporary one
    # and the name starts with "temp".
    # This is not 100% reliable, but works in practice.

    screen = getattr(win, "screen", None)
    screen_name = getattr(screen, "name", "")

    return screen_name is not None and not screen_name.startswith("temp")


def main_window_generator()-> Generator[Window, None, None]:
    yield from (win for win in bpy.context.window_manager.windows if is_main_window(win))


#################################
# --- Window state handling --- #
#################################
def get_window_state(win:Window, attr_names=("scene", "workspace", "view_layer")):
    """Return a list describing the properties of the datablocks holding each window attribute."""

    snapshot = []
    for attr_name in attr_names:
        datablock = getattr(win, attr_name, None)
        if datablock is None:
            continue                                # skip if attr missing (rare)
        snapshot.append((attr_name, (datablock.name, _plural(attr_name))))
        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Stored screen context setting ({attr_name},({datablock.name},{_plural(attr_name)}))...')

    return snapshot


def set_window_state(win:Window, snapshot):
    """Apply snapshot, as produced by get_screen_context(), to win."""

    scene_obj = None  # cache the scene object for use with view layers
    
    LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: After file reload, re-setting context...')
    for attr, (name, collection) in snapshot:
        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Re-setting {attr}, {name}, {collection} for window={win}...')
        if attr == "view_layer":
            if scene_obj is None:
                LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Skipping view_layer because scene not yet set.')
                continue
            view_layer = scene_obj.view_layers.get(name)
            if view_layer:
                setattr(win, attr, view_layer)
            else:
                LOG.warning(f'{__name__}.{sys._getframe(0).f_code.co_name}: View layer {name} not found in scene {scene_obj.name}.')
        else:
            datablock_collection = getattr(bpy.data, collection, None)
            if not datablock_collection:
                LOG.warning(f'{__name__}.{sys._getframe(0).f_code.co_name}: Datablock collection {collection} not found.')
                continue
            obj = datablock_collection.get(name)
            if obj:
                setattr(win, attr, obj)
                if attr == "scene":
                    scene_obj = obj
            else:
                LOG.warning(f'{__name__}.{sys._getframe(0).f_code.co_name}: {attr} named {name} not found in {collection}.')


###########################
# --- Object Handling --- #
###########################
def resolve_snapshot_names_to_objects(win:Window, snapshot):
    """Resolve a snapshot list of names into a dict of actual bpy.data objects."""
    # NOTE: Requires override of context.window to work properly.

    resolved = {}
    scene = None

    # First, resolve all regular items and save a reference to the scene if present
    for typename, (name, plural) in snapshot:
        if typename == "view_layer":
            continue  # defer view_layer handling
        collection = getattr(bpy.data, plural, None)
        if collection is None:
            continue
        obj = collection.get(name)
        if obj:
            resolved[typename] = obj
            if typename == "scene":
                scene = obj

    # Handle view_layer specially
    for typename, (name, plural) in snapshot:
        if typename != "view_layer":
            continue
        if scene is None:
            raise ValueError("Cannot resolve view_layer: scene not present in snapshot.")
        view_layer = scene.view_layers.get(name)
        if view_layer:
            resolved[typename] = view_layer

    return resolved


def top_level_excluded_collections(view_layer=None):
    """Return LayerCollections that are *explicitly* excluded and have no
    excluded ancestor i.e. the true “ancestor” exclusions."""

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


def objects_under_excluded_colls(view_layer=None, unique=True):
    """
    Return a list of all objects that belong to the *top-level* excluded
    LayerCollections of the given view-layer (recursively through children).

    Parameters
    ----------
    view_layer : bpy.types.ViewLayer
    unique : bool
        If True (default) de-duplicates objects that live in multiple collections.

    Returns
    -------
    list[bpy.types.Object]
    """

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

######################
# --- UI Classes --- #
######################
class VDIFF_OT_BrowseBlend(Operator, ImportHelper):
    """Operator to browse and select a .blend file for comparison."""
    bl_idname  = "vdiff.browse_blend"
    bl_label   = "Choose .blend"
    bl_options = {'INTERNAL'}

    # Restrict the visible/choosable files
    filename_ext = ".blend"
    filter_glob: StringProperty(default="*.blend", options={'HIDDEN'})

    # Allow multi-select in future:
    #files: CollectionProperty(type=OperatorFileListElement)

    def execute(self, context):
        # Guard in case user types a path manually
        fp = self.filepath
        if not fp.lower().endswith(".blend"):
            self.report({'ERROR'}, "Please select a .blend file")
            return {'CANCELLED'}
        
        context.window_manager.compare_filepath = fp

        return {'FINISHED'}
    

class VDIFF_PT_MainPanel(Panel):
    """Panel to display the vDiff UI in the 3D Viewport Sidebar."""
    bl_label       = "Blender vDiff"
    bl_idname      = "VDIFF_PT_main"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'vDiff'

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager

        row = layout.row(align=True)
        path_box = row.row(align=True)
        path_box.enabled = False
        path_box.prop(wm, "compare_filepath", text="File to compare")
        row.operator("vdiff.browse_blend", text="", icon='FILEBROWSER')

        layout.operator("vdiff.compare", icon='VIEWZOOM')


######################
# --- UI Helpers --- #
######################
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


#############################
# --- Operator Classses --- #
#############################
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

        ### MULTI-WINDOW HANDLING ###
        # Save window state(s) for restore after file change

        window_state_snapshots = []
        for win in main_window_generator():
            window_state_snapshots += [(win,get_window_state(win))]

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
        #   calls to the UI context will fail during that time.
        #   -> A timer bpy.app.timers.register() CAN be used to wait on the next UI tick,
        #      but it would NOT survive a subsequent/intervening reload.
        #   -> As above, use of load post will guarantee a working environment
        diff = BD.diff_current_vs_other(target)

        LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Got JSON diff:\n{json.dumps(diff, indent=2)}')
        if "error" in diff:
            self.report({'ERROR'}, f"Diff failed: {diff['error']} ({diff.get('stage')})")
            return {'CANCELLED'}

        #######################
        ### Restore Context ###
        #######################
        # We need to register a method that will be called when the UI context is restored
        # after the original file is re-loaded. Register this AFTER reloading the first file so that
        # it does not fire when the other file is temporarily loaded.
        #
        # NOTE: The timer can return an interval to be called again, or None to unregister itself.
        def _call_set_context():
            
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: After file reload, inspecting context...')
            if (not hasattr(bpy,"context")) or bpy.context is None:
                return 0.1
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: After file reload, got context...')
            if (not hasattr(bpy.context,"window")) or bpy.context.window is None:
                return 0.1
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: After file reload, got window(s)...')

            ## Restore state of all main windows
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Restoring state for {len(window_state_snapshots)} main windows...')
            for snapshot in window_state_snapshots:
                set_window_state(snapshot[0], snapshot[1])
                LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Restored')

            return None  # unregister the timer

        timers.register(_call_set_context, first_interval=0.0)

        # -- Get diff content from JSON --#
        diff_changed = diff.get("changed", {})
        diff_added = diff.get("added",{})
        removed = diff.get("removed", {})
        # Report findings
        self.report({'INFO'}, f"Found a total of {len(diff_changed)} changed, {len(diff_added)} added, and {len(removed)} removed items in the file.")

        # -- Select Objects -- #
        # For each main window, select the changed and added objects. 
        # The snapshots we took before reloading the file contain the relevant scene and 
        # view layer. These will be used to select the objects in the viewport.
        #
        # NOTE: Since re-setting the scene will run async, we will need to manually extract the 
        # scene and view layer objects by name from bpy.data
        for snapshot in window_state_snapshots:
            win = snapshot[0]
            state = snapshot[1]
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: Using window={win} to resolve screen context...')

            window_attr_objs = resolve_snapshot_names_to_objects(win, state)

            target_scene        = window_attr_objs.get("scene")
            target_view_layer   = window_attr_objs.get("view_layer")
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: From screen context resolve, target_scene={target_scene}')
            LOG.debug(f'{__name__}.{sys._getframe(0).f_code.co_name}: From screen context resolve, target_vl={target_view_layer}')

            # Only objects can be selected
            changed_obj_names = diff_changed.get("objects",{}) # This has all such objects independent of scene
            added_obj_names   = diff_added.get("objects",{})   # This has all such objects independent of scene

            # Pare collections disabled in the selected current view layer
            candidate_objects = [object for object in target_scene.objects if object not in objects_under_excluded_colls(target_view_layer)]
            target_objects = [object for object in candidate_objects if object.name in (list(changed_obj_names.keys()) + list(added_obj_names.keys()))]

            if LOG.isEnabledFor(logging.DEBUG):
                for object in objects_under_excluded_colls(target_view_layer):
                    LOG.debug(f"Excluded object: {object.name}")
                for object in candidate_objects:
                    LOG.debug(f"Candidate object: {object.name}")
                for object in target_objects:
                    LOG.debug(f"Target object: {object.name}")

            with bpy.context.temp_override(window=win):
                bpy.ops.object.select_all(action='DESELECT')

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
        # Supports 'Run' via search menu
        return self.invoke(context, None)

# --- Register ---
classes = (
    VDIFF_Preferences,
    VDIFF_OT_BrowseBlend,
    VDIFF_PT_MainPanel,
    VDIFF_OT_confirm,
    VDIFF_OT_dialog,
    VDIFF_OT_Compare,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    # The visual (volatile) compare filepath stored in the WindowManager
    bpy.types.WindowManager.compare_filepath = StringProperty(
      name="File to compare",
      description="Path to the .blend file to compare with",
      subtype='NONE',
      default="",
      update=_on_compare_path_update,
    )

    # Prime WM value from prefs immediately (covers first enable & each restart)
    p = _prefs()
    if p and p.sticky_compare_path:
        bpy.context.window_manager.compare_filepath = p.sticky_compare_path

    h = bpy.app.handlers.load_post
    if _restore_compare_after_load in h:
        h.remove(_restore_compare_after_load)
    h.append(_restore_compare_after_load)

    global BD
    BD = BlendDiff()


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.compare_filepath

    h = bpy.app.handlers.load_post
    if _restore_compare_after_load in h:
        h.remove(_restore_compare_after_load)

    # Remove WM prop
    if hasattr(bpy.types.WindowManager, "compare_filepath"):
        del bpy.types.WindowManager.compare_filepath

    global BD
    BD = None

if __name__ == "__main__":
    register()
