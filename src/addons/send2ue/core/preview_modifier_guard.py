"""Temporarily exclude explicitly tagged viewport previews from exports.

Geometry Nodes groups opt into this contract with the boolean ID property
``send2ue_preview_only``.  No modifier or node-group name is required, so other tools
can use the same export-safe contract without adding another hard-coded name here.
"""

from __future__ import annotations

import bpy


PREVIEW_ONLY_PROPERTY = "send2ue_preview_only"
STATE_KEY = "send2ue_preview_modifier_guard_state"


def is_preview_only_modifier(modifier) -> bool:
    group = getattr(modifier, "node_group", None)
    return bool(
        modifier.type == "NODES"
        and group is not None
        and group.get(PREVIEW_ONLY_PROPERTY, False)
    )


def prepare() -> int:
    """Disable preview-only modifiers and retain their exact visibility state."""
    cleanup()
    state = []
    for obj in bpy.data.objects:
        for modifier in obj.modifiers:
            if not is_preview_only_modifier(modifier):
                continue
            state.append(
                {
                    "object": obj.name_full,
                    "modifier": modifier.name,
                    "show_viewport": modifier.show_viewport,
                    "show_render": modifier.show_render,
                }
            )
            modifier.show_viewport = False
            modifier.show_render = False
            obj.update_tag()
    bpy.app.driver_namespace[STATE_KEY] = state
    if state:
        bpy.context.view_layer.update()
    return len(state)


def cleanup() -> int:
    """Restore every preview visibility state captured by :func:`prepare`."""
    state = bpy.app.driver_namespace.pop(STATE_KEY, [])
    restored = 0
    for item in state:
        obj = bpy.data.objects.get(item["object"])
        if obj is None:
            continue
        modifier = obj.modifiers.get(item["modifier"])
        if modifier is None or not is_preview_only_modifier(modifier):
            continue
        modifier.show_viewport = item["show_viewport"]
        modifier.show_render = item["show_render"]
        obj.update_tag()
        restored += 1
    if restored:
        bpy.context.view_layer.update()
    return restored
