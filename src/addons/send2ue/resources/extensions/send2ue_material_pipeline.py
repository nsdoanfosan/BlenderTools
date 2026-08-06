# send2ue extension: run the Unreal material setup after each StaticMesh import.
#
# The actual material work lives in ../pipeline/ue_material_setup.py.
# This hook only resolves the sidecar JSON written by UE Unique Names and asks
# Unreal to process the imported mesh through the shared surface-layer pipeline.

import hashlib
import json
from pathlib import Path

import bpy
from send2ue.constants import UnrealTypes
from send2ue.core import utilities
from send2ue.core.extension import ExtensionBase
from send2ue.dependencies.unreal import run_commands


BUNDLED_PIPELINE_DIR = (Path(__file__).resolve().parent.parent / "pipeline").as_posix()
# The material runtime is part of this Git repository.  Do not allow a stale
# Documents copy or process environment override to shadow the bundled source.
PIPELINE_DIR = BUNDLED_PIPELINE_DIR
_TEXTURELESS_FBX_RESTORE = {}
TEXTURELESS_FBX_EXPORT_FLAG = "send2ue_material_pipeline_textureless_fbx_export"
MATERIAL_PIPELINE_JSON_PATH_KEY = "_material_pipeline_json_path"
MATERIAL_PIPELINE_JSON_FROM_EXPORT_KEY = "_material_pipeline_json_from_export"
MATERIAL_PIPELINE_EXPECTED_MESH_NAME_KEY = "_material_pipeline_expected_mesh_name"
MATERIAL_PIPELINE_JSON_SHA256_KEY = "_material_pipeline_json_sha256"
_POST_OPERATION_SKELETAL_ASSET_PATHS = []


