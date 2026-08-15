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


def load_pair_packer():
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_pack_unorm8_pair"
    )
    isolated = ast.Module(body=[function], type_ignores=[])
    namespace = {}
    exec(compile(isolated, str(MODULE_PATH), "exec"), namespace)
    return namespace["_pack_unorm8_pair"]


class TestHairToolUvPairMath(unittest.TestCase):
    def test_every_unorm8_pair_roundtrips_through_one_float(self):
        pack = load_pair_packer()
        for first_byte in range(256):
            for second_byte in range(256):
                packed = pack(first_byte / 255.0, second_byte / 255.0)
                combined = round(packed * 65535.0)
                self.assertEqual(combined // 256, first_byte)
                self.assertEqual(combined % 256, second_byte)


if __name__ == "__main__":
    unittest.main()
