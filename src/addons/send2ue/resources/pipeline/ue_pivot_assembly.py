"""Build explicitly requested nested export-pivot assemblies in Unreal Editor.

This module never discovers assemblies from asset names, edits meshes/materials,
or runs on import. The caller must provide a versioned manifest after every mesh
in it was imported successfully. Transforms are local to the direct parent in
Unreal coordinates (centimeters, quaternion x/y/z/w, unitless scale).
"""

import json
import hashlib
import math
import re
import sys
import types
import uuid
from collections import OrderedDict
from contextlib import contextmanager


OWNER_KEY = "Send2UE.NestedPivots.Owner"
MANIFEST_KEY = "Send2UE.NestedPivots.Manifest"
OWNER_VERSION = "send2ue.nested_pivots.v1:"
COMPONENT_TAG = "send2ue_nested_pivot:"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_]+$")
_RECEIPT_MODULE = "_send2ue_nested_pivot_import_receipts_v1"
_MAX_RECEIPT_RUNS = 8


def _asset_path(value):
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError("Expected an absolute Unreal asset path.")
    package, separator, object_name = value.partition(".")
    segments = package[1:].split("/")
    if len(segments) < 2 or any(not _PATH_SEGMENT.fullmatch(part) for part in segments):
        raise ValueError("Invalid Unreal asset path: {!r}".format(value))
    if separator and object_name != segments[-1]:
        raise ValueError("Object path must name its package asset: {!r}".format(value))
    return package


def _vector(value, count, field):
    if not isinstance(value, (tuple, list)) or len(value) != count:
        raise ValueError("{} must contain {} numbers.".format(field, count))
    if any(isinstance(number, bool) or not isinstance(number, (int, float))
           or not math.isfinite(number) for number in value):
        raise ValueError("{} must contain finite numbers.".format(field))
    return [float(number) for number in value]


def validate_assembly(assembly):
    """Return a normalized, parent-first manifest without importing Unreal."""
    if (not isinstance(assembly, dict) or type(assembly.get("schema_version")) is not int
            or assembly["schema_version"] != 1):
        raise ValueError("Unsupported nested-pivot assembly schema.")
    root = assembly.get("root")
    name = assembly.get("blueprint_name")
    if not isinstance(root, str) or not _NAME.fullmatch(root):
        raise ValueError("Assembly root must be an export-safe identifier.")
    if name != "bc_" + root:
        raise ValueError("Assembly Blueprint must be named bc_<root>.")
    components = assembly.get("components")
    if not isinstance(components, list) or len(components) < 2:
        raise ValueError("An assembly requires a root and at least one nested pivot.")
    by_name, folded_names, mesh_paths = {}, set(), set()
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("Each assembly component must be a dictionary.")
        component_name = component.get("name")
        if not isinstance(component_name, str) or not _NAME.fullmatch(component_name):
            raise ValueError("Component name must be an export-safe identifier.")
        if component_name.casefold() in folded_names:
            raise ValueError("Duplicate assembly component: " + component_name)
        folded_names.add(component_name.casefold())
        parent = component.get("parent")
        if (parent is not None and not isinstance(parent, str)) or parent == component_name:
            raise ValueError("Invalid component parent: " + component_name)
        path = _asset_path(component.get("mesh_asset_path"))
        if path.casefold() in mesh_paths:
            raise ValueError("Nested pivots must reference distinct exported meshes.")
        mesh_paths.add(path.casefold())
        location = _vector(component.get("location"), 3, "location")
        rotation = _vector(component.get("rotation"), 4, "rotation quaternion")
        scale = _vector(component.get("scale"), 3, "scale")
        norm = math.sqrt(sum(number * number for number in rotation))
        if abs(norm - 1.0) > 1e-3:
            raise ValueError("Component rotation quaternion must have unit length.")
        if any(abs(number) < 1e-8 for number in scale):
            raise ValueError("Component scale must be invertible.")
        by_name[component_name] = dict(
            name=component_name, parent=parent, mesh_asset_path=path,
            location=location, rotation=[number / norm for number in rotation], scale=scale,
        )
    if root not in by_name or by_name[root]["parent"] is not None:
        raise ValueError("Assembly root must exist and have no parent.")
    base = by_name[root]
    if (any(abs(number) > 1e-5 for number in base["location"] + base["rotation"][:3])
            or abs(abs(base["rotation"][3]) - 1.0) > 1e-5
            or any(abs(number - 1.0) > 1e-5 for number in base["scale"])):
        raise ValueError("Root component transform must be identity; use actor placement for the assembly.")
    ordered = [base]
    visited = {root}
    remaining = {key: value for key, value in by_name.items() if key != root}
    while remaining:
        ready = [key for key, value in remaining.items() if value["parent"] in visited]
        if not ready:
            raise ValueError("Assembly hierarchy has a cycle, missing parent, or second root.")
        for key in ready:
            ordered.append(remaining.pop(key))
            visited.add(key)
    folder = base["mesh_asset_path"].rsplit("/", 1)[0]
    path = folder + "/" + name
    if assembly.get("blueprint_asset_path") and _asset_path(assembly["blueprint_asset_path"]) != path:
        raise ValueError("Assembly Blueprint must be alongside its root mesh.")
    if path.casefold() in mesh_paths:
        raise ValueError("Blueprint destination collides with a mesh asset.")
    return dict(schema_version=1, root=root, blueprint_name=name,
                blueprint_asset_path=path, components=ordered)


