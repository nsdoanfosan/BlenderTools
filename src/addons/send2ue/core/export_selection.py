"""Explicit export units and source objects, independent of preview visibility.

An Export Empty may carry JSON ``send2ue_export_source_names``. The same
original objects are temporarily activated; copies and parallel meshes are not
created. ``wm['send2ue_export_unit_names']`` optionally scopes a native send.
"""
import json
import bpy

_state = None
_rules = None


def includes(obj):
    if _rules is None:
        return True
    units, selected, declared = _rules
    if obj.get('_send2ue_hair_tool_temp'):
        return True  # These were generated only from already admitted sources.
    current = obj.get('_htue_export_target') or obj
    while current:
        if current.name in units and units[current.name] == current:
            name = current.name
            return name in selected and (name not in declared or obj == current or obj in declared[name])
        current = current.parent
    return True  # Shared rig/dependency, rather than an export unit.


def prepare():
    global _state, _rules
    if _state is not None:
        raise RuntimeError('An explicit export selection is already active')
    collection = bpy.data.collections.get('Export')
    if collection is None:
        return
    units = {o.name: o for o in collection.objects if o.type == 'EMPTY'}
    raw = bpy.context.window_manager.get('send2ue_export_unit_names', '')
    selected = set(json.loads(raw)) if raw else set(units)
    if selected - set(units):
        raise ValueError('Requested Export unit is missing: ' + str(sorted(selected - set(units))))
    declared = {}
    for name in selected:
        unit = units[name]
        if unit.get('send2ue_export_source_names'):
            names = json.loads(unit['send2ue_export_source_names'])
            objects = [bpy.data.objects.get(name) for name in names]
            if len(set(names)) != len(names) or any(o is None for o in objects):
                raise ValueError('Explicit export source list is incomplete: ' + name)
            declared[name] = set(objects)
    def owner(obj):
        current = obj
        while current:
            if current.name in units and units[current.name] == current:
                return current.name
            current = current.parent
        return None
    for name, objects in declared.items():
        if any(owner(o) != name for o in objects):
            raise ValueError('Explicit export source has a different Empty owner: ' + name)
    affected = set(collection.objects) | set().union(*declared.values()) if declared else set(collection.objects)
    _state = [(o, collection in o.users_collection, o.hide_get(), o.hide_viewport, o.hide_render) for o in affected]
    _rules = units, selected, declared
    try:
        for obj in affected:
            name = owner(obj)
            if name and (name not in selected or (name in declared and obj != units[name] and obj not in declared[name])):
                if collection in obj.users_collection:
                    collection.objects.unlink(obj)
        for objects in declared.values():
            for obj in objects:
                if collection not in obj.users_collection:
                    collection.objects.link(obj)
                obj.hide_viewport = False
                obj.hide_render = False
                obj.hide_set(False)
        bpy.context.view_layer.update()
    except BaseException:
        cleanup()
        raise


def cleanup():
    global _state, _rules
    state, _state = _state, None
    _rules = None
    collection = bpy.data.collections.get('Export')
    for obj, linked, hidden, viewport, render in state or []:
        if linked and collection not in obj.users_collection:
            collection.objects.link(obj)
        elif not linked and collection in obj.users_collection:
            collection.objects.unlink(obj)
        obj.hide_viewport, obj.hide_render = viewport, render
        obj.hide_set(hidden)
