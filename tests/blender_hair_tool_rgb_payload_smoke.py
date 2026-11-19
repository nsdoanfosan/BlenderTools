from pathlib import Path
import math
import sys
import tempfile

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "addons"))

from send2ue.core import hair_mesh_ao, hair_tool_export, ue_groom_adapter


def close(actual, expected, tolerance=1.0e-6):
    assert math.isclose(actual, expected, abs_tol=tolerance), (actual, expected)


# Removing an empty slot must preserve the material choice of every polygon.
slot_mesh = bpy.data.meshes.new("HTUE_MATERIAL_SLOT_SMOKE")
slot_mesh.from_pydata(
    [(0, 0, 0), (1, 0, 0), (0, 1, 0),
     (2, 0, 0), (3, 0, 0), (2, 1, 0),
     (4, 0, 0), (5, 0, 0), (4, 1, 0)],
    [],
    [(0, 1, 2), (3, 4, 5), (6, 7, 8)],
)
slot_a = bpy.data.materials.new("HTUE_SLOT_A")
slot_b = bpy.data.materials.new("HTUE_SLOT_B")
slot_mesh.materials.append(slot_a)
slot_mesh.materials.append(None)
slot_mesh.materials.append(slot_b)
for polygon, material_index in zip(slot_mesh.polygons, (0, 1, 2)):
    polygon.material_index = material_index
slot_object = bpy.data.objects.new("HTUE_MATERIAL_SLOT_SMOKE", slot_mesh)
bpy.context.scene.collection.objects.link(slot_object)
hair_tool_export._remove_empty_material_slots(slot_object)
assert [material.name for material in slot_mesh.materials] == [
    "HTUE_SLOT_A",
    "HTUE_SLOT_B",
]
assert [polygon.material_index for polygon in slot_mesh.polygons] == [0, 0, 1]
bpy.data.objects.remove(slot_object, do_unlink=True)
bpy.data.meshes.remove(slot_mesh)
bpy.data.materials.remove(slot_a)
bpy.data.materials.remove(slot_b)


# Whole-mesh AO includes reference geometry while preserving card topology.
card_mesh = bpy.data.meshes.new("AO_CARD")
card_mesh.from_pydata([(0, 0, 0), (0.001, 0, 0), (0, 0.001, 0)], [], [(0, 1, 2)])
card = bpy.data.objects.new("AO_CARD", card_mesh)
face_mesh = bpy.data.meshes.new("AO_FACE")
face_mesh.from_pydata(
    [(-1, -1, 0.002), (1, -1, 0.002), (1, 1, 0.002), (-1, 1, 0.002)],
    [], [(0, 1, 2, 3)],
)
face = bpy.data.objects.new("AO_FACE", face_mesh)
for obj in (card, face):
    bpy.context.scene.collection.objects.link(obj)
depsgraph = bpy.context.evaluated_depsgraph_get()
hair_mesh_ao.bake_group([(card, 0.011)], (card,), (), depsgraph)
assert all(item.value == 1.0 for item in card_mesh.attributes[hair_mesh_ao.ATTRIBUTE].data)
hair_mesh_ao.bake_group([(card, 0.011)], (card,), (face,), depsgraph)
assert all(item.value < 0.8 for item in card_mesh.attributes[hair_mesh_ao.ATTRIBUTE].data)
assert len(card_mesh.polygons) == 1 and face_mesh.attributes.get(hair_mesh_ao.ATTRIBUTE) is None
for obj in (card, face):
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(data)


# An unrelated nearby surface and another fur part do not occlude a system.
def make_triangle(name, height, *, size=0.02):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(-size,-size,height),(size,-size,height),(0,size,height)], [], [(0,1,2)])
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


setup = bpy.data.node_groups.new("Hair_System_Setup_AO_SMOKE", "GeometryNodeTree")
source_input = setup.interface.new_socket(
    name="Source Surface", in_out="INPUT", socket_type="NodeSocketObject")
profile = bpy.data.node_groups.new("Hair_System_Profile_AO_SMOKE", "GeometryNodeTree")
for group in (setup, profile):
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group.links.new(group.nodes.new("NodeGroupInput").outputs["Geometry"],
                    group.nodes.new("NodeGroupOutput").inputs["Geometry"])