def _parent_object(unreal, data, blueprint):
    library = unreal.SubobjectDataBlueprintFunctionLibrary
    handle = library.get_parent_handle(data)
    if not library.is_handle_valid(handle):
        return None
    return library.get_object_for_blueprint(library.get_data(handle), blueprint)


def _gather(unreal, subsystem, blueprint):
    library = unreal.SubobjectDataBlueprintFunctionLibrary
    handles = list(subsystem.k2_gather_subobject_data_for_blueprint(blueprint))
    context, rows, generated, seen_components = None, [], {}, {}
    for handle in handles:
        data = library.get_data(handle)
        if library.is_root_actor(data):
            context = handle
        if not library.is_component(data):
            continue
        obj = library.get_object_for_blueprint(data, blueprint)
        if obj is None:
            raise RuntimeError("Cannot obtain Blueprint component template.")
        # UE 5.8's recursive gather appends each child twice. Python handle
        # wrappers do not compare by native identity, so compare templates and
        # their direct parents instead. Distinct components cannot share an ID.
        object_path = obj.get_path_name()
        parent_object = _parent_object(unreal, data, blueprint)
        if object_path in seen_components:
            if seen_components[object_path] != parent_object:
                raise RuntimeError("Component template appears under multiple parents.")
            continue
        seen_components[object_path] = parent_object
        tags = [str(tag) for tag in obj.get_editor_property("component_tags")]
        identifiers = [tag[len(COMPONENT_TAG):] for tag in tags if tag.startswith(COMPONENT_TAG)]
        if len(identifiers) > 1:
            raise RuntimeError("Ambiguous generated component tags.")
        identifier = identifiers[0] if identifiers else None
        row = dict(handle=handle, data=data, object=obj, identifier=identifier,
                   variable=str(library.get_variable_name(data)), tags=tags, parent_object=parent_object)
        rows.append(row)
        if identifier is not None:
            if identifier in generated or not isinstance(obj, unreal.StaticMeshComponent):
                raise RuntimeError("Invalid or duplicate generated pivot component: {} ({!r}, variable={}, duplicate={})".format(
                    identifier, obj, row["variable"], identifier in generated))
            if library.is_inherited_component(data) or library.is_native_component(data):
                raise RuntimeError("Cannot edit inherited or native pivot components.")
            generated[identifier] = row
    if context is None:
        raise RuntimeError("Blueprint actor context is missing.")
    return context, rows, generated


