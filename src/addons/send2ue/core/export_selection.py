"""Explicit export units and source objects, independent of preview visibility.

An Export Empty may carry JSON ``send2ue_export_source_names``. The same
original objects are temporarily activated; copies and parallel meshes are not
created. ``scene['send2ue_export_unit_names']`` persists the default native-send
scope with the file. The same WindowManager key overrides it for one operation.
"""
import json
import bpy

_state = None
_rules = None
_collection_state = None
_visible_export_objects = None
_declared_hair_outputs = None


def keep_prepared_hair_in_mesh_export(selected, anchor, combined_parent=None):
    """An explicit combined Empty owns its cards; a common rig alone does not."""
    if not selected.get('_send2ue_hair_tool_temp'):
        return True
    source_key = '_send2ue_hair_tool_source_name'
    if selected.get(source_key, selected.name) == anchor.get(source_key, anchor.name):
        return True
    return (combined_parent is not None and combined_parent.type == 'EMPTY'
            and selected.parent == combined_parent)


def resolve_hair_outputs(sources, candidates, owner, is_hair, input_of, owner_of):
    """Resolve declared Hair Tool pipeline roots to their native final outputs.

    Surface -> profiled guide -> generated hair exports the generated hair;
    capture_source then takes its immediate profiled guide as simulation input.
    Follow actual Hair System Setup links, never names or hierarchy depth.
    Ordinary meshes and unrelated/other-unit children keep their exact scope.
    """
    pool = [obj for obj in candidates if owner_of(obj) == owner and is_hair(obj)]
    downstream = {}
    for obj in pool:
        upstream = input_of(obj)
        if upstream is not None:
            downstream.setdefault(upstream, []).append(obj)

    def terminals(obj, visiting):
        if obj in visiting:
            raise ValueError('Cyclic Hair Tool source pipeline: ' + obj.name)
        children = downstream.get(obj, []) if is_hair(obj) else []
        if not children:
            return {obj}
        result = set()
        for child in children:
            result.update(terminals(child, visiting | {obj}))
        return result

    result = set()
    for source in sources:
        result.update(terminals(source, set()))
    return result


def _requested_unit_names(units):
    key = 'send2ue_export_unit_names'
    wm = bpy.context.window_manager
    raw = wm[key] if key in wm else bpy.context.scene.get(key, '')
    return set(json.loads(raw)) if raw else set(units)


def _activate_export_collection(collection):
    """Reveal the explicit activation collection, not preview collections."""
    global _collection_state
    root = getattr(bpy.context.view_layer, 'layer_collection', None)
    paths = []
    def visit(layer, ancestors):
        path = ancestors + [layer]
        if layer.collection == collection:
            paths.append(path)
        for child in layer.children:
            visit(child, path)
    if root is not None:
        visit(root, [])
    layers = []
    for path in paths:
        for layer in path:
            if layer not in layers:
                layers.append(layer)
    _collection_state = [(layer, layer.exclude, layer.hide_viewport,
                          layer.collection.hide_viewport, layer.collection.hide_render)
                         for layer in layers]
    for layer in layers:
        layer.exclude = False
        layer.hide_viewport = False
        layer.collection.hide_viewport = False
        layer.collection.hide_render = False


def includes(obj):
    if _rules is None:
        return True
    from . import park_spline_export
    obj = park_spline_export.source_for_export(obj)
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


def original_visible_export_objects(collection):
    """Keep AO context stable when a partial send narrows the output set."""
    if _visible_export_objects is not None:
        return _visible_export_objects
    return tuple(obj for obj in collection.objects
                 if not (obj.hide_get() or obj.hide_viewport or obj.hide_render))


def declared_hair_outputs():
    """The terminal Hair Tool systems explicitly declared across Export units."""
    return _declared_hair_outputs or {}


