"""Build a house Blueprint from its freshly imported mesh and existing assets.

Only Send2UE-owned house components are managed. Referenced source Blueprints,
their nested pivots, materials and meshes are read-only inputs.
"""

from contextlib import contextmanager
import hashlib
import json
import math
import re


OWNER_KEY = 'Send2UE.LinkedOpenings.Owner'
MANIFEST_KEY = 'Send2UE.LinkedOpenings.Manifest'
OWNER_VERSION = 'send2ue.linked_openings.v1:'
COMPONENT_TAG = 'send2ue_linked_opening:'
NESTED_OWNER_KEY = 'Send2UE.NestedPivots.Owner'
NESTED_MANIFEST_KEY = 'Send2UE.NestedPivots.Manifest'
NESTED_OWNER_VERSION = 'send2ue.nested_pivots.v1:'
_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_PATH = re.compile(r'^/(?:[A-Za-z0-9_]+/)+[A-Za-z0-9_]+$')
IDENTITY = dict(location=[0.0, 0.0, 0.0], rotation=[0.0, 0.0, 0.0, 1.0], scale=[1.0, 1.0, 1.0])


def _asset_path(value):
    if not isinstance(value, str):
        raise ValueError('Expected an absolute Unreal asset path.')
    package, separator, object_name = value.partition('.')
    if not _PATH.fullmatch(package) or (separator and object_name != package.rsplit('/', 1)[-1]):
        raise ValueError('Invalid Unreal asset path: ' + repr(value))
    return package


def _numbers(value, size, field):
    if not isinstance(value, (tuple, list)) or len(value) != size:
        raise ValueError(field + ' has an invalid length.')
    if any(isinstance(n, bool) or not isinstance(n, (float, int)) or not math.isfinite(n) for n in value):
        raise ValueError(field + ' must contain finite numbers.')
    return [float(n) for n in value]


def validate_assembly(value):
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError('Unsupported linked-opening assembly schema.')
    root = value.get('root')
    if not isinstance(root, str) or not _NAME.fullmatch(root):
        raise ValueError('House root must be an export-safe identifier.')
    assembly_id = value.get('assembly_id')
    if not isinstance(assembly_id, str) or not assembly_id:
        raise ValueError('A stable assembly_id is required.')
    root_path = _asset_path(value.get('root_mesh_asset_path'))
    destination = root_path.rsplit('/', 1)[0] + '/bc_' + root
    if destination == root_path:
        raise ValueError('House Blueprint destination collides with its mesh.')
    placements = value.get('placements')
    if not isinstance(placements, list):
        raise ValueError('Assembly placements must be a list.')
    normalized, identifiers = [], set()
    for entry in placements:
        if not isinstance(entry, dict):
            raise ValueError('Each placement must be a dictionary.')
        identifier = entry.get('instance_id')
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError('Each placement requires a unique stable instance_id.')
        identifiers.add(identifier)
        pivot = entry.get('source_pivot')
        if not isinstance(pivot, str) or not _NAME.fullmatch(pivot):
            raise ValueError('Source pivot must be an export-safe identifier.')
        required = entry.get('source_requires_blueprint', False)
        if type(required) is not bool:
            raise ValueError('source_requires_blueprint must be a boolean.')
        row = dict(instance_id=identifier, source_pivot=pivot,
                   source_blend=str(entry.get('source_blend') or ''), source_requires_blueprint=required,
                   asset_path=_asset_path(entry['asset_path']) if entry.get('asset_path') else '')
        for field, size in (('location', 3), ('rotation', 4), ('scale', 3)):
            row[field] = _numbers(entry.get(field), size, field)
        norm = math.sqrt(sum(n * n for n in row['rotation']))
        if abs(norm - 1.0) > 1e-3:
            raise ValueError('Placement quaternion must have unit length.')
        row['rotation'] = [n / norm for n in row['rotation']]
        if any(abs(n) < 1e-8 for n in row['scale']):
            raise ValueError('Placement scale must be invertible.')
        normalized.append(row)
    return dict(schema_version=1, root=root, root_mesh_asset_path=root_path,
                blueprint_asset_path=destination, assembly_id=assembly_id,
                source_blend=str(value.get('source_blend') or ''), placements=normalized)