def _prepare(unreal, subsystem, assembly):
    meshes = {}
    for component in assembly["components"]:
        path = component["mesh_asset_path"]
        mesh = unreal.load_asset(path)
        if not isinstance(mesh, unreal.StaticMesh):
            raise RuntimeError("Assembly mesh is missing or is not a StaticMesh: " + path)
        meshes[component["name"]] = mesh
    owner = OWNER_VERSION + assembly["components"][0]["mesh_asset_path"]
    path = assembly["blueprint_asset_path"]
    exists = unreal.EditorAssetLibrary.does_asset_exist(path)
    blueprint = unreal.load_asset(path) if exists else None
    if exists:
        if not isinstance(blueprint, unreal.Blueprint):
            raise RuntimeError("Assembly destination is not a Blueprint: " + path)
        if unreal.EditorAssetLibrary.get_metadata_tag(blueprint, OWNER_KEY) != owner:
            raise RuntimeError("Refusing to overwrite a Blueprint not owned by this assembly: " + path)
        context, rows, generated = _gather(unreal, subsystem, blueprint)
        wanted = {component["name"] for component in assembly["components"]}
        for row in rows:
            if row["identifier"] is None and row["variable"].casefold() in {name.casefold() for name in wanted}:
                raise RuntimeError("Assembly component name collides with a user component: " + row["variable"])
        stale = [row for key, row in generated.items() if key not in wanted]
        library = unreal.SubobjectDataBlueprintFunctionLibrary
        for row in rows:
            if row["identifier"] is None and any(
                    row["parent_object"] == old["object"] for old in stale):
                raise RuntimeError("A removed pivot has user-authored children; detach them before reimport.")
        roots = [row for row in rows if library.is_root_component(row["data"])]
        if len(roots) != 1 or roots[0]["identifier"] != assembly["root"]:
            raise RuntimeError("Generated Blueprint root was changed; refusing to replace user hierarchy.")
    return dict(assembly=assembly, meshes=meshes, owner=owner, blueprint=blueprint)


def _set_component(unreal, component, specification, mesh):
    component.modify()
    # SetStaticMesh returns false when the desired mesh is already assigned.
    if component.get_editor_property("static_mesh") != mesh:
        component.set_static_mesh(mesh)
        if component.get_editor_property("static_mesh") != mesh:
            raise RuntimeError("Failed to assign assembly mesh: " + specification["mesh_asset_path"])
    component.set_editor_property("absolute_location", False)
    component.set_editor_property("absolute_rotation", False)
    component.set_editor_property("absolute_scale", False)
    component.set_editor_property("relative_location", unreal.Vector(*specification["location"]))
    component.set_editor_property("relative_rotation", unreal.Quat(*specification["rotation"]).rotator())
    component.set_editor_property("relative_scale3d", unreal.Vector(*specification["scale"]))


def _verify(unreal, subsystem, blueprint, assembly, meshes):
    _, _, generated = _gather(unreal, subsystem, blueprint)
    if set(generated) != {component["name"] for component in assembly["components"]}:
        raise RuntimeError("Generated Blueprint component set does not match the assembly.")
    library = unreal.SubobjectDataBlueprintFunctionLibrary
    for specification in assembly["components"]:
        row = generated[specification["name"]]
        component = row["object"]
        if component.get_editor_property("static_mesh") != meshes[specification["name"]]:
            raise RuntimeError("Blueprint mesh reference verification failed.")
        parent = specification["parent"]
        if parent is None:
            if not library.is_root_component(row["data"]):
                raise RuntimeError("Top export pivot is not the Blueprint root component.")
        elif row["parent_object"] != generated[parent]["object"]:
            raise RuntimeError("Blueprint component hierarchy verification failed.")
        for property_name, field in (("relative_location", "location"), ("relative_scale3d", "scale")):
            value = component.get_editor_property(property_name)
            if any(not math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-4)
                   for actual, expected in zip((value.x, value.y, value.z), specification[field])):
                raise RuntimeError("Blueprint {} verification failed.".format(field))
        value = component.get_editor_property("relative_rotation").quaternion()
        dot = sum(actual * expected for actual, expected in zip(
            (value.x, value.y, value.z, value.w), specification["rotation"]))
        if abs(abs(dot) - 1.0) > 1e-5:
            raise RuntimeError("Blueprint rotation verification failed.")
        if any(component.get_editor_property(name) for name in (
                "absolute_location", "absolute_rotation", "absolute_scale")):
            raise RuntimeError("Blueprint component does not inherit its parent transform.")


