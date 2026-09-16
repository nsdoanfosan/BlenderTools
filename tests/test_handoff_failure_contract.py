"""Regression coverage for sidecar ownership and save failure propagation."""

import unittest

import test_ue_material_setup as fixtures

FakeMaterialInstanceConstant = fixtures.FakeMaterialInstanceConstant


class TestHandoffFailureContract(unittest.TestCase):
    setUp = fixtures.TestRuntimeTolerantMaterialProcess.setUp
    configure_process = fixtures.TestRuntimeTolerantMaterialProcess.configure_process

    def configure_material(self, *, name="M_Test", entry_options=None, preset_options=None):
        entry = {"name": name, "slot_index": 0, "textures": [], "layers": []}
        entry.update(entry_options or {})
        preset = {
            "key": "prop",
            "master": "/Game/Material/M_Master",
            "mi_folder": "/Game/Material/MI",
            "assignment": "asset_surface_flat",
        }
        preset.update(preset_options or {})
        self.runtime.assets[preset["master"]] = FakeMaterialInstanceConstant(preset["master"])
        self.configure_process({"mesh_name": "SM_Test", "materials": [entry]}, preset)

    def assert_no_material_created(self):
        self.assertEqual(self.runtime.created_assets, [])
        self.assertEqual(self.runtime.created_directories, [])
        self.assertEqual(self.runtime.mark_add_calls, [])
        self.assertEqual(self.assignments, [])

    def test_generated_target_respects_entry_create_if_missing_false(self):
        self.configure_material(entry_options={"create_if_missing": False})
        self.assertFalse(self.module.process_mesh(self.mesh_path))
        self.assert_no_material_created()

    def test_generated_target_respects_preset_create_if_missing_false(self):
        self.configure_material(preset_options={"create_if_missing": False})
        self.assertFalse(self.module.process_mesh(self.mesh_path))
        self.assert_no_material_created()

    def test_generated_target_respects_string_false_before_suffix_copy(self):
        source = "/Game/Material/MI/MI_Test"
        self.runtime.assets[source] = FakeMaterialInstanceConstant(source)
        copy_calls = []
        self.runtime.unreal_module.EditorAssetLibrary.duplicate_asset = (
            lambda *args: copy_calls.append(args) or FakeMaterialInstanceConstant(args[1])
        )
        self.configure_material(name="M_Test_01", entry_options={"create_if_missing": "false"})
        self.assertFalse(self.module.process_mesh(self.mesh_path))
        self.assert_no_material_created()
        self.assertEqual(copy_calls, [])

    def test_explicit_target_respects_false_before_suffix_or_explicit_copy(self):
        source = "/Game/Material/MI/MI_Test"
        target = source + "_01"
        self.runtime.assets[source] = FakeMaterialInstanceConstant(source)
        copies = []
        self.runtime.unreal_module.EditorAssetLibrary.duplicate_asset = (
            lambda *args: copies.append(args) or FakeMaterialInstanceConstant(args[1])
        )
        for copy_from in (None, source):
            with self.subTest(copy_from=copy_from):
                result = self.module._load_or_copy_target_material(
                    self.runtime.unreal_module.AssetToolsHelpers.get_asset_tools(),
                    target,
                    copy_from_path=copy_from,
                    create_if_missing=False,
                )
                self.assertEqual(result, (None, target, False, "missing"))
        self.assertEqual(copies, [])
        self.assert_no_material_created()

    def test_existing_target_still_reused_when_creation_forbidden(self):
        target = "/Game/Material/MI/MI_Test"
        existing = FakeMaterialInstanceConstant(target)
        self.runtime.assets[target] = existing
        self.configure_material(entry_options={"create_if_missing": False})
        self.assertTrue(self.module.process_mesh(self.mesh_path))
        self.assertIs(self.assignments[0][1], existing)
        self.assertEqual(self.runtime.created_assets, [])
        self.assertEqual(self.runtime.parent_changes, [])

    def test_static_mesh_save_failure_blocks_success_report(self):
        target = "/Game/Material/MI/MI_Test"
        self.runtime.assets[target] = FakeMaterialInstanceConstant(target)
        self.configure_material()
        self.runtime.fail_save = True
        with self.assertRaisesRegex(RuntimeError, "static-mesh save failed"):
            self.module.process_mesh(self.mesh_path)
        self.assertIn(self.mesh_path, self.runtime.save_calls)
        self.assertFalse(any("완료" in line for line in self.runtime.logs))

    def test_nanite_only_save_failure_blocks_success_report(self):
        self.configure_material()
        self.module._load_json = lambda *_args, **_kwargs: {"materials": []}
        self.module.ENABLE_NANITE = True
        self.module._set_nanite = lambda *_args: True
        self.runtime.fail_save = True
        with self.assertRaisesRegex(RuntimeError, "static-mesh save failed"):
            self.module.process_mesh(self.mesh_path)
        self.assertEqual(self.runtime.created_assets, [])


if __name__ == "__main__":
    unittest.main()