def _exact_paths(unreal, name, class_name):
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    rows = registry.get_assets_by_class(unreal.TopLevelAssetPath('/Script/Engine', class_name), True)
    return sorted({str(row.package_name) for row in rows if str(row.asset_name) == name})


def _check_source_blueprint(unreal, blueprint, source_pivot):
    owner = str(unreal.EditorAssetLibrary.get_metadata_tag(blueprint, NESTED_OWNER_KEY))
    try:
        manifest = json.loads(str(unreal.EditorAssetLibrary.get_metadata_tag(blueprint, NESTED_MANIFEST_KEY)))
    except (ValueError, TypeError) as error:
        raise RuntimeError('Source Blueprint is missing its native nested-pivot manifest: ' + blueprint.get_path_name()) from error
    components = manifest.get('components', []) if isinstance(manifest, dict) else []
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1
            or manifest.get('root') != source_pivot or not isinstance(components, list) or not components
            or not isinstance(components[0], dict) or components[0].get('name') != source_pivot
            or owner != NESTED_OWNER_VERSION + str(components[0].get('mesh_asset_path', ''))):
        raise RuntimeError('Source Blueprint is not owned by the requested Send2UE export pivot: ' + blueprint.get_path_name())
    source_class = unreal.EditorAssetLibrary.load_blueprint_class(blueprint.get_path_name())
    if source_class is None:
        raise RuntimeError('Cannot load source Blueprint class: ' + blueprint.get_path_name())
    return dict(kind='BLUEPRINT', asset=blueprint, source_class=source_class,
                asset_path=_asset_path(blueprint.get_path_name()))


def resolve_source(unreal, specification):
    """Resolve one explicit mapping or unique exact native asset name."""
    pivot = specification['source_pivot']
    if specification.get('asset_path'):
        path = specification['asset_path']
    else:
        candidates = _exact_paths(unreal, 'bc_' + pivot, 'Blueprint')
        if len(candidates) > 1:
            raise RuntimeError('Multiple source Blueprints match ' + pivot + '; set its exact Unreal asset path.')
        if candidates:
            path = candidates[0]
        else:
            if specification['source_requires_blueprint']:
                raise RuntimeError('Send the source file first; its native bc_' + pivot + ' Blueprint is missing.')
            candidates = _exact_paths(unreal, pivot, 'StaticMesh')
            if len(candidates) != 1:
                raise RuntimeError('Expected one source StaticMesh for ' + pivot + '; send it first or set its exact Unreal asset path.')
            path = candidates[0]
    asset = unreal.load_asset(path)
    if isinstance(asset, unreal.Blueprint):
        return _check_source_blueprint(unreal, asset, pivot)
    if isinstance(asset, unreal.StaticMesh) and not specification['source_requires_blueprint']:
        return dict(kind='MESH', asset=asset, asset_path=_asset_path(path))
    raise RuntimeError('Source asset is missing or does not preserve its required export pivots: ' + path)


def _parent_object(unreal, data, blueprint):
    library = unreal.SubobjectDataBlueprintFunctionLibrary
    handle = library.get_parent_handle(data)
    if not library.is_handle_valid(handle):
        return None
    return library.get_object_for_blueprint(library.get_data(handle), blueprint)


