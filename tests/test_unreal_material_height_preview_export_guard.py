import ast
from pathlib import Path
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "addons"
    / "send2ue"
    / "core"
    / "hair_tool_export.py"
)
OPERATORS_PATH = MODULE_PATH.parents[1] / "operators.py"


class TestUnrealMaterialHeightPreviewExportGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MODULE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def function_source(self, name):
        function = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        return ast.get_source_segment(self.source, function)

    def test_preview_is_suspended_before_export_sources_are_discovered(self):
        prepare = self.function_source("prepare")
        self.assertLess(
            prepare.index("suspend_height_previews"),
            prepare.index("_final_export_sources"),
        )
        self.assertIn("MATERIAL_HEIGHT_PREVIEW_RESTORE_KEY", prepare)
        self.assertNotIn("if height_preview_states:", prepare)

    def test_preview_state_is_restored_after_send_to_unreal(self):
        restore = self.function_source("restore_bridge_previews")
        self.assertIn("restore_height_previews", restore)
        self.assertIn("MATERIAL_HEIGHT_PREVIEW_RESTORE_KEY", restore)

    def test_render_flag_is_not_the_only_export_protection(self):
        prepare = self.function_source("prepare")
        self.assertIn("suspend_height_previews", prepare)
        self.assertNotIn("show_render", prepare)

    def test_send_to_unreal_operation_runs_both_guard_sides(self):
        operators = OPERATORS_PATH.read_text(encoding="utf-8")
        self.assertIn("hair_tool_export.prepare()", operators)
        self.assertIn("hair_tool_export.restore_bridge_previews()", operators)
        self.assertLess(
            operators.index("hair_tool_export.cleanup()"),
            operators.index("hair_tool_export.restore_bridge_previews()"),
        )


if __name__ == "__main__":
    unittest.main()
