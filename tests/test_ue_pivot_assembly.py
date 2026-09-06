"""Nested-pivot manifest validation and protections around existing assets.

Native create/update/reparent/cleanup is additionally exercised by the isolated
Unreal smoke check; these tests require neither Unreal nor Blender.
"""

import copy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from contextlib import nullcontext
import unittest
import uuid
from unittest.mock import Mock, patch


MODULE_PATH = (Path(__file__).resolve().parents[1] / "src" / "addons" / "send2ue"
               / "resources" / "pipeline" / "ue_pivot_assembly.py")
spec = importlib.util.spec_from_file_location("ue_pivot_assembly_test", MODULE_PATH)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def component(name, parent):
    return dict(name=name, parent=parent, mesh_asset_path="/Game/Meshes/00_common/window/" + name,
                location=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])


def manifest():
    return dict(schema_version=1, blueprint_name="bc_window_wood_single_02", root="window_wood_single_02",
                components=[component("window_wood_single_02", None),
                            component("window_wood_single_02_glass", "window_wood_single_02")])


class ManifestTests(unittest.TestCase):
    def test_digit_leading_content_folder_and_object_paths(self):
        value = manifest()
        value["components"][1]["mesh_asset_path"] += ".window_wood_single_02_glass"
        validated = runtime.validate_assembly(value)
        self.assertEqual(validated["blueprint_asset_path"],
                         "/Game/Meshes/00_common/window/bc_window_wood_single_02")
        self.assertNotIn(".", validated["components"][1]["mesh_asset_path"])

    def test_parent_first_sort_with_arbitrary_depth_does_not_mutate_input(self):
        value = manifest()
        value["components"].append(component("latch", "window_wood_single_02_glass"))
        value["components"].reverse()
        original = copy.deepcopy(value)
        result = runtime.validate_assembly(value)
        self.assertEqual([row["name"] for row in result["components"]],
                         ["window_wood_single_02", "window_wood_single_02_glass", "latch"])
        self.assertEqual(value, original)

    def test_invalid_hierarchies_rejected(self):
        for parent in (None, "missing", "window_wood_single_02_glass"):
            with self.subTest(parent=parent):
                value = manifest()
                value["components"][1]["parent"] = parent
                with self.assertRaises(ValueError):
                    runtime.validate_assembly(value)
        value = manifest()
        value["components"].append(component("latch", "window_wood_single_02_glass"))
        value["components"][1]["parent"] = "latch"
        with self.assertRaisesRegex(ValueError, "cycle"):
            runtime.validate_assembly(value)

    def test_invalid_paths_rejected(self):
        for path in ("/Game/X.Y", "/Game/../X", "Game/X", "/Game//X", "/Game/X:Part", "/Game/X X"):
            with self.subTest(path=path):
                value = manifest()
                value["components"][1]["mesh_asset_path"] = path
                with self.assertRaises(ValueError):
                    runtime.validate_assembly(value)

    def test_duplicate_names_casefold_and_duplicate_meshes_rejected(self):
        value = manifest()
        value["components"][1]["name"] = value["root"].upper()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            runtime.validate_assembly(value)
        value = manifest()
        value["components"][1]["mesh_asset_path"] = value["components"][0]["mesh_asset_path"]
        with self.assertRaisesRegex(ValueError, "distinct"):
            runtime.validate_assembly(value)

    def test_invalid_transform_data_rejected(self):
        for field, values in (("location", [float("nan"), 0, 0]),
                              ("location", [True, 0, 0]), ("rotation", [0, 0, 0]),
                              ("rotation", [0, 0, 0, 2]), ("scale", [1, 0, 1])):
            with self.subTest(field=field, values=values):
                value = manifest()
                value["components"][1][field] = values
                with self.assertRaises(ValueError):
                    runtime.validate_assembly(value)

    def test_negative_scale_and_unit_quaternion_supported(self):
        value = manifest()
        value["components"][1]["scale"] = [-1, 2, 3]
        value["components"][1]["rotation"] = [0, 0, 0.6, 0.8]
        result = runtime.validate_assembly(value)
        self.assertEqual(result["components"][1]["scale"], [-1, 2, 3])

    def test_root_must_be_identity(self):
        value = manifest()
        value["components"][0]["location"] = [1, 0, 0]
        with self.assertRaisesRegex(ValueError, "identity"):
            runtime.validate_assembly(value)

    def test_empty_or_single_component_not_an_assembly(self):
        for count in (0, 1):
            value = manifest()
            value["components"] = value["components"][:count]
            with self.assertRaisesRegex(ValueError, "at least one nested"):
                runtime.validate_assembly(value)

    def test_schema_and_destination_constraints(self):
        for change in ({"schema_version": True}, {"schema_version": 2},
                       {"blueprint_name": "UserBlueprint"}, {"blueprint_asset_path": "/Game/Other/BP"}):
            with self.subTest(change=change):
                value = manifest()
                value.update(change)
                with self.assertRaises(ValueError):
                    runtime.validate_assembly(value)

    def test_empty_manifest_list_never_imports_unreal(self):
        with patch.dict(sys.modules, {"unreal": None}):
            self.assertEqual(runtime.apply_pivot_assemblies([]), [])


