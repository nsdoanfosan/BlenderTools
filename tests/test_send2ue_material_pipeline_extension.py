import importlib.util
from pathlib import Path
import sys
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


if __name__ == "__main__":
    unittest.main()
