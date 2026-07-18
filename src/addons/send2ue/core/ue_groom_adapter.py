# Copyright Epic Games, Inc. All Rights Reserved.

"""Hair Tool to Unreal Engine 5.8 Groom adapter.

This module deliberately has no dependency on Hair Tool's Python package. It
only reads the evaluated Blender Curves geometry and its named attributes.
"""

import math
import os
import re

import bpy


OUTPUT_CARDS = 'CARDS'
OUTPUT_GROOM = 'GROOM'
OUTPUT_BOTH = 'BOTH'

PRESET_CARD_RIG = 'CARD_RIG'
PRESET_UE_RIGGED_GUIDES = 'UE_RIGGED_GUIDES'

EXTENSION_NAME = 'ue_groom_adapter'

AUTO_ATTRIBUTES = {
    'id': ('groom_id', 'id'),
    'group_id': ('groom_group_id', 'group_id', 'curve_group_id_ht'),
    'root_uv': ('groom_root_uv', 'UVMap', 'surface_uv_coordinate', 'root_uv'),
    'guide': ('groom_guide', 'GUIDE', 'guide'),
    'parent_id': ('groom_parent_id', 'ParentID', 'parent_id'),
    'width': ('groom_width', 'width', 'radius'),
    # groom_debug_color is intentionally excluded: it is a viewport diagnostic
    # channel (group/id/root-UV modes), not authored hair color.
    'color': ('groom_color', 'HS_BaseColor', 'color'),
    'roughness': ('groom_roughness', 'roughness'),
    'ao': ('groom_ao', 'AO', 'ao'),
    'clump_id': ('groom_clump_id', 'ClumpID', 'clump_id'),
    'factor': ('groom_factor', 'Factor', 'factor'),
    'random': ('groom_random', 'Random', 'random'),
    'roundness': ('groom_roundness', 'roundness'),
}

DEFAULT_UNREAL_GROOM_MATERIAL = '/Game/Material/Groom/M_UE_Groom_HairTool_Preview'
DEFAULT_UNREAL_GROOM_MATERIAL_SOURCE = '/HairStrands/Materials/HairDefaultMaterial'

CYCLES_VIEW_FAST = 'FAST'
CYCLES_VIEW_BALANCED = 'BALANCED'
CYCLES_VIEW_QUALITY = 'QUALITY'

CYCLES_VIEW_PRESETS = {
    CYCLES_VIEW_FAST: {
        'preview_samples': 1,
        'use_preview_denoising': False,
        'preview_adaptive_threshold': 0.5,
        'preview_scrambling_distance': True,
    },
    CYCLES_VIEW_BALANCED: {
        'preview_samples': 8,
        'use_preview_denoising': True,
        'preview_adaptive_threshold': 0.1,
        'preview_scrambling_distance': False,
    },
    CYCLES_VIEW_QUALITY: {
        'preview_samples': 32,
        'use_preview_denoising': True,
        'preview_adaptive_threshold': 0.02,
        'preview_scrambling_distance': False,
    },
}


def get_settings(properties=None):
    """Return the extension settings, or ``None`` before registration."""
    properties = properties or getattr(bpy.context.scene, 'send2ue', None)
    extensions = getattr(properties, 'extensions', None)
    return getattr(extensions, EXTENSION_NAME, None)


def is_enabled(properties=None):
    adapter = get_settings(properties)
    return bool(adapter and adapter.enabled)


def get_output_mode(properties=None):
    adapter = get_settings(properties)
    return getattr(adapter, 'output_mode', OUTPUT_CARDS) if adapter else OUTPUT_CARDS


def wants_cards(properties=None):
    if not is_enabled(properties):
        return True
    return get_output_mode(properties) in {OUTPUT_CARDS, OUTPUT_BOTH}


def wants_groom(properties=None):
    if not is_enabled(properties):
        return False
    return get_output_mode(properties) in {OUTPUT_GROOM, OUTPUT_BOTH}


def is_hair_tool_groom(scene_object, properties=None):
    if not wants_groom(properties) or not scene_object or scene_object.type != 'CURVES':
        return False
    from . import hair_tool_export
    return hair_tool_export.is_hair_tool_object(scene_object)


def get_hair_tool_grooms(properties=None):
    from . import utilities
    return [
        scene_object
        for scene_object in utilities.get_from_collection('CURVES')
        if is_hair_tool_groom(scene_object, properties)
    ]


def _attribute_value(item, data_type):
    if data_type in {'FLOAT_VECTOR', 'FLOAT2', 'FLOAT_COLOR', 'BYTE_COLOR'}:
        property_name = 'color' if 'COLOR' in data_type else 'vector'
        return tuple(float(value) for value in getattr(item, property_name))
    value = getattr(item, 'value')
    if data_type in {'INT', 'INT8', 'BOOLEAN'}:
        return int(value)
    return float(value)


def _read_attribute(attribute):
    return [_attribute_value(item, attribute.data_type) for item in attribute.data]


def _find_attribute(curves, setting_name, kind):
    attributes = {attribute.name.casefold(): attribute for attribute in curves.attributes}
    requested = str(getattr(setting_name, kind + '_attribute', '') or '').strip()
    candidates = (requested,) if requested else AUTO_ATTRIBUTES[kind]
    matches = [attributes.get(name.casefold()) for name in candidates]
    matches = [attribute for attribute in matches if attribute]

    # Unreal requires an actual float2 Root UV. Prefer a Blender FLOAT2 source
    # during auto mapping, but still permit an explicit 2/3 component override.
    if kind == 'root_uv' and not requested:
        float2_match = next(
            (attribute for attribute in matches if attribute.data_type == 'FLOAT2'),
            None,
        )
        if float2_match:
            return float2_match
    return matches[0] if matches else None


def _modifier_input(modifier, socket_name):
    """Read a Geometry Nodes modifier input by its stable interface label."""
    node_group = getattr(modifier, 'node_group', None)
    interface = getattr(node_group, 'interface', None)
    if not interface:
        return None
    for item in interface.items_tree:
        if (
            getattr(item, 'item_type', None) == 'SOCKET'
            and getattr(item, 'in_out', None) == 'INPUT'
            and item.name.casefold() == socket_name.casefold()
        ):
            return modifier.get(item.identifier, getattr(item, 'default_value', None))
    return None


