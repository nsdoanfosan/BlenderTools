"""Manual end-to-end check of the negative-scale export fix against real Unreal.

The automated smoke test (``tests/blender_negative_scale_export_smoke.py``)
models what an importer does. This one measures what Unreal actually does, which
is how the first attempt at this fix was caught: it corrected the winding and
silently inverted the shading normals, and a winding-only assertion passed.

Requires a running Unreal Editor with Python remote execution enabled
(``[/Script/PythonScriptPlugin.PythonScriptPluginSettings] bRemoteExecution=True``).
Assets are imported with ``save=False`` into ``/Game/Codex/Tests/NegScaleFinal``
and deleted at the end, so nothing reaches disk or source control.

Usage:

    # 1. Export the probe FBX files
    blender --background --factory-startup --addons io_scene_fbx \
        --python tests/manual/verify_negative_scale_in_unreal.py -- --export <dir>

    # 2. Measure them in the running editor
    python tests/manual/verify_negative_scale_in_unreal.py --measure <dir> \
        [--multicast-bind-address <local ip>]

Correct means both measures match the no-negative-scale baseline:

    winding_outward   index-buffer order after Unreal bakes the node transform
    shading_outward   the explicit per-corner normals the FBX authored

Reference result on Unreal 5.8.2 / Blender 5.1.2:

    asset          det  winding  shading   verdict
    Baseline       +1     -1       +1      CORRECT
    Raw            -1     +1       +1      winding inverted   (control)
    Fixed          -1     -1       +1      CORRECT
    UniformNeg     -1     -1       +1      CORRECT
    TwoAxisNeg     +1     -1       +1      CORRECT (det>0, not compensated)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CASES = (
    ("SM_NegFinal_Baseline", (1.0, 1.0, 1.0), False, "reference / no handling"),
    ("SM_NegFinal_Raw", (-1.0, 1.0, 1.0), False, "control / no handling"),
    ("SM_NegFinal_Fixed", (-1.0, 1.0, 1.0), True, "shipped fix"),
    ("SM_NegFinal_UniformNeg", (-1.0, -1.0, -1.0), True, "shipped fix"),
    ("SM_NegFinal_TwoAxisNeg", (-1.0, -1.0, 1.0), True, "shipped fix, det > 0"),
)

EXPORT_SETTINGS = dict(
    use_selection=True,
    object_types={"ARMATURE", "MESH", "EMPTY"},
    global_scale=1.0,
    apply_scale_options="FBX_SCALE_NONE",
    axis_forward="Y",
    axis_up="Z",
    apply_unit_scale=True,
    bake_space_transform=False,
    mesh_smooth_type="FACE",
    use_mesh_modifiers=True,
    use_mesh_modifiers_render=True,
    use_mesh_edges=False,
    use_tspace=False,
    use_custom_props=False,
    bake_anim=False,
)

UNREAL_SIDE = r'''
import json
import unreal

DEST = "/Game/Codex/Tests/NegScaleFinal"


def build_task(fbx_path, asset_name):
    options = unreal.FbxImportUI()
    options.set_editor_property("import_mesh", True)
    options.set_editor_property("import_as_skeletal", False)
    options.set_editor_property("import_materials", False)
    options.set_editor_property("import_textures", False)
    options.set_editor_property(
        "mesh_type_to_import", unreal.FBXImportType.FBXIT_STATIC_MESH
    )
    mesh_data = options.static_mesh_import_data
    mesh_data.set_editor_property("combine_meshes", True)
    mesh_data.set_editor_property("generate_lightmap_u_vs", False)
    mesh_data.set_editor_property("auto_generate_collision", False)
    mesh_data.set_editor_property("transform_vertex_to_absolute", True)

    task = unreal.AssetImportTask()
    task.set_editor_property("filename", fbx_path)
    task.set_editor_property("destination_path", DEST)
    task.set_editor_property("destination_name", asset_name)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("save", False)
    task.set_editor_property("options", options)
    return task


def analyse(asset_name):
    static_mesh = unreal.EditorAssetLibrary.load_asset(DEST + "/" + asset_name)
    if static_mesh is None:
        return {"name": asset_name, "error": "not loaded"}
    vertices, triangles, normals, _uvs, _tangents = (
        unreal.ProceduralMeshLibrary.get_section_from_static_mesh(static_mesh, 0, 0)
    )
    centre = unreal.Vector(0.0, 0.0, 0.0)
    for vertex in vertices:
        centre = centre + vertex
    centre = centre / float(len(vertices))
    winding = shading = 0
    for index in range(0, len(triangles), 3):
        ids = (triangles[index], triangles[index + 1], triangles[index + 2])
        p0, p1, p2 = vertices[ids[0]], vertices[ids[1]], vertices[ids[2]]
        outward = (p0 + p1 + p2) / 3.0 - centre
        geometric = (p1 - p0).cross(p2 - p0)
        explicit = (normals[ids[0]] + normals[ids[1]] + normals[ids[2]]) / 3.0
        winding += 1 if geometric.dot(outward) >= 0.0 else -1
        shading += 1 if explicit.dot(outward) >= 0.0 else -1
    faces = float(len(triangles) // 3)
    return {
        "name": asset_name,
        "winding_outward": winding / faces,
        "shading_outward": shading / faces,
    }


cases = json.loads(CASES_PAYLOAD)
unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(
    [build_task(case["fbx"], case["name"]) for case in cases]
)

results = []
for case in cases:
    entry = analyse(case["name"])
    entry["blender_determinant"] = case["determinant"]
    entry["note"] = case["note"]
    results.append(entry)

reference = next(item for item in results if item["name"].endswith("Baseline"))
ok = True
for item in results:
    if "winding_outward" not in item:
        ok = False
        continue
    winding_ok = item["winding_outward"] == reference["winding_outward"]
    shading_ok = item["shading_outward"] == reference["shading_outward"]
    item["verdict"] = (
        "CORRECT" if winding_ok and shading_ok
        else "winding inverted" if shading_ok
        else "normals inverted" if winding_ok
        else "both inverted"
    )
    if not item["name"].endswith("Raw"):
        ok = ok and winding_ok and shading_ok

unreal.EditorAssetLibrary.delete_directory(DEST)
print("VERIFY_RESULT " + json.dumps({
    "results": results,
    "all_non_control_cases_correct": ok,
    "cleaned_up": not unreal.EditorAssetLibrary.does_directory_exist(DEST),
}))
'''


def export_probe_meshes(out_dir):
    import bpy

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "addons"))
    from send2ue.core import export as send2ue_export

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def clear():
        for scene_object in list(bpy.data.objects):
            bpy.data.objects.remove(scene_object, do_unlink=True)
        for mesh in list(bpy.data.meshes):
            bpy.data.meshes.remove(mesh)

    def make_cube(name):
        verts = [
            (0.0, -1.0, -1.0), (2.0, -1.0, -1.0), (2.0, 1.0, -1.0), (0.0, 1.0, -1.0),
            (0.0, -1.0, 1.0), (2.0, -1.0, 1.0), (2.0, 1.0, 1.0), (0.0, 1.0, 1.0),
        ]
        faces = [
            (0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
            (2, 3, 7, 6), (1, 2, 6, 5), (0, 4, 7, 3),
        ]
        mesh = bpy.data.meshes.new(f"{name}_mesh")
        mesh.from_pydata(verts, [], faces)
        mesh.validate()
        mesh.update()
        scene_object = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(scene_object)
        return scene_object

    cases = []
    for name, scale, compensate, note in CASES:
        clear()
        cube = make_cube(name)
        cube.scale = scale
        bpy.context.view_layer.update()
        bpy.ops.object.select_all(action="DESELECT")
        cube.select_set(True)
        bpy.context.view_layer.objects.active = cube
        path = out_dir / f"{name}.fbx"
        if compensate:
            with send2ue_export.compensate_negative_scale_winding():
                bpy.ops.export_scene.fbx(filepath=str(path), **EXPORT_SETTINGS)
        else:
            bpy.ops.export_scene.fbx(filepath=str(path), **EXPORT_SETTINGS)
        cases.append({
            "name": name,
            "fbx": str(path),
            "determinant": cube.matrix_world.to_3x3().determinant(),
            "note": note,
        })

    (out_dir / "cases.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"EXPORTED {len(cases)} cases to {out_dir}")


def measure_in_unreal(out_dir, bind_address):
    engine_python = Path(
        r"C:\Program Files\Epic Games\UE_5.8\Engine\Plugins\Experimental"
        r"\PythonScriptPlugin\Content\Python"
    )
    sys.path.insert(0, str(engine_python))
    import remote_execution  # type: ignore[import-not-found]
    import time

    cases_payload = (Path(out_dir) / "cases.json").read_text(encoding="utf-8")
    code = f"CASES_PAYLOAD = {cases_payload!r}\n" + UNREAL_SIDE

    config = remote_execution.RemoteExecutionConfig()
    if bind_address:
        config.multicast_bind_address = bind_address
    remote = remote_execution.RemoteExecution(config)
    remote.start()
    try:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and not remote.remote_nodes:
            time.sleep(0.2)
        if not remote.remote_nodes:
            raise SystemExit("No Unreal remote node found.")
        remote.open_command_connection(remote.remote_nodes[0]["node_id"])
        result = remote.run_command(
            code,
            unattended=True,
            exec_mode=remote_execution.MODE_EXEC_FILE,
            raise_on_failure=False,
        )
    finally:
        remote.stop()

    if not result.get("success"):
        raise SystemExit(f"Unreal command failed: {result.get('result')}")

    for entry in result.get("output") or []:
        text = str(entry.get("output", ""))
        if not text.startswith("VERIFY_RESULT "):
            continue
        report = json.loads(text[len("VERIFY_RESULT "):])
        header = "{:26} {:>5} {:>8} {:>8} {:>18}".format(
            "asset", "det", "winding", "shading", "verdict"
        )
        print(header)
        print("-" * len(header))
        for item in report["results"]:
            print("{:26} {:>5} {:>8} {:>8} {:>18}".format(
                item["name"].replace("SM_NegFinal_", ""),
                item.get("blender_determinant"),
                item.get("winding_outward"),
                item.get("shading_outward"),
                item.get("verdict", ""),
            ))
        print()
        print("all non-control cases correct :", report["all_non_control_cases_correct"])
        print("test assets cleaned up        :", report["cleaned_up"])
        return 0 if report["all_non_control_cases_correct"] else 1

    print("No VERIFY_RESULT in output:", json.dumps(result, indent=2))
    return 1


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", metavar="DIR")
    parser.add_argument("--measure", metavar="DIR")
    parser.add_argument("--multicast-bind-address", default="")
    args = parser.parse_args(argv)

    if args.export:
        export_probe_meshes(args.export)
        return 0
    if args.measure:
        return measure_in_unreal(args.measure, args.multicast_bind_address)
    parser.error("pass --export <dir> (in Blender) or --measure <dir>")


if __name__ == "__main__":
    raise SystemExit(main())
