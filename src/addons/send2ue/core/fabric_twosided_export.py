"""Disable thickness only for FabricTwoSided's native Unreal mesh export."""
from contextlib import contextmanager
import re


def _uses_two_sided_material(obj):
    return any(
        slot.material and re.search(
            r'(?:^|_)fabrictwosided(?:_|$)', slot.material.name.lower()
        )
        for slot in obj.material_slots
    )


def _is_solidify(modifier):
    if modifier.type == 'SOLIDIFY':
        return True
    if modifier.type != 'NODES' or not modifier.node_group:
        return False
    return any(name.startswith('Solidify Plus') for name in
               (modifier.name, modifier.node_group.name))


@contextmanager
def render_surfaces(scene_objects, update=None):
    """Keep Painter/source untouched; restore Solidify flags even if FBX fails."""
    states = []
    try:
        for obj in scene_objects:
            if obj.type != 'MESH' or obj.library is not None:
                continue
            if not _uses_two_sided_material(obj):
                continue
            for modifier in obj.modifiers:
                if not _is_solidify(modifier):
                    continue
                states.append((obj, modifier, modifier.show_viewport, modifier.show_render))
                modifier.show_viewport = False
                modifier.show_render = False
            obj.update_tag(refresh={'DATA'})
        if states and update:
            update()
        yield
    finally:
        for obj, modifier, viewport, render in reversed(states):
            modifier.show_viewport = viewport
            modifier.show_render = render
            obj.update_tag(refresh={'DATA'})
        if states and update:
            update()
