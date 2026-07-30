import hashlib
import importlib.util
import json
import os
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
    / "pipeline"
    / "ue_material_setup.py"
)


class FakeTexture:
    def __init__(
        self,
        srgb=True,
        compression_settings="TC_DEFAULT",
        max_texture_size=2048,
        virtual_texture_streaming=False,
    ):
        self.properties = {
            "srgb": srgb,
            "compression_settings": compression_settings,
            "max_texture_size": max_texture_size,
            "virtual_texture_streaming": virtual_texture_streaming,
        }

    def get_editor_property(self, name):
        return self.properties[name]

    def set_editor_property(self, name, value):
        self.properties[name] = value

    def set_virtual_texture_streaming(self, value):
        self.properties["virtual_texture_streaming"] = bool(value)


class FakeUnrealClass:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name


class FakeMaterialInstanceConstant:
    def __init__(self, asset_path, parent=None):
        self.asset_path = asset_path
        self.parent = parent

    def get_path_name(self):
        return self.asset_path

    def get_editor_property(self, name):
        if name == "parent":
            return self.parent
        raise KeyError(name)

    def get_class(self):
        return FakeUnrealClass("MaterialInstanceConstant")


class FakeSkeletalMaterial:
    def __init__(self, slot_name="", material=None):
        self.properties = {
            "material_interface": material,
            "material_slot_name": slot_name,
            "imported_material_slot_name": slot_name,
            "uv_channel_data": None,
            "overlay_material_interface": None,
        }

    def get_editor_property(self, name):
        return self.properties[name]

    def set_editor_property(self, name, value):
        self.properties[name] = value


class FakeSkeletalMesh:
    def __init__(self, materials, skeleton=None):
        self.materials = list(materials)
        self.skeleton = skeleton

    def get_editor_property(self, name):
        if name == "static_materials":
            raise KeyError(name)
        if name == "materials":
            return list(self.materials)
        if name == "skeleton":
            return self.skeleton
        raise KeyError(name)

    def set_editor_property(self, name, value):
        if name != "materials":
            raise KeyError(name)
        self.materials = list(value)


class FakeAssetData:
    def __init__(self, runtime, asset_path):
        self.runtime = runtime
        self.asset_path = asset_path

    def get_tag_value(self, tag_name):
        if tag_name != "AssetImportData":
            return ""
        value = self.runtime.asset_import_tags.get(self.asset_path)
        if value is not None:
            return value
        file_md5 = self.runtime.asset_md5.get(self.asset_path, "")
        return json.dumps(
            [
                {
                    "RelativeFilename": "source.png",
                    "Timestamp": "0",
                    "FileMD5": file_md5,
                    "DisplayLabelName": "",
                }
            ]
        )


class FakeSourceControlState:
    def __init__(
        self,
        is_valid=True,
        is_checked_out=False,
        is_added=False,
        is_checked_out_other=False,
    ):
        self.is_valid = is_valid
        self.is_checked_out = is_checked_out
        self.is_added = is_added
        self.is_checked_out_other = is_checked_out_other


class FakeSourceControl:
    def __init__(self, runtime):
        self.runtime = runtime

    def query_file_state(self, asset_path, *args):
        self.runtime.query_calls.append(asset_path)
        return self.runtime.source_control_states.get(
            asset_path,
            FakeSourceControlState(),
        )

    def check_out_file(self, asset_path, silent=True):
        self.runtime.checkout_calls.append(asset_path)
        return not self.runtime.fail_checkout

    def revert_unchanged_file(self, asset_path, silent=True):
        self.runtime.revert_unchanged_calls.append(asset_path)
        return True

    def mark_file_for_add(self, asset_path, silent=True):
        self.runtime.mark_add_calls.append(asset_path)
        return not self.runtime.fail_mark_add

    @staticmethod
    def last_error_msg():
        return "fake source-control error"


class FakeAssetImportTask:
    def __init__(self):
        self.properties = {}
        self.objects = []

    def set_editor_property(self, name, value):
        self.properties[name] = value

    def get_editor_property(self, name):
        return self.properties[name]

    def get_objects(self):
        return list(self.objects)


class FakeEditorAssetLibrary:
    def __init__(self, runtime):
        self.runtime = runtime

    def does_asset_exist(self, asset_path):
        return asset_path in self.runtime.assets

    def find_asset_data(self, asset_path):
        return FakeAssetData(self.runtime, asset_path)

    def save_asset(self, asset_path, *args, **kwargs):
        self.runtime.save_calls.append(asset_path)
        return not self.runtime.fail_save

    def make_directory(self, asset_path):
        self.runtime.created_directories.append(asset_path)
        return True

    def delete_asset(self, asset_path):
        self.runtime.delete_calls.append(asset_path)
        return self.runtime.assets.pop(asset_path, None) is not None


class FakeAssetTools:
    def __init__(self, runtime):
        self.runtime = runtime

    def import_asset_tasks(self, tasks):
        for task in tasks:
            properties = dict(task.properties)
            self.runtime.import_tasks.append(properties)
            asset_path = (
                f"{properties['destination_path'].rstrip('/')}"
                f"/{properties['destination_name']}"
            )
            if self.runtime.fail_import:
                continue
            texture = self.runtime.assets.get(asset_path) or FakeTexture()
            self.runtime.assets[asset_path] = texture
            self.runtime.asset_md5[asset_path] = (
                self.runtime.import_md5_override or _md5(properties["filename"])
            )
            task.objects = [texture]
            task.properties["imported_object_paths"] = [asset_path]

    def create_asset(self, asset_name, asset_folder, _asset_class, _factory):
        asset_path = f"{asset_folder.rstrip('/')}/{asset_name}"
        raced_asset = self.runtime.race_create_assets.pop(asset_path, None)
        if raced_asset is not None:
            self.runtime.assets[asset_path] = raced_asset
            return None
        if asset_path in self.runtime.fail_create_paths:
            return None
        if asset_path in self.runtime.assets:
            return None
        asset = FakeMaterialInstanceConstant(asset_path)
        self.runtime.assets[asset_path] = asset
        self.runtime.created_assets.append(asset_path)
        return asset


class FakeAssetToolsHelpers:
    def __init__(self, runtime):
        self.asset_tools = FakeAssetTools(runtime)

    def get_asset_tools(self):
        return self.asset_tools