def _gather(unreal, subsystem, blueprint):
    library = unreal.SubobjectDataBlueprintFunctionLibrary
    context, rows, generated, seen = None, [], {}, {}
    for handle in subsystem.k2_gather_subobject_data_for_blueprint(blueprint):
        data = library.get_data(handle)
        if library.is_root_actor(data):
            context = handle
        if not library.is_component(data):
            continue
        obj = library.get_object_for_blueprint(data, blueprint)
        if obj is None:
            raise RuntimeError('Cannot obtain Blueprint component template.')
        path, parent = obj.get_path_name(), _parent_object(unreal, data, blueprint)
        if path in seen:
            if seen[path] != parent:
                raise RuntimeError('Blueprint component appears under multiple parents.')
            continue
        seen[path] = parent
        identifiers = [str(tag)[len(COMPONENT_TAG):] for tag in obj.get_editor_property('component_tags')
                       if str(tag).startswith(COMPONENT_TAG)]
        if len(identifiers) > 1:
            raise RuntimeError('Ambiguous linked-opening component ownership.')
        identifier = identifiers[0] if identifiers else None
        row = dict(handle=handle, data=data, object=obj, parent_object=parent,
                   identifier=identifier, variable=str(library.get_variable_name(data)))
        rows.append(row)
        if identifier is not None:
            if identifier in generated or not isinstance(obj, unreal.SceneComponent):
                raise RuntimeError('Invalid or duplicate linked-opening component: ' + identifier)
            if library.is_inherited_component(data) or library.is_native_component(data):
                raise RuntimeError('Cannot edit inherited or native assembly components.')
            generated[identifier] = row
    if context is None:
        raise RuntimeError('Blueprint actor context is missing.')
    return context, rows, generated


def _component_specs(unreal, assembly, root_mesh, sources):
    specs = [dict(identifier='root', name=assembly['root'], parent=None,
                  component_class=unreal.StaticMeshComponent, mesh=root_mesh, **IDENTITY)]
    for placement in assembly['placements']:
        key = placement['instance_id']
        suffix = hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]
        name = placement['source_pivot'][:40] + '_' + suffix
        source = sources[key]
        specs.append(dict(identifier='pivot:' + key, name=name + '_Pivot', parent='root',
                          component_class=unreal.SceneComponent,
                          **{field: placement[field] for field in IDENTITY}))
        visual = dict(identifier='visual:' + key, name=name + '_Asset', parent='pivot:' + key, **IDENTITY)
        if source['kind'] == 'BLUEPRINT':
            visual.update(component_class=unreal.ChildActorComponent, source_class=source['source_class'])
        else:
            visual.update(component_class=unreal.StaticMeshComponent, mesh=source['asset'])
        specs.append(visual)
    if len({row['name'].casefold() for row in specs}) != len(specs):
        raise ValueError('Assembly component names collide.')
    return specs


def _prepare(unreal, subsystem, assembly):
    mesh = unreal.load_asset(assembly['root_mesh_asset_path'])
    if not isinstance(mesh, unreal.StaticMesh):
        raise RuntimeError('Imported house mesh is missing: ' + assembly['root_mesh_asset_path'])
    sources, cache = {}, {}
    for placement in assembly['placements']:
        key = (placement['source_pivot'], placement['asset_path'], placement['source_requires_blueprint'])
        if key not in cache:
            cache[key] = resolve_source(unreal, placement)
        sources[placement['instance_id']] = cache[key]
    specs = _component_specs(unreal, assembly, mesh, sources)
    path = assembly['blueprint_asset_path']
    owner = OWNER_VERSION + assembly['assembly_id'] + ':' + assembly['root_mesh_asset_path']
    blueprint = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        if not isinstance(blueprint, unreal.Blueprint) or unreal.EditorAssetLibrary.get_metadata_tag(blueprint, OWNER_KEY) != owner:
            raise RuntimeError('Refusing to overwrite a Blueprint not owned by this linked assembly: ' + path)
        _, rows, generated = _gather(unreal, subsystem, blueprint)
        wanted = {spec['identifier']: spec for spec in specs}
        names = {spec['name'].casefold() for spec in specs}
        stale_objects = [row['object'] for key, row in generated.items() if key not in wanted]
        for row in rows:
            if row['identifier'] is None and row['variable'].casefold() in names:
                raise RuntimeError('Assembly component name collides with a user component: ' + row['variable'])
            if row['identifier'] is None and row['parent_object'] in stale_objects:
                raise RuntimeError('A removed placement has user components; detach those before reimport.')
        for key in generated.keys() & wanted.keys():
            if not isinstance(generated[key]['object'], wanted[key]['component_class']):
                raise RuntimeError('A placement changed component type; remove its old assembly instance before reimport: ' + key)
        library = unreal.SubobjectDataBlueprintFunctionLibrary
        roots = [row for row in rows if library.is_root_component(row['data'])]
        if len(roots) != 1 or roots[0]['identifier'] != 'root':
            raise RuntimeError('Assembly root was changed; refusing to replace user hierarchy.')
    return dict(blueprint=blueprint, owner=owner, specs=specs, sources=sources)


