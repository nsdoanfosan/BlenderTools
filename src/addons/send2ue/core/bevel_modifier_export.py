# Copyright Epic Games, Inc. All Rights Reserved.

from contextlib import contextmanager


def requested_for_export(properties, *, is_static_mesh):
    """Existing scenes retain their viewport modifier evaluation by default."""
    if not is_static_mesh or not getattr(properties, 'export_render_bevels', False):
        return False
    geometry = properties.blender.export_method.fbx.geometry
    return bool(geometry.use_mesh_modifiers and geometry.use_mesh_modifiers_render)


@contextmanager
def enabled_for_export(scene_objects, update=None, *, enabled=False):
    """Temporarily evaluate explicitly requested, render-enabled Bevels."""
    states = []
    try:
        if enabled:
            for scene_object in scene_objects:
                if getattr(scene_object, 'type', None) != 'MESH':
                    continue
                if getattr(scene_object, 'library', None) is not None:
                    continue
                if getattr(scene_object, 'name', '').upper().startswith(('UCX_', 'UBX_', 'UCP_', 'USP_')):
                    continue
                for modifier in scene_object.modifiers:
                    if (modifier.type != 'BEVEL' or modifier.show_viewport
                            or not modifier.show_render):
                        continue
                    states.append((modifier, modifier.show_viewport))
                    modifier.show_viewport = True
        if states and update:
            update()
        yield
    finally:
        for modifier, show_viewport in reversed(states):
            modifier.show_viewport = show_viewport
        if states and update:
            update()