class FakeRuntime:
    def __init__(self):
        self.assets = {}
        self.asset_md5 = {}
        self.asset_import_tags = {}
        self.source_control_states = {}
        self.fail_import = False
        self.fail_save = False
        self.fail_checkout = False
        self.fail_mark_add = False
        self.import_md5_override = None
        self.query_calls = []
        self.checkout_calls = []
        self.revert_unchanged_calls = []
        self.mark_add_calls = []
        self.import_tasks = []
        self.save_calls = []
        self.created_assets = []
        self.created_directories = []
        self.parent_changes = []
        self.delete_calls = []
        self.fail_create_paths = set()
        self.race_create_assets = {}
        self.logs = []
        self.warnings = []

        unreal_module = types.ModuleType("unreal")
        unreal_module.TextureCompressionSettings = types.SimpleNamespace(
            TC_DEFAULT="TC_DEFAULT",
            TC_NORMALMAP="TC_NORMALMAP",
            TC_MASKS="TC_MASKS",
            TC_GRAYSCALE="TC_GRAYSCALE",
        )
        unreal_module.EditorAssetLibrary = FakeEditorAssetLibrary(self)
        unreal_module.SourceControl = FakeSourceControl(self)
        unreal_module.AssetImportTask = FakeAssetImportTask
        unreal_module.AssetToolsHelpers = FakeAssetToolsHelpers(self)
        unreal_module.AssetRegistryHelpers = types.SimpleNamespace(
            get_tag_value=lambda asset_data, tag_name: asset_data.get_tag_value(tag_name)
        )
        unreal_module.MaterialInstanceConstant = FakeMaterialInstanceConstant
        unreal_module.MaterialInstanceConstantFactoryNew = object
        unreal_module.MaterialEditingLibrary = types.SimpleNamespace(
            set_material_instance_parent=self.set_material_instance_parent
        )
        unreal_module.load_asset = self.load_asset
        unreal_module.log = self.logs.append
        unreal_module.log_warning = self.warnings.append
        self.unreal_module = unreal_module

    def load_asset(self, asset_path):
        return self.assets.get(asset_path)

    def set_material_instance_parent(self, asset, parent):
        asset.parent = parent
        self.parent_changes.append((asset.get_path_name(), parent.get_path_name()))


def _md5(file_path):
    return hashlib.md5(Path(file_path).read_bytes()).hexdigest()


def _load_module(runtime):
    module_name = f"test_ue_material_setup_{id(runtime)}"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    previous_unreal = sys.modules.get("unreal")
    sys.modules["unreal"] = runtime.unreal_module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous_unreal is None:
            sys.modules.pop("unreal", None)
        else:
            sys.modules["unreal"] = previous_unreal
    return module