def make_system(name, upstream):
    obj = make_triangle(name, 0)
    modifier = obj.modifiers.new("Setup", "NODES")
    modifier.node_group = setup
    hair_tool_export._modifier_input_set(modifier, source_input.identifier, upstream)
    obj.modifiers.new("Profile", "NODES").node_group = profile
    return obj


base = make_triangle("AO_GROWTH_BASE", 0.002)
distant_base = make_triangle("AO_GROWTH_DISTANT", 0.1)
rope = make_triangle("AO_UNRELATED_ROPE", 0.001)
mirrored_base = make_triangle("AO_DECLARED_MIRROR_BASE", 0.003)
guide = make_system("AO_GROWTH_GUIDE", base)
system_one = make_system("AO_GROWTH_SYSTEM_ONE", guide)
system_two = make_system("AO_GROWTH_SYSTEM_TWO", distant_base)
fur_one = make_triangle("AO_FUR_ONE", 0, size=0.001)
fur_two = make_triangle("AO_FUR_TWO", 0, size=0.001)
root = bpy.data.objects.new("AO_GROWTH_ROOT", None)
bpy.context.scene.collection.objects.link(root)
short_fur = bpy.data.materials.new("M_HT_Default_Material_short_02")
unused = bpy.data.materials.new("AO_UNUSED_MATERIAL")
for part in (fur_one, fur_two):
    part.data.materials.append(unused)
    part.data.materials.append(short_fur)
    part.data.polygons[0].material_index = 1
bpy.context.view_layer.update()
parts = ((system_one, [fur_one]), (system_two, [fur_two]))
receipt, mirrors, fur_corners = hair_tool_export._bake_short_fur_ao(root, parts)
assert mirrors == {}
assert receipt == {system_one.name:[base.name], system_two.name:[distant_base.name]}
assert fur_corners == 6
ao_values = lambda part: [value.value-1.0 for value in part.data.attributes[hair_tool_export.GROWTH_AO_ATTRIBUTE].data]
assert all(value < 0.8 for value in ao_values(fur_one))
assert ao_values(fur_two) == [1.0] * 3
system_two[hair_tool_export.EXTRA_GROWTH_SURFACE_PROPERTY] = mirrored_base
receipt, mirrors, _ = hair_tool_export._bake_short_fur_ao(root, parts)
assert mirrors == {}
assert receipt[system_two.name] == [distant_base.name, mirrored_base.name]
assert all(value < 0.9 for value in ao_values(fur_two))
expected = ao_values(fur_one) + ao_values(fur_two)
joined_fur = hair_tool_export._join_objects([fur_one,fur_two])
assert ao_values(joined_fur) == expected
hair_tool_export._write_hair_tool_uvs(joined_fur.data)
hair_tool_export._pack_rfaos(joined_fur.data, has_whole_mesh_ao=True)
assert hair_tool_export.get_rfaos_payload_contract(joined_fur)['ao_uv_tag'] == 8.0

mixed_mesh = bpy.data.meshes.new("AO_MIXED_FUR")
mixed_mesh.from_pydata([(0,0,0), (0.001,0,0), (0,0.001,0), (-0.001,0,0)], [],
                       [(0,1,2),(0,2,3)])
mixed_mesh.materials.append(short_fur)
mixed_mesh.materials.append(unused)
mixed_mesh.polygons[1].material_index = 1
mixed_fur = bpy.data.objects.new("AO_MIXED_FUR", mixed_mesh)
bpy.context.scene.collection.objects.link(mixed_fur)
_, _, mixed_corners = hair_tool_export._bake_short_fur_ao(root, ((system_one,[mixed_fur]),))
assert mixed_corners == 3
growth = [item.value for item in mixed_mesh.attributes[hair_tool_export.GROWTH_AO_ATTRIBUTE].data]
assert all(value > 1.0 for value in growth[:3]) and growth[3:] == [0.0, 0.0, 0.0]
hair_tool_export._write_hair_tool_uvs(mixed_mesh)
hair_tool_export._pack_rfaos(mixed_mesh, has_whole_mesh_ao=True)
assert hair_tool_export.get_rfaos_payload_contract(mixed_fur)['ao_uv_tag'] == 6.0
broad = mixed_mesh.attributes.new(hair_mesh_ao.ATTRIBUTE, 'FLOAT', 'POINT')
broad.data.foreach_set('value', [0.25] * len(broad.data))
hair_tool_export._pack_rfaos(mixed_mesh, has_whole_mesh_ao=True)
assert hair_tool_export.get_rfaos_payload_contract(mixed_fur)['ao_uv_tag'] == 8.0
blues = [item.color_srgb[2] for item in mixed_mesh.color_attributes['RFAOS'].data]
assert all(abs(value - (source - 1.0)) < 1/255 for value, source in zip(blues[:3], growth[:3]))
assert all(abs(value - 0.25) < 1/255 for value in blues[3:])