def _preview_settings(scene_object, adapter):
    """Read the optional Blender preview node/material values used for UE parity."""
    preview_modifier = None
    required_inputs = {'hair width (cm)', 'root scale', 'tip scale'}
    for modifier in scene_object.modifiers:
        node_group = getattr(modifier, 'node_group', None)
        interface = getattr(node_group, 'interface', None)
        if not interface:
            continue
        input_names = {
            item.name.casefold()
            for item in interface.items_tree
            if getattr(item, 'item_type', None) == 'SOCKET'
            and getattr(item, 'in_out', None) == 'INPUT'
        }
        if required_inputs.issubset(input_names):
            preview_modifier = modifier
            break

    if not preview_modifier:
        return None

    preview_material = _modifier_input(preview_modifier, 'Preview Material')
    color = _modifier_input(preview_modifier, 'Hair Color')
    roughness = None
    if preview_material and preview_material.use_nodes:
        hair_node = next(
            (
                node for node in preview_material.node_tree.nodes
                if node.bl_idname == 'ShaderNodeBsdfHairPrincipled'
            ),
            None,
        )
        roughness_input = hair_node.inputs.get('Roughness') if hair_node else None
        if roughness_input:
            roughness = float(roughness_input.default_value)

    explicit_material_path = ''
    if preview_material:
        explicit_material_path = str(preview_material.get('unreal_groom_material_path', '') or '')
    unreal_material_path = (
        explicit_material_path
        or str(getattr(adapter, 'unreal_material_path', '') or '')
        or DEFAULT_UNREAL_GROOM_MATERIAL
    )

    return {
        'modifier': preview_modifier.name,
        'material_name': preview_material.name if preview_material else None,
        'unreal_material_path': unreal_material_path,
        'groom_id_offset': int(round(float(
            _modifier_input(preview_modifier, 'Groom ID Offset') or 0
        ))),
        'hair_width_cm': float(_modifier_input(preview_modifier, 'Hair Width (cm)') or 0.01),
        'root_scale': float(_modifier_input(preview_modifier, 'Root Scale') or 1.0),
        'tip_scale': float(_modifier_input(preview_modifier, 'Tip Scale') or 1.0),
        'color': tuple(float(component) for component in color[:3]) if color else None,
        'roughness': roughness,
    }


def configure_cycles_groom_view(context, preset=CYCLES_VIEW_FAST):
    """Apply a Curves-only Cycles viewport preset without changing final render samples."""
    preset = preset if preset in CYCLES_VIEW_PRESETS else CYCLES_VIEW_FAST
    values = CYCLES_VIEW_PRESETS[preset]
    scene = context.scene
    scene.render.engine = 'CYCLES'

    cycles = scene.cycles
    for property_name, value in values.items():
        if hasattr(cycles, property_name):
            setattr(cycles, property_name, value)

    viewport_count = 0
    window_manager = getattr(context, 'window_manager', None)
    for window in getattr(window_manager, 'windows', ()):
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.shading.type = 'RENDERED'
                viewport_count += 1

    return {
        'preset': preset,
        'preview_samples': int(cycles.preview_samples),
        'use_preview_denoising': bool(cycles.use_preview_denoising),
        'preview_adaptive_threshold': float(cycles.preview_adaptive_threshold),
        'preview_scrambling_distance': bool(cycles.preview_scrambling_distance),
        'render_samples': int(cycles.samples),
        'device': str(cycles.device),
        'viewport_count': viewport_count,
    }


def _curve_values(attribute, starts, counts, report_variation=False):
    if not attribute:
        return None, False

    values = _read_attribute(attribute)
    varied = False
    if attribute.domain == 'CURVE':
        return values, varied
    if attribute.domain == 'POINT':
        roots = []
        for start, count in zip(starts, counts):
            curve_values = values[start:start + count]
            roots.append(curve_values[0])
            if report_variation and any(value != curve_values[0] for value in curve_values[1:]):
                varied = True
        return roots, varied
    if attribute.domain in {'CONSTANT', 'INSTANCE'} and values:
        return [values[0]] * len(counts), varied
    return None, varied


def _point_values(attribute, starts, counts):
    if not attribute:
        return None
    values = _read_attribute(attribute)
    if attribute.domain == 'POINT':
        return values
    if attribute.domain == 'CURVE':
        expanded = []
        for value, count in zip(values, counts):
            expanded.extend([value] * count)
        return expanded
    if attribute.domain in {'CONSTANT', 'INSTANCE'} and values:
        return [values[0]] * sum(counts)
    return None


HAIR_TOOL_SHADER_INPUTS = {
    'Base Color',
    'Root Color',
    'Root Color Mix Factor',
    'Root Color Range',
    'Tip Color',
    'Tip Color Mix Factor',
    'Tip Color Range',
    'Debug  Color  Mix',
    'Factor [Map]',
}


def _find_hair_tool_shader(scene_object):
    """Find Hair Tool's authored HairShaderMain material node, read-only."""
    candidates = []
    for modifier in scene_object.modifiers:
        material = _modifier_input(modifier, 'Strands Material')
        if isinstance(material, bpy.types.Material):
            candidates.append(material)
    candidates.extend(material for material in scene_object.data.materials if material)
    candidates.extend(bpy.data.materials)

    # Adapter preview materials contain the same node group, but they are only
    # driven mirrors. Resolve their recorded Hair Tool source first and never
    # select the mirror itself as the authored material.
    source_candidates = []
    for material in candidates:
        source_name = str(material.get('hair_tool_source_material', '') or '')
        source_material = bpy.data.materials.get(source_name)
        if source_material:
            source_candidates.append(source_material)
    candidates = source_candidates + candidates

    seen = set()
    for material in candidates:
        if (
            material in seen
            or not material.use_nodes
            or material.get('unreal_groom_material_path')
            or material.get('hair_tool_source_material')
        ):
            continue
        seen.add(material)
        for node in material.node_tree.nodes:
            if node.bl_idname != 'ShaderNodeGroup' or not node.node_tree:
                continue
            input_names = {socket.name for socket in node.inputs}
            if HAIR_TOOL_SHADER_INPUTS.issubset(input_names):
                return material, node
    return None, None


def _socket_value(node, name, fallback=None):
    socket = node.inputs.get(name) if node else None
    if not socket:
        return fallback
    value = socket.default_value
    if hasattr(value, '__len__') and not isinstance(value, str):
        return tuple(float(component) for component in value)
    return float(value)