class MaterialPipelineExtension(ExtensionBase):
    name = "material_pipeline"

    enabled: bpy.props.BoolProperty(
        name="Auto-setup materials on import",
        default=True,
        description=(
            "After import, run the surface-layer material setup for matching StaticMesh assets."
        ),
    )

    def pre_operation(self, properties):
        _POST_OPERATION_SKELETAL_ASSET_PATHS.clear()

    def post_operation(self, properties):
        asset_paths = list(dict.fromkeys(_POST_OPERATION_SKELETAL_ASSET_PATHS))
        _POST_OPERATION_SKELETAL_ASSET_PATHS.clear()
        if not self.enabled or not asset_paths:
            return

        paths_arg = repr(asset_paths)
        commands = [
            "import sys",
            "import importlib.util",
            "import unreal",
            f'_d = r"{PIPELINE_DIR}"',
            "_pipeline_file = _d.rstrip('/') + '/ue_material_setup.py'",
            "_spec = importlib.util.spec_from_file_location('send2ue_bundled_ue_material_setup_post_operation', _pipeline_file)",
            "if _spec is None or _spec.loader is None:",
            "\traise RuntimeError('Could not load bundled material pipeline: ' + _pipeline_file)",
            "_p = importlib.util.module_from_spec(_spec)",
            "sys.modules[_spec.name] = _p",
            "_spec.loader.exec_module(_p)",
            f"_p.persist_generated_skeleton_dependencies({paths_arg})",
        ]
        run_commands(commands)

    def pre_mesh_export(self, asset_data, properties):
        if not self.enabled:
            return

        asset_data = asset_data or {}
        target = bpy.data.objects.get(asset_data.get("_mesh_object_name", ""))
        if not target:
            return

        refresh_result = self._refresh_unreal_handoff_json_or_error(target)

        sidecar = self._load_json_sidecar_for_export(
            asset_data,
            target,
            refresh_result,
        )
        if sidecar is None:
            return
        self._prepare_textureless_fbx_materials(target)

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

    def post_mesh_export(self, asset_data, properties):
        target_name = (asset_data or {}).get("_mesh_object_name", "")
        self._restore_textureless_fbx_materials(target_name)

    def _prepare_textureless_fbx_materials(self, target):
        if target.name in _TEXTURELESS_FBX_RESTORE:
            return

        namespace = bpy.app.driver_namespace
        _TEXTURELESS_FBX_RESTORE[target.name] = {
            "had_flag": TEXTURELESS_FBX_EXPORT_FLAG in namespace,
            "previous": namespace.get(TEXTURELESS_FBX_EXPORT_FLAG),
        }
        namespace[TEXTURELESS_FBX_EXPORT_FLAG] = True

    def _textureless_fbx_mesh_objects(self, target):
        if target.type == "MESH":
            return [target]
        return [child for child in target.children_recursive if child.type == "MESH"]

    def _restore_textureless_fbx_materials(self, target_name, state=None):
        state = state or _TEXTURELESS_FBX_RESTORE.pop(target_name, None)
        if not state:
            return

        namespace = bpy.app.driver_namespace
        if state["had_flag"]:
            namespace[TEXTURELESS_FBX_EXPORT_FLAG] = state["previous"]
        else:
            namespace.pop(TEXTURELESS_FBX_EXPORT_FLAG, None)

    def _refresh_unreal_handoff_json_or_error(self, target):
        try:
            from ue_unique_export_names_addon import api as handoff_api

            renamed_materials = self._normalize_export_material_names(handoff_api)
            if renamed_materials:
                print(
                    "[material_pipeline] normalized export material names: "
                    + ", ".join(
                        f"{old_name} -> {new_name}"
                        for old_name, new_name in renamed_materials
                    )
                )
            result = handoff_api.refresh_handoff_json(
                bpy.context,
                scope="EXPORT_COLLECTION",
            )
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
            return result
        except RuntimeError:
            raise
        except Exception as exc:
            utilities.report_error(
                "Could not validate Unreal handoff before Send to Unreal.",
                f' Target: "{target.name}". {exc}',
            )

    def _normalize_export_material_names(self, handoff_api):
        """Apply Unreal material naming to the current live export scope.

        Send2UE runs against a disposable Blender process in the batch pipeline,
        so this repairs the FBX/JSON handoff without saving the source blend.
        Only materials that the handoff API reports as reachable from the current
        Export collection are touched.
        """
        from ue_unique_export_names_addon.constants import MATERIAL_PREFIX
        from ue_unique_export_names_addon.utils import clean_token

        data = handoff_api.collect_handoff_data(
            bpy.context,
            scope="EXPORT_COLLECTION",
        )
        materials = list(data.get("materials") or [])
        all_materials = list(getattr(bpy.data, "materials", ()) or ())
        used_names = {
            str(material.name).casefold(): material for material in all_materials
        }
        renamed = []

        for material in materials:
            old_name = str(material.name)
            clean_name = clean_token(old_name)
            base_name = (
                clean_name
                if clean_name.startswith(MATERIAL_PREFIX)
                else f"{MATERIAL_PREFIX}{clean_name}"
            )
            candidate = base_name
            suffix = 2
            while (
                candidate.casefold() in used_names
                and used_names[candidate.casefold()] is not material
            ):
                candidate = f"{base_name}_{suffix:02d}"
                suffix += 1

            if candidate == old_name:
                continue
            if used_names.get(old_name.casefold()) is material:
                used_names.pop(old_name.casefold(), None)
            material.name = candidate
            actual_name = str(material.name)
            used_names[actual_name.casefold()] = material
            renamed.append((old_name, actual_name))

        return renamed

    def _load_json_sidecar_for_export(
        self,
        asset_data,
        target,
        refresh_result,
    ):
        json_path = self._resolve_json_path_for_export(
            asset_data,
            target,
            refresh_result,
        )
        if not json_path:
            utilities.report_error(
                "UE Unique JSON sidecar is missing.",
                f'Run "Check Unreal Handoff" before Send to Unreal. Target: "{target.name}".',
            )
            return None

        try:
            payload = Path(json_path).read_bytes()
            sidecar = json.loads(payload.decode("utf-8"))
        except OSError as exc:
            utilities.report_error(
                f"Could not read UE Unique JSON sidecar: {json_path}",
                str(exc),
            )
            return None
        except json.JSONDecodeError as exc:
            utilities.report_error(
                f"Invalid UE Unique JSON sidecar: {json_path}",
                str(exc),
            )
            return None

        expected_mesh_name = str(
            asset_data.get(MATERIAL_PIPELINE_EXPECTED_MESH_NAME_KEY) or ""
        ).strip()
        sidecar_mesh_name = str(sidecar.get("mesh_name") or "").strip()
        if (
            not expected_mesh_name
            or sidecar_mesh_name.casefold() != expected_mesh_name.casefold()
        ):
            utilities.report_error(
                "UE Unique JSON sidecar identity mismatch.",
                f' Expected: "{expected_mesh_name or "-"}". '
                f'JSON mesh_name: "{sidecar_mesh_name or "-"}".',
            )
            return None

        # Preserve both the exact asset-unit identity and the exact bytes
        # selected for this export. The path alone is not execution evidence.
        asset_data[MATERIAL_PIPELINE_JSON_PATH_KEY] = str(json_path).replace(
            "\\", "/"
        )
        asset_data[MATERIAL_PIPELINE_JSON_SHA256_KEY] = hashlib.sha256(
            payload
        ).hexdigest()
        asset_data[MATERIAL_PIPELINE_JSON_FROM_EXPORT_KEY] = True
        return sidecar

    def _resolve_json_path_for_export(
        self,
        asset_data,
        target,
        refresh_result,
    ):
        try:
            from ue_unique_export_names_addon import api as handoff_api
        except Exception as exc:
            utilities.report_error(
                "UE Unique Names add-on is required before Send to Unreal.",
                f"JSON sidecar lookup failed: {exc}",
            )
            return None

        asset_unit_name = str(
            handoff_api.resolve_asset_unit_name(target, bpy.context) or ""
        ).strip()
        if not asset_unit_name:
            utilities.report_error(
                "Could not resolve the exact Send to Unreal asset unit.",
                f' Target: "{target.name}".',
            )
            return None
        asset_data[MATERIAL_PIPELINE_EXPECTED_MESH_NAME_KEY] = asset_unit_name

        matches = []
        for value in (refresh_result or {}).get("json_paths") or []:
            path = Path(value)
            if path.stem.casefold() == asset_unit_name.casefold():
                matches.append(path)
        unique_matches = {
            str(path.resolve(strict=False)).casefold(): path
            for path in matches
        }
        if len(unique_matches) != 1:
            utilities.report_error(
                "Exact asset-unit JSON sidecar was not produced uniquely.",
                f' Asset unit: "{asset_unit_name}". '
                f"Refresh matches: {len(unique_matches)}.",
            )
            return None
        return str(next(iter(unique_matches.values())))

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

    def pre_import(self, asset_data, properties):
        if not self.enabled:
            return
        if asset_data.get("skip"):
            return
        if asset_data.get("_asset_type") not in {
            UnrealTypes.STATIC_MESH,
            UnrealTypes.SKELETAL_MESH,
        }:
            return
        asset_path = asset_data.get("asset_path", "")
        from_mesh_export = bool(
            asset_data.pop(MATERIAL_PIPELINE_JSON_FROM_EXPORT_KEY, False)
        )
        json_path = (
            asset_data.get(MATERIAL_PIPELINE_JSON_PATH_KEY)
            if from_mesh_export
            else None
        )
        if not from_mesh_export:
            asset_data.pop(MATERIAL_PIPELINE_JSON_PATH_KEY, None)
            asset_data.pop(MATERIAL_PIPELINE_EXPECTED_MESH_NAME_KEY, None)
            asset_data.pop(MATERIAL_PIPELINE_JSON_SHA256_KEY, None)
        if not json_path:
            json_path = self._resolve_json_path(asset_path)
        if not json_path:
            asset_data.pop(MATERIAL_PIPELINE_JSON_PATH_KEY, None)
            return
        json_path = str(json_path).replace("\\", "/")
        asset_data[MATERIAL_PIPELINE_JSON_PATH_KEY] = json_path
        expected_mesh_name = str(
            asset_data.get(MATERIAL_PIPELINE_EXPECTED_MESH_NAME_KEY)
            or self._asset_name_from_value(asset_path)
        ).strip()
        sidecar_sha256 = str(
            asset_data.get(MATERIAL_PIPELINE_JSON_SHA256_KEY) or ""
        ).strip()
        if from_mesh_export and (not expected_mesh_name or not sidecar_sha256):
            utilities.report_error(
                "Export-bound JSON sidecar evidence is incomplete.",
                f' Asset: "{asset_path}".',
            )
            return
        asset_data[MATERIAL_PIPELINE_EXPECTED_MESH_NAME_KEY] = expected_mesh_name
        asset_data[MATERIAL_PIPELINE_JSON_SHA256_KEY] = sidecar_sha256
        preflight_asset_path = self._preflight_asset_path(
            asset_path,
            expected_mesh_name,
        )

        json_arg = repr(json_path)
        expected_name_arg = repr(expected_mesh_name)
        sidecar_sha_arg = repr(sidecar_sha256)
        commands = [
            "import sys",
            "import importlib.util",
            "import unreal",
            f'_d = r"{PIPELINE_DIR}"',
            f'_asset_path = r"{preflight_asset_path}".split(".")[0]',
            "_pipeline_file = _d.rstrip('/') + '/ue_material_setup.py'",
            "_spec = importlib.util.spec_from_file_location('send2ue_bundled_ue_material_setup_preflight', _pipeline_file)",
            "if _spec is None or _spec.loader is None:",
            "\traise RuntimeError('Could not load bundled material pipeline: ' + _pipeline_file)",
            "_p = importlib.util.module_from_spec(_spec)",
            "sys.modules[_spec.name] = _p",
            "_spec.loader.exec_module(_p)",
            (
                "_p.preflight_mesh_materials("
                f"_asset_path, json_path={json_arg}, "
                f"expected_mesh_name={expected_name_arg}, "
                f"sidecar_sha256={sidecar_sha_arg})"
            ),
        ]
        run_commands(commands)

        # The JSON material pipeline imports textures and creates/assigns MIs itself.
        # Letting the FBX importer also create source materials/textures adds duplicate
        # assets that immediately need cleanup, which is expensive in Unreal/P4.
        asset_data["_import_materials_and_textures"] = False

    def _preflight_asset_path(self, asset_path, expected_mesh_name):
        """Use the Blender-authored asset unit, never JSON, as authority."""
        mesh_name = str(expected_mesh_name or "").strip()
        base_path = str(asset_path or "").split(".")[0]
        if not mesh_name or "/" not in base_path:
            return asset_path
        folder = base_path.rsplit("/", 1)[0]
        return f"{folder}/{mesh_name}"

    def post_import(self, asset_data, properties):
        if not self.enabled:
            return
        if asset_data.get("skip"):
            return
        if asset_data.get("_asset_type") not in {
            UnrealTypes.STATIC_MESH,
            UnrealTypes.SKELETAL_MESH,
        }:
            return

        asset_path = asset_data.get("asset_path")
        if not asset_path:
            return

        json_path = asset_data.get(MATERIAL_PIPELINE_JSON_PATH_KEY)
        if not json_path:
            return
        expected_mesh_name = str(
            asset_data.get(MATERIAL_PIPELINE_EXPECTED_MESH_NAME_KEY) or ""
        ).strip()
        sidecar_sha256 = str(
            asset_data.get(MATERIAL_PIPELINE_JSON_SHA256_KEY) or ""
        ).strip()
        if asset_data.get("_asset_type") == UnrealTypes.SKELETAL_MESH:
            _POST_OPERATION_SKELETAL_ASSET_PATHS.append(asset_path)
        json_arg = repr(str(json_path))
        expected_name_arg = repr(expected_mesh_name)
        sidecar_sha_arg = repr(sidecar_sha256)

        commands = [
            "import sys",
            "import importlib.util",
            "import unreal",
            f'_d = r"{PIPELINE_DIR}"',
            f'_asset_path = r"{asset_path}".split(".")[0]',
            "_pipeline_file = _d.rstrip('/') + '/ue_material_setup.py'",
            "_spec = importlib.util.spec_from_file_location('send2ue_bundled_ue_material_setup', _pipeline_file)",
            "if _spec is None or _spec.loader is None:",
            "\traise RuntimeError('Could not load bundled material pipeline: ' + _pipeline_file)",
            "_p = importlib.util.module_from_spec(_spec)",
            "sys.modules[_spec.name] = _p",
            "_spec.loader.exec_module(_p)",
            "def _sync_to_imported_asset(_path):",
            "\ttry:",
            "\t\t_command_line = unreal.SystemLibrary.get_command_line().casefold()",
            "\t\tif '-unattended' in _command_line or '-run=' in _command_line:",
            "\t\t\treturn",
            "\t\tunreal.EditorAssetLibrary.sync_browser_to_objects([_path])",
            "\texcept Exception as _sync_error:",
            "\t\tunreal.log_warning('[material_pipeline] content browser sync failed: ' + str(_sync_error))",
            "try:",
            (
                "\t_p.process_mesh("
                f"_asset_path, json_path={json_arg}, "
                f"expected_mesh_name={expected_name_arg}, "
                f"sidecar_sha256={sidecar_sha_arg})"
            ),
            "finally:",
            "\t_sync_to_imported_asset(_asset_path)",
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
                f"automatic material pipeline skipped: {exc}"
            )
        return None

    def draw_import(self, dialog, layout, properties):
        box = layout.box()
        box.label(text="Material Pipeline (Surface Layers)")
        dialog.draw_property(self, box, "enabled")
