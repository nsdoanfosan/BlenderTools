"""Native failure -> receipt -> extension -> operator, without production edits."""
import ast
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_native_coordination as native_fixture
from test_ue_hair_guide_cloth import M as PIPELINE

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('failure_cloth_receipt',
    ROOT / 'src/addons/send2ue/core/cloth_receipt.py')
RECEIPTS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RECEIPTS)


class FailureVisibilityTests(unittest.TestCase):
    def harness(self):
        operator = native_fixture.NativeOperatorTests(methodName='test_validation_no_jobs_never_completes')
        operator.setUp()
        self.addCleanup(operator.doCleanups)
        diagnostics = []
        record = {'render_asset_path': '/Game/Fixture/Render'}
        def remote(commands):
            receipt = PIPELINE.apply_hair_guide_cloth(record)
            return RECEIPTS.PREFIX + json.dumps(receipt)
        env = {'hair_guide_cloth': SimpleNamespace(import_record=lambda *args: record,
                   diagnostic=lambda *args: diagnostics.append(args)),
               'cloth_receipt': RECEIPTS, '_run_cloth_commands': remote,
               'PIPELINE_FILE': 'fixture.py', 'RECEIPT_PREFIX': RECEIPTS.PREFIX,
               'json': json}
        tree = ast.parse((ROOT / 'src/addons/send2ue/resources/extensions/hair_guide_cloth.py').read_text('utf-8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'post_import')
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<extension>', 'exec'), env)
        def imported_then_failed_cloth():
            operator.verified_import()
            env['post_import'](SimpleNamespace(enabled=True), {'asset_path': record['render_asset_path']}, None)
        operator.enqueue(imported_then_failed_cloth)
        return operator, diagnostics

    def test_execute_keeps_original_failure_and_never_completes_after_recovery(self):
        h, diagnostics = self.harness()
        output = io.StringIO()
        with patch.object(PIPELINE, '_apply_hair_guide_cloth', side_effect=ValueError('injected native G mismatch')), redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, 'Hair Guide Cloth import failed:.*injected native G mismatch'):
                h.op.execute(h.context)
        h.bridge.complete_handoff.assert_not_called()
        h.bridge.fail_handoff.assert_called_once()
        self.assertEqual(len(diagnostics), 1)
        self.assertIn('SEND2UE_HAIR_GUIDE_CLOTH_ERROR:', output.getvalue())
        self.assertIn('ValueError', output.getvalue())
        self.assertIn('Traceback', output.getvalue())

    def test_modal_reports_error_and_returns_cancelled_without_success(self):
        h, diagnostics = self.harness()
        with patch.object(PIPELINE, '_apply_hair_guide_cloth', side_effect=ValueError('injected native G mismatch')), redirect_stdout(io.StringIO()):
            h.op.invoke(h.context, SimpleNamespace())
            result = h.op.modal(h.context, SimpleNamespace(type='TIMER'))
        self.assertEqual(result, {'CANCELLED'})
        h.bridge.complete_handoff.assert_not_called()
        h.bridge.fail_handoff.assert_called_once()
        h.op.report.assert_called_once()
        self.assertEqual(h.op.report.call_args.args[0], {'ERROR'})
        self.assertIn('injected native G mismatch', h.op.report.call_args.args[1])
        self.assertEqual(len(diagnostics), 1)


if __name__ == '__main__':
    unittest.main()