def _hair_tool_color_settings(scene_object):
    material, node = _find_hair_tool_shader(scene_object)
    if not node:
        return None
    return {
        'material': material.name,
        'node': node.name,
        'node_group': node.node_tree.name,
        'base_color': _socket_value(node, 'Base Color', (0.8, 0.8, 0.8, 1.0))[:3],
        'root_color': _socket_value(node, 'Root Color', (0.0, 0.0, 0.0, 1.0))[:3],
        'root_mix': _socket_value(node, 'Root Color Mix Factor', 0.0),
        'root_range': _socket_value(node, 'Root Color Range', 0.0),
        'root_texture_overlay': _socket_value(node, 'Root Texture Overaly', 0.0),
        'root_texture_brightness': _socket_value(node, 'Root  Texture Brightness', 0.0),
        'tip_color': _socket_value(node, 'Tip Color', (0.0, 0.0, 0.0, 1.0))[:3],
        'tip_mix': _socket_value(node, 'Tip Color Mix Factor', 0.0),
        'tip_range': _socket_value(node, 'Tip Color Range', 0.0),
        'tip_texture_overlay': _socket_value(node, 'Tip Texture Overlay', 0.0),
        'tip_texture_brightness': _socket_value(node, 'Tip  Texture Brightness', 0.0),
        'system_color_mix': _socket_value(node, 'Debug  Color  Mix', 0.0),
        'roughness': _socket_value(node, 'SpecRoughness', None),
        'texture_inputs': tuple(
            socket.name for socket in node.inputs
            if socket.is_linked and socket.name in {
                'Depth [Map]', 'Alpha [Map]', 'Random Id [Map]', 'Vert Color AO [Map]'
            }
        ),
    }


def _hair_tool_profile_settings(scene_object):
    """Read Hair Tool's card-profile settings without editing its node groups."""
    profile_group_names = {
        'Curve_Profile_UV', 'Flat_Profile_UV', 'Circle_Profile_UV',
        'Curls_Profile_UV', 'Multi_Curve_Profile_UV', 'Mesh_Profile',
        'No_Profile', 'Round_Profile',
    }
    for modifier in scene_object.modifiers:
        node_group = getattr(modifier, 'node_group', None)
        if not node_group:
            continue
        for node in node_group.nodes:
            nested = getattr(node, 'node_tree', None)
            if not nested or nested.name not in profile_group_names:
                continue
            values = {}
            for name in (
                'Width', 'Roundness', 'Influence Roundness', 'Uplift', 'Bow Up',
                'Taper UV by Strand Radius', 'Use UV Tiling', 'Profile Res',
                'Resolution',
            ):
                socket = node.inputs.get(name)
                if socket:
                    value = socket.default_value
                    values[name] = (
                        tuple(value) if hasattr(value, '__len__') and not isinstance(value, str)
                        else value
                    )
            return {
                'modifier': modifier.name,
                'modifier_enabled': bool(modifier.show_viewport),
                'node': node.name,
                'profile_type': nested.name,
                'settings': values,
                'groom_mapping': (
                    'Card profile cross-section is preserved for Cards; '
                    'Groom uses calibrated Hair Width multiplied by evaluated radius.'
                ),
            }
    return None


def _drive_socket(target_tree, target_socket, source_tree, source_socket):
    """Drive one external preview socket from a Hair Tool material socket."""
    source_path = source_socket.path_from_id('default_value')
    value = source_socket.default_value
    indices = range(len(value)) if hasattr(value, '__len__') and not isinstance(value, str) else (None,)
    for index in indices:
        try:
            if index is None:
                target_socket.driver_remove('default_value')
                fcurve = target_socket.driver_add('default_value')
            else:
                target_socket.driver_remove('default_value', index)
                fcurve = target_socket.driver_add('default_value', index)
        except (TypeError, RuntimeError):
            if index is None:
                fcurve = target_socket.driver_add('default_value')
            else:
                fcurve = target_socket.driver_add('default_value', index)
        driver = fcurve.driver
        driver.type = 'SCRIPTED'
        variable = driver.variables.new()
        variable.name = 'value'
        variable.type = 'SINGLE_PROP'
        variable.targets[0].id_type = 'NODETREE'
        variable.targets[0].id = source_tree
        variable.targets[0].data_path = source_path + (f'[{index}]' if index is not None else '')
        driver.expression = 'value'


def connect_hair_tool_preview_material(scene_object, properties=None):
    """Connect the external Cycles preview to Hair Tool's authored shader rule."""
    adapter = get_settings(properties)
    preview = _preview_settings(scene_object, adapter)
    if not preview:
        raise RuntimeError(f'{scene_object.name}: UE Groom preview modifier was not found.')
    modifier = scene_object.modifiers.get(preview['modifier'])
    target_material = _modifier_input(modifier, 'Preview Material')
    if not target_material or not target_material.use_nodes:
        raise RuntimeError(f'{scene_object.name}: UE Groom preview material was not found.')

    source_material, source_node = _find_hair_tool_shader(scene_object)
    if not source_node:
        raise RuntimeError(f'{scene_object.name}: Hair Tool HairShaderMain material was not found.')
    if target_material == source_material:
        raise RuntimeError('UE Groom preview material must remain separate from Hair Tool material.')

    tree = target_material.node_tree
    output = next((node for node in tree.nodes if node.bl_idname == 'ShaderNodeOutputMaterial'), None)
    if not output:
        output = tree.nodes.new('ShaderNodeOutputMaterial')
        output.name = 'Material Output'

    keep_names = {'Material Output', 'Hair Tool Color Bridge', 'Hair Tool Factor', 'Hair Tool SystemColor'}
    for node in list(tree.nodes):
        if node.name not in keep_names and node != output:
            tree.nodes.remove(node)

    bridge = tree.nodes.get('Hair Tool Color Bridge')
    if not bridge:
        bridge = tree.nodes.new('ShaderNodeGroup')
        bridge.name = 'Hair Tool Color Bridge'
    bridge.node_tree = source_node.node_tree

    factor_node = tree.nodes.get('Hair Tool Factor')
    if not factor_node:
        factor_node = tree.nodes.new('ShaderNodeAttribute')
        factor_node.name = 'Hair Tool Factor'
    factor_node.attribute_name = 'Factor'

    system_node = tree.nodes.get('Hair Tool SystemColor')
    if not system_node:
        system_node = tree.nodes.new('ShaderNodeAttribute')
        system_node.name = 'Hair Tool SystemColor'
    system_node.attribute_name = 'SystemColor'

    for socket in bridge.inputs:
        for link in list(socket.links):
            tree.links.remove(link)

    tree.links.new(factor_node.outputs['Fac'], bridge.inputs['Factor [Map]'])
    tree.links.new(system_node.outputs['Color'], bridge.inputs['Debug Color'])
    for link in list(output.inputs['Surface'].links):
        tree.links.remove(link)
    tree.links.new(bridge.outputs['Cycles'], output.inputs['Surface'])

    excluded = {
        'Factor [Map]', 'Debug Color', 'Depth [Map]', 'Alpha [Map]',
        'Random Id [Map]', 'Vert Color AO [Map]', 'Normal', 'UV',
    }
    driven = []
    for source_socket in source_node.inputs:
        target_socket = bridge.inputs.get(source_socket.name)
        if not target_socket or source_socket.name in excluded:
            continue
        try:
            target_socket.default_value = source_socket.default_value
            _drive_socket(tree, target_socket, source_material.node_tree, source_socket)
            driven.append(source_socket.name)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            continue

    target_material['hair_tool_source_material'] = source_material.name
    target_material['hair_tool_source_node'] = source_node.name
    target_material['unreal_groom_material_path'] = DEFAULT_UNREAL_GROOM_MATERIAL
    target_material.update_tag()
    scene_object.update_tag()
    bpy.context.view_layer.update()
    return {
        'object': scene_object.name,
        'preview_material': target_material.name,
        'source_material': source_material.name,
        'source_node': source_node.name,
        'source_group': source_node.node_tree.name,
        'driven_inputs': driven,
    }