def geometry_group(name):
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    for direction in ("INPUT", "OUTPUT"):
        group.interface.new_socket(name="Geometry", in_out=direction, socket_type="NodeSocketGeometry")
    return group


mirror_group = geometry_group("HD_Mirror")
for name, socket_type, value in (
    ("Mask", "NodeSocketFloat", 1.0),
    ("Bisect", "NodeSocketBool", False),
    ("Flip", "NodeSocketBool", False),
    ("Mirror Target", "NodeSocketObject", None),
):
    mirror_group.interface.new_socket(name=name, in_out="INPUT", socket_type=socket_type).default_value = value
reflection = mirror_group.nodes.new("FunctionNodeCombineTransform")
reflection.name = "Combine Transform.002"
reflection.inputs["Scale"].default_value = (-1, 1, 1)
transform = mirror_group.nodes.new("GeometryNodeTransform")
transform.inputs["Mode"].default_value = 'Matrix'
mirror_group.links.new(reflection.outputs[0], transform.inputs["Transform"])
mirror_group.links.new(mirror_group.nodes.new("NodeGroupInput").outputs["Geometry"], transform.inputs["Geometry"])
mirror_group.links.new(transform.outputs["Geometry"], mirror_group.nodes.new("NodeGroupOutput").inputs["Geometry"])

main_group = geometry_group("Hair_System_Main_Mirror_AO_SMOKE")
mirror = main_group.nodes.new("GeometryNodeGroup")
mirror.node_tree = mirror_group
main_group.links.new(main_group.nodes.new("NodeGroupInput").outputs["Geometry"], mirror.inputs["Geometry"])
main_group.links.new(mirror.outputs["Geometry"], main_group.nodes.new("NodeGroupOutput").inputs["Geometry"])
first_mirror = system_one.modifiers.new("Strands from Grid Surface", "NODES")
first_mirror.node_group = main_group
second_mirror = guide.modifiers.new("Filter", "NODES")
second_mirror.node_group = main_group
mirror_fur = make_triangle("AO_FUR_REFLECTION", 0.0, size=0.001)
mirror_fur.data.materials.append(short_fur)
base.location.x = 0.1
mirror_fur.location.x = -0.1
bpy.context.view_layer.update()
mirror_parts = ((system_one, [mirror_fur]),)
receipt, mirrors, _ = hair_tool_export._bake_short_fur_ao(root, mirror_parts)
assert receipt == {system_one.name: [base.name]}
assert mirrors == {system_one.name: ((-1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1))}
assert all(value < 0.8 for value in ao_values(mirror_fur))
first_mirror.show_viewport = False
mirror.mute = True
bpy.context.view_layer.update()
receipt, mirrors, _ = hair_tool_export._bake_short_fur_ao(root, mirror_parts)
assert not mirrors and ao_values(mirror_fur) == [1.0] * 3
mirror.mute = False
first_mirror.show_viewport = True
guide.location.x = 0.01
bpy.context.view_layer.update()
try:
    hair_tool_export._bake_short_fur_ao(root, mirror_parts)
except RuntimeError as error:
    assert "Conflicting Hair Tool growth reflections" in str(error)
else:
    raise AssertionError("Conflicting mirrors must not bake incorrect AO")
system_one[hair_tool_export.EXTRA_GROWTH_SURFACE_PROPERTY] = mirrored_base
receipt, mirrors, _ = hair_tool_export._bake_short_fur_ao(root, mirror_parts)
assert receipt == {system_one.name: [base.name, mirrored_base.name]}
assert not mirrors and ao_values(mirror_fur) == [1.0] * 3