@contextmanager
def _cleanup_failed_new_blueprint(unreal, blueprint, asset_path, created):
    """A failed first import must not strand an unowned partial Blueprint."""
    try:
        yield
    except Exception as error:
        if not created:
            raise
        try:
            loaded = unreal.load_asset(asset_path)
            if loaded is not None or unreal.EditorAssetLibrary.does_asset_exist(asset_path):
                if loaded != blueprint or _asset_path(blueprint.get_path_name()) != asset_path:
                    raise RuntimeError("The destination no longer identifies the newly created Blueprint.")
                if not unreal.EditorAssetLibrary.delete_loaded_asset(blueprint):
                    raise RuntimeError("EditorAssetLibrary.delete_loaded_asset returned false.")
                if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
                    raise RuntimeError("The failed Blueprint still exists after cleanup.")
        except Exception as cleanup_error:
            raise RuntimeError(
                "Nested-pivot assembly failed and its newly created Blueprint could not be cleaned up: {}. "
                "Resolve this exact failed asset before retrying. Original error: {}. Cleanup error: {}".format(
                    asset_path, error, cleanup_error)
            ) from error
        raise


def _apply(unreal, subsystem, prepared):
    assembly, blueprint = prepared["assembly"], prepared["blueprint"]
    created = blueprint is None
    if created:
        factory = unreal.BlueprintFactory()
        factory.set_editor_property("parent_class", unreal.Actor)
        blueprint = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            assembly["blueprint_name"], assembly["blueprint_asset_path"].rsplit("/", 1)[0],
            unreal.Blueprint, factory,
        )
        if blueprint is None:
            raise RuntimeError("Could not create assembly Blueprint.")
    with _cleanup_failed_new_blueprint(unreal, blueprint, assembly["blueprint_asset_path"], created), \
            unreal.ScopedEditorTransaction("Send2UE: update nested pivot assembly"):
        blueprint.modify()
        context, rows, generated = _gather(unreal, subsystem, blueprint)
        wanted = {component["name"] for component in assembly["components"]}
        for specification in assembly["components"]:
            name, parent = specification["name"], specification["parent"]
            if parent is not None:
                parent_handle = generated[parent]["handle"]
            else:
                scene_roots = [row for row in rows if
                               unreal.SubobjectDataBlueprintFunctionLibrary.is_root_component(row["data"])]
                parent_handle = scene_roots[0]["handle"] if scene_roots else context
            row = generated.get(name)
            if row is None:
                handle, reason = subsystem.add_new_subobject(unreal.AddNewSubobjectParams(
                    parent_handle=parent_handle, new_class=unreal.StaticMeshComponent,
                    blueprint_context=blueprint,
                ))
                if not reason.is_empty():
                    raise RuntimeError("Could not create pivot component: " + str(reason))
                if not subsystem.rename_subobject(handle, unreal.Text(name)):
                    raise RuntimeError("Could not name pivot component: " + name)
                library = unreal.SubobjectDataBlueprintFunctionLibrary
                obj = library.get_object_for_blueprint(library.get_data(handle), blueprint)
                obj.set_editor_property("component_tags", [unreal.Name(COMPONENT_TAG + name)])
                # Adding/renaming may recompile and replace templates. Always regather.
                unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
                context, _, generated = _gather(unreal, subsystem, blueprint)
                row = generated[name]
            if parent is None:
                data = unreal.SubobjectDataBlueprintFunctionLibrary.get_data(row["handle"])
                if not unreal.SubobjectDataBlueprintFunctionLibrary.is_root_component(data):
                    if not subsystem.make_new_scene_root(context, row["handle"], blueprint):
                        raise RuntimeError("Could not make top pivot the Blueprint scene root.")
            else:
                if not subsystem.attach_subobject(generated[parent]["handle"], row["handle"]):
                    raise RuntimeError("Could not preserve pivot parent: " + name)
            context, _, generated = _gather(unreal, subsystem, blueprint)
        stale = [row["handle"] for key, row in generated.items() if key not in wanted]
        if stale:
            if subsystem.delete_subobjects(context, stale, blueprint) != len(stale):
                raise RuntimeError("Could not remove obsolete generated pivot components.")
        # Finish all structural edits first; compilation can replace templates.
        _, _, generated = _gather(unreal, subsystem, blueprint)
        for specification in assembly["components"]:
            _set_component(unreal, generated[specification["name"]]["object"],
                           specification, prepared["meshes"][specification["name"]])
        unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
        status = str(blueprint.get_editor_property("status")).upper()
        if "ERROR" in status:
            raise RuntimeError("Assembly Blueprint compilation failed.")
        _verify(unreal, subsystem, blueprint, assembly, prepared["meshes"])
        unreal.EditorAssetLibrary.set_metadata_tag(blueprint, OWNER_KEY, prepared["owner"])
        unreal.EditorAssetLibrary.set_metadata_tag(blueprint, MANIFEST_KEY, json.dumps(assembly, sort_keys=True))
        if not unreal.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False):
            raise RuntimeError("Could not save assembly Blueprint: " + assembly["blueprint_asset_path"])
    return dict(blueprint_asset_path=assembly["blueprint_asset_path"], created=created,
                component_count=len(assembly["components"]), verified=True)