def prepare():
    global _state, _rules, _visible_export_objects, _declared_hair_outputs
    if _state is not None:
        raise RuntimeError('An explicit export selection is already active')
    collection = bpy.data.collections.get('Export')
    if collection is None:
        return
    units = {o.name: o for o in collection.objects if o.type == 'EMPTY'}
    selected = _requested_unit_names(units)
    if selected - set(units):
        raise ValueError('Requested Export unit is missing: ' + str(sorted(selected - set(units))))
    def owner(obj):
        current = obj
        while current:
            if current.name in units and units[current.name] == current:
                return current.name
            current = current.parent
        return None

    declared = {}
    hair_outputs = {}
    for name, unit in units.items():
        if unit.get('send2ue_export_source_names'):
            try:
                names = json.loads(unit['send2ue_export_source_names'])
                objects = [bpy.data.objects.get(obj_name) for obj_name in names]
                if len(set(names)) != len(names) or any(o is None for o in objects):
                    raise ValueError('Explicit export source list is incomplete: ' + name)
                # Only native Hair Tool systems have an implicit downstream pipeline.
                native = any(
                    getattr(getattr(modifier, 'node_group', None), 'name', '').startswith('Hair_System_Setup')
                    for obj in objects for modifier in obj.modifiers
                )
                if native:
                    from . import hair_tool_export as hair
                    outputs = resolve_hair_outputs(objects, bpy.data.objects, name,
                        hair.is_hair_tool_object, hair._get_hair_tool_input_object, owner)
                    hair_outputs[unit] = {obj for obj in outputs if hair.is_hair_tool_object(obj)}
                else:
                    outputs = set(objects)
                if name in selected:
                    declared[name] = outputs
            except (TypeError, ValueError) as error:
                if name in selected:
                    raise
                print(f'[send2ue] Ignoring invalid unselected AO context for {name}: {error}')
    for name, objects in declared.items():
        if any(owner(o) != name for o in objects):
            raise ValueError('Explicit export source has a different Empty owner: ' + name)
    # The source mesh can be visible while its shared armature is hidden for
    # preview. The native exporter discovers rigs from visible Export objects;
    # omitting it creates a dummy skeleton and can drop sibling chain meshes.
    # Activate only rig dependencies of explicitly declared source meshes.
    dependencies = set()
    for objects in declared.values():
        for obj in objects:
            for modifier in getattr(obj, 'modifiers', ()):
                rig = getattr(modifier, 'object', None)
                if modifier.type == 'ARMATURE' and rig is not None:
                    dependencies.add(rig)
            parent = obj.parent
            while parent is not None:
                if parent.type == 'ARMATURE':
                    dependencies.add(parent)
                parent = parent.parent
    affected = (set(collection.objects) | set().union(*declared.values()) | dependencies) if declared else set(collection.objects)
    _state = [(o, collection in o.users_collection, o.hide_get(), o.hide_viewport, o.hide_render, o.hide_select) for o in affected]
    _rules = units, selected, declared
    _declared_hair_outputs = hair_outputs
    try:
        _activate_export_collection(collection)
        _visible_export_objects = original_visible_export_objects(collection)
        for obj in affected:
            name = owner(obj)
            if name and (name not in selected or (name in declared and obj != units[name] and obj not in declared[name])):
                if collection in obj.users_collection:
                    collection.objects.unlink(obj)
        for objects in [*declared.values(), dependencies]:
            for obj in objects:
                if collection not in obj.users_collection:
                    collection.objects.link(obj)
                obj.hide_viewport = False
                obj.hide_render = False
                obj.hide_select = False
                obj.hide_set(False)
        bpy.context.view_layer.update()
    except BaseException:
        cleanup()
        raise


def cleanup():
    global _state, _rules, _collection_state, _visible_export_objects, _declared_hair_outputs
    state, _state = _state, None
    _rules = None
    _visible_export_objects = None
    _declared_hair_outputs = None
    collection = bpy.data.collections.get('Export')
    for obj, linked, hidden, viewport, render, selectable in state or []:
        if linked and collection not in obj.users_collection:
            collection.objects.link(obj)
        elif not linked and collection in obj.users_collection:
            collection.objects.unlink(obj)
        obj.hide_viewport, obj.hide_render = viewport, render
        obj.hide_select = selectable
        obj.hide_set(hidden)
    collection_state, _collection_state = _collection_state, None
    for layer, excluded, hidden, viewport, render in reversed(collection_state or []):
        layer.hide_viewport = hidden
        layer.collection.hide_viewport = viewport
        layer.collection.hide_render = render
        layer.exclude = excluded