def _set_component(unreal, component, specification):
    component.modify()
    if 'mesh' in specification:
        if component.get_editor_property('static_mesh') != specification['mesh']:
            component.set_static_mesh(specification['mesh'])
    if 'source_class' in specification:
        component.set_child_actor_class(specification['source_class'])
    for prop in ('absolute_location', 'absolute_rotation', 'absolute_scale'):
        component.set_editor_property(prop, False)
    component.set_editor_property('relative_location', unreal.Vector(*specification['location']))
    component.set_editor_property('relative_rotation', unreal.Quat(*specification['rotation']).rotator())
    component.set_editor_property('relative_scale3d', unreal.Vector(*specification['scale']))


def _configure_house_collision(unreal, mesh, expected_path):
    """Keep the evaluated wall openings traversable on the imported house only."""
    if (not isinstance(mesh, unreal.StaticMesh)
            or _asset_path(mesh.get_path_name()) != _asset_path(expected_path)):
        raise RuntimeError('Collision setup must target the exact imported house mesh.')
    body = mesh.get_editor_property('body_setup')
    if body is None:
        raise RuntimeError('Imported house mesh has no BodySetup for opening collision: ' + expected_path)
    desired = unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE
    if body.get_editor_property('collision_trace_flag') == desired:
        return False
    mesh.modify()
    body.modify()
    body.set_editor_property('collision_trace_flag', desired)
    if body.get_editor_property('collision_trace_flag') != desired:
        raise RuntimeError('Could not configure collision around the house openings: ' + expected_path)
    if not unreal.EditorAssetLibrary.save_loaded_asset(mesh):
        raise RuntimeError('Could not save the house opening collision: ' + expected_path)
    return True


def _verify(unreal, subsystem, blueprint, specs):
    _, _, generated = _gather(unreal, subsystem, blueprint)
    if set(generated) != {spec['identifier'] for spec in specs}:
        raise RuntimeError('Generated component set does not match linked-opening intent.')
    for spec in specs:
        row = generated[spec['identifier']]
        obj = row['object']
        if not isinstance(obj, spec['component_class']):
            raise RuntimeError('Assembly component class mismatch.')
        if spec['parent'] is None:
            if not unreal.SubobjectDataBlueprintFunctionLibrary.is_root_component(row['data']):
                raise RuntimeError('House export pivot is not the Blueprint root.')
        elif row['parent_object'] != generated[spec['parent']]['object']:
            raise RuntimeError('Linked source pivot hierarchy mismatch.')
        if 'mesh' in spec and obj.get_editor_property('static_mesh') != spec['mesh']:
            raise RuntimeError('Assembly mesh reference mismatch.')
        if 'source_class' in spec and obj.get_editor_property('child_actor_class') != spec['source_class']:
            raise RuntimeError('Assembly source Blueprint reference mismatch.')
        for property_name, field in (('relative_location', 'location'), ('relative_scale3d', 'scale')):
            actual = obj.get_editor_property(property_name)
            if any(not math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-4)
                   for a, b in zip((actual.x, actual.y, actual.z), spec[field])):
                raise RuntimeError('Assembly ' + field + ' mismatch.')
        actual = obj.get_editor_property('relative_rotation').quaternion()
        dot = sum(a * b for a, b in zip((actual.x, actual.y, actual.z, actual.w), spec['rotation']))
        if abs(abs(dot) - 1.0) > 1e-5:
            raise RuntimeError('Assembly rotation mismatch.')


@contextmanager
def _cleanup_new(unreal, blueprint, path, created):
    try:
        yield
    except Exception as error:
        if created:
            loaded = unreal.load_asset(path)
            if loaded != blueprint or not unreal.EditorAssetLibrary.delete_loaded_asset(blueprint):
                raise RuntimeError('Assembly failed and its new Blueprint could not be cleaned up: ' + path) from error
        raise