def connect_hair_tool_radius_preview(scene_object, properties=None):
    """Multiply the calibrated Groom radius by Hair Tool's evaluated radius."""
    adapter = get_settings(properties)
    preview = _preview_settings(scene_object, adapter)
    if not preview:
        raise RuntimeError(f'{scene_object.name}: UE Groom preview modifier was not found.')
    modifier = scene_object.modifiers.get(preview['modifier'])
    node_group = getattr(modifier, 'node_group', None)
    if not node_group or node_group.name.startswith('Hair_System_'):
        raise RuntimeError('Hair Tool node groups are read-only for the UE Groom Adapter.')

    taper = node_group.nodes.get('Root to Tip Taper')
    set_radius = node_group.nodes.get('Set Unreal Width')
    if not taper or not set_radius:
        raise RuntimeError('UE Groom preview radius nodes were not found.')

    hair_tool_radius = node_group.nodes.get('Hair Tool Radius')
    if not hair_tool_radius:
        hair_tool_radius = node_group.nodes.new('GeometryNodeInputRadius')
        hair_tool_radius.name = 'Hair Tool Radius'
        hair_tool_radius.label = 'Hair Tool evaluated radius'

    multiply = node_group.nodes.get('Apply Hair Tool Radius')
    if not multiply:
        multiply = node_group.nodes.new('ShaderNodeMath')
        multiply.name = 'Apply Hair Tool Radius'
        multiply.label = 'Groom radius × Hair Tool radius'
    multiply.operation = 'MULTIPLY'

    _replace_node_input_link(
        node_group, taper.outputs['Result'], multiply.inputs[0]
    )
    _replace_node_input_link(
        node_group, hair_tool_radius.outputs['Radius'], multiply.inputs[1]
    )
    _replace_node_input_link(
        node_group, multiply.outputs['Value'], set_radius.inputs['Radius']
    )

    node_group.update_tag()
    scene_object.update_tag()
    bpy.context.view_layer.update()
    return {
        'object': scene_object.name,
        'modifier': modifier.name,
        'node_group': node_group.name,
        'radius_source': 'Hair Tool evaluated radius',
        'calibration': 'Hair Width (cm) × Root/Tip Scale',
    }


def connect_hair_tool_guide_rules(scene_object):
    """Restore Hair Tool's GUIDE tag on the adapter-owned Generator copy.

    Hair Tool node groups remain read-only.  The adapter only synchronizes the
    local ``*_UEGroomPreview`` copy that was created for this Blender file.
    """
    source_group = bpy.data.node_groups.get('Hair_System_Main')
    source_node = source_group.nodes.get('Parent Tag') if source_group else None
    source_socket = source_node.inputs.get('Tag') if source_node else None
    source_tag = str(source_socket.default_value) if source_socket else 'GUIDE'
    if not source_tag:
        source_tag = 'GUIDE'

    connected = []
    for modifier in scene_object.modifiers:
        node_group = getattr(modifier, 'node_group', None)
        if not node_group or not node_group.name.endswith('_UEGroomPreview'):
            continue
        parent_tag = node_group.nodes.get('Parent Tag')
        tag_socket = parent_tag.inputs.get('Tag') if parent_tag else None
        if not tag_socket:
            continue
        tag_socket.default_value = source_tag
        node_group['ue_groom_adapter_owned'] = True
        node_group.update_tag()
        connected.append({
            'modifier': modifier.name,
            'node_group': node_group.name,
            'parent_tag': source_tag,
        })

    scene_object.update_tag(refresh={'DATA'})
    bpy.context.view_layer.update()
    return {'object': scene_object.name, 'connected': connected}


def _replace_node_input_link(node_group, output_socket, input_socket):
    for link in list(input_socket.links):
        node_group.links.remove(link)
    node_group.links.new(output_socket, input_socket)


def _clamp01(value):
    return max(0.0, min(1.0, float(value)))


def _lerp(a, b, factor):
    return (1.0 - factor) * a + factor * b


def _map_range_clamped(value, from_min, from_max, to_min, to_max):
    if abs(from_max - from_min) <= 1e-12:
        return to_max
    factor = _clamp01((value - from_min) / (from_max - from_min))
    return _lerp(to_min, to_max, factor)


