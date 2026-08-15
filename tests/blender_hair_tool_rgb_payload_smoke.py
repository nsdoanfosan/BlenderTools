from pathlib import Path
import math
import sys

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "addons"))

from send2ue.core import hair_tool_export


def close(actual, expected, tolerance=1.0e-6):
    assert math.isclose(actual, expected, abs_tol=tolerance), (actual, expected)


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
    "Depth": (0.5, 0.0, 1.0),
}
for name, values in scalar_values.items():
    attribute = mesh.attributes.new(name=name, type="FLOAT", domain="POINT")
    for item, value in zip(attribute.data, values):
        item.value = value

system_colors = (
    (0.125, 0.25, 0.375, 0.0),
    (0.5, 0.625, 0.75, 1.0),
    (0.875, 1.0, 0.0, 0.5),
)
system_color = mesh.attributes.new(name="SystemColor", type="FLOAT_COLOR", domain="POINT")
for item, value in zip(system_color.data, system_colors):
    item.color = value

hair_tool_export._write_hair_tool_uvs(mesh)
hair_tool_export._pack_rfaos(mesh)

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
    close(uv3[0], hair_tool_export.RFAOS_NANITE_UV_TAG + ao)
    close(uv3[1], 1.0 - blue)

    color_item = mesh.color_attributes["RFAOS"].data[loop_index]
    color = color_item.color_srgb if hasattr(color_item, "color_srgb") else color_item.color
    close(color[0], random_value, 1.0 / 255.0 + 1.0e-6)
    close(color[1], factor, 1.0 / 255.0 + 1.0e-6)
    close(color[2], ao, 1.0 / 255.0 + 1.0e-6)
    close(color[3], 1.0, 1.0 / 255.0 + 1.0e-6)

contract = hair_tool_export.get_rfaos_payload_contract()
assert contract["version"] == 3
assert contract["encoding"] == "HTUE_RGB_TAGGED_UV"
assert contract["system_color_alpha_used"] is False
assert contract["material_texcoord_indices"] == [1, 2, 3]

print("SEND2UE_HAIR_TOOL_RGB_PAYLOAD_SMOKE_OK")