for obj in (joined_fur, mixed_fur, mirror_fur, system_one, system_two, guide, base, distant_base, rope, mirrored_base, root):
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data and data.users == 0:
        bpy.data.meshes.remove(data)
bpy.data.node_groups.remove(setup)
bpy.data.node_groups.remove(profile)
bpy.data.node_groups.remove(main_group)
bpy.data.node_groups.remove(mirror_group)
bpy.data.materials.remove(short_fur)
bpy.data.materials.remove(unused)


mesh = bpy.data.meshes.new("HTUE_RGB_PAYLOAD_SMOKE")
mesh.from_pydata(
    [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
    [],
    [(0, 1, 2)],
)
mesh.update()

uv_source = mesh.attributes.new(name="UVMapGN", type="FLOAT_VECTOR", domain="CORNER")
for item, value in zip(uv_source.data, ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))):
    item.vector = value

scalar_values = {
    "Random": (0.125, 0.5, 0.875),
    "Factor": (0.25, 0.5, 1.0),
    "AO": (0.25, 0.75, 0.5),
    hair_mesh_ao.ATTRIBUTE: (0.55, 0.45, 0.65),
    "Depth": (0.5, 0.0, 1.0),
    "ChaosWeight": (0.0, 0.25, 1.0),
    "HairPixelDepthOffset": (1.0, 0.75, 0.125),
}
for name, values in scalar_values.items():
    attribute = mesh.attributes.new(name=name, type="FLOAT", domain="POINT")
    for item, value in zip(attribute.data, values):
        item.value = value

# A bridge-assigned Export Empty groups a directly linked Hair Tool output
# without reparenting it. Render-disabled links are intentionally skipped.
export_collection = bpy.data.collections.new("Export")
bpy.context.scene.collection.children.link(export_collection)
inherited_target = bpy.data.objects.new("Hair_Parent_Target_SMOKE", None)
export_target = bpy.data.objects.new("Hair_Export_Target_SMOKE", None)
export_collection.objects.link(inherited_target)
export_collection.objects.link(export_target)
export_source = bpy.data.objects.new("Hair_Export_Source_SMOKE", mesh)
export_collection.objects.link(export_source)
export_source.parent = inherited_target
upstream_source = bpy.data.objects.new("Hair_Upstream_Source_SMOKE", mesh)
export_collection.objects.link(upstream_source)
upstream_source.parent = inherited_target
setup_group = bpy.data.node_groups.new("Hair_System_Setup_EXPORT_SMOKE", "GeometryNodeTree")
setup_input = setup_group.interface.new_socket(
    name="Upstream Hair System",
    in_out="INPUT",
    socket_type="NodeSocketObject",
)
profile_group = bpy.data.node_groups.new("Hair_System_Profile_EXPORT_SMOKE", "GeometryNodeTree")
setup_modifier = export_source.modifiers.new("Hair_System_Setup", "NODES")
setup_modifier.node_group = setup_group
profile_modifier = export_source.modifiers.new("Profile", "NODES")
profile_modifier.node_group = profile_group
upstream_setup = upstream_source.modifiers.new("Hair_System_Setup", "NODES")
upstream_setup.node_group = setup_group
upstream_profile = upstream_source.modifiers.new("Profile", "NODES")
upstream_profile.node_group = profile_group
hair_tool_export._modifier_input_set(setup_modifier, setup_input.identifier, upstream_source)
assert hair_tool_export._modifier_input_has(setup_modifier, setup_input.identifier)
assert (
    hair_tool_export._modifier_input_get(setup_modifier, setup_input.identifier)
    is upstream_source
)
assert upstream_source in hair_tool_export._modifier_input_values(setup_modifier)
assert (
    ue_groom_adapter._modifier_input(setup_modifier, "Upstream Hair System")
    is upstream_source
)
export_source[hair_tool_export.EXPORT_TARGET_PROPERTY] = export_target
upstream_source[hair_tool_export.EXPORT_TARGET_PROPERTY] = inherited_target
original_parent = export_source.parent
original_matrix = export_source.matrix_world.copy()
assert hair_tool_export._asset_group_key(export_source) == export_target
export_target.name = "Hair_Export_Target_Renamed_SMOKE"
assert hair_tool_export._asset_group_key(export_source) == export_target
assert export_source.parent == original_parent
assert export_source.matrix_world == original_matrix
assert export_source in hair_tool_export._export_source_candidates(export_collection)
final_sources = hair_tool_export._final_export_sources(export_collection)
assert export_source in final_sources
assert upstream_source not in final_sources
export_source.hide_render = True
assert export_source not in hair_tool_export._export_source_candidates(export_collection)
export_source.hide_render = False
export_collection.objects.unlink(export_target)
try:
    hair_tool_export._asset_group_key(export_source, export_collection)