class AssetProtectionTests(unittest.TestCase):
    def setUp(self):
        self.assembly = runtime.validate_assembly(manifest())
        self.blueprint = type("Blueprint", (), {})()
        self.mesh_class = type("StaticMesh", (), {})
        self.meshes = {row["mesh_asset_path"]: self.mesh_class() for row in self.assembly["components"]}
        self.assets = dict(self.meshes, **{self.assembly["blueprint_asset_path"]: self.blueprint})
        self.library = Mock()
        self.library.does_asset_exist.return_value = True
        self.library.get_metadata_tag.return_value = runtime.OWNER_VERSION + self.assembly["components"][0]["mesh_asset_path"]
        self.unreal = SimpleNamespace(EditorAssetLibrary=self.library, StaticMesh=self.mesh_class,
                                      Blueprint=type(self.blueprint), load_asset=self.assets.get,
                                      SubobjectDataBlueprintFunctionLibrary=Mock())
        self.root_object, self.glass_object, self.user_object = Mock(), Mock(), Mock()
        self.root = dict(identifier=self.assembly["root"], variable=self.assembly["root"],
                         data="root", handle="root", object=self.root_object, parent_object=None)
        self.glass = dict(identifier="window_wood_single_02_glass", variable="window_wood_single_02_glass",
                          data="glass", handle="glass", object=self.glass_object, parent_object=self.root_object)
        self.unreal.SubobjectDataBlueprintFunctionLibrary.is_root_component.side_effect = lambda data: data == "root"

    def prepare(self, extra_rows=()):
        rows = [self.root, self.glass] + list(extra_rows)
        generated = {row["identifier"]: row for row in rows if row["identifier"] is not None}
        with patch.object(runtime, "_gather", return_value=("context", rows, generated)):
            return runtime._prepare(self.unreal, Mock(), self.assembly)

    def test_unowned_blueprint_rejected_before_edit(self):
        self.library.get_metadata_tag.return_value = ""
        with self.assertRaisesRegex(RuntimeError, "not owned"):
            self.prepare()
        self.library.set_metadata_tag.assert_not_called()
        self.library.save_loaded_asset.assert_not_called()

    def test_missing_or_wrong_class_mesh_rejected_before_edit(self):
        del self.assets[self.assembly["components"][1]["mesh_asset_path"]]
        with self.assertRaisesRegex(RuntimeError, "not a StaticMesh"):
            self.prepare()
        self.library.does_asset_exist.assert_not_called()

    def test_user_component_name_collision_rejected(self):
        extra = dict(identifier=None, variable="WINDOW_WOOD_SINGLE_02_GLASS", data="user",
                     object=self.user_object, parent_object=self.root_object)
        with self.assertRaisesRegex(RuntimeError, "user component"):
            self.prepare([extra])

    def test_unrelated_user_component_is_preserved_by_preflight(self):
        extra = dict(identifier=None, variable="UserHandle", data="user",
                     object=self.user_object, parent_object=self.root_object)
        self.assertIs(self.prepare([extra])["blueprint"], self.blueprint)
        self.library.save_loaded_asset.assert_not_called()

    def test_stale_generated_pivot_with_user_children_is_rejected(self):
        stale_object = Mock()
        stale = dict(identifier="obsolete", variable="obsolete", data="obsolete", handle="obsolete",
                     object=stale_object, parent_object=self.root_object)
        user = dict(identifier=None, variable="UserHandle", data="user", object=self.user_object,
                    parent_object=stale_object)
        with self.assertRaisesRegex(RuntimeError, "user-authored children"):
            self.prepare([stale, user])

    def test_changed_root_is_not_replaced(self):
        self.unreal.SubobjectDataBlueprintFunctionLibrary.is_root_component.side_effect = lambda data: data == "glass"
        with self.assertRaisesRegex(RuntimeError, "root was changed"):
            self.prepare()

    def test_all_assemblies_preflight_before_first_mutation(self):
        self.unreal.get_engine_subsystem = Mock(return_value=Mock())
        self.unreal.SubobjectDataSubsystem = object()
        second = manifest()
        second["root"] = "other"
        second["blueprint_name"] = "bc_other"
        second["components"] = [component("other", None), component("other_glass", "other")]
        with patch.dict(sys.modules, {"unreal": self.unreal}), \
                patch.object(runtime, "_prepare", side_effect=[{}, RuntimeError("missing second mesh")]), \
                patch.object(runtime, "_apply") as apply:
            with self.assertRaisesRegex(RuntimeError, "missing second mesh"):
                runtime.apply_pivot_assemblies([manifest(), second])
            apply.assert_not_called()