def apply_assembly(value):
    """Run only after the native import guard confirms the exact house result."""
    import unreal

    assembly = validate_assembly(value)
    subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    prepared = _prepare(unreal, subsystem, assembly)
    collision_updated = _configure_house_collision(
        unreal, prepared['specs'][0]['mesh'], assembly['root_mesh_asset_path'])
    blueprint = prepared['blueprint']
    created = blueprint is None
    path = assembly['blueprint_asset_path']
    if created:
        factory = unreal.BlueprintFactory()
        factory.set_editor_property('parent_class', unreal.Actor)
        blueprint = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            'bc_' + assembly['root'], path.rsplit('/', 1)[0], unreal.Blueprint, factory)
        if blueprint is None:
            raise RuntimeError('Could not create house Blueprint: ' + path)
    with _cleanup_new(unreal, blueprint, path, created), unreal.ScopedEditorTransaction('Send2UE: linked-opening assembly'):
        blueprint.modify()
        context, rows, generated = _gather(unreal, subsystem, blueprint)
        library = unreal.SubobjectDataBlueprintFunctionLibrary
        for spec in prepared['specs']:
            identifier, parent = spec['identifier'], spec['parent']
            row = generated.get(identifier)
            if row is None:
                if parent is None:
                    roots = [entry for entry in rows if library.is_root_component(entry['data'])]
                    parent_handle = roots[0]['handle'] if roots else context
                else:
                    parent_handle = generated[parent]['handle']
                handle, reason = subsystem.add_new_subobject(unreal.AddNewSubobjectParams(
                    parent_handle=parent_handle, new_class=spec['component_class'], blueprint_context=blueprint))
                if not reason.is_empty():
                    raise RuntimeError('Cannot add assembly component: ' + str(reason))
                if not subsystem.rename_subobject(handle, unreal.Text(spec['name'])):
                    raise RuntimeError('Cannot name assembly component: ' + spec['name'])
                obj = library.get_object_for_blueprint(library.get_data(handle), blueprint)
                obj.set_editor_property('component_tags', [unreal.Name(COMPONENT_TAG + identifier)])
                unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
                context, rows, generated = _gather(unreal, subsystem, blueprint)
                row = generated[identifier]
            if parent is None:
                if not library.is_root_component(library.get_data(row['handle'])):
                    if not subsystem.make_new_scene_root(context, row['handle'], blueprint):
                        raise RuntimeError('Cannot set the house export pivot as root.')
            elif not subsystem.attach_subobject(generated[parent]['handle'], row['handle']):
                raise RuntimeError('Cannot attach linked source pivot: ' + spec['name'])
            context, rows, generated = _gather(unreal, subsystem, blueprint)
        wanted = {spec['identifier'] for spec in prepared['specs']}
        stale = [row['handle'] for key, row in generated.items() if key not in wanted]
        if stale and subsystem.delete_subobjects(context, stale, blueprint) != len(stale):
            raise RuntimeError('Could not remove obsolete assembly components.')
        _, _, generated = _gather(unreal, subsystem, blueprint)
        for spec in prepared['specs']:
            _set_component(unreal, generated[spec['identifier']]['object'], spec)
        unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
        if blueprint.get_editor_property('status') not in (
                unreal.BlueprintStatus.BS_UP_TO_DATE, unreal.BlueprintStatus.BS_UP_TO_DATE_WITH_WARNINGS):
            raise RuntimeError('House Blueprint compilation failed: ' + path)
        _verify(unreal, subsystem, blueprint, prepared['specs'])
        unreal.EditorAssetLibrary.set_metadata_tag(blueprint, OWNER_KEY, prepared['owner'])
        unreal.EditorAssetLibrary.set_metadata_tag(blueprint, MANIFEST_KEY, json.dumps(assembly, sort_keys=True))
        if not unreal.EditorAssetLibrary.save_loaded_asset(blueprint):
            raise RuntimeError('Could not save house Blueprint: ' + path)
    return dict(blueprint_asset_path=path, created=created, verified=True,
                house_collision_updated=collision_updated,
                placement_count=len(assembly['placements']),
                source_assets={key: source['asset_path'] for key, source in prepared['sources'].items()})
