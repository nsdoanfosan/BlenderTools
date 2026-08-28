from pathlib import Path
import math
import sys
import tempfile

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "addons"))

from send2ue.core import hair_tool_export


mesh = bpy.data.meshes.new("HTUE_CHAOS_FBX_ROUNDTRIP")
mesh.from_pydata(
    [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
    [],
    [(0, 1, 2)],
)
mesh.update()

uv_source = mesh.attributes.new("UVMapGN", "FLOAT_VECTOR", "CORNER")
for item, value in zip(
    uv_source.data,
    ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
):
    item.vector = value

values = {
    "Random": (0.1, 0.5, 0.9),
    "Factor": (0.0, 0.5, 1.0),
    "AO": (0.25, 0.5, 0.75),
    "Depth": (0.0, 0.5, 1.0),
    "ChaosWeight": (0.0, 0.375, 1.0),
    "HairPixelDepthOffset": (1.0, 0.625, 0.125),
}
for name, samples in values.items():
    attribute = mesh.attributes.new(name, "FLOAT", "POINT")
    for item, value in zip(attribute.data, samples):
        item.value = value

system_color = mesh.attributes.new("SystemColor", "FLOAT_COLOR", "POINT")
for item, color in zip(
    system_color.data,
    ((0.1, 0.2, 0.3, 1.0), (0.4, 0.5, 0.6, 1.0), (0.7, 0.8, 0.9, 1.0)),
):
    item.color = color

hair_tool_export._write_hair_tool_uvs(mesh)
hair_tool_export._pack_rfaos(mesh)

source = bpy.data.objects.new("HTUE_CHAOS_FBX_ROUNDTRIP", mesh)
bpy.context.scene.collection.objects.link(source)
bpy.ops.object.select_all(action="DESELECT")
source.select_set(True)
bpy.context.view_layer.objects.active = source

with tempfile.TemporaryDirectory(prefix="htue_chaos_fbx_") as temporary_directory:
    fbx_path = str(Path(temporary_directory) / "chaos_masks.fbx")
    export_result = bpy.ops.export_scene.fbx(
        filepath=fbx_path,
        use_selection=True,
        bake_anim=False,
        add_leaf_bones=False,
    )
    assert export_result == {"FINISHED"}, export_result

    bpy.data.objects.remove(source, do_unlink=True)
    import_result = bpy.ops.import_scene.fbx(filepath=fbx_path)
    assert import_result == {"FINISHED"}, import_result

imported_mesh_objects = [
    obj
    for obj in bpy.context.scene.objects
    if obj.type == "MESH" and obj.name.startswith("HTUE_CHAOS_FBX_ROUNDTRIP")
]
assert len(imported_mesh_objects) == 1, [obj.name for obj in imported_mesh_objects]
imported = imported_mesh_objects[0]
imported_mesh = imported.data
assert len(imported_mesh.uv_layers) == 4
assert "RFAOS" in imported_mesh.color_attributes

attribute = imported_mesh.color_attributes["RFAOS"]
assert attribute.domain == "CORNER"
for loop_index, loop in enumerate(imported_mesh.loops):
    vertex_index = loop.vertex_index
    color_item = attribute.data[loop_index]
    color = (
        color_item.color_srgb
        if hasattr(color_item, "color_srgb")
        else color_item.color
    )
    expected = (
        values["HairPixelDepthOffset"][vertex_index],
        values["ChaosWeight"][vertex_index],
        values["AO"][vertex_index],
        1.0,
    )
    for actual, target in zip(color, expected):
        assert math.isclose(
            actual,
            target,
            abs_tol=1.0 / 255.0 + 1.0e-6,
        ), (loop_index, color, expected)

print("SEND2UE_HAIR_TOOL_CHAOS_FBX_ROUNDTRIP_OK")
