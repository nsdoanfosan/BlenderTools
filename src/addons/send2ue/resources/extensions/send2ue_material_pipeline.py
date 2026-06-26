# send2ue extension: run the Unreal material setup after each StaticMesh import.
#
# The actual material work lives in ../pipeline/ue_material_setup.py.
# This hook only resolves the sidecar JSON written by UE Unique Names and asks
# Unreal to process the imported mesh through the shared surface-layer pipeline.

import json
import os
from pathlib import Path

import bpy
from send2ue.constants import UnrealTypes
from send2ue.core import utilities
from send2ue.core.extension import ExtensionBase
from send2ue.dependencies.unreal import run_commands


BUNDLED_PIPELINE_DIR = (Path(__file__).resolve().parent.parent / "pipeline").as_posix()
PIPELINE_DIR = os.environ.get("UE_BLENDER_PIPELINE_DIR", BUNDLED_PIPELINE_DIR).replace(
    "\\",
    "/",
)


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
        if not self.enabled:
            return

        asset_data = asset_data or {}
        target = bpy.data.objects.get(asset_data.get("_mesh_object_name", ""))
        if not target or target.type != "MESH":
            return

        self._refresh_unreal_handoff_json_or_error(target)

        sidecar = self._load_json_sidecar_for_export(asset_data, target)
        if sidecar is None:
            return
        transfer = self._transfer_entry_for_target(sidecar, target)
        shape_keys = bool(transfer.get("shape_keys"))
        weights = bool(transfer.get("weights"))
        if not shape_keys and not weights:
            return

        if not hasattr(target, "vdt_object_props"):
            utilities.report_error(
                "Vertex Data Tools object props are unavailable.",
                "The UE Unique JSON requests transfer postprocess, but VDT is not available in Blender.",
            )

        source_name = transfer.get("source")
        source = bpy.data.objects.get(source_name) if source_name else None
        if source is None:
            utilities.report_error(
                f'Transfer source "{source_name or "-"}" not found for "{target.name}".',
                "Run Check Unreal Handoff again after setting Export Transfer Source.",
            )

        target.vdt_object_props.transfer_source = source
        self._run_vertex_data_transfer(target, shape_keys, weights)

    def _refresh_unreal_handoff_json_or_error(self, target):
        try:
            from ue_unique_export_names_addon import api as handoff_api

            result = handoff_api.refresh_handoff_json(bpy.context)
            errors = result.get("errors") or []
            if errors:
                first = errors[0]
                utilities.report_error(
                    "Unreal handoff validation failed before Send to Unreal.",
                    f' Target: "{target.name}". First: {first}',
                )

            json_paths = result.get("json_paths") or []
            if not json_paths:
                utilities.report_error(
                    "Unreal handoff JSON refresh produced no files.",
                    f' Target: "{target.name}". Run Check Unreal Handoff.',
                )
        except RuntimeError:
            raise
        except Exception as exc:
            utilities.report_error(
                "Could not validate Unreal handoff before Send to Unreal.",
                f' Target: "{target.name}". {exc}',
            )

    def _load_json_sidecar_for_export(self, asset_data, target):
        json_path = self._resolve_json_path_for_export(asset_data, target)
        if not json_path:
            utilities.report_error(
                "UE Unique JSON sidecar is missing.",
                f'Run "Check Unreal Handoff" before Send to Unreal. Target: "{target.name}".',
            )
            return None

        try:
            with open(json_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except OSError as exc:
            utilities.report_error(
                f"Could not read UE Unique JSON sidecar: {json_path}",
                str(exc),
            )
        except json.JSONDecodeError as exc:
            utilities.report_error(
                f"Invalid UE Unique JSON sidecar: {json_path}",
                str(exc),
            )

    def _resolve_json_path_for_export(self, asset_data, target):
        candidates = []
        for value in (
            asset_data.get("file_path"),
            asset_data.get("asset_path"),
            target.name,
        ):
            name = self._asset_name_from_value(value)
            if name and name not in candidates:
                candidates.append(name)

        try:
            from ue_unique_export_names_addon import api as handoff_api
        except Exception as exc:
            utilities.report_error(
                "UE Unique Names add-on is required before Send to Unreal.",
                f"JSON sidecar lookup failed: {exc}",
            )
            return None

        return handoff_api.resolve_sidecar_json_path(candidates, bpy.context)

    def _asset_name_from_value(self, value):
        if not value:
            return ""
        value = str(value).replace("\\", "/").rstrip("/")
        name = value.rsplit("/", 1)[-1]
        if "." in name:
            name = name.rsplit(".", 1)[0]
        return name

    def _transfer_entry_for_target(self, sidecar, target):
        transfer = sidecar.get("transfer_source")
        if isinstance(transfer, dict) and transfer.get("enabled"):
            return transfer

        transfers = [
            entry
            for entry in sidecar.get("transfer_sources", [])
            if isinstance(entry, dict) and entry.get("enabled")
        ]
        if not transfers:
            return {}

        for entry in transfers:
            if entry.get("target") == target.name:
                return entry
        return transfers[0]

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
            from ue_unique_export_names_addon import api as handoff_api

            json_path = handoff_api.resolve_sidecar_json_path(asset_path, bpy.context)
            if json_path:
                return str(json_path).replace("\\", "/")
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