except RuntimeError as error:
    assert "Relink" in str(error)
else:
    raise AssertionError("A stale explicit Export assignment must not silently fall back")
del export_source[hair_tool_export.EXPORT_TARGET_PROPERTY]
assert hair_tool_export._asset_group_key(export_source, export_collection) == inherited_target
bpy.data.objects.remove(export_source, do_unlink=True)
bpy.data.objects.remove(upstream_source, do_unlink=True)
bpy.data.objects.remove(export_target, do_unlink=True)
bpy.data.objects.remove(inherited_target, do_unlink=True)
bpy.data.node_groups.remove(setup_group)
bpy.data.node_groups.remove(profile_group)
bpy.data.collections.remove(export_collection)
system_colors = (
    (0.125, 0.25, 0.375, 0.0),
    (0.5, 0.625, 0.75, 1.0),
    (0.875, 1.0, 0.0, 0.5),
)
system_color = mesh.attributes.new(name="SystemColor", type="FLOAT_COLOR", domain="POINT")
for item, value in zip(system_color.data, system_colors):
    item.color = value

# Per-System mode preserves Hair Tool's AO while evaluated card geometry is
# joined. Validation must not replace valid values.
selector_object = bpy.data.objects.new("HTUE_AO_POLICY_SMOKE", mesh)
selection_state = {"ao_stats": {}}
selected = hair_tool_export._preserve_per_system_ao(
    selector_object,
    selection_state,
)
close(selected["mean"], 0.5)
assert selected["source"] == "per_hair_tool_system"
assert selected["fallback"] is False
bpy.data.objects.remove(selector_object, do_unlink=True)

hair_tool_export._write_hair_tool_uvs(mesh)
hair_tool_export._pack_rfaos(mesh, has_whole_mesh_ao=True)

assert [layer.name for layer in mesh.uv_layers] == [
    "UVMap",
    "HairTool_SystemColor_RG",
    "HairTool_RFAOS_RG",
    "HairTool_AO_SystemB",
]

for loop_index, loop in enumerate(mesh.loops):
    vertex_index = loop.vertex_index
    random_value = scalar_values["Random"][vertex_index]
    factor = scalar_values["Factor"][vertex_index]
    ao = scalar_values["AO"][vertex_index]
    depth = scalar_values["Depth"][vertex_index]
    chaos_weight = scalar_values["ChaosWeight"][vertex_index]
    pixel_depth_offset = scalar_values["HairPixelDepthOffset"][vertex_index]
    red, green, blue, _alpha = system_colors[vertex_index]

    uv1 = mesh.uv_layers[1].data[loop_index].uv
    uv2 = mesh.uv_layers[2].data[loop_index].uv
    uv3 = mesh.uv_layers[3].data[loop_index].uv
    close(uv1[0], red)
    close(uv1[1], 1.0 - green)
    close(
        uv2[0],
        hair_tool_export.RFAOS_NANITE_UV_TAG
        + hair_tool_export._pack_unorm8_pair(random_value, depth),
    )
    close(uv2[1], 1.0 - factor)
    close(uv3[0], hair_tool_export.WHOLE_MESH_AO_UV_TAG + ao)
    close(uv3[1], 1.0 - blue)

    color_item = mesh.color_attributes["RFAOS"].data[loop_index]
    color = color_item.color_srgb if hasattr(color_item, "color_srgb") else color_item.color
    close(color[0], pixel_depth_offset, 1.0 / 255.0 + 1.0e-6)
    close(color[1], chaos_weight, 1.0 / 255.0 + 1.0e-6)
    close(color[2], scalar_values[hair_mesh_ao.ATTRIBUTE][vertex_index], 1.0 / 255.0 + 1.0e-6)
    close(color[3], 1.0, 1.0 / 255.0 + 1.0e-6)