def _bake_hair_tool_colors(curves, starts, counts, settings):
    """Bake HairShaderMain's Base→Root→Tip→SystemColor rule per point."""
    by_name = {attribute.name.casefold(): attribute for attribute in curves.attributes}
    factor_attribute = by_name.get('factor')
    factors = _point_values(factor_attribute, starts, counts)
    if factors is None:
        factors = []
        for count in counts:
            denominator = max(count - 1, 1)
            factors.extend(index / denominator for index in range(count))
    factors = [float(value) for value in factors]

    system_attribute = by_name.get('systemcolor')
    system_colors = _point_values(system_attribute, starts, counts)
    if system_colors is None:
        system_colors = [(0.0, 0.0, 0.0)] * sum(counts)

    random_attribute = by_name.get('random')
    random_values = _point_values(random_attribute, starts, counts)
    if random_values is None:
        random_values = [0.0] * sum(counts)

    root_range = _clamp01(settings['root_range'])
    tip_range = _clamp01(settings['tip_range'])
    colors = []
    for factor, system_color, random_value in zip(factors, system_colors, random_values):
        if isinstance(random_value, (tuple, list)):
            random_value = random_value[0]
        random_value = float(random_value)

        root_extent = _lerp(
            1.0,
            random_value + settings['root_texture_brightness'],
            settings['root_texture_overlay'],
        )
        root_weight = _clamp01(
            _map_range_clamped(factor, 0.0, root_range, root_extent, 0.0)
            * settings['root_mix']
        )
        color = tuple(
            _lerp(settings['base_color'][channel], settings['root_color'][channel], root_weight)
            for channel in range(3)
        )

        tip_extent = _lerp(
            1.0,
            random_value + settings['tip_texture_brightness'],
            settings['tip_texture_overlay'],
        )
        tip_weight = _clamp01(
            _map_range_clamped(factor, 1.0 - tip_range, 1.0, 0.0, tip_extent)
            * settings['tip_mix']
        )
        color = tuple(
            _lerp(color[channel], settings['tip_color'][channel], tip_weight)
            for channel in range(3)
        )

        system_color = tuple(float(component) for component in system_color[:3])
        colors.append(tuple(
            color[channel] + system_color[channel] * settings['system_color_mix']
            for channel in range(3)
        ))
    return colors, factor_attribute is not None, system_attribute is not None


def _mapping_error(report, adapter, kind, attribute):
    requested = str(getattr(adapter, kind + '_attribute', '') or '').strip()
    if requested and not attribute:
        report['errors'].append(
            f'지정한 {kind} 속성 "{requested}"을(를) 찾을 수 없습니다.'
        )


def _guide_source_object(scene_object):
    for modifier in scene_object.modifiers:
        guide_object = _modifier_input(modifier, 'Curve Guide')
        if isinstance(guide_object, bpy.types.Object):
            return guide_object
    return None


def _guide_source_geometry(scene_object):
    """Return actual guide counts and world-space points from the Hair Tool input."""
    guide_object = _guide_source_object(scene_object)
    if not guide_object:
        return None
    evaluated = guide_object.evaluated_get(bpy.context.evaluated_depsgraph_get())
    world_matrix = evaluated.matrix_world.copy()
    if evaluated.type == 'CURVES':
        counts = [int(curve.points_length) for curve in evaluated.data.curves]
        points = [
            tuple(float(value) for value in (world_matrix @ point.position))
            for point in evaluated.data.points
        ]
    elif evaluated.type == 'CURVE':
        counts = []
        points = []
        for spline in evaluated.data.splines:
            spline_points = spline.bezier_points if spline.type == 'BEZIER' else spline.points
            counts.append(len(spline_points))
            for point in spline_points:
                position = point.co if spline.type == 'BEZIER' else point.co.xyz
                points.append(tuple(float(value) for value in (world_matrix @ position)))
    else:
        return None
    if not counts or any(count < 2 for count in counts):
        return None
    return {'object': guide_object.name, 'counts': counts, 'points': points}


def _guide_source_info(scene_object):
    """Find the actual Hair Tool Curve Guide object connected to a GN modifier."""
    geometry = _guide_source_geometry(scene_object)
    if not geometry:
        return None
    return {
        'object': geometry['object'],
        'curve_count': len(geometry['counts']),
        'point_count': len(geometry['points']),
    }


