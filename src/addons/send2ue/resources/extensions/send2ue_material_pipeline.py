# send2ue extension: run the Unreal material setup after each StaticMesh import.
#
# The actual material work lives in UE_Blender_Pipeline/ue_material_setup.py.
# This hook only resolves the sidecar JSON written by UE Unique Names and asks
# Unreal to process the imported mesh through the shared surface-layer pipeline.

import os

import bpy
from send2ue.constants import UnrealTypes
from send2ue.core.extension import ExtensionBase
from send2ue.dependencies.unreal import run_commands


PIPELINE_DIR = os.environ.get(
    "UE_BLENDER_PIPELINE_DIR",
    os.path.join(os.path.expanduser("~"), "Documents", "UE_Blender_Pipeline"),
).replace("\\", "/")


class MaterialPipelineExtension(ExtensionBase):
    name = "material_pipeline"

    enabled: bpy.props.BoolProperty(
        name="Auto-setup materials on import",
        default=True,
        description=(
            "After import, run the surface-layer material setup for matching StaticMesh assets."
        ),
    )

    def pre_mesh_export(self, asset_data, properties):
        target = bpy.data.objects.get(asset_data.get("_mesh_object_name", ""))
        if not target or target.type != "MESH":
            return

        shape_keys = bool(getattr(target, "ue_unique_transfer_shape_keys", False))
        weights = bool(getattr(target, "ue_unique_transfer_weights", False))
        if not shape_keys and not weights:
            return

        if not hasattr(target, "vdt_object_props"):
            print("[material_pipeline] Vertex Data Tools object props are unavailable.")
            return

        source = target.vdt_object_props.transfer_source
        if source is None:
            print(f"[material_pipeline] Transfer source not set for {target.name}; skipping.")
            return

        self._run_vertex_data_transfer(target, shape_keys, weights)

    def _run_vertex_data_transfer(self, target, shape_keys, weights):
        active = bpy.context.view_layer.objects.active
        selected = list(bpy.context.selected_objects)
        mode = bpy.context.mode
        vdt_props = getattr(bpy.context.scene, "vdt_props", None)
        previous_overwrite_shape_keys = (
            getattr(vdt_props, "overwrite_shape_keys", None)
            if vdt_props is not None else None
        )

        try:
            if mode != "OBJECT" and active:
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.select_all(action="DESELECT")
            target.select_set(True)
            bpy.context.view_layer.objects.active = target

            if shape_keys:
                if not hasattr(bpy.ops.object, "vdt_pointer_transfer_shape_keys"):
                    print("[material_pipeline] Shape Key transfer operator is unavailable.")
                else:
                    if vdt_props is not None:
                        vdt_props.overwrite_shape_keys = True
                    bpy.ops.object.vdt_pointer_transfer_shape_keys()

            if weights:
                if not hasattr(bpy.ops.object, "vdt_pointer_transfer_weights"):
                    print("[material_pipeline] Weight transfer operator is unavailable.")
                else:
                    bpy.ops.object.vdt_pointer_transfer_weights()
        finally:
            if bpy.context.mode != "OBJECT" and bpy.context.active_object:
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.select_all(action="DESELECT")
            for obj in selected:
                if obj.name in bpy.context.view_layer.objects:
                    obj.select_set(True)
            if active and active.name in bpy.context.view_layer.objects:
                bpy.context.view_layer.objects.active = active
            if vdt_props is not None and previous_overwrite_shape_keys is not None:
                vdt_props.overwrite_shape_keys = previous_overwrite_shape_keys

    def post_import(self, asset_data, properties):
        if not self.enabled:
            return
        if asset_data.get("skip"):
            return
        if asset_data.get("_asset_type") != UnrealTypes.STATIC_MESH:
            return

        asset_path = asset_data.get("asset_path")
        if not asset_path:
            return

        json_path = self._resolve_json_path(asset_path)
        json_arg = f'r"{json_path}"' if json_path else "None"

        commands = [
            "import sys",
            f'_d = r"{PIPELINE_DIR}"',
            "sys.path.append(_d) if _d not in sys.path else None",
            "import importlib",
            "import ue_material_setup as _p",
            "importlib.reload(_p)",
            f'_p.process_mesh(r"{asset_path}", json_path={json_arg})',
        ]
        run_commands(commands)

    def _resolve_json_path(self, asset_path):
        """Return the Blender-authored sidecar JSON path for this imported mesh."""
        try:
            from pathlib import Path

            import ue_unique_export_names_addon as addon

            mesh_name = asset_path.rsplit("/", 1)[-1]
            props = bpy.context.scene.ue_unique_names
            export_dir = addon.resolve_export_dir(props.texture_export_dir)
            candidate = Path(export_dir) / f"{mesh_name}.json"
            if candidate.exists():
                return str(candidate).replace("\\", "/")
        except Exception as exc:
            print(
                "[material_pipeline] json_path resolve failed; "
                f"falling back to pipeline search: {exc}"
            )
        return None

    def draw_import(self, dialog, layout, properties):
        box = layout.box()
        box.label(text="Material Pipeline (Surface Layers)")
        dialog.draw_property(self, box, "enabled")
