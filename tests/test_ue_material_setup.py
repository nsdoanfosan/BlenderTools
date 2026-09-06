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


class FakeVector4:
    def __init__(self, x=0.0, y=0.0, z=0.0, w=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
        self.w = float(w)


class FakeTexture:
    def __init__(
        self,
        srgb=True,
        compression_settings="TC_DEFAULT",
        max_texture_size=2048,
        virtual_texture_streaming=False,
        do_scale_mips_for_alpha_coverage=False,
        alpha_coverage_thresholds=None,
    ):
        self.properties = {
            "srgb": srgb,
            "compression_settings": compression_settings,
            "max_texture_size": max_texture_size,
            "virtual_texture_streaming": virtual_texture_streaming,
            "do_scale_mips_for_alpha_coverage": (
                do_scale_mips_for_alpha_coverage
            ),
            "alpha_coverage_thresholds": (
                alpha_coverage_thresholds or FakeVector4()
            ),
        }

    def get_editor_property(self, name):
        return self.properties[name]

    def set_editor_property(self, name, value):
        self.properties[name] = value

    def set_virtual_texture_streaming(self, value):
        self.properties["virtual_texture_streaming"] = bool(value)

    def get_class(self):
        return FakeUnrealClass("Texture2D")


class FakeUnrealClass:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name


class FakeParameterInfo:
    def __init__(self, name, association="GLOBAL_PARAMETER", index=-1):
        self.name = name
        self.association = association
        self.index = index

    def get_editor_property(self, name):
        if name == "name":
            return self.name
        if name == "association":
            return self.association
        if name == "index":
            return self.index
        raise KeyError(name)


class FakeTextureParameterValue:
    def __init__(
        self,
        name,
        texture=None,
        association="GLOBAL_PARAMETER",
        index=-1,
    ):
        self.parameter_info = FakeParameterInfo(name, association, index)
        self.texture = texture

    def get_editor_property(self, name):
        if name == "parameter_info":
            return self.parameter_info
        if name == "parameter_value":
            return self.texture
        raise KeyError(name)


class FakeMaterialInstanceConstant:
    def __init__(self, asset_path, parent=None, texture_parameter_values=None):
        self.asset_path = asset_path
        self.parent = parent
        self.texture_parameter_values = list(texture_parameter_values or [])
        self.texture_values_by_name = {}
        self.scalar_values_by_name = {}
        self.vector_values_by_name = {}

    def get_path_name(self):
        return self.asset_path

    def get_editor_property(self, name):
        if name == "parent":
            return self.parent
        if name == "texture_parameter_values":
            return list(self.texture_parameter_values)
        raise KeyError(name)

    def set_editor_property(self, name, value):
        if name == "parent":
            self.parent = value
            return
        if name == "texture_parameter_values":
            self.texture_parameter_values = list(value)
            return
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


class FakeStaticMesh:
    pass


class FakeNaniteSettings:
    def __init__(self):
        self.properties = {
            "enabled": False,
            "shape_preservation": "NONE",
            "voxel_ndf": False,
            "voxel_opacity": False,
        }

    def get_editor_property(self, name):
        return self.properties[name]

    def set_editor_property(self, name, value):
        self.properties[name] = value


class FakeNaniteMesh:
    def __init__(self):
        self.nanite_settings = FakeNaniteSettings()
        self.notified = False

    def get_editor_property(self, name):
        if name == "nanite_settings":
            return self.nanite_settings
        raise KeyError(name)

    def set_editor_property(self, name, value):
        if name == "nanite_settings":
            self.nanite_settings = value
            return
        raise KeyError(name)

    def notify_nanite_settings_changed(self):
        self.notified = True


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

    def get_metadata_tag(self, asset, key):
        return self.runtime.metadata_tags.get((id(asset), key), "")

    def set_metadata_tag(self, asset, key, value):
        self.runtime.metadata_tags[(id(asset), key)] = value
        self.runtime.metadata_set_calls.append((asset, key, value))
        return True


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
        self.metadata_tags = {}
        self.metadata_set_calls = []
        self.source_control_states = {}
        self.fail_import = False
        self.fail_save = False
        self.fail_checkout = False
        self.fail_mark_add = False
        self.fail_texture_parameter_names = set()
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
        self.texture_parameter_sets = []
        self.scalar_parameter_sets = []
        self.vector_parameter_sets = []
        self.material_instance_updates = []
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
        unreal_module.Vector4 = FakeVector4
        unreal_module.EditorAssetLibrary = FakeEditorAssetLibrary(self)
        unreal_module.SourceControl = FakeSourceControl(self)
        unreal_module.AssetImportTask = FakeAssetImportTask
        unreal_module.AssetToolsHelpers = FakeAssetToolsHelpers(self)
        unreal_module.AssetRegistryHelpers = types.SimpleNamespace(
            get_tag_value=lambda asset_data, tag_name: asset_data.get_tag_value(tag_name)
        )
        unreal_module.MaterialInstanceConstant = FakeMaterialInstanceConstant
        unreal_module.Texture2D = FakeTexture
        unreal_module.MaterialInstanceConstantFactoryNew = object
        unreal_module.MaterialEditingLibrary = types.SimpleNamespace(
            set_material_instance_parent=self.set_material_instance_parent,
            get_material_instance_texture_parameter_value=(
                self.get_material_instance_texture_parameter_value
            ),
            set_material_instance_texture_parameter_value=(
                self.set_material_instance_texture_parameter_value
            ),
            set_material_instance_scalar_parameter_value=(
                self.set_material_instance_scalar_parameter_value
            ),
            set_material_instance_vector_parameter_value=(
                self.set_material_instance_vector_parameter_value
            ),
            update_material_instance=self.update_material_instance,
        )
        unreal_module.LinearColor = lambda r, g, b, a: (r, g, b, a)
        unreal_module.MaterialParameterAssociation = types.SimpleNamespace(
            LAYER_PARAMETER="LAYER_PARAMETER"
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

    @staticmethod
    def get_material_instance_texture_parameter_value(asset, name, *args):
        return asset.texture_values_by_name.get(str(name))

    def set_material_instance_texture_parameter_value(
        self,
        asset,
        name,
        texture,
        *args,
    ):
        if str(name) in self.fail_texture_parameter_names:
            raise RuntimeError(f"injected texture setter failure: {name}")
        asset.texture_values_by_name[str(name)] = texture
        self.texture_parameter_sets.append(
            (asset.get_path_name(), str(name), texture)
        )

    def update_material_instance(self, asset):
        self.material_instance_updates.append(asset.get_path_name())

    def set_material_instance_scalar_parameter_value(self, asset, name, value):
        asset.scalar_values_by_name[str(name)] = float(value)
        self.scalar_parameter_sets.append((asset.get_path_name(), str(name), float(value)))

    def set_material_instance_vector_parameter_value(self, asset, name, value):
        asset.vector_values_by_name[str(name)] = value
        self.vector_parameter_sets.append((asset.get_path_name(), str(name), value))


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

    def test_asset_class_name_reads_top_level_asset_path_struct(self):
        asset_data = types.SimpleNamespace(
            asset_class_path=types.SimpleNamespace(asset_name="Texture2D"),
            asset_class="None",
        )

        self.assertEqual(
            self.module._asset_data_class_name(asset_data),
            "Texture2D",
        )

    def test_layered_surface_presets_route_textures_through_myi(self):
        expected = {
            "prop": (
                "/Game/Material/AssetSurface/Master/MaterialLayer/MY_Mesh_UV0",
                "/Game/Material/AssetSurface/MYI/Surface",
            ),
            "asset_surface": (
                "/Game/Material/AssetSurface/Master/MaterialLayer/MY_Mesh_UV0",
                "/Game/Material/AssetSurface/MYI/Surface",
            ),
            "cloth": (
                "/Game/Material/AssetSurface/Master/MaterialLayer/MY_Cloth",
                "/Game/Material/AssetSurface/MYI/Cloth",
            ),
        }

        for key, (layer_parent, layer_folder) in expected.items():
            with self.subTest(preset=key):
                preset = self.module.MASTER_PRESETS[key]
                self.assertEqual(preset["assignment"], "material_layer_instance")
                self.assertEqual(preset["layer_parent"], layer_parent)
                self.assertEqual(preset["layer_instance_folder"], layer_folder)

    def test_matching_md5_and_settings_skip_mutation_even_when_forced(self):
        self.add_existing_texture()
        untouched_path = "/Game/texture/T_Untouched"
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
        other_path = "/Game/texture/T_Other"
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
        self.assertTrue(
            texture.properties["do_scale_mips_for_alpha_coverage"]
        )
        self.assertAlmostEqual(
            texture.properties["alpha_coverage_thresholds"].x,
            0.3333,
        )

    def test_only_opacity_roles_receive_alpha_coverage_settings(self):
        for role in ("Opacity", "Opacity Map", "Alpha"):
            with self.subTest(role=role):
                settings = self.module._desired_texture_settings(role)
                self.assertTrue(settings["do_scale_mips_for_alpha_coverage"])
                self.assertEqual(
                    self.module._vector4_components(
                        settings["alpha_coverage_thresholds"]
                    ),
                    (0.3333, 0.0, 0.0, 0.0),
                )
        self.assertNotIn(
            "do_scale_mips_for_alpha_coverage",
            self.module._desired_texture_settings("Albedo"),
        )


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

    def test_auto_detected_normal_compression_is_corrected_before_albedo_srgb(self):
        class EngineNormalTexture(FakeTexture):
            def set_editor_property(self, name, value):
                if name == "srgb" and self.properties['compression_settings'] == "TC_NORMALMAP":
                    value = False
                super().set_editor_property(name, value)

        texture = EngineNormalTexture(srgb=False, compression_settings='TC_NORMALMAP')
        self.module._configure_imported_texture(texture, 'Albedo')
        expected = self.module._desired_texture_settings('Albedo')
        self.assertTrue(self.module._texture_settings_match(texture, expected))
        self.assertTrue(texture.properties['srgb'])

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

    def test_declared_disk_source_import_precedes_registry_fallback(self):
        self.module._texture_asset_paths_named = (
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("registry fallback must not run for an available source")
            )
        )

        self.assertEqual(self.import_texture({}), self.asset_path)
        self.assertEqual(len(self.runtime.import_tasks), 1)
        self.assertEqual(
            self.runtime.import_tasks[0]["filename"],
            str(self.source_path),
        )

    def test_local_source_reuses_unique_verified_exact_name_texture(self):
        existing_path = "/Game/Textures/T_Surface_extra"
        self.runtime.assets[existing_path] = FakeTexture(
            srgb=False,
            compression_settings="TC_MASKS",
            max_texture_size=0,
            virtual_texture_streaming=True,
        )
        self.runtime.asset_md5[existing_path] = self.source_md5
        self.module._texture_asset_paths_named = lambda _name: [existing_path]
        cache = {}

        self.assertEqual(self.import_texture(cache), existing_path)

        self.assertEqual(self.runtime.import_tasks, [])
        self.assertIn(existing_path, cache)
        self.assertNotIn(self.asset_path, cache)
        self.assertTrue(
            any(
                "reused before import" in message
                for message in self.runtime.logs
            )
        )

    def test_local_source_does_not_reuse_same_name_with_wrong_md5(self):
        existing_path = "/Game/Textures/T_Surface_extra"
        self.runtime.assets[existing_path] = FakeTexture(
            srgb=False,
            compression_settings="TC_MASKS",
            max_texture_size=0,
            virtual_texture_streaming=True,
        )
        self.runtime.asset_md5[existing_path] = "0" * 32
        self.module._texture_asset_paths_named = lambda _name: [existing_path]

        self.assertEqual(self.import_texture({}), self.asset_path)

        self.assertEqual(len(self.runtime.import_tasks), 1)
        self.assertFalse(self.runtime.import_tasks[0]["replace_existing"])

    def test_local_source_updates_role_settings_on_source_matching_texture(self):
        existing_path = "/Game/Textures/T_Surface_extra"
        existing_texture = FakeTexture(
            srgb=False,
            compression_settings="TC_MASKS",
            max_texture_size=0,
            virtual_texture_streaming=False,
        )
        self.runtime.assets[existing_path] = existing_texture
        self.runtime.asset_md5[existing_path] = self.source_md5
        self.module._texture_asset_paths_named = lambda _name: [existing_path]

        self.assertEqual(self.import_texture({}), existing_path)

        self.assertEqual(self.runtime.import_tasks, [])
        self.assertEqual(self.runtime.checkout_calls, [existing_path])
        self.assertEqual(self.runtime.save_calls, [existing_path])
        self.assertEqual(self.runtime.revert_unchanged_calls, [existing_path])
        self.assertTrue(
            existing_texture.properties["virtual_texture_streaming"]
        )

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

    def test_other_user_checkout_leaves_texture_unassigned_without_mutation(self):
        self.add_existing_texture(file_md5="1" * 32)
        cache = {}
        self.runtime.source_control_states[self.asset_path] = FakeSourceControlState(
            is_checked_out_other=True
        )

        self.assertIsNone(self.import_texture(cache))

        self.assertEqual(self.runtime.checkout_calls, [])
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertEqual(self.runtime.save_calls, [])
        self.assertNotIn(self.asset_path, cache)
        self.assertTrue(self.runtime.warnings)

    def test_checkout_failure_leaves_texture_unassigned_without_mutation(self):
        self.add_existing_texture(file_md5="1" * 32)
        cache = {}
        self.runtime.fail_checkout = True

        self.assertIsNone(self.import_texture(cache))

        self.assertEqual(self.runtime.checkout_calls, [self.asset_path])
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertEqual(self.runtime.save_calls, [])
        self.assertNotIn(self.asset_path, cache)
        self.assertTrue(self.runtime.warnings)

    def test_mark_add_failure_does_not_cache_success(self):
        cache = {}
        self.runtime.fail_mark_add = True

        self.assertIsNone(self.import_texture(cache))

        self.assertEqual(self.runtime.save_calls, [self.asset_path])
        self.assertEqual(self.runtime.mark_add_calls, [self.asset_path])
        self.assertNotIn(self.asset_path, cache)
        self.assertTrue(self.runtime.warnings)

    def test_missing_source_reuses_exact_existing_texture_without_cache_mutation(self):
        self.add_existing_texture()
        cache = {self.asset_path: 42.0}
        self.source_path.unlink()

        self.assertEqual(self.import_texture(cache), self.asset_path)
        self.assertEqual(cache[self.asset_path], 42.0)
        self.assertEqual(self.runtime.checkout_calls, [])
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertEqual(self.runtime.warnings, [])

    def test_missing_source_uses_unique_exact_name_texture_registry_fallback(self):
        self.source_path.unlink()
        fallback_path = "/Game/Shared/Textures/T_Surface_extra"
        self.runtime.assets[fallback_path] = FakeTexture()

        class RegistryAssetData:
            asset_name = self.asset_name
            package_name = fallback_path
            asset_class = "Texture2D"

        registry = types.SimpleNamespace(
            get_assets_by_path=lambda *args, **kwargs: [RegistryAssetData()]
        )
        self.runtime.unreal_module.AssetRegistryHelpers = types.SimpleNamespace(
            get_asset_registry=lambda: registry,
            get_tag_value=lambda asset_data, tag_name: asset_data.get_tag_value(
                tag_name
            ),
        )

        self.assertEqual(self.import_texture({}), fallback_path)
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertEqual(self.runtime.warnings, [])

    def test_local_import_reuses_only_verified_exact_registry_texture(self):
        fallback_path = "/Game/Shared/Textures/T_Surface_extra"
        self.runtime.assets[fallback_path] = FakeTexture(
            srgb=False,
            compression_settings="TC_MASKS",
            max_texture_size=0,
            virtual_texture_streaming=True,
        )
        self.runtime.asset_md5[fallback_path] = self.source_md5
        self.runtime.fail_import = True

        class RegistryAssetData:
            asset_name = self.asset_name
            package_name = fallback_path
            asset_class = "Texture2D"

        registry = types.SimpleNamespace(
            get_assets_by_path=lambda *args, **kwargs: [RegistryAssetData()]
        )
        self.runtime.unreal_module.AssetRegistryHelpers = types.SimpleNamespace(
            get_asset_registry=lambda: registry,
            get_tag_value=lambda asset_data, tag_name: asset_data.get_tag_value(
                tag_name
            ),
        )

        self.assertEqual(self.import_texture({}), fallback_path)
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertTrue(
            any("verified existing texture reused" in message for message in self.runtime.logs)
        )

    def test_ambiguous_exact_name_texture_registry_fallback_is_unresolved(self):
        self.source_path.unlink()
        fallback_paths = [
            "/Game/SharedA/T_Surface_extra",
            "/Game/SharedB/T_Surface_extra",
        ]
        for path in fallback_paths:
            self.runtime.assets[path] = FakeTexture()

        class RegistryAssetData:
            def __init__(self, package_name):
                self.asset_name = self_outer.asset_name
                self.package_name = package_name
                self.asset_class = "Texture2D"

        self_outer = self
        registry = types.SimpleNamespace(
            get_assets_by_path=lambda *args, **kwargs: [
                RegistryAssetData(path) for path in fallback_paths
            ]
        )
        self.runtime.unreal_module.AssetRegistryHelpers = types.SimpleNamespace(
            get_asset_registry=lambda: registry,
            get_tag_value=lambda asset_data, tag_name: asset_data.get_tag_value(
                tag_name
            ),
        )

        self.assertIsNone(self.import_texture({}))
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertEqual(self.runtime.warnings, [])
        self.assertTrue(
            any("ambiguous" in message for message in self.runtime.logs)
        )

    def test_wrong_class_preferred_asset_is_omitted_before_registry_fallback(self):
        self.source_path.unlink()
        self.runtime.assets[self.asset_path] = FakeMaterialInstanceConstant(
            self.asset_path
        )
        fallback_path = "/Game/Shared/Textures/T_Surface_extra"
        self.runtime.assets[fallback_path] = FakeTexture()

        class RegistryAssetData:
            asset_name = self.asset_name
            package_name = fallback_path
            asset_class = "Texture2D"

        registry = types.SimpleNamespace(
            get_assets_by_path=lambda *args, **kwargs: [RegistryAssetData()]
        )
        self.runtime.unreal_module.AssetRegistryHelpers = types.SimpleNamespace(
            get_asset_registry=lambda: registry,
            get_tag_value=lambda asset_data, tag_name: asset_data.get_tag_value(
                tag_name
            ),
        )

        self.assertEqual(self.import_texture({}), fallback_path)
        self.assertTrue(
            any("non-Texture2D" in message for message in self.runtime.logs)
        )

    def test_pipeline_owned_missing_role_clears_only_managed_flat_override(self):
        material = FakeMaterialInstanceConstant(
            "/Game/Material/MI_Test",
            texture_parameter_values=[
                FakeTextureParameterValue("BaseColor"),
                FakeTextureParameterValue("ArtistDetailMask"),
            ],
        )

        changed = self.module._assign_flat_textures(
            material,
            [],
            self.module.FLAT_PARAM_BY_LAYER_PARAM,
            "prop",
            clear_missing_managed=True,
        )

        self.assertTrue(changed)
        self.assertEqual(
            [
                self.module._texture_parameter_name(value)
                for value in material.texture_parameter_values
            ],
            ["ArtistDetailMask"],
        )

    def test_prune_uses_parameter_association_and_layer_index(self):
        layer_zero = FakeTextureParameterValue(
            "BaseColor", association="LAYER_PARAMETER", index=0
        )
        layer_one = FakeTextureParameterValue(
            "BaseColor", association="LAYER_PARAMETER", index=1
        )
        global_value = FakeTextureParameterValue(
            "BaseColor", association="GLOBAL_PARAMETER", index=-1
        )
        material = FakeMaterialInstanceConstant(
            "/Game/Material/MI_Test",
            texture_parameter_values=[layer_zero, layer_one, global_value],
        )

        changed = self.module._prune_managed_texture_parameter_overrides(
            material,
            {"BaseColor"},
            set(),
            managed_bindings={("BaseColor", "LAYER_PARAMETER", 0)},
            keep_bindings=set(),
        )

        self.assertTrue(changed)
        self.assertEqual(
            material.texture_parameter_values,
            [layer_one, global_value],
        )

    def test_live_unreal_enum_repr_prunes_stale_global_role_only(self):
        class LiveAssociation:
            def __str__(self):
                return "<MaterialParameterAssociation.GLOBAL_PARAMETER: 2>"

        stale = FakeTextureParameterValue("Normal", association=LiveAssociation())
        kept = FakeTextureParameterValue("Albedo", association=LiveAssociation())
        artist_layer = FakeTextureParameterValue(
            "Normal", association="<MaterialParameterAssociation.LAYER_PARAMETER: 0>", index=1
        )
        material = FakeMaterialInstanceConstant(
            "/Game/Material/MYI_Test",
            texture_parameter_values=[stale, kept, artist_layer],
        )
        changed = self.module._prune_managed_texture_parameter_overrides(
            material, {"Normal", "Albedo"}, {"Albedo"},
            managed_bindings={("Normal", "GLOBAL_PARAMETER", -1), ("Albedo", "GLOBAL_PARAMETER", -1)},
            keep_bindings={("Albedo", "GLOBAL_PARAMETER", -1)},
        )
        self.assertTrue(changed)
        self.assertEqual(material.texture_parameter_values, [kept, artist_layer])

    def test_flat_texture_setter_failure_omits_only_failed_role(self):
        albedo_path = "/Game/texture/T_Albedo"
        normal_path = "/Game/texture/T_Normal"
        self.runtime.assets[albedo_path] = FakeTexture()
        self.runtime.assets[normal_path] = FakeTexture()
        self.runtime.fail_texture_parameter_names.add("BaseColor")
        material = FakeMaterialInstanceConstant(
            "/Game/Material/MI_Test",
            texture_parameter_values=[
                FakeTextureParameterValue("BaseColor"),
                FakeTextureParameterValue("Normal"),
            ],
        )

        changed = self.module._assign_flat_textures(
            material,
            [
                {
                    "index": 0,
                    "textures": {
                        "Albedo": albedo_path,
                        "Normal": normal_path,
                    },
                }
            ],
            {"Albedo": "BaseColor", "Normal": "Normal"},
            "test",
            clear_missing_managed=True,
        )

        self.assertTrue(changed)
        self.assertEqual(
            [
                self.module._texture_parameter_name(value)
                for value in material.texture_parameter_values
            ],
            ["Normal"],
        )
        self.assertIn("Normal", material.texture_values_by_name)
        self.assertTrue(self.runtime.warnings)

    def test_layer_zero_failure_does_not_keep_stale_failed_override(self):
        albedo_path = "/Game/texture/T_Albedo"
        normal_path = "/Game/texture/T_Normal"
        self.runtime.assets[albedo_path] = FakeTexture()
        self.runtime.assets[normal_path] = FakeTexture()
        self.runtime.fail_texture_parameter_names.add("Albedo")
        stale_albedo = FakeTextureParameterValue(
            "Albedo", association="LAYER_PARAMETER", index=0
        )
        kept_normal = FakeTextureParameterValue(
            "Normal", association="LAYER_PARAMETER", index=0
        )
        material = FakeMaterialInstanceConstant(
            "/Game/Material/MI_Test",
            texture_parameter_values=[stale_albedo, kept_normal],
        )

        changed = self.module._assign_layer_zero_textures(
            material,
            {"Albedo": albedo_path, "Normal": normal_path},
            clear_missing_managed=True,
        )

        self.assertTrue(changed)
        self.assertEqual(material.texture_parameter_values, [kept_normal])
        self.assertIn("Normal", material.texture_values_by_name)
        self.assertTrue(self.runtime.warnings)

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
        self.assertNotIn("/Game/texture/T_Direct", paths)
        self.assertNotIn("/Game/texture/T_Layer", paths)

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

    def test_material_layer_normalization_revalidates_same_asset_live(self):
        calls = []

        class Helper:
            def normalize_material_layer_placeholders(inner_self, asset_path):
                calls.append(asset_path)
                return True

        asset_path = "/Game/Codex/Tests/Elm/_MaterialPipeline/MYI/MYI_Bark"
        for _ in range(2):
            self.module._normalize_material_layer_asset(
                Helper(),
                "normalize_material_layer_placeholders",
                asset_path,
                "material layer instance",
                mutation_scope_path="/Game/Codex/Tests/Elm/SK_Tree",
            )

        self.assertEqual(calls, [asset_path, asset_path])

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

    def test_existing_profile_target_does_not_require_base_or_master(self):
        entry = {
            "name": "M_stem_common_01",
            "master_preset": "tree",
            "tree_shading": "stem",
            "instance_profile": "dead",
            "material_instance_mode": "create_or_reuse",
        }
        preset = self.module._master_preset({}, entry)
        paths = self.module._instance_profile_material_paths(entry, preset)
        user_parent = FakeMaterialInstanceConstant("/Game/User/MI_CustomParent")
        target = FakeMaterialInstanceConstant(
            paths["target_path"],
            parent=user_parent,
        )
        self.runtime.assets[paths["target_path"]] = target

        targets = self.module._validate_instance_profile_targets(
            {"materials": [entry]},
            "/Game/Meshes/SK_CommonGrass",
        )
        self.module._ensure_instance_profile_targets(
            self.runtime.unreal_module.AssetToolsHelpers.get_asset_tools(),
            targets,
        )

        self.assertIs(targets[0]["asset"], target)
        self.assertFalse(targets[0]["create_base"])
        self.assertIs(target.parent, user_parent)
        self.assertEqual(self.runtime.created_assets, [])
        self.assertEqual(self.runtime.parent_changes, [])
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
            wood_preset = dict(preset, tree_shading="wood")
            self.assertFalse(
                self.module._tree_texture_param_allowed("Opacity Map", wood_preset)
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

    def _contract_export_path(self, mesh_name="SK_CommonGrass"):
        path = Path(self.temp_dir.name) / f"{mesh_name}.fbx"
        if not path.exists():
            path.write_bytes(f"fbx-payload:{mesh_name}".encode("ascii"))
        return path

    def _contract_sidecar(self, mesh_name="SK_CommonGrass"):
        contract_api = self.module._speedtree_handoff_api()
        export_path = self._contract_export_path(mesh_name)
        identity_fixture = json.loads(
            (
                Path(__file__).parent
                / "fixtures"
                / "prototype_identity_v1.json"
            ).read_text(encoding="utf-8")
        )
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
            "speedtree_prototype_handoff": {
                "schema_version": 2,
                "prototype_identity": identity_fixture[
                    "single_member_lineage"
                ],
                "prototype_identity_members": [
                    identity_fixture["identity"]
                ],
                "blender_geometry_content": {
                    "kind": (
                        "speedtree_blender_export_geometry_content"
                    ),
                    "schema_version": 1,
                    "algorithm": "sha256",
                    "digest": "0" * 64,
                },
                "output_content": {
                    "kind": "speedtree_blender_fbx_payload_content",
                    "schema_version": 1,
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(
                        export_path.read_bytes()
                    ).hexdigest(),
                },
            },
            "materials": [entry],
        }

    def test_new_sidecar_descriptor_and_intent_validate_before_mutation(self):
        data = self._contract_sidecar()
        descriptor = self.module._validate_speedtree_handoff_contract(
            data,
            "SK_CommonGrass",
            export_file_path=str(self._contract_export_path()),
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

    def test_new_sidecar_mismatch_and_descriptor_free_tree_are_blocked(self):
        bad_descriptor = self._contract_sidecar()
        bad_descriptor["speedtree_handoff_contract"]["fingerprint"] = "stale"
        with self.assertRaisesRegex(RuntimeError, "fingerprint mismatch"):
            self.module._validate_speedtree_handoff_contract(
                bad_descriptor,
                "SK_CommonGrass",
                export_file_path=str(self._contract_export_path()),
            )

        bad_intent = self._contract_sidecar()
        bad_intent["materials"][0]["speedtree_intent"][
            "material_instance_base"
        ] = "wrong"
        with self.assertRaisesRegex(RuntimeError, "material_instance_base mismatch"):
            self.module._validate_speedtree_handoff_contract(
                bad_intent,
                "SK_CommonGrass",
                export_file_path=str(self._contract_export_path()),
            )

        wrong_mesh = self._contract_sidecar("SK_Other")
        with self.assertRaisesRegex(RuntimeError, "mesh mismatch|mesh_name mismatch"):
            self.module._validate_speedtree_handoff_contract(
                wrong_mesh,
                "SK_CommonGrass",
                export_file_path=str(
                    self._contract_export_path("SK_Other")
                ),
            )

        legacy = {
            "mesh_name": "SK_Other",
            "materials": [
                {"name": "M_stem_common_01", "master_preset": "tree"}
            ],
        }
        with self.assertRaisesRegex(
            RuntimeError,
            "tree sidecar has no speedtree_handoff_contract",
        ):
            self.module._validate_speedtree_handoff_contract(
                legacy,
                "SK_CommonGrass",
            )

        legacy_prop = {
            "mesh_name": "SM_Prop",
            "materials": [
                {"name": "M_Prop", "master_preset": "prop"}
            ],
        }
        self.assertIsNone(
            self.module._validate_speedtree_handoff_contract(
                legacy_prop,
                "SM_Prop",
                "/Game/Meshes/Props/SM_Prop",
            )
        )

    def test_existing_speedtree_contract_without_prototype_keeps_legacy_path(self):
        data = self._contract_sidecar()
        data.pop("speedtree_prototype_handoff")
        descriptor = self.module._validate_speedtree_handoff_contract(
            data, "SK_CommonGrass")
        self.assertEqual(descriptor["asset_kind"], "speedtree")
        self.assertFalse(self.module._persist_prototype_metadata(
            FakeSkeletalMesh([]), data, ""))
        self.assertEqual(self.runtime.checkout_calls, [])
        self.assertEqual(self.runtime.created_assets, [])
        self.assertEqual(self.runtime.save_calls, [])

    def test_prototype_prop_sidecar_validates_before_mutation(self):
        handoff = self._contract_sidecar()["speedtree_prototype_handoff"]
        prop = {
            "mesh_name": "SM_Prop",
            "materials": [{"name": "M_Prop", "master_preset": "prop"}],
            "speedtree_prototype_handoff": handoff,
        }
        export_path = self._contract_export_path()
        self.assertIsNone(self.module._validate_speedtree_handoff_contract(
            prop, "SM_Prop", "/Game/Meshes/Props/SM_Prop", str(export_path)))
        prop["speedtree_prototype_handoff"] = {"schema_version": -1}
        with self.assertRaisesRegex(RuntimeError, "before mutation"):
            self.module._validate_speedtree_handoff_contract(
                prop, "SM_Prop", "/Game/Meshes/Props/SM_Prop", str(export_path))
        prop["speedtree_prototype_handoff"] = handoff
        export_path.write_bytes(b"unrelated replacement FBX bytes")
        with self.assertRaisesRegex(RuntimeError, "before mutation"):
            self.module._validate_speedtree_handoff_contract(
                prop, "SM_Prop", "/Game/Meshes/Props/SM_Prop", str(export_path))
        self.assertEqual(self.runtime.checkout_calls, [])
        self.assertEqual(self.runtime.created_assets, [])
        self.assertEqual(self.runtime.save_calls, [])

    def test_prototype_metadata_persists_exact_handoff_and_sidecar_hash(self):
        mesh = FakeSkeletalMesh([])
        data = self._contract_sidecar()
        sidecar_sha256 = "a" * 64

        self.assertTrue(
            self.module._persist_prototype_metadata(
                mesh,
                data,
                sidecar_sha256,
                export_file_path=str(self._contract_export_path()),
            )
        )
        expected = {
            self.module.PROTOTYPE_METADATA_IDENTITY: json.dumps(
                data["speedtree_prototype_handoff"][
                    "prototype_identity"
                ],
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
            self.module.PROTOTYPE_METADATA_MEMBERS: json.dumps(
                data["speedtree_prototype_handoff"][
                    "prototype_identity_members"
                ],
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
            self.module.PROTOTYPE_METADATA_OUTPUT: json.dumps(
                data["speedtree_prototype_handoff"]["output_content"],
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
            self.module.PROTOTYPE_METADATA_SIDECAR: sidecar_sha256,
        }
        self.assertEqual(
            {
                key: self.runtime.metadata_tags[(id(mesh), key)]
                for key in expected
            },
            expected,
        )
        call_count = len(self.runtime.metadata_set_calls)
        self.assertFalse(
            self.module._persist_prototype_metadata(
                mesh,
                data,
                sidecar_sha256,
                export_file_path=str(self._contract_export_path()),
            )
        )
        self.assertEqual(
            len(self.runtime.metadata_set_calls),
            call_count,
        )

    def test_prototype_metadata_rejects_malformed_sidecar_before_mutation(self):
        mesh = FakeSkeletalMesh([])
        data = self._contract_sidecar()
        data["speedtree_prototype_handoff"]["output_content"][
            "digest"
        ] = "stale"
        with self.assertRaisesRegex(ValueError, "output content"):
            self.module._persist_prototype_metadata(
                mesh,
                data,
                "b" * 64,
                export_file_path=str(self._contract_export_path()),
            )
        self.assertEqual(self.runtime.metadata_set_calls, [])

    def test_prototype_metadata_rehashes_current_fbx_before_mutation(self):
        mesh = FakeSkeletalMesh([])
        data = self._contract_sidecar()
        export_path = self._contract_export_path()
        original_stat = export_path.stat()
        changed = bytearray(export_path.read_bytes())
        changed[0] ^= 1
        export_path.write_bytes(changed)
        os.utime(
            export_path,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )

        with self.assertRaisesRegex(
            ValueError,
            "current FBX export payload does not match",
        ):
            self.module._persist_prototype_metadata(
                mesh,
                data,
                "c" * 64,
                export_file_path=str(export_path),
            )
        self.assertEqual(self.runtime.metadata_set_calls, [])
        data = self._contract_sidecar()
        with self.assertRaisesRegex(RuntimeError, "sidecar sha256"):
            self.module._persist_prototype_metadata(
                mesh,
                data,
                "not-a-sha",
                export_file_path=str(self._contract_export_path()),
            )
        self.assertEqual(self.runtime.metadata_set_calls, [])

    def test_json_fallback_rejects_ambiguous_candidates(self):
        first = Path(self.temp_dir.name) / "first" / "SK_CommonGrass.json"
        second = Path(self.temp_dir.name) / "second" / "SK_CommonGrass.json"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_text("{}", encoding="utf-8")
        second.write_text("{}", encoding="utf-8")
        self.module._mesh_path_to_disk_folder = lambda mesh_path: None
        self.module.JSON_SEARCH_ROOTS = [self.temp_dir.name]
        self.module.EXPORT_DIR = str(Path(self.temp_dir.name) / "missing")
        self.module._walk_for_json = lambda roots, filename: [
            str(first),
            str(second),
        ]

        with self.assertRaisesRegex(RuntimeError, "ambiguous JSON sidecar fallback"):
            self.module._find_json_path(
                "SK_CommonGrass",
                "/Game/Meshes/Tree/SK_CommonGrass",
            )

    def test_explicit_json_sidecar_sha_detects_changed_bytes(self):
        sidecar = Path(self.temp_dir.name) / "SK_CommonGrass.json"
        payload = json.dumps({"mesh_name": "SK_CommonGrass"}).encode("utf-8")
        sidecar.write_bytes(payload)
        expected_sha256 = hashlib.sha256(payload).hexdigest()

        data = self.module._load_json(
            "SK_CommonGrass",
            explicit_path=str(sidecar),
            mesh_path="/Game/Meshes/Tree/SK_CommonGrass",
            expected_sha256=expected_sha256,
        )
        self.assertEqual(data["mesh_name"], "SK_CommonGrass")

        sidecar.write_text('{"mesh_name": "SK_Changed"}', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "content changed"):
            self.module._load_json(
                "SK_CommonGrass",
                explicit_path=str(sidecar),
                mesh_path="/Game/Meshes/Tree/SK_CommonGrass",
                expected_sha256=expected_sha256,
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

    def test_existing_myi_rebuilds_owner_render_state_and_saves_without_thumbnail(self):
        layer_path = '/Game/Material/MYI_Test'
        material = FakeMaterialInstanceConstant('/Game/Material/MI_Test')
        self.runtime.assets[layer_path] = FakeMaterialInstanceConstant(layer_path)
        events = []
        class Helper:
            def create_or_update_material_layer_instance(inner_self, *args):
                return True, json.dumps({'created': False, 'changed': True}), []
            def set_material_instance_background_layer(inner_self, *args):
                return False
            def save_asset_package_without_thumbnail(inner_self, asset):
                events.append(('save', asset))
                return True
        self.runtime.unreal_module.CodexMaterialToolsLibrary = Helper()
        self.runtime.unreal_module.MaterialEditingLibrary.update_material_instance = lambda asset: events.append(('refresh', asset))
        self.runtime.unreal_module.MaterialEditingLibrary.update_material_function = lambda asset: events.append(('refresh_layer', asset))
        self.module._normalize_material_layer_asset = lambda *args, **kwargs: None
        self.module._layer_parent_path = lambda *args: '/Game/Layer/Parent'
        self.module._layer_instance_path = lambda *args: layer_path
        self.module._call_set_material_instance_background_layer = lambda *args: (True, [])
        self.assertTrue(self.module._assign_material_layer_instance(material, 'Test', [], {'key':'prop','master':'/Game/Master'}, {}))
        layer = self.runtime.assets[layer_path]
        self.assertEqual(events, [('refresh_layer', layer), ('save', layer), ('refresh', material), ('save', material)])

    def test_pipeline_owned_myi_clears_missing_managed_role_only(self):
        layer_path = "/Game/Material/Tree/MYI/MYI_Test"
        layer_asset = FakeMaterialInstanceConstant(
            layer_path,
            texture_parameter_values=[
                FakeTextureParameterValue("Albedo"),
                FakeTextureParameterValue("ArtistLayerMask"),
            ],
        )
        material = FakeMaterialInstanceConstant(
            "/Game/Material/Tree/MI/MI_Test",
            texture_parameter_values=[
                FakeTextureParameterValue("Albedo"),
                FakeTextureParameterValue("ArtistTopMask"),
            ],
        )
        self.runtime.assets[layer_path] = layer_asset

        class Helper:
            @staticmethod
            def create_or_update_material_layer_instance(*args):
                return True, json.dumps({"created": False}), []

            @staticmethod
            def set_material_instance_background_layer(*args):
                return True

        self.runtime.unreal_module.CodexMaterialToolsLibrary = Helper()
        self.module._normalize_material_layer_asset = lambda *args, **kwargs: None
        self.module._layer_parent_path = lambda preset, entry: "/Game/Layer/Parent"
        self.module._layer_instance_path = lambda *args: layer_path

        changed = self.module._assign_material_layer_instance(
            material,
            "Test",
            [],
            {
                "key": "tree",
                "master": "/Game/Master",
                "layer_texture_remap": {
                    "Albedo": "Albedo",
                    "Normal": "Normal",
                },
            },
            {},
            clear_missing_managed=True,
        )

        self.assertTrue(changed)
        self.assertEqual(
            [
                self.module._texture_parameter_name(value)
                for value in layer_asset.texture_parameter_values
            ],
            ["ArtistLayerMask"],
        )
        self.assertEqual(
            [
                self.module._texture_parameter_name(value)
                for value in material.texture_parameter_values
            ],
            ["ArtistTopMask"],
        )
        self.assertIn(layer_path, self.runtime.save_calls)


class TestVerifiedHairNaniteSettings(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.module = _load_module(self.runtime)

    @staticmethod
    def tagged_hair_data(version=3, encoding="HTUE_RGB_TAGGED_UV"):
        return {
            "materials": [
                {
                    "name": "M_HT_Default_Material_01",
                    "master_preset": "hair",
                    "hair_tool": {
                        "vertex_uv_payload": {
                            "version": version,
                            "encoding": encoding,
                        }
                    },
                }
            ]
        }

    def test_tagged_v3_hair_is_the_only_voxel_opacity_candidate(self):
        data = self.tagged_hair_data()
        self.assertTrue(
            self.module._uses_verified_hair_uv_payload(
                data,
                "/Game/Meshes/Hair_Back_Sibuki_02",
            )
        )
        self.assertFalse(
            self.module._uses_verified_hair_uv_payload(
                data,
                "/Game/Meshes/hair_eyelash_02",
            )
        )
        self.assertFalse(
            self.module._uses_verified_hair_uv_payload(
                self.tagged_hair_data(version=2),
                "/Game/Meshes/Hair_Back_Sibuki_02",
            )
        )
        self.assertFalse(
            self.module._uses_verified_hair_uv_payload(
                self.tagged_hair_data(encoding="LEGACY"),
                "/Game/Meshes/Hair_Back_Sibuki_02",
            )
        )

    def test_set_nanite_enables_voxel_ndf_and_voxel_opacity(self):
        mesh = FakeNaniteMesh()

        changed = self.module._set_nanite(
            mesh,
            True,
            "VOXELIZE",
            voxel_ndf=True,
            voxel_opacity=True,
        )

        self.assertTrue(changed)
        self.assertTrue(mesh.nanite_settings.properties["enabled"])
        self.assertEqual(
            mesh.nanite_settings.properties["shape_preservation"],
            "VOXELIZE",
        )
        self.assertTrue(mesh.nanite_settings.properties["voxel_ndf"])
        self.assertTrue(mesh.nanite_settings.properties["voxel_opacity"])
        self.assertTrue(mesh.notified)


class TestRuntimeTolerantMaterialProcess(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.runtime.unreal_module.StaticMesh = FakeStaticMesh
        self.module = _load_module(self.runtime)
        self.mesh_path = "/Game/Meshes/SM_Test"
        self.mesh = FakeStaticMesh()
        self.runtime.assets[self.mesh_path] = self.mesh
        self.assignments = []

    def configure_process(self, data, preset):
        self.module.ENABLE_NANITE = False
        self.module._load_json = lambda *args, **kwargs: data
        self.module._validate_speedtree_handoff_contract = (
            lambda *args, **kwargs: None
        )
        self.module._validate_codex_test_material_scope = (
            lambda *args, **kwargs: True
        )
        self.module._validate_instance_profile_targets = (
            lambda *args, **kwargs: {}
        )
        self.module._checkout_material_pipeline_assets = (
            lambda *args, **kwargs: []
        )
        self.module._ensure_instance_profile_targets = (
            lambda *args, **kwargs: {}
        )
        self.module._master_preset = lambda *args, **kwargs: dict(preset)
        self.module._slot_index_for_entry = lambda *args, **kwargs: 0
        self.module._import_dynamic_wind_if_available = (
            lambda *args, **kwargs: False
        )
        self.module._load_texture_cache = lambda: {}
        self.module._save_texture_cache = lambda cache: None
        self.module._normalize_skeletal_material_slots = (
            lambda *args, **kwargs: False
        )
        self.module._cleanup_imported_source_assets = (
            lambda *args, **kwargs: None
        )
        self.module._sync_browser_to_mesh = lambda *args, **kwargs: None

        def assign_slot(mesh, slot_index, material, slot_name=None):
            self.assignments.append((slot_index, material, slot_name))
            return True

        self.module._assign_slot = assign_slot

    def test_non_tree_layer_skips_tree_function_normalization(self):
        calls = []
        self.module._normalize_material_layer_asset = (
            lambda _helper, method, path, label, **kwargs: calls.append(
                (method, path, label)
            )
        )

        self.module._normalize_material_layer_dependencies(
            object(),
            {"key": "layer", "master": "/Game/Material/M_LayerBlend"},
            "/Game/Material/Layer/MY_Mesh_UV0",
            mutation_scope_path="/Game/Material/MYI/MYI_Test",
        )

        self.assertEqual(
            calls,
            [
                (
                    "normalize_material_layer_placeholders",
                    "/Game/Material/M_LayerBlend",
                    "material master",
                )
            ],
        )

    def test_tree_layer_keeps_tree_function_normalization(self):
        calls = []
        self.module._normalize_material_layer_asset = (
            lambda _helper, method, path, label, **kwargs: calls.append(
                (method, path, label)
            )
        )

        self.module._normalize_material_layer_dependencies(
            object(),
            {"key": "tree", "master": "/Game/Material/M_Tree"},
            "/Game/Material/Tree/MY_Tree",
            mutation_scope_path="/Game/Material/Tree/MYI/MYI_Test",
        )

        self.assertEqual(
            [method for method, _path, _label in calls],
            [
                "normalize_material_layer_placeholders",
                "normalize_material_function_attribute_nodes",
            ],
        )

    def test_unverified_material_layer_assignment_blocks_handoff(self):
        self.module._assign_master_textures_impl = (
            lambda *args, **kwargs: False
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "material layer instance handoff was not created or verified",
        ):
            self.module._assign_master_textures(
                FakeMaterialInstanceConstant("/Game/Material/MI_Test"),
                [],
                "material_layer_instance",
            )

    def test_nonstructural_texture_failure_remains_tolerant(self):
        self.module._assign_master_textures_impl = (
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("optional"))
        )

        self.assertTrue(
            self.module._assign_master_textures(
                FakeMaterialInstanceConstant("/Game/Material/MI_Test"),
                [],
                "asset_surface_flat",
            )
        )

    def test_background_report_none_is_verified_from_dump(self):
        mi = FakeMaterialInstanceConstant("/Game/Material/MI_Test")
        layer = FakeMaterialInstanceConstant("/Game/Material/MYI_Test")

        class Helper:
            assigned = False

            @classmethod
            def set_material_instance_background_layer_report(cls, *_args):
                cls.assigned = True
                return None

            @classmethod
            def dump_material_layers(cls, _path):
                layer_path = layer.get_path_name() if cls.assigned else ""
                return json.dumps(
                    {
                        "ok": True,
                        "has_layers": True,
                        "layers": [{"index": 0, "path": layer_path}],
                    }
                )

        verified, errors = self.module._call_set_material_instance_background_layer(
            Helper, mi, layer
        )

        self.assertTrue(verified)
        self.assertEqual(errors, [])

    def test_unreal_helper_result_scans_out_parameters_by_type(self):
        report = json.dumps({"ok": True})

        returned_ok, report_text, errors = self.module._unreal_helper_result_parts(
            (report, ["warning"], True)
        )

        self.assertTrue(returned_ok)
        self.assertEqual(report_text, report)
        self.assertEqual(errors, ["warning"])

    def test_create_layer_accepts_python_out_params_without_return_bool(self):
        report = json.dumps(
            {
                "layer_instance": "/Game/Material/MYI_Test.MYI_Test",
                "created": True,
            }
        )

        class Helper:
            @staticmethod
            def create_or_update_material_layer_instance(*_args):
                return report, []

        ok, errors, parsed = self.module._call_create_or_update_layer_instance(
            Helper,
            "/Game/Material/MY_Parent",
            "/Game/Material/MYI_Test",
            {},
        )

        self.assertTrue(ok)
        self.assertEqual(errors, [])
        self.assertTrue(parsed["created"])

    def test_existing_explicit_mi_assigns_without_master_or_mutation(self):
        target_path = "/Game/User/Materials/MI_Existing"
        user_parent = FakeMaterialInstanceConstant("/Game/User/M_UserParent")
        user_override = FakeTextureParameterValue("ArtistDetailMask")
        existing = FakeMaterialInstanceConstant(
            target_path,
            parent=user_parent,
            texture_parameter_values=[user_override],
        )
        self.runtime.assets[target_path] = existing
        data = {
            "mesh_name": "SM_Test",
            "materials": [
                {
                    "name": "ExternalMaterial",
                    "slot_index": 0,
                    "target_material_path": target_path,
                    "textures": [
                        {
                            "param": "Albedo",
                            "asset_name": "T_ShouldNotImport",
                            "file": "Z:/missing/T_ShouldNotImport.tga",
                        }
                    ],
                }
            ],
        }
        preset = {
            "key": "prop",
            "master": "/Game/Missing/M_Master",
            "mi_folder": "/Game/Material/MI",
            "assignment": "asset_surface_flat",
            "virtual_textures": True,
        }
        self.configure_process(data, preset)

        changed = self.module.process_mesh(self.mesh_path)

        self.assertTrue(changed)
        self.assertEqual(self.assignments[0][1], existing)
        self.assertIs(existing.parent, user_parent)
        self.assertEqual(existing.texture_parameter_values, [user_override])
        self.assertEqual(self.runtime.parent_changes, [])
        self.assertEqual(self.runtime.texture_parameter_sets, [])
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertNotIn(target_path, self.runtime.save_calls)

    def test_existing_generated_mi_reuses_material_without_missing_texture_import(self):
        target_path = "/Game/Material/MI/MI_Test"
        user_parent = FakeMaterialInstanceConstant("/Game/User/M_UserParent")
        user_override = FakeTextureParameterValue("ArtistDetailMask")
        existing = FakeMaterialInstanceConstant(
            target_path,
            parent=user_parent,
            texture_parameter_values=[user_override],
        )
        self.runtime.assets[target_path] = existing
        data = {
            "mesh_name": "SM_Test",
            "materials": [
                {
                    "name": "M_Test",
                    "slot_index": 0,
                    "textures": [
                        {
                            "param": "Albedo",
                            "asset_name": "T_ShouldNotImport",
                            "file": "Z:/missing/T_ShouldNotImport.tga",
                        }
                    ],
                }
            ],
        }
        preset = {
            "key": "prop",
            "master": "/Game/Missing/M_Master",
            "mi_folder": "/Game/Material/MI",
            "assignment": "asset_surface_flat",
            "virtual_textures": True,
        }
        self.configure_process(data, preset)
        self.module._load_master_material = lambda _preset: self.fail(
            "master lookup must not run when an exact MI already exists"
        )

        changed = self.module.process_mesh(self.mesh_path)

        self.assertTrue(changed)
        self.assertEqual(self.assignments[0][1], existing)
        self.assertIs(existing.parent, user_parent)
        self.assertEqual(existing.texture_parameter_values, [user_override])
        self.assertEqual(self.runtime.parent_changes, [])
        self.assertEqual(self.runtime.texture_parameter_sets, [])
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertNotIn(target_path, self.runtime.save_calls)

    def test_existing_generated_mi_reimports_changed_texture_without_material_mutation(
        self,
    ):
        target_path = "/Game/Material/MI/MI_Test"
        layer_path = "/Game/Material/MYI/MYI_Test"
        user_parent = FakeMaterialInstanceConstant("/Game/User/M_UserParent")
        mi_override = FakeTextureParameterValue("ArtistDetailMask")
        myi_override = FakeTextureParameterValue("ArtistLayerMask")
        existing_mi = FakeMaterialInstanceConstant(
            target_path,
            parent=user_parent,
            texture_parameter_values=[mi_override],
        )
        existing_myi = FakeMaterialInstanceConstant(
            layer_path,
            texture_parameter_values=[myi_override],
        )
        self.runtime.assets[target_path] = existing_mi
        self.runtime.assets[layer_path] = existing_myi

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "T_Test_color.png"
            source_path.write_bytes(b"changed texture bytes")
            expected_md5 = _md5(source_path)
            texture_path = "/Game/texture/T_Test_color"
            self.runtime.assets[texture_path] = FakeTexture(
                srgb=True,
                compression_settings="TC_DEFAULT",
                max_texture_size=0,
                virtual_texture_streaming=True,
            )
            self.runtime.asset_md5[texture_path] = "0" * 32
            data = {
                "mesh_name": "SM_Test",
                "materials": [
                    {
                        "name": "M_Test",
                        "slot_index": 0,
                        "layers": [
                            {
                                "name": "Base",
                                "index": 0,
                                "textures": [
                                    {
                                        "param": "Albedo",
                                        "asset_name": "T_Test_color",
                                        "file": str(source_path),
                                    }
                                ],
                            }
                        ],
                        "material_layer": {"instance_path": layer_path},
                    }
                ],
            }
            preset = {
                "key": "prop",
                "master": "/Game/Missing/M_Master",
                "mi_folder": "/Game/Material/MI",
                "assignment": "material_layer_instance",
                "layer_parent": "/Game/Material/MY_Parent",
                "layer_instance_folder": "/Game/Material/MYI",
                "virtual_textures": True,
            }
            self.configure_process(data, preset)

            changed = self.module.process_mesh(self.mesh_path)

        self.assertTrue(changed)
        self.assertEqual(len(self.runtime.import_tasks), 1)
        self.assertTrue(self.runtime.import_tasks[0]["replace_existing"])
        self.assertEqual(self.runtime.checkout_calls, [texture_path])
        self.assertEqual(self.runtime.asset_md5[texture_path], expected_md5)
        self.assertIs(existing_mi.parent, user_parent)
        self.assertEqual(existing_mi.texture_parameter_values, [mi_override])
        self.assertEqual(existing_myi.texture_parameter_values, [myi_override])
        self.assertEqual(self.runtime.parent_changes, [])
        self.assertEqual(self.runtime.texture_parameter_sets, [])
        self.assertNotIn(target_path, self.runtime.save_calls)
        self.assertNotIn(layer_path, self.runtime.save_calls)

    def test_empty_background_generated_mi_is_initialized_from_sidecar(self):
        target_path = "/Game/Material/MI/MI_Test"
        master_path = "/Game/Material/M_Master"
        existing = FakeMaterialInstanceConstant(target_path)
        master = FakeMaterialInstanceConstant(master_path)
        self.runtime.assets[target_path] = existing
        self.runtime.assets[master_path] = master

        class Helper:
            @staticmethod
            def dump_material_layers(_material_path):
                return True, json.dumps(
                    {
                        "ok": True,
                        "has_layers": True,
                        "layers": [{"index": 0, "path": ""}],
                    }
                )

        self.runtime.unreal_module.CodexMaterialToolsLibrary = Helper()
        data = {
            "mesh_name": "SM_Test",
            "materials": [
                {
                    "name": "M_Test",
                    "slot_index": 0,
                    "layers": [
                        {
                            "name": "Base",
                            "index": 0,
                            "textures": [
                                {
                                    "param": "Albedo",
                                    "asset_name": "T_Test_color",
                                }
                            ],
                        }
                    ],
                    "material_layer": {
                        "instance_path": "/Game/Material/MYI/MYI_Test",
                    },
                }
            ],
        }
        preset = {
            "key": "tree",
            "master": master_path,
            "mi_folder": "/Game/Material/MI",
            "assignment": "material_layer_instance",
            "layer_parent": "/Game/Material/MY_Parent",
            "layer_instance_folder": "/Game/Material/MYI",
            "virtual_textures": True,
        }
        self.configure_process(data, preset)
        assigned = []
        self.module._assign_master_textures = (
            lambda mi, *args, **kwargs: assigned.append(mi) or True
        )

        changed = self.module.process_mesh(self.mesh_path)

        self.assertTrue(changed)
        self.assertEqual(assigned, [existing])
        self.assertIs(existing.parent, master)
        self.assertEqual(self.assignments[0][1], existing)

    def test_empty_generated_mi_without_explicit_layer_contract_is_repairable(self):
        existing = FakeMaterialInstanceConstant("/Game/Material/MI/MI_Test")

        class Helper:
            @staticmethod
            def dump_material_layers(_material_path):
                return True, json.dumps(
                    {
                        "ok": True,
                        "has_layers": True,
                        "layers": [{"index": 0, "path": ""}],
                    }
                )

        self.runtime.unreal_module.CodexMaterialToolsLibrary = Helper()
        entry = {"name": "M_Test"}
        preset = {
            "assignment": "material_layer_instance",
            "mi_folder": "/Game/Material/MI",
            "layer_parent": "/Game/Material/MY_Parent",
            "layer_instance_folder": "/Game/Material/MYI",
        }

        self.assertTrue(
            self.module._material_instance_has_empty_background_layer(
                existing,
                entry,
                preset,
            )
        )

    def test_empty_external_mi_without_layer_contract_remains_assignment_only(self):
        existing = FakeMaterialInstanceConstant("/Game/User/MI_Artist")
        entry = {"name": "M_Test"}
        preset = {
            "assignment": "material_layer_instance",
            "mi_folder": "/Game/Material/MI",
            "layer_parent": "/Game/Material/MY_Parent",
            "layer_instance_folder": "/Game/Material/MYI",
        }

        self.assertFalse(
            self.module._material_instance_has_empty_background_layer(
                existing,
                entry,
                preset,
            )
        )

    def test_empty_background_generated_mi_is_initialized_without_textures(self):
        target_path = "/Game/Material/MI/MI_Test"
        master_path = "/Game/Material/M_Master"
        existing = FakeMaterialInstanceConstant(target_path)
        master = FakeMaterialInstanceConstant(master_path)
        self.runtime.assets[target_path] = existing
        self.runtime.assets[master_path] = master

        class Helper:
            @staticmethod
            def dump_material_layers(_material_path):
                return True, json.dumps(
                    {
                        "ok": True,
                        "has_layers": True,
                        "layers": [{"index": 0, "path": ""}],
                    }
                )

        self.runtime.unreal_module.CodexMaterialToolsLibrary = Helper()
        data = {
            "mesh_name": "SM_Test",
            "materials": [
                {
                    "name": "M_Test",
                    "slot_index": 0,
                    "material_layer": {
                        "instance_path": "/Game/Material/MYI/MYI_Test",
                    },
                }
            ],
        }
        preset = {
            "key": "layer",
            "master": master_path,
            "mi_folder": "/Game/Material/MI",
            "assignment": "material_layer_instance",
            "layer_parent": "/Game/Material/MY_Parent",
            "layer_instance_folder": "/Game/Material/MYI",
            "virtual_textures": True,
        }
        self.configure_process(data, preset)
        assigned = []
        self.module._assign_master_textures = (
            lambda mi, *args, **kwargs: assigned.append(mi) or True
        )

        changed = self.module.process_mesh(self.mesh_path)

        self.assertTrue(changed)
        self.assertEqual(assigned, [existing])
        self.assertEqual(self.assignments[0][1], existing)

    def test_nonempty_artist_background_remains_assignment_only(self):
        existing = FakeMaterialInstanceConstant("/Game/Material/MI/MI_Test")

        class Helper:
            @staticmethod
            def dump_material_layers(_material_path):
                return True, json.dumps(
                    {
                        "ok": True,
                        "has_layers": True,
                        "layers": [
                            {
                                "index": 0,
                                "path": "/Game/User/MYI/MYI_ArtistLayer.MYI_ArtistLayer",
                            }
                        ],
                    }
                )

        self.runtime.unreal_module.CodexMaterialToolsLibrary = Helper()
        entry = {
            "name": "M_Test",
            "layers": [
                {
                    "textures": [
                        {
                            "param": "Albedo",
                            "asset_name": "T_Test_color",
                        }
                    ]
                }
            ],
            "material_layer": {
                "instance_path": "/Game/Material/MYI/MYI_Test",
            },
        }
        preset = {
            "assignment": "material_layer_instance",
            "layer_instance_folder": "/Game/Material/MYI",
        }

        self.assertFalse(
            self.module._material_instance_has_empty_background_layer(
                existing,
                entry,
                preset,
            )
        )

    def test_existing_generated_mi_removes_material_layer_preflight_gate(self):
        target_path = "/Game/Material/MI/MI_Test"
        self.runtime.assets[target_path] = FakeMaterialInstanceConstant(target_path)
        data = {"materials": [{"name": "M_Test", "slot_index": 0}]}
        preset = {
            "key": "tree",
            "master": "/Game/Missing/M_Master",
            "mi_folder": "/Game/Material/MI",
            "assignment": "material_layer_instance",
            "layer_parent": "/Game/Missing/MY_Parent",
            "layer_instance_folder": "/Game/Material/MYI",
        }
        self.module._load_json = lambda *args, **kwargs: data
        self.module._validate_speedtree_handoff_contract = (
            lambda *args, **kwargs: None
        )
        self.module._validate_codex_test_material_scope = (
            lambda *args, **kwargs: True
        )
        self.module._validate_instance_profile_targets = (
            lambda *args, **kwargs: {}
        )
        self.module._checkout_material_pipeline_assets = (
            lambda *args, **kwargs: []
        )
        self.module._ensure_instance_profile_targets = (
            lambda *args, **kwargs: {}
        )
        self.module._master_preset = lambda *args, **kwargs: dict(preset)

        self.assertFalse(
            self.module.preflight_mesh_materials(
                self.mesh_path,
                expected_mesh_name="SM_Test",
            )
        )

    def test_existing_profile_mi_skips_base_master_and_missing_texture_import(self):
        target_path = "/Game/Material/MI/MI_Test_canopy"
        existing = FakeMaterialInstanceConstant(target_path)
        self.runtime.assets[target_path] = existing
        data = {
            "mesh_name": "SM_Test",
            "materials": [
                {
                    "name": "M_Test",
                    "slot_index": 0,
                    "instance_profile": "canopy",
                    "textures": [
                        {
                            "param": "Albedo",
                            "asset_name": "T_ShouldNotImport",
                            "file": "Z:/missing/T_ShouldNotImport.tga",
                        }
                    ],
                }
            ],
        }
        preset = {
            "key": "tree",
            "master": "/Game/Missing/M_Master",
            "mi_folder": "/Game/Material/MI",
            "assignment": "asset_surface_flat",
            "virtual_textures": True,
        }
        self.configure_process(data, preset)
        plan = {
            "profile": "canopy",
            "target_path": target_path,
            "target_existed": True,
            "asset": existing,
        }
        self.module._validate_instance_profile_targets = (
            lambda *args, **kwargs: {0: plan}
        )
        self.module._load_master_material = lambda _preset: self.fail(
            "master lookup must not run for an existing profile MI"
        )

        changed = self.module.process_mesh(self.mesh_path)

        self.assertTrue(changed)
        self.assertEqual(self.assignments[0][1], existing)
        self.assertEqual(self.runtime.parent_changes, [])
        self.assertEqual(self.runtime.texture_parameter_sets, [])
        self.assertEqual(self.runtime.import_tasks, [])

    def test_assignment_only_existing_mi_is_excluded_from_preflight_mutations(self):
        target_path = "/Game/User/Materials/MI_Existing"
        self.runtime.assets[target_path] = FakeMaterialInstanceConstant(target_path)
        data = {
            "materials": [
                {
                    "name": "ExternalMaterial",
                    "target_material_path": target_path,
                }
            ]
        }
        preset = {
            "key": "layer",
            "master": "/Game/Material/M_Master",
            "mi_folder": "/Game/Material/MI",
            "assignment": "material_layer_instance",
            "layer_parent": "/Game/Material/MY_Parent",
            "layer_instance_folder": "/Game/Material/MYI",
        }
        self.module._master_preset = lambda *args, **kwargs: dict(preset)

        paths = self.module._material_pipeline_mutation_paths(
            self.mesh_path,
            data,
        )

        self.assertEqual(paths, [self.mesh_path])

    def test_all_missing_textures_assigns_new_blank_pipeline_mi(self):
        master_path = "/Game/Material/M_Master"
        master = FakeMaterialInstanceConstant(master_path)
        self.runtime.assets[master_path] = master
        data = {
            "mesh_name": "SM_Test",
            "materials": [
                {
                    "name": "M_Test",
                    "slot_index": 0,
                    "layers": [
                        {
                            "name": "Base",
                            "index": 0,
                            "textures": [
                                {
                                    "param": "Albedo",
                                    "asset_name": "T_Missing_color",
                                    "file": "Z:/missing/T_Missing_color.tga",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        preset = {
            "key": "prop",
            "master": master_path,
            "mi_folder": "/Game/Material/MI",
            "assignment": "asset_surface_flat",
            "virtual_textures": True,
        }
        self.configure_process(data, preset)

        changed = self.module.process_mesh(self.mesh_path)

        expected_mi_path = "/Game/Material/MI/MI_Test"
        self.assertTrue(changed)
        self.assertIn(expected_mi_path, self.runtime.assets)
        assigned_mi = self.assignments[0][1]
        self.assertIs(assigned_mi, self.runtime.assets[expected_mi_path])
        self.assertEqual(assigned_mi.texture_parameter_values, [])
        self.assertEqual(self.runtime.texture_parameter_sets, [])
        self.assertEqual(self.runtime.import_tasks, [])
        self.assertEqual(self.runtime.warnings, [])


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

    def test_duplicate_imported_slots_map_to_the_canonical_slots(self):
        mesh = FakeSkeletalMesh(
            [
                FakeSkeletalMaterial("M_Branch"),
                FakeSkeletalMaterial("M_Bark"),
                FakeSkeletalMaterial("M_Branch"),
                FakeSkeletalMaterial("M_Bark"),
            ]
        )
        branch = FakeMaterialInstanceConstant("/Game/MI/MI_Branch")
        bark = FakeMaterialInstanceConstant("/Game/MI/MI_Bark")

        changed = self.module._normalize_skeletal_material_slots(
            mesh,
            {
                0: ("M_Branch", branch),
                1: ("M_Bark", bark),
            },
        )

        self.assertTrue(changed)
        self.assertEqual(
            self.calls,
            [{
                "slot_count": 2,
                "old": [0, 1, 2, 3],
                "new": [0, 1, 0, 1],
                "apply": True,
            }],
        )
        self.assertEqual(len(mesh.materials), 2)
        self.assertEqual(
            [
                str(entry.get_editor_property("material_slot_name"))
                for entry in mesh.materials
            ],
            ["M_Branch", "M_Bark"],
        )

    def test_stale_species_slots_map_by_unambiguous_tree_part(self):
        mesh = FakeSkeletalMesh(
            [
                FakeSkeletalMaterial("M_Branch_black_locast_01"),
                FakeSkeletalMaterial("M_bark_black_locast_02"),
                FakeSkeletalMaterial("M_branch_NothofagusSolandri_03"),
                FakeSkeletalMaterial("M_branch_NothofagusSolandri_04"),
            ]
        )
        branch = FakeMaterialInstanceConstant("/Game/MI/MI_Branch_black_locast_01")
        bark = FakeMaterialInstanceConstant("/Game/MI/MI_bark_black_locast_02")

        changed = self.module._normalize_skeletal_material_slots(
            mesh,
            {
                0: ("M_Branch_black_locast_01", branch),
                1: ("M_bark_black_locast_02", bark),
            },
        )

        self.assertTrue(changed)
        self.assertEqual(
            self.calls,
            [{
                "slot_count": 2,
                "old": [0, 1, 2, 3],
                "new": [0, 1, 0, 0],
                "apply": True,
            }],
        )
        self.assertEqual(
            [
                str(entry.get_editor_property("material_slot_name"))
                for entry in mesh.materials
            ],
            ["M_Branch_black_locast_01", "M_bark_black_locast_02"],
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


class TestHairToolBridgeParameterSync(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.module = _load_module(self.runtime)
        self.mi = FakeMaterialInstanceConstant(
            "/Game/Material/HairTool/MI/MI_HT_Default_Material_01"
        )

    def test_explicit_sync_list_makes_blender_authoritative_for_system_colors(self):
        entry = {
            "hair_tool": {
                "sync_parameters": [
                    "System Color 01",
                    "System Color Influence",
                ],
                "vector_parameters": {
                    "System Color 01": [0.1, 0.2, 0.3, 1.0],
                },
                "scalar_parameters": {
                    "System Color Influence": 0.75,
                    "System Mask Bias": 0.25,
                    "Root Blend Mode": 2.0,
                },
            }
        }

        changed = self.module._assign_hair_tool_parameters(
            self.mi,
            entry,
            {},
            initialize_instance_owned_parameters=False,
        )

        self.assertTrue(changed)
        self.assertEqual(
            self.mi.vector_values_by_name["System Color 01"],
            (0.1, 0.2, 0.3, 1.0),
        )
        self.assertEqual(
            self.mi.scalar_values_by_name["System Color Influence"], 0.75
        )
        self.assertEqual(self.mi.scalar_values_by_name["Root Blend Mode"], 2.0)
        self.assertNotIn("System Mask Bias", self.mi.scalar_values_by_name)


if __name__ == "__main__":
    unittest.main()