def inspect_object(scene_object, properties=None):
    """Collect and validate one evaluated Hair Tool Curves object."""
    adapter = get_settings(properties)
    report = {
        'object': scene_object.name,
        'errors': [],
        'warnings': [],
        'mappings': {},
    }
    if not adapter:
        report['errors'].append('UE Groom Adapter 설정이 등록되지 않았습니다.')
        return report

    evaluated_object = scene_object.evaluated_get(bpy.context.evaluated_depsgraph_get())
    curves = evaluated_object.data
    if evaluated_object.type != 'CURVES' or not hasattr(curves, 'curves'):
        report['errors'].append('평가 결과가 Blender Curves가 아닙니다.')
        return report

    counts = [int(curve.points_length) for curve in curves.curves]
    starts = [int(curve.first_point_index) for curve in curves.curves]
    point_count = len(curves.points)
    curve_count = len(curves.curves)
    report['curve_count'] = curve_count
    report['point_count'] = point_count
    report['guide_source'] = _guide_source_info(scene_object)
    if not curve_count or not point_count:
        report['errors'].append('내보낼 커브 또는 포인트가 없습니다.')
        return report
    if sum(counts) != point_count:
        report['errors'].append('커브별 포인트 수 합계가 전체 포인트 수와 다릅니다.')
    if any(count < 2 for count in counts):
        report['errors'].append('포인트가 2개 미만인 커브가 있습니다.')

    attributes = {kind: _find_attribute(curves, adapter, kind) for kind in AUTO_ATTRIBUTES}
    for kind, attribute in attributes.items():
        report['mappings'][kind] = attribute.name if attribute else None
        _mapping_error(report, adapter, kind, attribute)

    preview = _preview_settings(scene_object, adapter)
    report['preview'] = preview
    if preview and not preview['unreal_material_path'].startswith('/Game/Material/'):
        report['errors'].append('Unreal Groom Material path must be under /Game/Material/.')

    hair_tool_color = _hair_tool_color_settings(scene_object)
    report['hair_tool_color'] = hair_tool_color
    hair_tool_profile = _hair_tool_profile_settings(scene_object)
    report['hair_tool_profile'] = hair_tool_profile

    ids, id_varied = _curve_values(attributes['id'], starts, counts, report_variation=True)
    if ids is None:
        ids = list(range(curve_count))
        report['warnings'].append('ID 속성이 없어 커브 순서로 groom_id를 생성합니다.')
    elif (
        id_varied
        and attributes['id'].name.casefold() == 'id'
        and not str(getattr(adapter, 'id_attribute', '') or '').strip()
    ):
        # Blender/Hair Tool commonly exposes POINT-domain ``id`` as a point ID,
        # not a strand ID. It is not valid Groom data, so auto mode generates a
        # stable uniform ID instead of misinterpreting it.
        ids = list(range(curve_count))
        id_varied = False
        report['mappings']['id'] = 'generated curve order'
        report['warnings'].append(
            'POINT id가 커브 안에서 변해 커브 순서로 groom_id를 생성합니다.'
        )
    ids = [int(value) for value in ids]
    if id_varied:
        report['errors'].append('POINT 도메인 ID가 한 커브 안에서 변합니다.')
    if len(set(ids)) != curve_count:
        report['errors'].append('groom_id는 커브마다 고유해야 합니다.')

    group_ids, group_varied = _curve_values(
        attributes['group_id'], starts, counts, report_variation=True
    )
    if group_ids is None:
        group_ids = [0] * curve_count
        report['warnings'].append('Group ID 속성이 없어 모든 커브를 그룹 0으로 지정합니다.')
    group_ids = [int(value) for value in group_ids]
    if group_varied:
        report['errors'].append('POINT 도메인 Group ID가 한 커브 안에서 변합니다.')
    if any(value < 0 for value in group_ids):
        report['errors'].append('groom_group_id에는 음수를 사용할 수 없습니다.')

    root_uvs, root_uv_varied = _curve_values(
        attributes['root_uv'], starts, counts, report_variation=True
    )
    if root_uvs is None:
        report['errors'].append('Root UV 후보가 없습니다. UVMap 또는 명시적 FLOAT2 속성을 지정하세요.')
        root_uvs = [(0.0, 0.0)] * curve_count
    converted_root_uvs = []
    for value in root_uvs:
        if not isinstance(value, (tuple, list)) or len(value) < 2:
            report['errors'].append('Root UV 값은 최소 2성분이어야 합니다.')
            converted_root_uvs.append((0.0, 0.0))
        else:
            converted_root_uvs.append((float(value[0]), float(value[1])))
    root_uvs = converted_root_uvs
    if root_uv_varied:
        report['warnings'].append('POINT Root UV는 각 커브의 루트 값만 사용합니다.')
    if any(not all(math.isfinite(component) for component in uv) for uv in root_uvs):
        report['errors'].append('Root UV에 NaN 또는 무한대가 있습니다.')

    parent_ids, parent_varied = _curve_values(
        attributes['parent_id'], starts, counts, report_variation=True
    )
    if parent_ids is not None:
        parent_ids = [int(value) for value in parent_ids]
        if parent_varied:
            report['errors'].append('POINT ParentID가 한 커브 안에서 변합니다.')

        # Hair Tool authors ParentID in its original zero-based ID space. When
        # the adapter offsets groom_id for UE, ParentID must receive the same
        # authored offset so both attributes remain in one reference space.
        groom_id_offset = int(preview['groom_id_offset']) if preview else 0
        if (
            groom_id_offset
            and attributes['id']
            and attributes['id'].name.casefold() == 'groom_id'
            and attributes['parent_id']
            and attributes['parent_id'].name.casefold() == 'parentid'
        ):
            parent_ids = [
                value + groom_id_offset if value >= 0 else value
                for value in parent_ids
            ]
            report['mappings']['parent_id'] = (
                f'{attributes["parent_id"].name} + Groom ID Offset '
                f'({groom_id_offset})'
            )

    guides, guide_varied = _curve_values(
        attributes['guide'], starts, counts, report_variation=True
    )
    if guides is not None:
        guides = [int(bool(value)) for value in guides]
        if guide_varied:
            report['errors'].append('POINT GUIDE가 한 커브 안에서 변합니다.')
    elif parent_ids and any(parent_id >= 0 for parent_id in parent_ids):
        report['errors'].append(
            'ParentID 연결은 있지만 실제 GUIDE 속성이 없습니다. '
            '가이드를 추론하지 않고 Hair Tool 평가 데이터를 수정하세요.'
        )

    guide_source = report.get('guide_source')
    if guide_source:
        if guides is None:
            report['errors'].append(
                f'Curve Guide "{guide_source["object"]}"가 연결됐지만 평가 결과에 '
                'GUIDE 속성이 없습니다. 외부 Generator 복제본의 Parent Tag를 확인하세요.'
            )
        elif sum(guides) != int(guide_source['curve_count']):
            report['errors'].append(
                f'실제 Curve Guide 수({guide_source["curve_count"]})와 GUIDE 속성 수'
                f'({sum(guides)})가 다릅니다.'
            )
        if parent_ids is None or not any(parent_id >= 0 for parent_id in parent_ids):
            report['errors'].append(
                f'Curve Guide "{guide_source["object"]}"가 연결됐지만 ParentID 연결이 없습니다.'
            )
        elif guides is not None:
            unlinked_children = sum(
                1
                for guide, parent_id in zip(guides, parent_ids)
                if not guide and parent_id < 0
            )
            if unlinked_children:
                report['errors'].append(
                    f'실제 Curve Guide가 있지만 {unlinked_children}개 자식 곡선의 '
                    'ParentID가 연결되지 않았습니다.'
                )

    if parent_ids is not None:
        id_to_index = {value: index for index, value in enumerate(ids)}
        invalid_parent_ids = sorted({
            parent_id for parent_id in parent_ids
            if parent_id >= 0 and parent_id not in id_to_index
        })
        if invalid_parent_ids:
            preview_ids = ', '.join(str(value) for value in invalid_parent_ids[:8])
            report['errors'].append(f'존재하지 않는 ParentID가 있습니다: {preview_ids}')
        if guides:
            bad_guide_parents = sorted({
                parent_id for parent_id in parent_ids
                if parent_id >= 0
                and parent_id in id_to_index
                and not guides[id_to_index[parent_id]]
            })
            if bad_guide_parents:
                preview_ids = ', '.join(str(value) for value in bad_guide_parents[:8])
                report['errors'].append(f'guide가 아닌 커브를 참조하는 ParentID가 있습니다: {preview_ids}')
        if (
            all(parent_id < 0 for parent_id in parent_ids)
            and getattr(adapter, 'deformation_preset', '') != PRESET_UE_RIGGED_GUIDES
        ):
            report['warnings'].append('ParentID가 모두 -1이라 guide-child 연결 정보가 없습니다.')

    # An all -1 ParentID array carries no relationship. Do not write it as
    # Groom data when there is no authored GUIDE attribute.
    if guides is None and parent_ids and all(parent_id < 0 for parent_id in parent_ids):
        parent_ids = None

    width_attribute = attributes['width']
    widths = _point_values(width_attribute, starts, counts)
    if widths is None:
        default_width = max(float(adapter.default_width), 1e-8)
        widths = [default_width] * point_count
        report['warnings'].append('폭 속성이 없어 기본 Width를 사용합니다.')
    else:
        widths = [float(value) for value in widths]
        if width_attribute.name.casefold() == 'radius':
            widths = [value * 2.0 for value in widths]

    world_scale = evaluated_object.matrix_world.to_scale()
    width_scale = sum(abs(float(value)) for value in world_scale) / 3.0
    widths = [value * width_scale for value in widths]
    if any(not math.isfinite(value) or value <= 0.0 for value in widths):
        report['errors'].append('Width에는 유한한 양수만 사용할 수 있습니다.')

    color_attribute = attributes['color']
    explicit_color = bool(str(getattr(adapter, 'color_attribute', '') or '').strip())
    if (
        color_attribute
        and (explicit_color or color_attribute.name.casefold() == 'groom_color')
    ):
        colors = _point_values(color_attribute, starts, counts)
    elif hair_tool_color:
        colors, has_factor, has_system_color = _bake_hair_tool_colors(
            curves, starts, counts, hair_tool_color
        )
        report['mappings']['color'] = (
            f'{hair_tool_color["material"]}:{hair_tool_color["node"]} '
            '(Base→Root→Tip→SystemColor)'
        )
        if not has_factor:
            report['warnings'].append(
                'Hair Tool Factor 속성이 없어 커브 길이 기준 0~1 Factor를 사용합니다.'
            )
        if hair_tool_color['system_color_mix'] and not has_system_color:
            report['warnings'].append(
                'SystemColor 속성이 없어 Hair Tool 시스템 컬러 가산값은 0으로 사용합니다.'
            )
        if hair_tool_color['texture_inputs']:
            report['warnings'].append(
                'Hair Tool 카드 프로필 텍스처 입력은 네이티브 Groom에 직접 대응하지 않아 '
                'Root/Tip/SystemColor 코어 규칙만 groom_color에 베이크합니다: '
                + ', '.join(hair_tool_color['texture_inputs'])
            )
    else:
        colors = _point_values(color_attribute, starts, counts)
        if colors is None and preview and preview['color']:
            colors = [preview['color']] * point_count
    if colors is not None:
        colors = [tuple(float(component) for component in value[:3]) for value in colors]
        if any(not all(math.isfinite(component) and component >= 0.0 for component in value) for value in colors):
            report['errors'].append('groom_color에는 유한한 0 이상의 RGB 값만 사용할 수 있습니다.')

    roughness = _point_values(attributes['roughness'], starts, counts)
    if roughness is None and hair_tool_color and hair_tool_color['roughness'] is not None:
        roughness = [hair_tool_color['roughness']] * point_count
        report['mappings']['roughness'] = (
            f'{hair_tool_color["material"]}:{hair_tool_color["node"]}.SpecRoughness'
        )
    elif roughness is None and preview and preview['roughness'] is not None:
        roughness = [preview['roughness']] * point_count
    if roughness is not None:
        roughness = [float(value) for value in roughness]
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in roughness):
            report['errors'].append('groom_roughness는 0~1 범위여야 합니다.')

    ao = _point_values(attributes['ao'], starts, counts)
    if ao is not None:
        ao = [float(value) for value in ao]
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in ao):
            report['errors'].append('groom_ao는 0~1 범위여야 합니다.')

    clump_ids, clump_varied = _curve_values(
        attributes['clump_id'], starts, counts, report_variation=True
    )
    if clump_ids is not None:
        clump_ids = [int(value) for value in clump_ids]
        if clump_varied:
            report['errors'].append('POINT Clump ID가 한 커브 안에서 변합니다.')

    factors = _point_values(attributes['factor'], starts, counts)
    if factors is not None:
        factors = [float(value) for value in factors]
        if any(not math.isfinite(value) for value in factors):
            report['errors'].append('Hair Tool Factor에 NaN 또는 무한대가 있습니다.')

    random_values = _point_values(attributes['random'], starts, counts)
    if random_values is not None:
        random_values = [
            float(value[0]) if isinstance(value, (tuple, list)) else float(value)
            for value in random_values
        ]
        if any(not math.isfinite(value) for value in random_values):
            report['errors'].append('Hair Tool Random에 NaN 또는 무한대가 있습니다.')

    roundness = _point_values(attributes['roundness'], starts, counts)
    if roundness is not None:
        roundness = [float(value) for value in roundness]

    attribute_names = {attribute.name.casefold() for attribute in curves.attributes}
    report['hair_tool_features'] = {
        'id': 'groom_id / Unreal strand seed',
        'group_id': 'groom_group_id / Unreal Hair Group Index',
        'root_uv': 'FLOAT2 groom_root_uv / Unreal Root UV',
        'guide_parent': (
            'GUIDE + ParentID' if guides is not None else
            'UE generated or rigged guides; ParentID is retained only when authored'
        ),
        'factor': 'color rule + Unreal Hair U' if factors is not None else 'spline fallback',
        'system_color': (
            'baked into groom_color' if 'systemcolor' in attribute_names else 'not authored'
        ),
        'base_color': (
            'HairShaderMain Base Color; HS_BaseColor fallback available'
            if hair_tool_color else 'attribute fallback'
        ),
        'random': 'color variation input' if random_values is not None else 'not authored',
        'ao': 'groom_ao' if ao is not None else 'not authored',
        'clump_id': 'groom_clump_id / Unreal Clump ID' if clump_ids is not None else 'not authored',
        'radius': 'vertex widths (diameter = radius × 2)',
        'roundness': (
            'Cards only; native Groom has no matching per-strand cross-section channel'
            if roundness is not None else 'not authored'
        ),
        'profile_ids': (
            'Profile_ID and UV_Box_ID stay in the Hair Tool card workflow'
            if {'profile_id', 'uv_box_id'} & attribute_names else 'not authored'
        ),
        'surface_frame': (
            'ParentPos/ParentTan/ParentNorm/ParentData are Hair Tool internals; '
            'Unreal derives Groom tangent data from the imported strands'
            if {'parentpos', 'parenttan', 'parentnorm', 'parentdata'} & attribute_names
            else 'not authored'
        ),
        'profile': hair_tool_profile['groom_mapping'] if hair_tool_profile else 'not found',
    }

    world_matrix = evaluated_object.matrix_world.copy()
    points = [tuple(float(value) for value in (world_matrix @ point.position)) for point in curves.points]
    if any(not all(math.isfinite(component) for component in point) for point in points):
        report['errors'].append('포인트 위치에 NaN 또는 무한대가 있습니다.')

    report['data'] = {
        'counts': counts,
        'points': points,
        'widths': widths,
        'ids': ids,
        'group_ids': group_ids,
        'root_uvs': root_uvs,
        'guides': guides,
        'parent_ids': parent_ids,
        'colors': colors,
        'roughness': roughness,
        'ao': ao,
        'clump_ids': clump_ids,
        'factors': factors,
        'random': random_values,
        'roundness': roundness,
        'group_count': max(group_ids) + 1 if group_ids else 1,
    }
    return report