class NativeApiRegressionTests(unittest.TestCase):
    def test_duplicate_gather_wrappers_for_one_template_are_deduplicated(self):
        mesh_component_class = type("StaticMeshComponent", (), {})
        obj = mesh_component_class()
        obj.get_path_name = lambda: "/Game/BP.BP_C:glass_GEN_VARIABLE"
        obj.get_editor_property = lambda name: [runtime.COMPONENT_TAG + "glass"]
        actor = object()
        library = Mock()
        library.get_data.side_effect = lambda handle: handle
        library.is_root_actor.side_effect = lambda data: data == "actor"
        library.is_component.side_effect = lambda data: data != "actor"
        library.get_object_for_blueprint.side_effect = lambda data, bp: actor if data == "actor" else obj
        library.get_parent_handle.return_value = "actor"
        library.is_handle_valid.return_value = True
        library.get_variable_name.return_value = "glass"
        library.is_inherited_component.return_value = False
        library.is_native_component.return_value = False
        unreal = SimpleNamespace(SubobjectDataBlueprintFunctionLibrary=library, StaticMeshComponent=mesh_component_class)
        subsystem = Mock()
        subsystem.k2_gather_subobject_data_for_blueprint.return_value = ["actor", "child_wrapper_1", "child_wrapper_2"]
        _, rows, generated = runtime._gather(unreal, subsystem, object())
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(generated), ["glass"])

    def test_set_static_mesh_false_for_unchanged_is_not_failure(self):
        mesh = object()
        component_object = Mock()
        component_object.get_editor_property.return_value = mesh
        component_object.set_static_mesh.return_value = False
        unreal = SimpleNamespace(Vector=Mock(), Quat=Mock())
        runtime._set_component(unreal, component_object, component("glass", "root"), mesh)
        component_object.set_static_mesh.assert_not_called()
        self.assertEqual(component_object.set_editor_property.call_count, 6)