def apply_pivot_assemblies(assemblies):
    """Create/update only explicit assemblies, after validating all destinations.

    Exceptions propagate to the import coordinator; a failed assembly is never
    reported as successful. Failed new Blueprints are removed; failed updates
    remain unsaved and undoable in the editor. All predictable validation
    failures precede asset mutation.
    """
    if not isinstance(assemblies, list):
        raise ValueError("Assemblies must be a list.")
    if not assemblies:
        return []
    manifests = [validate_assembly(assembly) for assembly in assemblies]
    paths = [assembly["blueprint_asset_path"].casefold() for assembly in manifests]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate assembly Blueprint destinations.")
    import unreal
    subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    if subsystem is None:
        raise RuntimeError("Unreal SubobjectDataSubsystem is unavailable.")
    prepared = [_prepare(unreal, subsystem, assembly) for assembly in manifests]
    return [_apply(unreal, subsystem, item) for item in prepared]


def _receipt_store():
    """Survive importlib reloads of this file between queued Unreal commands."""
    module = sys.modules.get(_RECEIPT_MODULE)
    if module is None:
        module = types.ModuleType(_RECEIPT_MODULE)
        module.runs = OrderedDict()
        sys.modules[_RECEIPT_MODULE] = module
    return module.runs


def _validate_import_receipt(run_id, record):
    if not isinstance(run_id, str):
        raise ValueError("Pivot import run ID must be a UUID string.")
    try:
        run_id = str(uuid.UUID(run_id))
    except ValueError as error:
        raise ValueError("Pivot import run ID must be a UUID string.") from error
    if not isinstance(record, dict):
        raise ValueError("Pivot import receipt must be a dictionary.")
    for field in ("root", "name"):
        if not isinstance(record.get(field), str) or not _NAME.fullmatch(record[field]):
            raise ValueError("Pivot receipt {} must be an export-safe identifier.".format(field))
    required = record.get("required_pivots")
    if (not isinstance(required, list) or len(required) < 2 or any(
            not isinstance(name, str) or not _NAME.fullmatch(name) for name in required)):
        raise ValueError("Pivot receipt requires the complete list of at least two pivot names.")
    if len({name.casefold() for name in required}) != len(required):
        raise ValueError("Pivot receipt required_pivots contains duplicate names.")
    if record["root"] not in required or record["name"] not in required:
        raise ValueError("Pivot receipt root and name must be in required_pivots.")
    parent = record.get("parent")
    if record["name"] == record["root"]:
        if parent is not None:
            raise ValueError("The root pivot receipt must have no parent.")
    elif parent not in required or parent == record["name"]:
        raise ValueError("Pivot receipt parent must be another required pivot.")
    component = dict(name=record["name"], parent=parent,
                     mesh_asset_path=_asset_path(record.get("mesh_asset_path")),
                     location=_vector(record.get("location"), 3, "location"),
                     rotation=_vector(record.get("rotation"), 4, "rotation quaternion"),
                     scale=_vector(record.get("scale"), 3, "scale"))
    norm = math.sqrt(sum(number * number for number in component["rotation"]))
    if abs(norm - 1.0) > 1e-3 or any(abs(number) < 1e-8 for number in component["scale"]):
        raise ValueError("Pivot receipt requires a unit quaternion and invertible scale.")
    component["rotation"] = [number / norm for number in component["rotation"]]
    return run_id, record["root"], tuple(sorted(required)), component


