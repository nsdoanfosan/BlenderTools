"""Recognize explicit, nested static-mesh export units without changing other hierarchies.

An ordinary grouping Empty, socket, or collection instance is not an export
pivot. Both pivots must be linked directly to Export and each must own direct
static mesh children linked there. The helpers intentionally do not import bpy
so the selection boundary can be tested without modifying a Blender session.
"""


_HELPER_PREFIXES = ('SOCKET_', 'UBX_', 'UCP_', 'USP_', 'UCX_')


def _directly_exported(scene_object, export_collection):
    return export_collection is not None and export_collection in getattr(
        scene_object, 'users_collection', ()
    )


def _is_exported_static_mesh(scene_object, export_collection):
    return (
        getattr(scene_object, 'type', None) == 'MESH'
        and _directly_exported(scene_object, export_collection)
        and not scene_object.name.startswith(_HELPER_PREFIXES)
        and not getattr(scene_object, 'active_shape_key', None)
        and not any(
            modifier.type == 'ARMATURE' and getattr(modifier, 'object', None)
            for modifier in getattr(scene_object, 'modifiers', ())
        )
    )


def is_export_pivot(scene_object, export_collection):
    """Return whether an Empty owns an explicitly enabled static mesh unit."""
    return (
        getattr(scene_object, 'type', None) == 'EMPTY'
        and getattr(scene_object, 'instance_type', 'NONE') != 'COLLECTION'
        and getattr(scene_object, 'instance_collection', None) is None
        and _directly_exported(scene_object, export_collection)
        and not scene_object.name.startswith(_HELPER_PREFIXES)
        and any(
            _is_exported_static_mesh(child, export_collection)
            for child in scene_object.children
        )
    )


def get_assembly_root(pivot, export_collection):
    """Return the highest connected pivot only for a pivot-in-pivot assembly."""
    if not is_export_pivot(pivot, export_collection):
        return None
    root = pivot
    while is_export_pivot(root.parent, export_collection):
        root = root.parent
    # Empty pivots can also be used as helpers inside a rig. Keep those
    # hierarchies on the legacy path even if they contain static meshes.
    ancestor = root.parent
    while ancestor is not None:
        if getattr(ancestor, 'type', None) == 'ARMATURE':
            return None
        ancestor = ancestor.parent
    if root != pivot or any(
        is_export_pivot(child, export_collection) for child in root.children
    ):
        return root
    return None


def iter_assembly_pivots(root, export_collection):
    """Yield the root followed by its directly nested export pivots."""
    if get_assembly_root(root, export_collection) != root:
        return
    pending = [root]
    while pending:
        pivot = pending.pop()
        yield pivot
        pending.extend(reversed(sorted(
            (child for child in pivot.children if is_export_pivot(child, export_collection)),
            key=lambda child: child.name,
        )))


def nested_pivot_descendants(pivot, export_collection):
    """Return objects owned by child pivots, which must never enter the parent FBX.

    Return an empty set for every legacy/non-assembly hierarchy. Once a child
    pivot establishes a boundary, its entire subtree belongs to that unit,
    including meshes, collisions and non-export helper objects.
    """
    if get_assembly_root(pivot, export_collection) is None:
        return set()
    pending = [child for child in pivot.children if is_export_pivot(child, export_collection)]
    descendants = set()
    while pending:
        child = pending.pop()
        if child in descendants:
            continue
        descendants.add(child)
        pending.extend(child.children)
    return descendants


def prune_nested_pivot_selection(pivot, export_collection, selected_objects):
    """Remove child units after the existing selector applies all legacy filters."""
    descendants = nested_pivot_descendants(pivot, export_collection)
    for scene_object in selected_objects:
        if scene_object in descendants:
            scene_object.select_set(False)