class FailedNewBlueprintCleanupTests(unittest.TestCase):
    def setUp(self):
        self.assembly = runtime.validate_assembly(manifest())
        self.path = self.assembly["blueprint_asset_path"]
        self.blueprint = Mock()
        self.blueprint.get_path_name.return_value = self.path + "." + self.assembly["blueprint_name"]
        self.error = RuntimeError("injected component failure")
        self.blueprint.modify.side_effect = self.error
        self.library = Mock()
        self.library.delete_loaded_asset.return_value = True
        self.library.does_asset_exist.return_value = False
        self.asset_tools = Mock()
        self.asset_tools.create_asset.return_value = self.blueprint
        self.unreal = SimpleNamespace(
            BlueprintFactory=Mock(), Actor=object(), Blueprint=object(),
            AssetToolsHelpers=SimpleNamespace(get_asset_tools=lambda: self.asset_tools),
            ScopedEditorTransaction=lambda label: nullcontext(),
            EditorAssetLibrary=self.library, load_asset=Mock(return_value=self.blueprint),
        )

    def apply(self, created):
        return runtime._apply(self.unreal, Mock(), dict(
            assembly=self.assembly, blueprint=None if created else self.blueprint,
            meshes={}, owner="owner",
        ))

    def test_failed_new_blueprint_is_deleted_and_original_error_preserved(self):
        with self.assertRaises(RuntimeError) as raised:
            self.apply(created=True)
        self.assertIs(raised.exception, self.error)
        self.library.delete_loaded_asset.assert_called_once_with(self.blueprint)
        self.library.does_asset_exist.assert_called_once_with(self.path)
        self.library.save_loaded_asset.assert_not_called()

    def test_existing_blueprint_failure_never_deletes_or_reloads_it(self):
        with self.assertRaises(RuntimeError) as raised:
            self.apply(created=False)
        self.assertIs(raised.exception, self.error)
        self.library.delete_loaded_asset.assert_not_called()
        self.unreal.load_asset.assert_not_called()
        self.asset_tools.create_asset.assert_not_called()

    def test_changed_destination_identity_is_not_deleted(self):
        self.unreal.load_asset.return_value = Mock()
        with self.assertRaisesRegex(RuntimeError, "Resolve this exact failed asset") as raised:
            self.apply(created=True)
        self.assertIs(raised.exception.__cause__, self.error)
        self.library.delete_loaded_asset.assert_not_called()

    def test_changed_object_path_is_not_deleted(self):
        self.blueprint.get_path_name.return_value = "/Game/Other/UserBP.UserBP"
        with self.assertRaisesRegex(RuntimeError, "no longer identifies"):
            self.apply(created=True)
        self.library.delete_loaded_asset.assert_not_called()

    def test_failed_cleanup_is_actionable_and_chains_original_error(self):
        self.library.delete_loaded_asset.return_value = False
        with self.assertRaisesRegex(RuntimeError, "could not be cleaned up") as raised:
            self.apply(created=True)
        self.assertIn(self.path, str(raised.exception))
        self.assertIs(raised.exception.__cause__, self.error)

    def test_deleted_asset_absence_is_verified(self):
        self.library.does_asset_exist.return_value = True
        with self.assertRaisesRegex(RuntimeError, "still exists") as raised:
            self.apply(created=True)
        self.assertIs(raised.exception.__cause__, self.error)