def record_imported_pivot(run_id, record):
    """Record a post-import command executed by Unreal, assembling when complete.

    Blender's deferred command recorder must only *emit* this call. No receipt
    is satisfied until the command actually runs in Unreal after its mesh import
    succeeded. A unique run UUID prevents old successful imports satisfying a
    later incomplete operation. Identical receipt replays are idempotent.
    """
    run_id, root, required, component = _validate_import_receipt(run_id, record)
    import unreal
    if not isinstance(unreal.load_asset(component["mesh_asset_path"]), unreal.StaticMesh):
        raise RuntimeError("Cannot record a pivot import without its Unreal StaticMesh: "
                           + component["mesh_asset_path"])
    digest = hashlib.sha256(json.dumps(component, sort_keys=True).encode("utf-8")).hexdigest()
    runs = _receipt_store()
    if run_id not in runs:
        runs[run_id] = dict(pending={}, completed={})
        while len(runs) > _MAX_RECEIPT_RUNS:
            runs.popitem(last=False)
    run = runs[run_id]
    completed = run["completed"].get(root)
    if completed is not None:
        if completed["required"] != required or completed["digests"].get(component["name"]) != digest:
            raise ValueError("Conflicting replay for a completed pivot assembly import.")
        return dict(status="replayed", root=root, received=len(required), required=len(required),
                    receipt=completed["receipt"])
    group = run["pending"].setdefault(root, dict(required=required, components={}, digests={}))
    if group["required"] != required:
        raise ValueError("Inconsistent required_pivots for one assembly import.")
    old_digest = group["digests"].get(component["name"])
    if old_digest is not None and old_digest != digest:
        raise ValueError("Conflicting duplicate pivot import receipt.")
    group["components"][component["name"]] = component
    group["digests"][component["name"]] = digest
    missing = [name for name in required if name not in group["components"]]
    if missing:
        return dict(status="pending", root=root, received=len(group["components"]),
                    required=len(required), missing=missing)
    manifest = dict(schema_version=1, root=root, blueprint_name="bc_" + root,
                    components=list(group["components"].values()))
    receipt = apply_pivot_assemblies([manifest])[0]
    # Keep hashes only for successful replay detection; discard pending payloads.
    run["completed"][root] = dict(required=required, digests=dict(group["digests"]), receipt=receipt)
    del run["pending"][root]
    return dict(status="applied", root=root, received=len(required), required=len(required), receipt=receipt)