contract = hair_tool_export.get_rfaos_payload_contract()
assert contract["version"] == 7
assert contract["uv_tag"] == 6.0
assert contract["whole_mesh_ao_uv_tag"] == 8.0
assert contract["vertex_color_channels"]["B"] == hair_mesh_ao.ATTRIBUTE
assert contract["encoding"] == "HTUE_RGB_TAGGED_UV"
assert contract["system_color_alpha_used"] is False
assert contract["material_texcoord_indices"] == [1, 2, 3]
assert contract["chaos_weight_attribute"] == "ChaosWeight"
assert contract["chaos_weight_channel"] == "G"
assert contract["pixel_depth_offset_attribute"] == "HairPixelDepthOffset"
assert contract["pixel_depth_offset_channel"] == "R"
assert contract["pixel_depth_offset_fallback"] == 1.0
assert contract["system_color_absent_raw_b"] == -1.0

# Joining an absent-color part with valid black must keep their distinction.
# Alpha=0 is still a real RGB attribute, and all Factor values must survive.
mesh.attributes.remove(mesh.attributes[hair_tool_export.SYSTEM_COLOR_PRESENT_ATTRIBUTE])
black_color = mesh.attributes.new(name='SystemColor', type='FLOAT_COLOR', domain='POINT')
for item in black_color.data:
    item.color = (0.0, 0.0, 0.0, 0.0)
absent_mesh = mesh.copy()
absent_mesh.attributes.remove(absent_mesh.attributes['SystemColor'])
parts = []
for source_mesh in (mesh, absent_mesh):
    hair_tool_export._stamp_system_color_presence(source_mesh)
    part = bpy.data.objects.new('HTUE_PRESENCE_JOIN', source_mesh)
    bpy.context.scene.collection.objects.link(part)
    parts.append(part)
joined = hair_tool_export._join_objects(parts)
hair_tool_export._pack_rfaos(joined.data)
present_count = absent_count = 0
for loop_index in range(len(joined.data.loops)):
    marker = joined.data.attributes[hair_tool_export.SYSTEM_COLOR_PRESENT_ATTRIBUTE].data[loop_index].value
    uv3 = joined.data.uv_layers[3].data[loop_index].uv
    assert 6.0 <= uv3[0] <= 7.0
    close(uv3[1], 1.0 if marker > 0.5 else 2.0)
    close(joined.data.color_attributes["RFAOS"].data[loop_index].color_srgb[2], 1.0)
    factor = hair_tool_export._attribute_component(joined.data, joined.data.attributes['Factor'], loop_index)
    close(joined.data.uv_layers[2].data[loop_index].uv[1], 1.0 - factor)
    if marker > 0.5:
        present_count += 1
    else:
        absent_count += 1
assert present_count == absent_count == 3

# The out-of-range sentinel must survive real FBX serialization as V=2.
bpy.ops.object.select_all(action='DESELECT')
joined.select_set(True)
bpy.context.view_layer.objects.active = joined
expected_v = sorted(item.uv[1] for item in joined.data.uv_layers[3].data)
expected_colors = sorted(tuple(item.color_srgb) for item in joined.data.color_attributes['RFAOS'].data)
with tempfile.TemporaryDirectory(prefix='htue_presence_fbx_') as temporary_directory:
    fbx_path = str(Path(temporary_directory) / 'presence.fbx')
    assert bpy.ops.export_scene.fbx(filepath=fbx_path, use_selection=True, bake_anim=False) == {'FINISHED'}
    bpy.data.objects.remove(joined, do_unlink=True)
    assert bpy.ops.import_scene.fbx(filepath=fbx_path) == {'FINISHED'}
    imported = next(obj for obj in bpy.context.selected_objects if obj.type == 'MESH')
    actual_v = sorted(item.uv[1] for item in imported.data.uv_layers[3].data)
    assert len(actual_v) == len(expected_v)
    for actual, expected in zip(actual_v, expected_v):
        close(actual, expected)
    actual_colors = sorted(tuple(item.color_srgb) for item in imported.data.color_attributes['RFAOS'].data)
    assert actual_colors == expected_colors

print("SEND2UE_HAIR_TOOL_RGB_PAYLOAD_SMOKE_OK")