class ImportReceiptTests(unittest.TestCase):
    def setUp(self):
        self.saved_store = sys.modules.pop(runtime._RECEIPT_MODULE, None)
        mesh_class = type("StaticMesh", (), {})
        self.unreal = SimpleNamespace(StaticMesh=mesh_class, load_asset=Mock(return_value=mesh_class()))
        self.unreal_patch = patch.dict(sys.modules, {"unreal": self.unreal})
        self.unreal_patch.start()
        self.run_id = str(uuid.uuid4())
        self.receipt = dict(blueprint_asset_path="/Game/Meshes/00_common/window/bc_window_wood_single_02",
                            created=True, component_count=2, verified=True)
        self.apply_patch = patch.object(runtime, "apply_pivot_assemblies", return_value=[self.receipt])
        self.apply = self.apply_patch.start()

    def tearDown(self):
        self.apply_patch.stop()
        self.unreal_patch.stop()
        sys.modules.pop(runtime._RECEIPT_MODULE, None)
        if self.saved_store is not None:
            sys.modules[runtime._RECEIPT_MODULE] = self.saved_store

    def record(self, index):
        assembly = manifest()
        result = copy.deepcopy(assembly["components"][index])
        result.update(root=assembly["root"], required_pivots=[row["name"] for row in assembly["components"]])
        return result

    def test_partial_and_duplicate_receipts_do_not_satisfy_missing_mesh(self):
        result = runtime.record_imported_pivot(self.run_id, self.record(0))
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["missing"], ["window_wood_single_02_glass"])
        runtime.record_imported_pivot(self.run_id, self.record(0))
        self.apply.assert_not_called()

    def test_different_runs_cannot_complete_each_other(self):
        runtime.record_imported_pivot(self.run_id, self.record(0))
        result = runtime.record_imported_pivot(str(uuid.uuid4()), self.record(1))
        self.assertEqual(result["received"], 1)
        self.assertEqual(result["status"], "pending")
        self.apply.assert_not_called()

    def test_completion_in_child_first_order_and_idempotent_replay(self):
        runtime.record_imported_pivot(self.run_id, self.record(1))
        result = runtime.record_imported_pivot(self.run_id, self.record(0))
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["receipt"], self.receipt)
        self.apply.assert_called_once()
        validated = runtime.validate_assembly(self.apply.call_args.args[0][0])
        self.assertEqual(validated["components"][0]["name"], "window_wood_single_02")
        self.assertFalse(runtime._receipt_store()[self.run_id]["pending"])
        result = runtime.record_imported_pivot(self.run_id, self.record(1))
        self.assertEqual(result["status"], "replayed")
        self.apply.assert_called_once()

    def test_conflicting_pending_duplicate_rejected(self):
        runtime.record_imported_pivot(self.run_id, self.record(1))
        changed = self.record(1)
        changed["location"] = [100, 0, 0]
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            runtime.record_imported_pivot(self.run_id, changed)
        self.apply.assert_not_called()

    def test_conflicting_complete_replay_rejected(self):
        runtime.record_imported_pivot(self.run_id, self.record(0))
        runtime.record_imported_pivot(self.run_id, self.record(1))
        changed = self.record(1)
        changed["location"] = [100, 0, 0]
        with self.assertRaisesRegex(ValueError, "Conflicting replay"):
            runtime.record_imported_pivot(self.run_id, changed)
        self.apply.assert_called_once()

    def test_required_set_consistency_and_order(self):
        first = self.record(0)
        first["required_pivots"].reverse()
        runtime.record_imported_pivot(self.run_id, first)
        changed = self.record(1)
        changed["required_pivots"].append("extra_pivot")
        with self.assertRaisesRegex(ValueError, "Inconsistent required_pivots"):
            runtime.record_imported_pivot(self.run_id, changed)
        self.assertEqual(runtime.record_imported_pivot(self.run_id, self.record(1))["status"], "applied")

    def test_missing_actual_unreal_mesh_never_records_receipt(self):
        self.unreal.load_asset.return_value = None
        with self.assertRaisesRegex(RuntimeError, "without its Unreal StaticMesh"):
            runtime.record_imported_pivot(self.run_id, self.record(0))
        self.assertNotIn(runtime._RECEIPT_MODULE, sys.modules)

    def test_store_survives_runtime_module_reload(self):
        runtime.record_imported_pivot(self.run_id, self.record(0))
        reload_spec = importlib.util.spec_from_file_location("ue_pivot_assembly_reloaded_test", MODULE_PATH)
        reloaded = importlib.util.module_from_spec(reload_spec)
        reload_spec.loader.exec_module(reloaded)
        with patch.object(reloaded, "apply_pivot_assemblies", return_value=[self.receipt]) as apply:
            self.assertEqual(reloaded.record_imported_pivot(self.run_id, self.record(1))["status"], "applied")
            apply.assert_called_once()

    def test_stale_runs_are_capped_and_cannot_supply_old_receipts(self):
        runtime.record_imported_pivot(self.run_id, self.record(0))
        for _ in range(runtime._MAX_RECEIPT_RUNS):
            runtime.record_imported_pivot(str(uuid.uuid4()), self.record(0))
        self.assertEqual(len(runtime._receipt_store()), runtime._MAX_RECEIPT_RUNS)
        self.assertNotIn(self.run_id, runtime._receipt_store())
        self.assertEqual(runtime.record_imported_pivot(self.run_id, self.record(1))["status"], "pending")
        self.apply.assert_not_called()

    def test_failed_apply_keeps_group_pending_for_retry(self):
        runtime.record_imported_pivot(self.run_id, self.record(0))
        self.apply.side_effect = RuntimeError("save failed")
        with self.assertRaisesRegex(RuntimeError, "save failed"):
            runtime.record_imported_pivot(self.run_id, self.record(1))
        self.assertFalse(runtime._receipt_store()[self.run_id]["completed"])
        self.apply.side_effect = None
        self.assertEqual(runtime.record_imported_pivot(self.run_id, self.record(1))["status"], "applied")


if __name__ == "__main__":
    unittest.main()
