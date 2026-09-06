import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "src"
    / "addons"
    / "send2ue"
    / "resources"
    / "pipeline"
    / "prototype_identity.py"
)
SPEC = importlib.util.spec_from_file_location("send2ue_prototype_identity", MODULE_PATH)
IDENTITY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IDENTITY)
FIXTURE = Path(__file__).parent / "fixtures" / "prototype_identity_v1.json"


class PrototypeIdentityTests(unittest.TestCase):
    def test_cross_repo_golden_vector(self):
        vector = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(
            IDENTITY.identity_for_content(vector["content"]),
            vector["identity"],
        )
        self.assertEqual(
            IDENTITY.lineage_identity([vector["identity"]]),
            vector["single_member_lineage"],
        )
        IDENTITY.validate_pair(vector["identity"], vector["content"])
        IDENTITY.validate_lineage(
            vector["single_member_lineage"],
            [vector["identity"]],
        )

    def test_swapped_lineage_is_rejected(self):
        vector = json.loads(FIXTURE.read_text(encoding="utf-8"))
        other = dict(vector["identity"], digest="0" * 64)
        with self.assertRaises(ValueError):
            IDENTITY.validate_lineage(
                vector["single_member_lineage"],
                [other],
            )


if __name__ == "__main__":
    unittest.main()
