import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "addons"
    / "send2ue"
    / "resources"
    / "extensions"
    / "send2ue_material_pipeline.py"
)


def _load_extension_module():
    command_calls = []
    module_names = (
        "bpy",
        "send2ue",
        "send2ue.constants",
        "send2ue.core",
        "send2ue.core.utilities",
        "send2ue.core.extension",
        "send2ue.dependencies",
        "send2ue.dependencies.unreal",
    )
    previous = {name: sys.modules.get(name) for name in module_names}

    bpy = types.ModuleType("bpy")
    bpy.props = types.SimpleNamespace(BoolProperty=lambda **kwargs: None)
    bpy.data = types.SimpleNamespace(objects={})
    bpy.app = types.SimpleNamespace(driver_namespace={})
    bpy.context = object()

    send2ue = types.ModuleType("send2ue")
    send2ue.__path__ = []
    constants = types.ModuleType("send2ue.constants")
    constants.UnrealTypes = types.SimpleNamespace(
        STATIC_MESH="STATIC_MESH",
        SKELETAL_MESH="SKELETAL_MESH",
    )
    core = types.ModuleType("send2ue.core")
    core.__path__ = []
    utilities = types.ModuleType("send2ue.core.utilities")
    utilities.report_error = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("unexpected report_error")
    )
    extension = types.ModuleType("send2ue.core.extension")
    extension.ExtensionBase = object
    dependencies = types.ModuleType("send2ue.dependencies")
    dependencies.__path__ = []
    unreal_dependency = types.ModuleType("send2ue.dependencies.unreal")
    unreal_dependency.run_commands = lambda commands: command_calls.append(
        list(commands)
    )

    replacements = {
        "bpy": bpy,
        "send2ue": send2ue,
        "send2ue.constants": constants,
        "send2ue.core": core,
        "send2ue.core.utilities": utilities,
        "send2ue.core.extension": extension,
        "send2ue.dependencies": dependencies,
        "send2ue.dependencies.unreal": unreal_dependency,
    }
    sys.modules.update(replacements)
    module_name = f"test_send2ue_material_pipeline_{id(command_calls)}"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    return module, command_calls