def validate_scene(properties=None):
    objects = get_hair_tool_grooms(properties)
    reports = [inspect_object(scene_object, properties) for scene_object in objects]
    errors = [f'{report["object"]}: {message}' for report in reports for message in report['errors']]
    warnings = [f'{report["object"]}: {message}' for report in reports for message in report['warnings']]
    adapter = get_settings(properties)
    if (
        adapter
        and wants_groom(properties)
        and getattr(adapter, 'deformation_preset', '') == PRESET_CARD_RIG
    ):
        warnings.append(
            'Card Rig (Bone)은 Hair Tool 카드 메시에만 적용됩니다. '
            '동시에 내보내는 Groom은 Unreal Generated Guides를 사용합니다.'
        )
    if wants_groom(properties) and not objects:
        errors.append('Export 컬렉션에서 Groom으로 보낼 Hair Tool CURVES를 찾지 못했습니다.')
    return {'objects': reports, 'errors': errors, 'warnings': warnings}


def _usd_identifier(name):
    identifier = re.sub(r'[^A-Za-z0-9_]', '_', name)
    if not identifier or identifier[0].isdigit():
        identifier = '_' + identifier
    return identifier


def _float(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('USD에 NaN 또는 무한대를 기록할 수 없습니다.')
    text = format(value, '.9g')
    return text if any(character in text for character in '.eE') else text + '.0'


def _array(values, formatter, indent='        ', per_line=8):
    formatted = [formatter(value) for value in values]
    lines = []
    for index in range(0, len(formatted), per_line):
        lines.append(indent + ', '.join(formatted[index:index + per_line]))
    return '[\n' + ',\n'.join(lines) + '\n    ]'


def _tuple2(value):
    return f'({_float(value[0])}, {_float(value[1])})'


def _tuple3(value):
    return f'({_float(value[0])}, {_float(value[1])}, {_float(value[2])})'


def _compensate_unreal_58_end_points(points, counts):
    """Keep imported UE 5.8 strand tips at the Blender positions.

    UE 5.8's GroomBuilder appends a point 10 percent beyond each render
    strand's final segment when triangle strips are enabled. Moving the final
    exported control point inward by the inverse operation makes that appended
    point land on Blender's original tip without changing the Blender scene.
    """
    adjusted = list(points)
    offset = 0
    for count in counts:
        if count >= 2:
            previous = points[offset + count - 2]
            tip = points[offset + count - 1]
            adjusted[offset + count - 1] = tuple(
                (tip[axis] + 0.1 * previous[axis]) / 1.1
                for axis in range(3)
            )
        offset += count
    return adjusted


def write_usda(scene_object, file_path, properties=None):
    """Write a static UE 5.8 Groom USD and return its validation report."""
    report = inspect_object(scene_object, properties)
    if report['errors']:
        raise RuntimeError('\n'.join(report['errors']))

    data = report['data']
    export_points = _compensate_unreal_58_end_points(data['points'], data['counts'])
    report['ue_58_end_point_compensation'] = True
    prim_name = _usd_identifier(scene_object.name)
    lines = [
        '#usda 1.0',
        '(',
        f'    defaultPrim = "{prim_name}"',
        '    metersPerUnit = 1',
        '    upAxis = "Z"',
        ')',
        '',
        f'def BasisCurves "{prim_name}" (',
        '    prepend apiSchemas = ["GroomAPI"]',
        ')',
        '{',
        '    uniform token type = "linear"',
        '    uniform token wrap = "nonperiodic"',
        '    int[] curveVertexCounts = ' + _array(data['counts'], str),
        '    point3f[] points = ' + _array(export_points, _tuple3, per_line=3),
        '    float[] widths = ' + _array(data['widths'], _float) + ' (',
        '        interpolation = "vertex"',
        '    )',
        '    int[] primvars:groom_id = ' + _array(data['ids'], str) + ' (',
        '        interpolation = "uniform"',
        '    )',
        '    int[] primvars:groom_group_id = ' + _array(data['group_ids'], str) + ' (',
        '        interpolation = "uniform"',
        '    )',
        '    float2[] primvars:groom_root_uv = ' + _array(data['root_uvs'], _tuple2, per_line=4) + ' (',
        '        interpolation = "uniform"',
        '    )',
    ]
    if data['colors'] is not None:
        lines.extend([
            '    float3[] primvars:groom_color = ' + _array(data['colors'], _tuple3, per_line=3) + ' (',
            '        interpolation = "vertex"',
            '    )',
        ])
    if data['roughness'] is not None:
        lines.extend([
            '    float[] primvars:groom_roughness = ' + _array(data['roughness'], _float) + ' (',
            '        interpolation = "vertex"',
            '    )',
        ])
    if data['ao'] is not None:
        lines.extend([
            '    float[] primvars:groom_ao = ' + _array(data['ao'], _float) + ' (',
            '        interpolation = "vertex"',
            '    )',
        ])
    if data['clump_ids'] is not None:
        lines.extend([
            '    int[] primvars:groom_clump_id = ' + _array(data['clump_ids'], str) + ' (',
            '        interpolation = "uniform"',
            '    )',
        ])
    if data['guides'] is not None:
        lines.extend([
            '    int[] primvars:groom_guide = ' + _array(data['guides'], str) + ' (',
            '        interpolation = "uniform"',
            '    )',
        ])
    if data['parent_ids'] is not None:
        lines.extend([
            '    int[] primvars:groom_parent_id = ' + _array(data['parent_ids'], str) + ' (',
            '        interpolation = "uniform"',
            '    )',
        ])
    lines.extend(['}', ''])

    folder = os.path.dirname(os.path.abspath(file_path))
    os.makedirs(folder, exist_ok=True)
    with open(file_path, 'w', encoding='utf-8', newline='\n') as usd_file:
        usd_file.write('\n'.join(lines))
    return report
