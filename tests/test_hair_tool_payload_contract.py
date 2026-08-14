import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "addons"
    / "send2ue"
    / "dependencies"
    / "unreal.py"
)


class FakeClass:
    def get_name(self):
        return "SkeletalMesh"


class FakeNaniteSettings:
    def __init__(self, enabled=False):
        self.enabled = enabled

    def get_editor_property(self, name):
        if name == "enabled":
            return self.enabled
        raise KeyError(name)

    def set_editor_property(self, name, value):
        if name == "enabled":
            self.enabled = bool(value)
            return
        raise KeyError(name)


class FakeAsset:
    def __init__(self):
        self.nanite_settings = FakeNaniteSettings()
        self.notified = False

    def get_class(self):
        return FakeClass()

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


class AuditLibrary:
    stream_payload = {}
    channel_payload = {}

    @classmethod
    def audit_skeletal_mesh_lod0_streams(cls, _path):
        return json.dumps(cls.stream_payload)

    @classmethod
    def dump_skeletal_mesh_lod_vertex_color_stats(cls, _path, _lod):
        return json.dumps(cls.channel_payload)


fake_unreal = types.ModuleType("unreal")
fake_unreal.CodexMaterialToolsLibrary = AuditLibrary
fake_unreal.CodexGraphDumpToolsLibrary = AuditLibrary
fake_unreal.load_asset = lambda _path: FakeAsset()
fake_unreal.log = lambda _message: None
fake_unreal.warning_messages = []
fake_unreal.log_warning = fake_unreal.warning_messages.append
previous_unreal_module = sys.modules.get("unreal")
sys.modules["unreal"] = fake_unreal

spec = importlib.util.spec_from_file_location("send2ue_unreal_dependency", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if previous_unreal_module is None:
    sys.modules.pop("unreal", None)
else:
    sys.modules["unreal"] = previous_unreal_module


class TestHairToolPayloadContract(unittest.TestCase):
    def setUp(self):
        self.asset = FakeAsset()
        fake_unreal.load_asset = lambda _path: self.asset
        self.importer = object.__new__(module.UnrealImportAsset)
        self.importer._asset_data = {
            "asset_path": "/Game/Test/SK_Hair",
            "_hair_tool_payload": {
                "version": 1,
                "encoding": "RFAOS_TAGGED_UV",
                "uv_rg_index": 2,
                "uv_ba_index": 3,
                "uv_tag": 2.0,
                "material_master": "/Game/Material/HairTool/Master/M_HT_HairCards",
            },
        }
        fake_unreal.warning_messages.clear()
        AuditLibrary.stream_payload = {"uv_channel_count": 4}
        AuditLibrary.channel_payload = {
            "sections": [
                {
                    "section_index": 0,
                    "uvs": [
                        {
                            "uv_index": 2,
                            "u": {"min": 2.0, "max": 3.0},
                            "v": {"min": 0.0, "max": 1.0},
                        },
                        {
                            "uv_index": 3,
                            "u": {"min": 2.0, "max": 3.0},
                            "v": {"min": 0.0, "max": 1.0},
                        },
                    ],
                }
            ]
        }

    def test_enables_skeletal_nanite_after_import(self):
        self.importer.ensure_hair_tool_nanite(["/Game/Test/SK_Hair"])
        self.assertTrue(self.asset.nanite_settings.enabled)
        self.assertTrue(self.asset.notified)

    def test_accepts_tagged_uv2_uv3_payload(self):
        self.importer.audit_hair_tool_payload(["/Game/Test/SK_Hair"])
        self.assertEqual(fake_unreal.warning_messages, [])

    def test_warns_without_failing_when_uv3_payload_is_missing(self):
        AuditLibrary.stream_payload = {"uv_channel_count": 3}
        AuditLibrary.channel_payload["sections"][0]["uvs"] = [
            AuditLibrary.channel_payload["sections"][0]["uvs"][0]
        ]
        self.importer.audit_hair_tool_payload(["/Game/Test/SK_Hair"])
        self.assertTrue(
            any("expected at least 4 UV channels" in item for item in fake_unreal.warning_messages)
        )

    def test_warns_without_failing_for_constant_ao(self):
        ao = AuditLibrary.channel_payload["sections"][0]["uvs"][1]["u"]
        ao["min"] = 3.0
        ao["max"] = 3.0
        self.importer.audit_hair_tool_payload(["/Game/Test/SK_Hair"])
        self.assertTrue(any("AO is constant" in item for item in fake_unreal.warning_messages))


if __name__ == "__main__":
    unittest.main()