class TestMaterialPipelineExactSidecar(unittest.TestCase):
    def setUp(self):
        self.module, self.command_calls = _load_extension_module()
        self.extension = self.module.MaterialPipelineExtension()
        self.extension.enabled = True
        self.asset_data = {
            "_asset_type": "STATIC_MESH",
            "asset_path": "/Game/Meshes/Tree/SK_CommonGrass.SK_CommonGrass",
        }

    def test_pre_and_post_import_use_the_same_exact_sidecar(self):
        exact_path = "D:/OneDrive/Forestportfolio/Tree/texture/SK_CommonGrass.json"
        self.extension._resolve_json_path = lambda asset_path: exact_path

        self.extension.pre_import(self.asset_data, None)

        key = self.module.MATERIAL_PIPELINE_JSON_PATH_KEY
        self.assertEqual(self.asset_data[key], exact_path)
        self.assertFalse(self.asset_data["_import_materials_and_textures"])
        self.assertEqual(len(self.command_calls), 1)
        self.assertIn(repr(exact_path), "\n".join(self.command_calls[0]))

        self.extension._resolve_json_path = lambda asset_path: (_ for _ in ()).throw(
            AssertionError("post_import must not resolve the sidecar again")
        )
        self.extension.post_import(self.asset_data, None)

        self.assertEqual(len(self.command_calls), 2)
        post_commands = "\n".join(self.command_calls[1])
        self.assertIn(repr(exact_path), post_commands)
        self.assertNotIn("json_path=None", post_commands)

    def test_missing_pre_import_sidecar_prevents_post_import_fallback(self):
        key = self.module.MATERIAL_PIPELINE_JSON_PATH_KEY
        self.asset_data[key] = "D:/stale.json"
        resolve_calls = []
        self.extension._resolve_json_path = lambda asset_path: resolve_calls.append(
            asset_path
        )

        self.extension.pre_import(self.asset_data, None)
        self.extension.post_import(self.asset_data, None)

        self.assertNotIn(key, self.asset_data)
        self.assertEqual(len(resolve_calls), 1)
        self.assertEqual(self.command_calls, [])

    def test_pre_import_preserves_sidecar_selected_during_mesh_export(self):
        exact_path = "D:/texture/SK_Branch_01.json"
        key = self.module.MATERIAL_PIPELINE_JSON_PATH_KEY
        self.asset_data[key] = exact_path
        self.asset_data[self.module.MATERIAL_PIPELINE_JSON_FROM_EXPORT_KEY] = True
        self.asset_data[
            self.module.MATERIAL_PIPELINE_EXPECTED_MESH_NAME_KEY
        ] = "SK_Branch_01"
        self.asset_data[self.module.MATERIAL_PIPELINE_JSON_SHA256_KEY] = "a" * 64
        self.asset_data["file_path"] = "D:/temp/SK_Branch_01.fbx"
        self.extension._resolve_json_path = lambda asset_path: (_ for _ in ()).throw(
            AssertionError("pre_import must keep the pre-export asset-unit sidecar")
        )

        self.extension.pre_import(self.asset_data, None)

        self.assertEqual(self.asset_data[key], exact_path)
        self.assertEqual(len(self.command_calls), 1)
        commands = "\n".join(self.command_calls[0])
        self.assertIn(repr(exact_path), commands)
        self.assertIn("expected_mesh_name='SK_Branch_01'", commands)
        self.assertIn(f"sidecar_sha256={'a' * 64!r}", commands)
        self.assertIn(
            "export_file_path='D:/temp/SK_Branch_01.fbx'",
            commands,
        )

        self.extension.post_import(self.asset_data, None)
        post_commands = "\n".join(self.command_calls[-1])
        self.assertIn(
            "export_file_path='D:/temp/SK_Branch_01.fbx'",
            post_commands,
        )

    def test_pre_export_selects_only_exact_asset_unit_from_current_refresh(self):
        package_name = "ue_unique_export_names_addon"
        api_name = f"{package_name}.api"
        previous_package = sys.modules.get(package_name)
        previous_api = sys.modules.get(api_name)
        package = types.ModuleType(package_name)
        package.__path__ = []
        api = types.ModuleType(api_name)
        api.resolve_asset_unit_name = lambda target, context: "SK_Branch_01"
        api.resolve_sidecar_json_path = lambda *args: (_ for _ in ()).throw(
            AssertionError("global sidecar resolution must not be used")
        )
        sys.modules[package_name] = package
        sys.modules[api_name] = api
        package.api = api
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                exact = Path(temp_dir) / "SK_Branch_01.json"
                child = Path(temp_dir) / "SK_Branch_01_Mesh.json"
                exact.write_text("{}", encoding="utf-8")
                child.write_text("{}", encoding="utf-8")
                asset_data = {
                    "file_path": "D:/temp/SK_Branch_01_Mesh.fbx",
                    "asset_path": "/Game/Meshes/SK_Branch_01_Mesh",
                }
                path = self.extension._resolve_json_path_for_export(
                    asset_data,
                    types.SimpleNamespace(name="SK_Branch_01_Mesh"),
                    {"json_paths": [str(child), str(exact)]},
                )

                self.assertEqual(Path(path), exact)
                self.assertEqual(
                    asset_data[
                        self.module.MATERIAL_PIPELINE_EXPECTED_MESH_NAME_KEY
                    ],
                    "SK_Branch_01",
                )
        finally:
            if previous_package is None:
                sys.modules.pop(package_name, None)
            else:
                sys.modules[package_name] = previous_package
            if previous_api is None:
                sys.modules.pop(api_name, None)
            else:
                sys.modules[api_name] = previous_api

    def test_refresh_auto_prefixes_only_live_export_materials(self):
        package_name = "ue_unique_export_names_addon"
        api_name = f"{package_name}.api"
        constants_name = f"{package_name}.constants"
        utils_name = f"{package_name}.utils"
        module_names = (package_name, api_name, constants_name, utils_name)
        previous = {name: sys.modules.get(name) for name in module_names}
        package = types.ModuleType(package_name)
        package.__path__ = []
        api = types.ModuleType(api_name)
        constants = types.ModuleType(constants_name)
        constants.MATERIAL_PREFIX = "M_"
        utils = types.ModuleType(utils_name)
        utils.clean_token = lambda value: str(value).replace(" ", "_")
        live = types.SimpleNamespace(name="Bark_ivy_01")
        unrelated = types.SimpleNamespace(name="Bark_unrelated_01")
        refresh_calls = []
        api.collect_handoff_data = lambda context, scope: {"materials": [live]}
        api.refresh_handoff_json = lambda context, scope: (
            refresh_calls.append((context, scope, live.name))
            or {"errors": [], "json_paths": ["D:/SK_Ivy.json"]}
        )
        package.api = api
        sys.modules.update(
            {
                package_name: package,
                api_name: api,
                constants_name: constants,
                utils_name: utils,
            }
        )
        self.module.bpy.data.materials = [live, unrelated]
        try:
            result = self.extension._refresh_unreal_handoff_json_or_error(
                types.SimpleNamespace(name="SK_Ivy")
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value

        self.assertEqual(live.name, "M_Bark_ivy_01")
        self.assertEqual(unrelated.name, "Bark_unrelated_01")
        self.assertEqual(
            refresh_calls,
            [(self.module.bpy.context, "EXPORT_COLLECTION", "M_Bark_ivy_01")],
        )
        self.assertEqual(result["errors"], [])

    def test_handoff_validation_reports_scope_and_every_error(self):
        package_name = "ue_unique_export_names_addon"
        api_name = f"{package_name}.api"
        constants_name = f"{package_name}.constants"
        utils_name = f"{package_name}.utils"
        module_names = (package_name, api_name, constants_name, utils_name)
        previous = {name: sys.modules.get(name) for name in module_names}
        package = types.ModuleType(package_name)
        package.__path__ = []
        api = types.ModuleType(api_name)
        constants = types.ModuleType(constants_name)
        constants.MATERIAL_PREFIX = "M_"
        utils = types.ModuleType(utils_name)
        utils.clean_token = lambda value: str(value)
        errors = [f"Missing texture file: T_wood_{index:02d}_color" for index in range(12)]
        api.collect_handoff_data = lambda context, scope: {"materials": []}
        api.refresh_handoff_json = lambda context, scope: {
            "errors": list(errors),
            "json_paths": [],
        }
        package.api = api
        sys.modules.update(
            {
                package_name: package,
                api_name: api,
                constants_name: constants,
                utils_name: utils,
            }
        )
        self.module.bpy.data.materials = []
        reported = []
        self.module.utilities.report_error = lambda message, details="": (
            reported.append((message, details))
            or (_ for _ in ()).throw(RuntimeError(message + details))
        )
        try:
            with self.assertRaises(RuntimeError):
                self.extension._refresh_unreal_handoff_json_or_error(
                    types.SimpleNamespace(name="S_side_floor_v_02")
                )
        finally:
            for name, value in previous.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value

        self.assertEqual(len(reported), 1)
        details = reported[0][1]
        # The failing material belongs to other assets, so the exported asset
        # must not read as the owner of the problem.
        self.assertIn("whole Export collection", details)
        self.assertIn("S_side_floor_v_02", details)
        self.assertIn("12 blocking issue(s)", details)
        # Every error up to the popup limit is listed, not only the first.
        for index in range(self.module.HANDOFF_ERROR_REPORT_LIMIT):
            self.assertIn(errors[index], details)
        self.assertNotIn(errors[self.module.HANDOFF_ERROR_REPORT_LIMIT], details)
        self.assertIn("and 2 more", details)

    def test_material_prefix_repair_avoids_existing_name_collision(self):
        package_name = "ue_unique_export_names_addon"
        constants_name = f"{package_name}.constants"
        utils_name = f"{package_name}.utils"
        previous = {
            name: sys.modules.get(name)
            for name in (constants_name, utils_name)
        }
        constants = types.ModuleType(constants_name)
        constants.MATERIAL_PREFIX = "M_"
        utils = types.ModuleType(utils_name)
        utils.clean_token = lambda value: str(value)
        sys.modules[constants_name] = constants
        sys.modules[utils_name] = utils
        live = types.SimpleNamespace(name="Bark_ivy_01")
        existing = types.SimpleNamespace(name="M_Bark_ivy_01")
        self.module.bpy.data.materials = [live, existing]
        api = types.SimpleNamespace(
            collect_handoff_data=lambda context, scope: {"materials": [live]}
        )
        try:
            renamed, skipped_linked = (
                self.extension._normalize_export_material_names(api)
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value

        self.assertEqual(live.name, "M_Bark_ivy_01_02")
        self.assertEqual(existing.name, "M_Bark_ivy_01")
        self.assertEqual(
            renamed,
            [("Bark_ivy_01", "M_Bark_ivy_01_02")],
        )
        self.assertEqual(skipped_linked, [])

    def test_linked_materials_are_skipped_not_renamed(self):
        package_name = "ue_unique_export_names_addon"
        constants_name = f"{package_name}.constants"
        utils_name = f"{package_name}.utils"
        previous = {
            name: sys.modules.get(name)
            for name in (constants_name, utils_name)
        }
        constants = types.ModuleType(constants_name)
        constants.MATERIAL_PREFIX = "M_"
        utils = types.ModuleType(utils_name)
        utils.clean_token = lambda value: str(value).replace(" ", "_")
        sys.modules[constants_name] = constants
        sys.modules[utils_name] = utils

        # A linked material is read-only: assigning .name raises AttributeError.
        class LinkedMaterial:
            def __init__(self, name, library):
                object.__setattr__(self, "_name", name)
                object.__setattr__(self, "library", library)

            @property
            def name(self):
                return self._name

            @name.setter
            def name(self, value):
                raise AttributeError(
                    'bpy_struct: attribute "name" from "Material" is read-only'
                )

        library = types.SimpleNamespace(
            filepath="//../material_library/wood_materials.blend",
            name="wood_materials.blend",
        )
        linked = LinkedMaterial("LayerBlend_Wood plank 03", library)
        local = types.SimpleNamespace(name="Bark_ivy_01", library=None)
        self.module.bpy.data.materials = [linked, local]
        api = types.SimpleNamespace(
            collect_handoff_data=lambda context, scope: {
                "materials": [linked, local]
            }
        )
        try:
            renamed, skipped_linked = (
                self.extension._normalize_export_material_names(api)
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value

        # The linked material is untouched and reported with the rename the
        # library owner has to make, plus which library that is.
        self.assertEqual(linked.name, "LayerBlend_Wood plank 03")
        self.assertEqual(
            skipped_linked,
            [
                (
                    "LayerBlend_Wood plank 03",
                    "M_LayerBlend_Wood_plank_03",
                    "wood_materials.blend",
                )
            ],
        )
        # The local material in the same scope is still normalized.
        self.assertEqual(local.name, "M_Bark_ivy_01")
        self.assertEqual(renamed, [("Bark_ivy_01", "M_Bark_ivy_01")])

    def test_linked_material_does_not_abort_the_handoff_refresh(self):
        package_name = "ue_unique_export_names_addon"
        api_name = f"{package_name}.api"
        constants_name = f"{package_name}.constants"
        utils_name = f"{package_name}.utils"
        module_names = (package_name, api_name, constants_name, utils_name)
        previous = {name: sys.modules.get(name) for name in module_names}
        package = types.ModuleType(package_name)
        package.__path__ = []
        api = types.ModuleType(api_name)
        constants = types.ModuleType(constants_name)
        constants.MATERIAL_PREFIX = "M_"
        utils = types.ModuleType(utils_name)
        utils.clean_token = lambda value: str(value)

        class LinkedMaterial:
            def __init__(self, name, library):
                object.__setattr__(self, "_name", name)
                object.__setattr__(self, "library", library)

            @property
            def name(self):
                return self._name

            @name.setter
            def name(self, value):
                raise AttributeError("read-only")

        linked = LinkedMaterial(
            "LayerBlend_Wood_plank_03",
            types.SimpleNamespace(filepath="//lib/wood.blend", name="wood.blend"),
        )
        api.collect_handoff_data = lambda context, scope: {"materials": [linked]}
        api.refresh_handoff_json = lambda context, scope: {
            "errors": [],
            "json_paths": ["D:/texture/SK_Ivy.json"],
        }
        package.api = api
        sys.modules.update(
            {
                package_name: package,
                api_name: api,
                constants_name: constants,
                utils_name: utils,
            }
        )
        self.module.bpy.data.materials = [linked]
        try:
            result = self.extension._refresh_unreal_handoff_json_or_error(
                types.SimpleNamespace(name="SK_Ivy")
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value

        # Previously the read-only assignment raised AttributeError, which the
        # extension turned into a bare "Could not validate Unreal handoff".
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["json_paths"], ["D:/texture/SK_Ivy.json"])

    def test_export_sidecar_persists_expected_name_and_content_sha(self):
        package_name = "ue_unique_export_names_addon"
        api_name = f"{package_name}.api"
        previous_package = sys.modules.get(package_name)
        previous_api = sys.modules.get(api_name)
        package = types.ModuleType(package_name)
        package.__path__ = []
        api = types.ModuleType(api_name)
        api.resolve_asset_unit_name = lambda target, context: "SK_Branch_01"
        sys.modules[package_name] = package
        sys.modules[api_name] = api
        package.api = api
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                sidecar = Path(temp_dir) / "SK_Branch_01.json"
                payload = json.dumps(
                    {"mesh_name": "SK_Branch_01"},
                    sort_keys=True,
                ).encode("utf-8")
                sidecar.write_bytes(payload)
                asset_data = {}

                loaded = self.extension._load_json_sidecar_for_export(
                    asset_data,
                    types.SimpleNamespace(name="SK_Branch_01_Mesh"),
                    {"json_paths": [str(sidecar)]},
                )

                self.assertEqual(loaded["mesh_name"], "SK_Branch_01")
                self.assertEqual(
                    asset_data[
                        self.module.MATERIAL_PIPELINE_EXPECTED_MESH_NAME_KEY
                    ],
                    "SK_Branch_01",
                )
                self.assertEqual(
                    asset_data[self.module.MATERIAL_PIPELINE_JSON_SHA256_KEY],
                    hashlib.sha256(payload).hexdigest(),
                )
                self.assertTrue(
                    asset_data[self.module.MATERIAL_PIPELINE_JSON_FROM_EXPORT_KEY]
                )
        finally:
            if previous_package is None:
                sys.modules.pop(package_name, None)
            else:
                sys.modules[package_name] = previous_package
            if previous_api is None:
                sys.modules.pop(api_name, None)
            else:
                sys.modules[api_name] = previous_api

    def test_blender_output_content_ignores_names_and_descendant_order(self):
        identity_fixture = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "prototype_identity_v1.json"
            ).read_text(encoding="utf-8")
        )

        class IdentityMatrix:
            def inverted_safe(self):
                return self

            def __matmul__(self, other):
                if isinstance(other, IdentityMatrix):
                    return self
                return other

        def mesh_object(name, x_offset):
            vertices = [
                types.SimpleNamespace(co=(x_offset + x, y, 0.0))
                for x, y in (
                    (0.0, 0.0),
                    (1.0, 0.0),
                    (1.0, 1.0),
                )
            ]
            loops = [
                types.SimpleNamespace(vertex_index=index)
                for index in range(3)
            ]
            uv_data = [
                types.SimpleNamespace(uv=uv)
                for uv in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0))
            ]
            mesh = types.SimpleNamespace(
                vertices=vertices,
                loops=loops,
                loop_triangles=[types.SimpleNamespace(loops=(0, 1, 2))],
                uv_layers=types.SimpleNamespace(
                    active=types.SimpleNamespace(data=uv_data)
                ),
                calc_loop_triangles=lambda: None,
            )
            return types.SimpleNamespace(
                name=name,
                type="MESH",
                data=mesh,
                matrix_world=IdentityMatrix(),
                children=[],
            )

        first = mesh_object("rename_is_not_identity", 0.0)
        second = mesh_object("also_renamable", 3.0)
        properties = {
            self.module.PROTOTYPE_IDENTITY_OBJECT_KEY: json.dumps(
                identity_fixture["single_member_lineage"]
            ),
            self.module.PROTOTYPE_IDENTITY_MEMBERS_OBJECT_KEY: json.dumps(
                [identity_fixture["identity"]]
            ),
        }
        target = types.SimpleNamespace(
            name="renamable_root",
            type="EMPTY",
            matrix_world=IdentityMatrix(),
            children=[first, second],
            get=properties.get,
        )

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        export_path = Path(temp_dir.name) / "tree.fbx"
        export_path.write_bytes(b"current-fbx-payload")

        original = self.module._prototype_handoff_for_target(
            target,
            export_path,
        )
        target.name = "different_name"
        first.name = "different_child_name"
        target.children.reverse()
        reordered = self.module._prototype_handoff_for_target(
            target,
            export_path,
        )
        self.assertEqual(original, reordered)

        first.data.vertices[1].co = (1.25, 0.0, 0.0)
        changed = self.module._prototype_handoff_for_target(
            target,
            export_path,
        )
        self.assertNotEqual(
            original["blender_geometry_content"],
            changed["blender_geometry_content"],
        )
        self.assertEqual(
            original["output_content"],
            changed["output_content"],
        )
        export_path.write_bytes(b"different-fbx-byte")
        changed_export = self.module._prototype_handoff_for_target(
            target,
            export_path,
        )
        self.assertNotEqual(
            changed["output_content"],
            changed_export["output_content"],
        )

        scoped = self.module._prototype_handoff_for_target(
            target, export_path, export_objects=[first])
        second.data.vertices[1].co = (999.0, 0.0, 0.0)
        self.assertEqual(scoped, self.module._prototype_handoff_for_target(
            target, export_path, export_objects=[first]))
        self.assertNotEqual(scoped["blender_geometry_content"],
            self.module._prototype_handoff_for_target(
                target, export_path, export_objects=[first, second])[
                    "blender_geometry_content"])

        sidecar_path = Path(temp_dir.name) / "tree.json"
        initial_sidecar = b'{"mesh_name":"tree"}'
        sidecar_path.write_bytes(initial_sidecar)
        asset_data = {
            "file_path": str(export_path),
            self.module.MATERIAL_PIPELINE_JSON_PATH_KEY: str(sidecar_path),
            self.module.MATERIAL_PIPELINE_JSON_SHA256_KEY: (
                hashlib.sha256(initial_sidecar).hexdigest()
            ),
        }
        self.extension._finalize_prototype_sidecar_for_export(
            asset_data,
            target,
        )
        finalized = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(
            finalized[self.module.PROTOTYPE_HANDOFF_KEY]["output_content"],
            changed_export["output_content"],
        )
        self.assertEqual(
            asset_data[self.module.MATERIAL_PIPELINE_JSON_SHA256_KEY],
            hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
        )

        # Every later mesh refresh rewrites the whole Export collection JSON.
        # Keep only the current operation's already validated FBX binding.
        self.module.bpy.data.objects[target.name] = target
        sidecar_path.write_bytes(initial_sidecar)
        self.extension._restore_finalized_prototype_sidecars_after_refresh()
        restored_bytes = sidecar_path.read_bytes()
        self.assertEqual(json.loads(restored_bytes), finalized)
        self.assertEqual(
            asset_data[self.module.MATERIAL_PIPELINE_JSON_SHA256_KEY],
            hashlib.sha256(restored_bytes).hexdigest(),
        )

        changed_intent = b'{"mesh_name":"tree","materials":["changed"]}'
        sidecar_path.write_bytes(changed_intent)
        with self.assertRaisesRegex(RuntimeError, "material intent changed"):
            self.extension._restore_finalized_prototype_sidecars_after_refresh()
        self.assertEqual(sidecar_path.read_bytes(), changed_intent)

        sidecar_path.write_bytes(initial_sidecar)
        self.module.bpy.data.objects.pop(target.name)
        with self.assertRaisesRegex(RuntimeError, "source lineage changed"):
            self.extension._restore_finalized_prototype_sidecars_after_refresh()
        self.assertEqual(sidecar_path.read_bytes(), initial_sidecar)
        self.module.bpy.data.objects[target.name] = target

        export_path.write_bytes(b"unrelated replacement FBX bytes")
        with self.assertRaisesRegex(RuntimeError, "FBX payload changed"):
            self.extension._restore_finalized_prototype_sidecars_after_refresh()
        self.assertEqual(sidecar_path.read_bytes(), initial_sidecar)

        self.extension.pre_operation(None)
        self.assertEqual(self.module._PROTOTYPE_FINALIZED_SIDECARS, {})
        self.extension._restore_finalized_prototype_sidecars_after_refresh()
        self.assertEqual(sidecar_path.read_bytes(), initial_sidecar)

    def test_disabled_post_mesh_export_has_no_sidecar_work(self):
        self.extension.enabled = False
        self.module.bpy.data.objects["marked"] = types.SimpleNamespace(name="marked")
        calls = []
        self.extension._finalize_prototype_sidecar_for_export = (
            lambda *args: calls.append("finalize"))
        self.extension._restore_textureless_fbx_materials = (
            lambda name: calls.append(("restore", name)))
        self.extension.post_mesh_export({"_mesh_object_name": "marked"}, None)
        self.assertEqual(calls, [("restore", "marked")])

    def test_export_sidecar_rejects_json_mesh_name_as_authority(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sidecar = Path(temp_dir) / "SK_Branch_01.json"
            sidecar.write_text(
                '{"mesh_name": "SK_Wrong"}',
                encoding="utf-8",
            )
            asset_data = {}

            def resolve_exact(asset_data_arg, target, refresh_result):
                asset_data_arg[
                    self.module.MATERIAL_PIPELINE_EXPECTED_MESH_NAME_KEY
                ] = "SK_Branch_01"
                return str(sidecar)

            self.extension._resolve_json_path_for_export = resolve_exact

            with self.assertRaises(RuntimeError):
                self.extension._load_json_sidecar_for_export(
                    asset_data,
                    types.SimpleNamespace(name="SK_Branch_01_Mesh"),
                    {"json_paths": [str(sidecar)]},
                )

        self.assertNotIn(
            self.module.MATERIAL_PIPELINE_JSON_PATH_KEY,
            asset_data,
        )
        self.assertNotIn(
            self.module.MATERIAL_PIPELINE_JSON_SHA256_KEY,
            asset_data,
        )

    def test_prototype_transfer_list_has_no_first_row_fallback(self):
        target, _lineage, _members = self._prototype_transfer_target()
        sidecar = {
            "transfer_sources": [
                {"enabled": True, "target": "Other_A"},
                {"enabled": True, "target": "Other_B"},
            ]
        }
        with self.assertRaises(RuntimeError):
            self.extension._transfer_entry_for_target(
                sidecar,
                target,
            )

    def test_unmarked_target_keeps_existing_transfer_contract(self):
        first = {"enabled": True, "target": "Other_A"}
        matching = {"enabled": True, "target": "Expected"}
        target = types.SimpleNamespace(name="Expected")
        self.assertIs(self.extension._transfer_entry_for_target(
            {"transfer_sources": [first, matching]}, target), matching)
        self.assertIs(self.extension._transfer_entry_for_target(
            {"transfer_sources": [first]}, target), first)
        self.assertIs(self.extension._transfer_entry_for_target(
            {"transfer_source": first}, target), first)

    def _prototype_transfer_target(self):
        identity_api = self.module._prototype_identity_api()
        member = {
            "kind": identity_api.IDENTITY_KIND,
            "schema_version": 1,
            "algorithm": "sha256",
            "digest": "1" * 64,
        }
        lineage = identity_api.lineage_identity([member])
        values = {
            self.module.PROTOTYPE_IDENTITY_OBJECT_KEY: json.dumps(
                lineage
            ),
            self.module.PROTOTYPE_IDENTITY_MEMBERS_OBJECT_KEY: json.dumps(
                [member]
            ),
        }
        target = types.SimpleNamespace(name="Renamed_Target")
        target.get = lambda key: values.get(key)
        return target, lineage, [member]

    def test_prototype_transfer_uses_lineage_not_target_name(self):
        target, lineage, members = self._prototype_transfer_target()
        expected = {
            "enabled": True,
            "target": "Stale_Name",
            "source": "Source",
            "prototype_identity": lineage,
            "prototype_identity_members": members,
        }
        selected = self.extension._transfer_entry_for_target(
            {"transfer_sources": [expected]},
            target,
        )
        self.assertIs(selected, expected)

    def test_prototype_transfer_rejects_name_only_and_duplicate_lineage(self):
        target, lineage, members = self._prototype_transfer_target()
        with self.assertRaises(RuntimeError):
            self.extension._transfer_entry_for_target(
                {
                    "transfer_sources": [{
                        "enabled": True,
                        "target": target.name,
                    }]
                },
                target,
            )

        duplicate = {
            "enabled": True,
            "prototype_identity": lineage,
            "prototype_identity_members": members,
        }
        with self.assertRaises(RuntimeError):
            self.extension._transfer_entry_for_target(
                {"transfer_sources": [duplicate, dict(duplicate)]},
                target,
            )

    def test_preflight_asset_path_uses_expected_asset_unit_not_json(self):
        path = self.extension._preflight_asset_path(
            "/Game/Meshes/Tree/SK_Branch_01_Mesh.SK_Branch_01_Mesh",
            "SK_Branch_01",
        )
        self.assertEqual(path, "/Game/Meshes/Tree/SK_Branch_01")

    def test_post_operation_persists_imported_skeletal_dependencies(self):
        self.asset_data["_asset_type"] = "SKELETAL_MESH"
        self.asset_data[self.module.MATERIAL_PIPELINE_JSON_PATH_KEY] = (
            "D:/texture/SK_CommonGrass.json"
        )

        self.extension.pre_operation(None)
        self.extension.post_import(self.asset_data, None)
        self.extension.post_operation(None)

        self.assertEqual(len(self.command_calls), 2)
        post_operation_commands = "\n".join(self.command_calls[1])
        self.assertIn(
            "persist_generated_skeleton_dependencies",
            post_operation_commands,
        )
        self.assertIn(self.asset_data["asset_path"], post_operation_commands)


if __name__ == "__main__":
    unittest.main()