class TestUeMaterialTextureImport(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source_path = Path(self.temp_dir.name) / "T_Surface_extra.png"
        self.source_path.write_bytes(b"current texture bytes")
        self.runtime = FakeRuntime()
        self.module = _load_module(self.runtime)
        self.asset_name = "T_Surface_extra"
        self.asset_path = f"{self.module.TEXTURES_FOLDER}/{self.asset_name}"
        self.source_md5 = _md5(self.source_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_existing_texture(self, texture=None, file_md5=None):
        self.runtime.assets[self.asset_path] = texture or FakeTexture(
            srgb=False,
            compression_settings="TC_MASKS",
            max_texture_size=0,
            virtual_texture_streaming=True,
        )
        self.runtime.asset_md5[self.asset_path] = file_md5 or self.source_md5

    def import_texture(self, cache=None, force=False, param="Albedo"):
        return self.module._import_texture(
            str(self.source_path),
            self.asset_name,
            param,
            cache,
            force_reimport=force,
        )

    def test_matching_md5_and_settings_skip_mutation_even_when_forced(self):
        self.add_existing_texture()
        untouched_path = "/Game/Textures/T_Untouched"
        cache = {
            self.asset_path: os.path.getmtime(self.source_path),
            untouched_path: 123.5,
        }

        self.assertEqual(self.import_texture(cache, force=True), self.asset_path)

        self.assertEqual(self.runtime.checkout_calls, [])
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertEqual(self.runtime.save_calls, [])
        self.assertEqual(self.runtime.revert_unchanged_calls, [])
        self.assertEqual(cache[self.asset_path]["version"], 2)
        self.assertEqual(cache[self.asset_path]["md5"], self.source_md5)
        self.assertEqual(cache[untouched_path], 123.5)

    def test_v2_cache_never_hides_same_stat_different_bytes(self):
        self.source_path.write_bytes(b"A" * 32)
        old_md5 = _md5(self.source_path)
        stat_result = self.source_path.stat()
        self.source_md5 = old_md5
        self.add_existing_texture(file_md5=old_md5)
        cache = {
            self.asset_path: {
                "version": 2,
                "source_path": str(self.source_path),
                "mtime_ns": stat_result.st_mtime_ns,
                "size": stat_result.st_size,
                "md5": old_md5,
            }
        }

        self.source_path.write_bytes(b"B" * 32)
        os.utime(
            self.source_path,
            ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns),
        )
        self.source_md5 = _md5(self.source_path)

        self.assertEqual(self.import_texture(cache), self.asset_path)
        self.assertEqual(self.runtime.checkout_calls, [self.asset_path])
        self.assertEqual(len(self.runtime.import_tasks), 1)
        self.assertEqual(cache[self.asset_path]["md5"], self.source_md5)

    def test_stale_md5_reimports_only_requested_existing_texture(self):
        self.add_existing_texture(file_md5="1" * 32)
        other_path = "/Game/Textures/T_Other"
        self.runtime.assets[other_path] = FakeTexture()
        self.runtime.asset_md5[other_path] = "2" * 32
        cache = {}

        self.assertEqual(self.import_texture(cache), self.asset_path)

        self.assertEqual(self.runtime.checkout_calls, [self.asset_path])
        self.assertEqual(self.runtime.revert_unchanged_calls, [self.asset_path])
        self.assertEqual(self.runtime.save_calls, [self.asset_path])
        self.assertEqual(len(self.runtime.import_tasks), 1)
        self.assertTrue(self.runtime.import_tasks[0]["replace_existing"])
        self.assertFalse(self.runtime.import_tasks[0]["save"])
        self.assertNotIn(other_path, self.runtime.checkout_calls)
        self.assertEqual(cache[self.asset_path]["md5"], self.source_md5)

    def test_matching_md5_role_drift_configures_without_reimport(self):
        texture = FakeTexture(
            srgb=True,
            compression_settings="TC_DEFAULT",
            max_texture_size=2048,
            virtual_texture_streaming=False,
        )
        self.add_existing_texture(texture)

        self.assertEqual(self.import_texture({}), self.asset_path)

        self.assertEqual(self.runtime.import_tasks, [])
        self.assertEqual(self.runtime.checkout_calls, [self.asset_path])
        self.assertEqual(self.runtime.save_calls, [self.asset_path])
        self.assertEqual(self.runtime.revert_unchanged_calls, [self.asset_path])
        self.assertFalse(texture.properties["srgb"])
        self.assertEqual(texture.properties["compression_settings"], "TC_MASKS")
        self.assertEqual(texture.properties["max_texture_size"], 0)
        self.assertTrue(texture.properties["virtual_texture_streaming"])

    def test_role_settings_cover_masks_normal_grayscale_and_color(self):
        cases = (
            ("Extra", False, "TC_MASKS", True),
            ("Normal", False, "TC_NORMALMAP", True),
            ("Height", False, "TC_GRAYSCALE", True),
            ("Opacity", False, "TC_GRAYSCALE", False),
            ("Opacity Map", False, "TC_GRAYSCALE", False),
            ("Alpha", False, "TC_GRAYSCALE", False),
            ("Albedo", True, "TC_DEFAULT", True),
            ("Subsurface", True, "TC_DEFAULT", True),
        )
        for role, expected_srgb, expected_compression, expected_vt in cases:
            with self.subTest(role=role):
                settings = self.module._desired_texture_settings(
                    role,
                    virtual_texture_streaming=False,
                )
                self.assertEqual(settings["srgb"], expected_srgb)
                self.assertEqual(
                    settings["compression_settings"],
                    expected_compression,
                )
                self.assertEqual(settings["max_texture_size"], 0)
                self.assertEqual(
                    settings["virtual_texture_streaming"],
                    expected_vt,
                )

    def test_opacity_role_drift_disables_virtual_texture_without_reimport(self):
        texture = FakeTexture(
            srgb=False,
            compression_settings="TC_GRAYSCALE",
            max_texture_size=0,
            virtual_texture_streaming=True,
        )

        changed = self.module._configure_imported_texture(
            texture,
            "Opacity Map",
            file_path=str(Path(self.temp_dir.name) / "T_leaf_opacity.tga"),
            asset_name="T_leaf_opacity",
        )

        self.assertTrue(changed)
        self.assertFalse(texture.properties["virtual_texture_streaming"])

    def test_preexisting_user_checkout_is_not_reverted(self):
        texture = FakeTexture(max_texture_size=1024)
        self.add_existing_texture(texture)
        self.runtime.source_control_states[self.asset_path] = FakeSourceControlState(
            is_checked_out=True
        )

        self.assertEqual(self.import_texture({}), self.asset_path)

        self.assertEqual(self.runtime.checkout_calls, [])
        self.assertEqual(self.runtime.revert_unchanged_calls, [])
        self.assertEqual(self.runtime.save_calls, [self.asset_path])

    def test_new_texture_import_marks_for_add_after_configured_save(self):
        normal_source = Path(self.temp_dir.name) / "T_Surface_normal.png"
        normal_source.write_bytes(self.source_path.read_bytes())
        self.source_path = normal_source
        self.source_md5 = _md5(self.source_path)
        self.asset_name = "T_Surface_normal"
        self.asset_path = f"{self.module.TEXTURES_FOLDER}/{self.asset_name}"
        cache = {}

        self.assertEqual(self.import_texture(cache, param="Normal"), self.asset_path)

        self.assertEqual(self.runtime.checkout_calls, [])
        self.assertEqual(self.runtime.revert_unchanged_calls, [])
        self.assertEqual(self.runtime.save_calls, [self.asset_path])
        self.assertEqual(self.runtime.mark_add_calls, [self.asset_path])
        self.assertFalse(self.runtime.import_tasks[0]["replace_existing"])
        texture = self.runtime.assets[self.asset_path]
        self.assertFalse(texture.properties["srgb"])
        self.assertEqual(texture.properties["compression_settings"], "TC_NORMALMAP")
        self.assertEqual(texture.properties["max_texture_size"], 0)
        self.assertTrue(texture.properties["virtual_texture_streaming"])
        self.assertEqual(cache[self.asset_path]["md5"], self.source_md5)

    def test_owned_checkout_reverts_unchanged_after_failed_reimport(self):
        self.add_existing_texture(file_md5="1" * 32)
        cache = {self.asset_path: os.path.getmtime(self.source_path)}
        self.runtime.fail_import = True

        self.assertIsNone(self.import_texture(cache))

        self.assertEqual(self.runtime.checkout_calls, [self.asset_path])
        self.assertEqual(self.runtime.revert_unchanged_calls, [self.asset_path])
        self.assertEqual(self.runtime.save_calls, [])
        self.assertIsInstance(cache[self.asset_path], float)

    def test_post_import_md5_mismatch_is_not_success_or_cached(self):
        self.add_existing_texture(file_md5="1" * 32)
        cache = {}
        self.runtime.import_md5_override = "f" * 32

        self.assertIsNone(self.import_texture(cache))

        self.assertEqual(len(self.runtime.import_tasks), 1)
        self.assertEqual(self.runtime.save_calls, [self.asset_path])
        self.assertEqual(self.runtime.revert_unchanged_calls, [self.asset_path])
        self.assertNotIn(self.asset_path, cache)

    def test_existing_save_failure_is_not_success_or_cached(self):
        self.add_existing_texture(FakeTexture(max_texture_size=1024))
        cache = {}
        self.runtime.fail_save = True

        self.assertIsNone(self.import_texture(cache))

        self.assertEqual(self.runtime.import_tasks, [])
        self.assertEqual(self.runtime.save_calls, [self.asset_path])
        self.assertEqual(self.runtime.revert_unchanged_calls, [self.asset_path])
        self.assertNotIn(self.asset_path, cache)

    def test_new_save_failure_is_not_added_or_cached(self):
        cache = {}
        self.runtime.fail_save = True

        self.assertIsNone(self.import_texture(cache))

        self.assertEqual(len(self.runtime.import_tasks), 1)
        self.assertEqual(self.runtime.save_calls, [self.asset_path])
        self.assertEqual(self.runtime.mark_add_calls, [])
        self.assertNotIn(self.asset_path, cache)

    def test_other_user_checkout_blocks_before_mutation(self):
        self.add_existing_texture(file_md5="1" * 32)
        cache = {}
        self.runtime.source_control_states[self.asset_path] = FakeSourceControlState(
            is_checked_out_other=True
        )

        with self.assertRaises(RuntimeError):
            self.import_texture(cache)

        self.assertEqual(self.runtime.checkout_calls, [])
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertEqual(self.runtime.save_calls, [])
        self.assertNotIn(self.asset_path, cache)

    def test_checkout_failure_blocks_before_mutation(self):
        self.add_existing_texture(file_md5="1" * 32)
        cache = {}
        self.runtime.fail_checkout = True

        with self.assertRaises(RuntimeError):
            self.import_texture(cache)

        self.assertEqual(self.runtime.checkout_calls, [self.asset_path])
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertEqual(self.runtime.save_calls, [])
        self.assertNotIn(self.asset_path, cache)

    def test_mark_add_failure_does_not_cache_success(self):
        cache = {}
        self.runtime.fail_mark_add = True

        with self.assertRaises(RuntimeError):
            self.import_texture(cache)

        self.assertEqual(self.runtime.save_calls, [self.asset_path])
        self.assertEqual(self.runtime.mark_add_calls, [self.asset_path])
        self.assertNotIn(self.asset_path, cache)

    def test_missing_source_does_not_migrate_legacy_cache(self):
        self.add_existing_texture()
        cache = {self.asset_path: 42.0}
        self.source_path.unlink()

        self.assertIsNone(self.import_texture(cache))
        self.assertEqual(cache[self.asset_path], 42.0)
        self.assertEqual(self.runtime.checkout_calls, [])

    def test_asset_import_md5_accepts_unreal_json_and_tuple_tag_result(self):
        expected = "a" * 32

        class TupleAssetData(FakeAssetData):
            def get_tag_value(self, tag_name):
                value = json.dumps([{"FileMD5": expected}])
                return True, value

        original_find = self.runtime.unreal_module.EditorAssetLibrary.find_asset_data
        self.runtime.unreal_module.EditorAssetLibrary.find_asset_data = (
            lambda asset_path: TupleAssetData(self.runtime, asset_path)
        )
        try:
            self.assertEqual(
                self.module._asset_import_file_md5(self.asset_path),
                expected,
            )
        finally:
            self.runtime.unreal_module.EditorAssetLibrary.find_asset_data = original_find

    def test_preflight_mutation_paths_exclude_texture_assets(self):
        self.module._master_preset = lambda data, entry, mesh_path: {
            "master": "/Game/Material/M_Master",
            "assignment": "none",
            "mi_folder": "",
        }
        self.module._layer_parent_path = lambda preset, entry: None
        self.module._entry_target_material_path = lambda entry: None
        data = {
            "materials": [
                {
                    "name": "M_Test",
                    "textures": [{"asset_name": "T_Direct"}],
                    "layers": [
                        {"textures": [{"asset_name": "T_Layer"}]}
                    ],
                }
            ]
        }

        paths = self.module._material_pipeline_mutation_paths(
            "/Game/Meshes/SM_Test",
            data,
        )

        self.assertIn("/Game/Meshes/SM_Test", paths)
        self.assertIn("/Game/Material/M_Master", paths)
        self.assertNotIn("/Game/Textures/T_Direct", paths)
        self.assertNotIn("/Game/Textures/T_Layer", paths)

    def test_codex_test_mutation_paths_keep_production_references_read_only(self):
        self.module._master_preset = lambda data, entry, mesh_path: {
            "master": "/Game/Material/Tree/Master/M_Tree",
            "assignment": "material_layer_instance",
            "mi_folder": "/Game/Codex/Tests/Elm/_MaterialPipeline/MI",
        }
        self.module._layer_parent_path = (
            lambda preset, entry: "/Game/Material/Tree/Layer/MY_Tree"
        )
        self.module._entry_target_material_path = (
            lambda entry: "/Game/Codex/Tests/Elm/_MaterialPipeline/MI/MI_Bark"
        )
        self.module._layer_instance_path = (
            lambda *args: "/Game/Codex/Tests/Elm/_MaterialPipeline/MYI/MYI_Bark"
        )

        paths = self.module._material_pipeline_mutation_paths(
            "/Game/Codex/Tests/Elm/SK_Tree",
            {"materials": [{"name": "M_Bark"}]},
        )

        self.assertIn("/Game/Codex/Tests/Elm/SK_Tree", paths)
        self.assertIn(
            "/Game/Codex/Tests/Elm/_MaterialPipeline/MI/MI_Bark", paths
        )
        self.assertIn(
            "/Game/Codex/Tests/Elm/_MaterialPipeline/MYI/MYI_Bark", paths
        )
        self.assertNotIn("/Game/Material/Tree/Master/M_Tree", paths)
        self.assertNotIn("/Game/Material/Tree/Layer/MY_Tree", paths)

    def test_codex_test_normalization_skips_production_reference(self):
        calls = []

        class Helper:
            def normalize_material_layer_placeholders(inner_self, asset_path):
                calls.append(asset_path)
                return True

        self.module._normalize_material_layer_asset(
            Helper(),
            "normalize_material_layer_placeholders",
            "/Game/Material/Tree/Master/M_Tree",
            "material master",
            mutation_scope_path="/Game/Codex/Tests/Elm/SK_Tree",
        )

        self.assertEqual(calls, [])

    def test_codex_test_scope_requires_isolated_explicit_targets(self):
        valid = {
            "codex_test_asset_scope": {
                "root": "/Game/Codex/Tests/Elm/_MaterialPipeline",
            },
            "materials": [
                {
                    "name": "M_Bark",
                    "target_material_path": (
                        "/Game/Codex/Tests/Elm/_MaterialPipeline/MI/MI_Bark"
                    ),
                    "material_layer": {
                        "instance_path": (
                            "/Game/Codex/Tests/Elm/_MaterialPipeline/MYI/MYI_Bark"
                        ),
                    },
                }
            ],
        }
        self.assertTrue(
            self.module._validate_codex_test_material_scope(
                valid, "/Game/Codex/Tests/Elm/SK_Tree"
            )
        )

        invalid = json.loads(json.dumps(valid))
        invalid["materials"][0]["target_material_path"] = (
            "/Game/Material/Tree/AssetTree/MI/MI_Bark"
        )
        with self.assertRaisesRegex(RuntimeError, "isolated MI scope"):
            self.module._validate_codex_test_material_scope(
                invalid, "/Game/Codex/Tests/Elm/SK_Tree"
            )

    def test_codex_test_scope_overrides_tree_contract_output_folders(self):
        self.module._tree_preset_contract_overlay = lambda: {
            "mi_folder": "/Game/Material/Tree/AssetTree/MI",
            "layer_instance_folder": "/Game/Material/Tree/AssetTree/MYI",
        }
        preset = self.module._master_preset(
            {
                "codex_test_asset_scope": {
                    "root": "/Game/Codex/Tests/Elm/_MaterialPipeline",
                }
            },
            {"name": "M_Bark", "master_preset": "tree"},
            "/Game/Codex/Tests/Elm/SK_Tree",
        )
        self.assertEqual(
            preset["mi_folder"],
            "/Game/Codex/Tests/Elm/_MaterialPipeline/MI",
        )
        self.assertEqual(
            preset["layer_instance_folder"],
            "/Game/Codex/Tests/Elm/_MaterialPipeline/MYI",
        )

    def test_material_checkout_skips_added_assets_and_blocks_other_user(self):
        added_path = "/Game/Material/Tree/AssetTree/MI/MI_New"
        other_path = "/Game/Material/Tree/AssetTree/MI/MI_Other"
        self.runtime.source_control_states[added_path] = FakeSourceControlState(
            is_added=True
        )
        self.runtime.source_control_states[other_path] = FakeSourceControlState(
            is_checked_out_other=True
        )

        self.assertFalse(self.module._material_asset_needs_checkout(added_path))
        with self.assertRaisesRegex(RuntimeError, "checked out by another user"):
            self.module._material_asset_needs_checkout(other_path)

    def test_tree_part_prefers_physical_branch_over_atlas_family_tokens(self):
        self.assertEqual(
            self.module._tree_part_key({"name": "M_leaf_parsley_atlas_02_stem"}),
            "leaf",
        )
        self.assertEqual(
            self.module._tree_part_key({"name": "M_bark_deadbranch_02"}),
            "branch",
        )
        self.assertEqual(
            self.module._tree_part_key({"name": "M_cluster_ladyfern_atlas_02"}),
            "leaf",
        )

    def test_tree_shading_selects_independent_leaf_stem_and_wood_masters(self):
        leaf_entry = {"name": "M_leaf_parsley_atlas_02_stem", "master_preset": "tree"}
        stem_entry = {"name": "M_stem_common_04", "master_preset": "tree"}
        wood_entry = {"name": "M_Branch_deadbranch_01", "master_preset": "tree"}

        leaf = self.module._master_preset({}, leaf_entry)
        stem = self.module._master_preset({}, stem_entry)
        wood = self.module._master_preset({}, wood_entry)

        self.assertEqual(leaf["tree_part"], "leaf")
        self.assertEqual(leaf["tree_shading"], "foliage")
        self.assertTrue(leaf["master"].endswith("M_TreeAsset_Foliage_Master"))
        self.assertEqual(stem["tree_part"], "branch")
        self.assertEqual(stem["tree_shading"], "stem")
        self.assertTrue(stem["master"].endswith("M_TreeAsset_Stem_Master"))
        self.assertEqual(wood["tree_shading"], "wood")
        self.assertTrue(wood["master"].endswith("M_TreeAsset_Master"))

        explicit = self.module._master_preset(
            {},
            {
                "name": "M_Branch_living_contract",
                "master_preset": "tree",
                "tree_shading": "stem",
            },
        )
        self.assertEqual(explicit["tree_shading"], "stem")
        self.assertTrue(explicit["master"].endswith("M_TreeAsset_Stem_Master"))

    def test_speedtree_instance_profile_uses_literal_existing_child(self):
        entry = {
            "name": "M_stem_common_01",
            "master_preset": "tree",
            "tree_shading": "stem",
            "instance_profile": "Dead",
            "material_instance_mode": "create_or_reuse",
        }
        preset = self.module._master_preset({}, entry)
        paths = self.module._instance_profile_material_paths(entry, preset)
        self.assertTrue(paths["base_path"].endswith("/MI_stem_common_01"))
        self.assertTrue(paths["target_path"].endswith("/MI_stem_common_01_dead"))

        base = FakeMaterialInstanceConstant(paths["base_path"])
        target = FakeMaterialInstanceConstant(paths["target_path"], parent=base)
        self.runtime.assets[paths["base_path"]] = base
        self.runtime.assets[paths["target_path"]] = target

        targets = self.module._validate_instance_profile_targets(
            {"materials": [entry]},
            "/Game/Meshes/SK_CommonGrass",
        )

        self.assertIs(targets[0]["asset"], target)
        self.assertEqual(targets[0]["profile"], "dead")
        self.assertEqual(self.runtime.checkout_calls, [])
        self.assertEqual(self.runtime.save_calls, [])

    def test_second_spm_reuses_profile_target_created_by_first_spm(self):
        entry = {
            "name": "M_stem_common_01",
            "master_preset": "tree",
            "tree_shading": "stem",
            "instance_profile": "dead",
            "material_instance_mode": "create_or_reuse",
        }
        preset = self.module._master_preset({}, entry)
        paths = self.module._instance_profile_material_paths(entry, preset)
        base = FakeMaterialInstanceConstant(paths["base_path"])
        self.runtime.assets[paths["base_path"]] = base

        first_spm_targets = self.module._validate_instance_profile_targets(
            {"materials": [dict(entry)]},
            "/Game/Meshes/SK_FirstGrass",
        )
        self.module._ensure_instance_profile_targets(
            self.runtime.unreal_module.AssetToolsHelpers.get_asset_tools(),
            first_spm_targets,
        )
        shared_target = first_spm_targets[0]["asset"]
        shared_target.user_tint = (0.09, 0.04, 0.01, 1.0)

        created_before = list(self.runtime.created_assets)
        saved_before = list(self.runtime.save_calls)
        parent_changes_before = list(self.runtime.parent_changes)
        mark_add_before = list(self.runtime.mark_add_calls)

        # A separate SPM builds a fresh plan for the same base MI + profile key.
        second_spm_targets = self.module._validate_instance_profile_targets(
            {"materials": [dict(entry)]},
            "/Game/Meshes/SK_SecondGrass",
        )
        self.module._ensure_instance_profile_targets(
            self.runtime.unreal_module.AssetToolsHelpers.get_asset_tools(),
            second_spm_targets,
        )

        self.assertIs(second_spm_targets[0]["asset"], shared_target)
        self.assertEqual(
            shared_target.user_tint,
            (0.09, 0.04, 0.01, 1.0),
        )
        self.assertEqual(self.runtime.created_assets, created_before)
        self.assertEqual(self.runtime.save_calls, saved_before)
        self.assertEqual(self.runtime.parent_changes, parent_changes_before)
        self.assertEqual(self.runtime.mark_add_calls, mark_add_before)
        self.assertEqual(self.runtime.checkout_calls, [])

    def test_missing_speedtree_profile_target_is_created_once(self):
        entry = {
            "name": "M_stem_common_01",
            "master_preset": "tree",
            "instance_profile": "dead",
            "material_instance_mode": "create_or_reuse",
        }
        preset = self.module._master_preset({}, entry)
        paths = self.module._instance_profile_material_paths(entry, preset)
        base = FakeMaterialInstanceConstant(paths["base_path"])
        self.runtime.assets[paths["base_path"]] = base

        targets = self.module._validate_instance_profile_targets(
            {"materials": [entry]},
            "/Game/Meshes/SK_CommonGrass",
        )
        self.assertTrue(targets[0]["create_target"])
        self.assertIsNone(targets[0]["asset"])

        self.module._ensure_instance_profile_targets(
            self.runtime.unreal_module.AssetToolsHelpers.get_asset_tools(),
            targets,
        )
        target = targets[0]["asset"]

        self.assertEqual(self.runtime.checkout_calls, [])
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertIs(target.parent, base)
        self.assertEqual(self.runtime.created_assets, [paths["target_path"]])
        self.assertEqual(self.runtime.save_calls, [paths["target_path"]])
        self.assertEqual(self.runtime.mark_add_calls, [paths["target_path"]])

        self.module._ensure_instance_profile_targets(
            self.runtime.unreal_module.AssetToolsHelpers.get_asset_tools(),
            targets,
        )
        self.assertIs(targets[0]["asset"], target)
        self.assertEqual(self.runtime.created_assets, [paths["target_path"]])
        self.assertEqual(self.runtime.save_calls, [paths["target_path"]])
        self.assertEqual(self.runtime.parent_changes, [
            (paths["target_path"], paths["base_path"])
        ])

    def test_existing_speedtree_profile_preserves_parent_and_rejects_unsafe_key(self):
        entry = {
            "name": "M_stem_common_01",
            "master_preset": "tree",
            "instance_profile": "dead",
        }
        preset = self.module._master_preset({}, entry)
        paths = self.module._instance_profile_material_paths(entry, preset)
        base = FakeMaterialInstanceConstant(paths["base_path"])
        wrong_parent = FakeMaterialInstanceConstant("/Game/Material/Tree/MI_Other")
        target = FakeMaterialInstanceConstant(
            paths["target_path"], parent=wrong_parent
        )
        self.runtime.assets[paths["base_path"]] = base
        self.runtime.assets[paths["target_path"]] = target

        targets = self.module._validate_instance_profile_targets(
            {"materials": [entry]},
            "/Game/Meshes/SK_CommonGrass",
        )
        self.module._ensure_instance_profile_targets(
            self.runtime.unreal_module.AssetToolsHelpers.get_asset_tools(),
            targets,
        )
        self.assertIs(target.parent, wrong_parent)
        self.assertEqual(self.runtime.parent_changes, [])
        self.assertEqual(self.runtime.save_calls, [])
        self.assertEqual(self.runtime.checkout_calls, [])

        with self.assertRaisesRegex(RuntimeError, "invalid SpeedTree instance_profile"):
            self.module._entry_instance_profile({"instance_profile": "../dead"})

    def test_profile_creation_rolls_back_only_assets_created_in_this_call(self):
        entries = [
            {
                "name": "M_stem_common_01",
                "master_preset": "tree",
                "instance_profile": "dead",
            },
            {
                "name": "M_leaf_common_01",
                "master_preset": "tree",
                "instance_profile": "dead",
            },
        ]
        for entry in entries:
            preset = self.module._master_preset({}, entry)
            master_path = preset["master"].split(".")[0]
            self.runtime.assets.setdefault(
                master_path,
                FakeMaterialInstanceConstant(master_path),
            )
        targets = self.module._validate_instance_profile_targets(
            {"materials": entries},
            "/Game/Meshes/SK_CommonGrass",
        )
        ordered = sorted(
            {plan["target_path"]: plan for plan in targets.values()}.values(),
            key=lambda plan: plan["target_path"].casefold(),
        )
        self.runtime.fail_create_paths.add(ordered[1]["target_path"])

        with self.assertRaisesRegex(RuntimeError, "creation failed"):
            self.module._ensure_instance_profile_targets(
                self.runtime.unreal_module.AssetToolsHelpers.get_asset_tools(),
                targets,
            )

        for path in self.runtime.created_assets:
            self.assertNotIn(path, self.runtime.assets)
        self.assertTrue(self.runtime.delete_calls)

    def test_material_group_names_do_not_imply_instance_profile(self):
        data = {
            "materials": [
                {"name": "M_Branch_deadbranch_01", "master_preset": "tree"},
                {"name": "M_Leaf_common_01_green", "master_preset": "tree"},
                {"name": "M_Leaf_common_01_yellow", "master_preset": "tree"},
            ]
        }
        self.assertEqual(
            self.module._validate_instance_profile_targets(
                data, "/Game/Meshes/SK_DeadBranch"
            ),
            {},
        )

    def test_shared_speedtree_golden_vectors_match_unreal_adapter(self):
        contract_api = self.module._speedtree_handoff_api()
        self.assertIsNotNone(contract_api)
        vectors = contract_api.golden_vectors()

        for vector in vectors["tree_axes"]:
            with self.subTest(tree_material=vector["name"]):
                entry = {"name": vector["name"], "master_preset": "tree"}
                preset = self.module._master_preset({}, entry)
                self.assertEqual(preset.get("tree_part"), vector["tree_part"])
                self.assertEqual(
                    preset.get("tree_shading"),
                    vector["tree_shading"],
                )

        for vector in vectors["profiles"]:
            entry = {"instance_profile": vector["value"]}
            if vector.get("error"):
                with self.assertRaises(RuntimeError):
                    self.module._entry_instance_profile(entry)
            else:
                self.assertEqual(
                    self.module._entry_instance_profile(entry),
                    vector["normalized"],
                )

        for vector in vectors["tree_texture_policy"]:
            self.assertEqual(
                self.module._tree_texture_param_allowed(
                    vector["param"],
                    {
                        "key": "tree",
                        "tree_shading": vector["tree_shading"],
                    },
                ),
                vector["allowed"],
            )

    def test_shared_tree_preset_overlay_supplies_unreal_paths(self):
        contract_api = self.module._speedtree_handoff_api()
        shared = contract_api.tree_unreal_preset()
        preset = self.module._master_preset(
            {},
            {"name": "M_stem_common_01", "master_preset": "tree"},
        )

        self.assertEqual(preset["mi_folder"], shared["mi_folder"])
        self.assertEqual(
            preset["layer_instance_folder"],
            shared["layer_instance_folder"],
        )
        self.assertEqual(
            preset["master"],
            shared["masters_by_shading"]["stem"],
        )

    def test_shared_api_unavailable_preserves_legacy_tree_fallbacks(self):
        cached_api = self.module._SPEEDTREE_HANDOFF_API
        self.module._SPEEDTREE_HANDOFF_API = None
        try:
            entry = {
                "name": "M_stem_common_01",
                "master_preset": "tree",
                "instance_profile": "Dead",
            }
            preset = self.module._master_preset({}, entry)
            self.assertEqual(preset["tree_part"], "branch")
            self.assertEqual(preset["tree_shading"], "stem")
            self.assertEqual(
                self.module._entry_instance_profile(entry),
                "dead",
            )
            self.assertEqual(
                self.module._material_instance_base_name("M_Bark_Mat"),
                "Bark_Mat",
            )
            self.assertTrue(
                self.module._tree_texture_param_allowed("Opacity", preset)
            )
            self.assertFalse(
                self.module._tree_texture_param_allowed("Transmission", preset)
            )
        finally:
            self.module._SPEEDTREE_HANDOFF_API = cached_api

    def test_tree_opacity_remap_is_canonical_and_transmission_is_excluded(self):
        preset = {
            "key": "tree",
            "tree_shading": "foliage",
            "layer_texture_remap": {
                "Albedo": "Albedo",
                "Transmission": "Transmission",
            },
        }

        remap = self.module._layer_texture_remap(preset, {})

        self.assertEqual(remap["Alpha"], "Opacity Map")
        self.assertEqual(remap["Opacity"], "Opacity Map")
        self.assertEqual(remap["Opacity Map"], "Opacity Map")
        self.assertNotIn("Transmission", remap)

    def _contract_sidecar(self, mesh_name="SK_CommonGrass"):
        contract_api = self.module._speedtree_handoff_api()
        entry = {
            "name": "M_stem_common_01",
            "slot_name": "M_stem_common_01",
            "slot_index": 0,
            "master_preset": "tree",
            "tree_part": "branch",
            "tree_shading": "stem",
            "instance_profile": "dead",
            "material_instance_mode": "create_or_reuse",
        }
        entry["speedtree_intent"] = contract_api.build_material_intent(
            entry["name"],
            explicit_tree_part=entry["tree_part"],
            explicit_tree_shading=entry["tree_shading"],
            instance_profile=entry["instance_profile"],
        )
        return {
            "schema_version": 3,
            "material_pipeline": "surface_layers",
            "mesh_name": mesh_name,
            "material_master": "tree",
            "speedtree_handoff_contract": (
                contract_api.build_sidecar_descriptor(mesh_name)
            ),
            "materials": [entry],
        }

    def test_new_sidecar_descriptor_and_intent_validate_before_mutation(self):
        data = self._contract_sidecar()
        descriptor = self.module._validate_speedtree_handoff_contract(
            data,
            "SK_CommonGrass",
        )

        self.assertEqual(descriptor["asset_kind"], "speedtree")
        self.assertTrue(
            self.module._is_speedtree_asset(
                data,
                "/Game/Meshes/OutsideTree/SK_CommonGrass",
            )
        )
        self.assertEqual(self.runtime.checkout_calls, [])
        self.assertEqual(self.runtime.created_assets, [])
        self.assertEqual(self.runtime.save_calls, [])

    def test_new_sidecar_mismatch_blocks_while_legacy_stays_compatible(self):
        bad_descriptor = self._contract_sidecar()
        bad_descriptor["speedtree_handoff_contract"]["fingerprint"] = "stale"
        with self.assertRaisesRegex(RuntimeError, "fingerprint mismatch"):
            self.module._validate_speedtree_handoff_contract(
                bad_descriptor,
                "SK_CommonGrass",
            )

        bad_intent = self._contract_sidecar()
        bad_intent["materials"][0]["speedtree_intent"][
            "material_instance_base"
        ] = "wrong"
        with self.assertRaisesRegex(RuntimeError, "material_instance_base mismatch"):
            self.module._validate_speedtree_handoff_contract(
                bad_intent,
                "SK_CommonGrass",
            )

        wrong_mesh = self._contract_sidecar("SK_Other")
        with self.assertRaisesRegex(RuntimeError, "mesh mismatch|mesh_name mismatch"):
            self.module._validate_speedtree_handoff_contract(
                wrong_mesh,
                "SK_CommonGrass",
            )

        legacy = {
            "mesh_name": "SK_Other",
            "materials": [
                {"name": "M_stem_common_01", "master_preset": "tree"}
            ],
        }
        self.assertIsNone(
            self.module._validate_speedtree_handoff_contract(
                legacy,
                "SK_CommonGrass",
            )
        )

    def test_explicit_json_path_never_falls_back_to_global_search(self):
        calls = []
        self.module._find_json_path = lambda *args: calls.append(args)
        missing = Path(self.temp_dir.name) / "missing.json"

        with self.assertRaisesRegex(RuntimeError, "explicit JSON sidecar is missing"):
            self.module._load_json(
                "SK_CommonGrass",
                explicit_path=str(missing),
                mesh_path="/Game/Meshes/SK_CommonGrass",
            )
        self.assertEqual(calls, [])

    def test_profile_create_race_reuses_exact_existing_target_unchanged(self):
        entry = {
            "name": "M_stem_common_01",
            "master_preset": "tree",
            "instance_profile": "dead",
            "material_instance_mode": "create_or_reuse",
        }
        preset = self.module._master_preset({}, entry)
        paths = self.module._instance_profile_material_paths(entry, preset)
        base = FakeMaterialInstanceConstant(paths["base_path"])
        raced_parent = FakeMaterialInstanceConstant("/Game/Material/UserParent")
        raced_target = FakeMaterialInstanceConstant(
            paths["target_path"],
            parent=raced_parent,
        )
        self.runtime.assets[paths["base_path"]] = base
        self.runtime.race_create_assets[paths["target_path"]] = raced_target
        targets = self.module._validate_instance_profile_targets(
            {"materials": [entry]},
            "/Game/Meshes/SK_CommonGrass",
        )

        self.module._ensure_instance_profile_targets(
            self.runtime.unreal_module.AssetToolsHelpers.get_asset_tools(),
            targets,
        )

        self.assertIs(targets[0]["asset"], raced_target)
        self.assertIs(raced_target.parent, raced_parent)
        self.assertEqual(self.runtime.created_assets, [])
        self.assertEqual(self.runtime.parent_changes, [])
        self.assertEqual(self.runtime.save_calls, [])
        self.assertEqual(self.runtime.mark_add_calls, [])
        self.assertEqual(self.runtime.delete_calls, [])

    def test_new_explicit_and_base_mis_are_saved_and_marked_for_add(self):
        asset_tools = self.runtime.unreal_module.AssetToolsHelpers.get_asset_tools()
        master = FakeMaterialInstanceConstant("/Game/Material/M_Master")
        explicit_path = "/Game/Material/MI/MI_Explicit"
        explicit, _path, created, _source = (
            self.module._load_or_copy_target_material(
                asset_tools,
                explicit_path,
                master_mat=master,
            )
        )
        self.assertTrue(created)
        self.assertIs(explicit.parent, master)
        self.assertIn(explicit_path, self.runtime.save_calls)
        self.assertIn(explicit_path, self.runtime.mark_add_calls)

        base, base_path, created, _parent_changed, _source = (
            self.module._create_or_load_mi(
                asset_tools,
                master,
                "TreeBase",
                "/Game/Material/Tree/MI",
            )
        )
        self.assertTrue(created)
        self.module._save_and_mark_new_material_asset(base_path)
        self.assertIs(base.parent, master)
        self.assertIn(base_path, self.runtime.save_calls)
        self.assertIn(base_path, self.runtime.mark_add_calls)

    def test_new_myi_report_marks_only_created_layer_for_add(self):
        layer_path = "/Game/Material/Tree/MYI/MYI_Test"

        class Helper:
            def create_or_update_material_layer_instance(inner_self, *args):
                self.runtime.assets[layer_path] = FakeMaterialInstanceConstant(
                    layer_path
                )
                return True, json.dumps({"created": True}), []

            def set_material_instance_background_layer(inner_self, *args):
                return True

        self.runtime.unreal_module.CodexMaterialToolsLibrary = Helper()
        self.module._normalize_material_layer_asset = lambda *args, **kwargs: None
        self.module._layer_parent_path = lambda preset, entry: "/Game/Layer/Parent"
        self.module._layer_instance_path = lambda *args: layer_path
        self.module._prune_texture_parameter_overrides = lambda *args, **kwargs: False
        self.module._call_set_material_instance_background_layer = (
            lambda *args: (True, [])
        )

        changed = self.module._assign_material_layer_instance(
            FakeMaterialInstanceConstant("/Game/Material/MI_Test"),
            "Test",
            [],
            {"key": "tree", "master": "/Game/Master"},
            {},
        )

        self.assertTrue(changed)
        self.assertEqual(self.runtime.mark_add_calls, [layer_path])


class TestSkeletalMaterialSectionRemap(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.runtime.unreal_module.SkeletalMesh = FakeSkeletalMesh
        self.runtime.unreal_module.SkeletalMaterial = FakeSkeletalMaterial
        self.calls = []

        class Helper:
            def __init__(inner_self, calls):
                inner_self.calls = calls

            def remap_skeletal_mesh_material_sections(
                inner_self, mesh, old_indices, new_indices, apply_fix
            ):
                inner_self.calls.append(
                    {
                        "slot_count": len(mesh.materials),
                        "old": list(old_indices),
                        "new": list(new_indices),
                        "apply": apply_fix,
                    }
                )
                return True, json.dumps({"changed": True}), []

        self.runtime.unreal_module.CodexMaterialToolsLibrary = Helper(self.calls)
        self.module = _load_module(self.runtime)

    def test_compaction_remaps_sections_and_persists_imported_slot_names(self):
        mesh = FakeSkeletalMesh(
            [FakeSkeletalMaterial(f"Legacy_{index}") for index in range(4)]
        )
        bark_a = FakeMaterialInstanceConstant("/Game/MI/MI_BarkA")
        bark_b = FakeMaterialInstanceConstant("/Game/MI/MI_BarkB")

        changed = self.module._normalize_skeletal_material_slots(
            mesh,
            {
                2: ("M_BarkA", bark_a),
                3: ("M_BarkB", bark_b),
            },
        )

        self.assertTrue(changed)
        self.assertEqual(len(mesh.materials), 2)
        self.assertEqual(self.calls[0]["slot_count"], 2)
        self.assertEqual(self.calls[0]["old"], [2, 3])
        self.assertEqual(self.calls[0]["new"], [0, 1])
        self.assertEqual(
            [
                entry.get_editor_property("imported_material_slot_name")
                for entry in mesh.materials
            ],
            ["M_BarkA", "M_BarkB"],
        )


class TestGeneratedSkeletonDependencySave(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.module = _load_module(self.runtime)
        self.saved = []

        class Helper:
            def __init__(inner_self, saved):
                inner_self.saved = saved

            def save_asset_package_without_thumbnail(inner_self, asset):
                inner_self.saved.append(asset.get_path_name())
                return True

        self.helper = Helper(self.saved)

    @staticmethod
    def mesh_with_skeleton(skeleton_path):
        class Skeleton:
            def get_path_name(self):
                return skeleton_path

        class Mesh:
            def get_editor_property(self, name):
                if name != "skeleton":
                    raise KeyError(name)
                return Skeleton()

        return Mesh()

    def test_saves_missing_default_generated_skeleton(self):
        mesh_path = "/Game/Test/SK_Branch"
        mesh = self.mesh_with_skeleton(
            "/Game/Test/SK_Branch_Skeleton.SK_Branch_Skeleton"
        )
        self.module._project_asset_package_file_exists = lambda _path: False

        changed = self.module._save_generated_skeleton_dependency(
            mesh, mesh_path, self.helper
        )

        self.assertTrue(changed)
        self.assertEqual(
            self.saved,
            ["/Game/Test/SK_Branch_Skeleton.SK_Branch_Skeleton"],
        )

    def test_leaves_shared_or_already_saved_skeleton_untouched(self):
        mesh_path = "/Game/Test/SK_Branch"
        shared_mesh = self.mesh_with_skeleton(
            "/Game/Shared/SK_Tree_Skeleton.SK_Tree_Skeleton"
        )
        generated_mesh = self.mesh_with_skeleton(
            "/Game/Test/SK_Branch_Skeleton.SK_Branch_Skeleton"
        )
        self.module._project_asset_package_file_exists = lambda _path: True

        self.assertFalse(
            self.module._save_generated_skeleton_dependency(
                shared_mesh, mesh_path, self.helper
            )
        )
        self.assertFalse(
            self.module._save_generated_skeleton_dependency(
                generated_mesh, mesh_path, self.helper
            )
        )
        self.assertEqual(self.saved, [])

    def test_post_operation_persists_skeleton_then_mesh(self):
        mesh_path = "/Game/Test/SK_Branch"

        class Skeleton:
            def get_path_name(self):
                return "/Game/Test/SK_Branch_Skeleton.SK_Branch_Skeleton"

        mesh = FakeSkeletalMesh([], skeleton=Skeleton())
        mesh.get_path_name = lambda: f"{mesh_path}.SK_Branch"
        self.runtime.unreal_module.SkeletalMesh = FakeSkeletalMesh
        self.runtime.assets[mesh_path] = mesh
        self.runtime.unreal_module.CodexMaterialToolsLibrary = self.helper
        self.module._project_asset_package_file_exists = lambda _path: False

        count = self.module.persist_generated_skeleton_dependencies(
            [mesh_path, mesh_path]
        )

        self.assertEqual(count, 1)
        self.assertEqual(
            self.saved,
            [
                "/Game/Test/SK_Branch_Skeleton.SK_Branch_Skeleton",
                "/Game/Test/SK_Branch.SK_Branch",
            ],
        )


if __name__ == "__main__":
    unittest.main()
