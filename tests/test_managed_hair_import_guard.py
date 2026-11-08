"""Exercise the actual importer's admission/finally paths without either app."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid

SOURCE = Path(__file__).resolve().parents[1]/'src/addons/send2ue/dependencies/unreal.py'
MESH = '/Game/Hair/Back_GuideSim'
CONTENT = 'a'*64


def importer_class(unreal):
    tree = ast.parse(SOURCE.read_text('utf8'))
    original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'UnrealImportAsset')
    names = {'_plugin_json', '_begin_managed_hair_import', '_end_managed_hair_import', 'run_import'}
    methods = [n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in names]
    cls = ast.ClassDef(name='ImporterUnderTest', bases=[], keywords=[], body=methods, decorator_list=[])
    namespace = {'unreal': unreal, 'json': json, 'uuid': uuid}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(SOURCE), 'exec'), namespace)
    return namespace['ImporterUnderTest']


class Guard:
    """Model the native owner/path contract, retaining preexisting manual work."""
    def __init__(self, calls):
        self.calls = calls
        self.active = {}
        self.pending = set()
        self.suppressed = []
        self.ordinary = []
        self.end_failure = False

    def begin_managed_hair_mesh_import(self, path, request, content, role):
        self.calls.append(('begin', path, request, content, role))
        if path in self.pending or request in self.active or path in self.active.values():
            return json.dumps({'success': False, 'guard_version': 1, 'state': 'busy'})
        self.active[request] = path
        return json.dumps({'success': True, 'guard_version': 1, 'state': 'active',
            'request_id': request, 'mesh_asset_path': path, 'content_id': content, 'role': role})

    def end_managed_hair_mesh_import(self, path, request, success):
        self.calls.append(('end', path, request, success))
        if self.end_failure or self.active.get(request) != path:
            return json.dumps({'success': False, 'guard_version': 1, 'state': 'end_incomplete', 'recovery_required': True})
        del self.active[request]
        return json.dumps({'success': True, 'guard_version': 1, 'state': 'ended',
            'request_id': request, 'mesh_asset_path': path, 'mesh_ready': True, 'import_succeeded': success})

    def read_managed_hair_mesh_imports(self):
        return json.dumps({'success': True, 'guard_version': 1, 'active': self.active})

    def notify(self, path):
        (self.suppressed if path in self.active.values() else self.ordinary).append(path)


class ManagedHairImportTests(unittest.TestCase):
    def setup_importer(self, *, marker=True, role='sim', paths=None, import_failure=False,
                       missing_api=False, completion_failure=False, completion_pending=False):
        self.calls, self.logs = [], []
        self.guard = Guard(self.calls)
        def imported(tasks):
            self.calls.append(('import',))
            if marker:
                self.assertFalse(tasks[0].async_)
            self.guard.notify(MESH)
            self.guard.notify('/Game/Body/OriginalBody')
            if import_failure:
                raise ValueError('factory failed after a partial task')
        def wait():
            self.calls.append(('wait_objects',))
            # The task's completion notification is still inside this owner's
            # scope, even after import_asset_tasks has returned or raised.
            self.guard.notify(MESH)
            if completion_failure:
                raise RuntimeError('native results unavailable')
            return []
        task = SimpleNamespace(async_=True, get_objects=wait,
            is_async_import_complete=lambda: not completion_pending,
            get_editor_property=lambda _: [MESH+'.Back_GuideSim'] if paths is None else paths)
        unreal = SimpleNamespace(AssetToolsHelpers=SimpleNamespace(get_asset_tools=lambda: SimpleNamespace(import_asset_tasks=imported)),
            log=self.logs.append)
        if not missing_api:
            unreal.CodexClothToolsLibrary = self.guard
        cls = importer_class(unreal)
        instance = cls()
        instance._asset_data = {'asset_path': MESH, '_asset_type': 'SkeletalMesh'}
        if marker:
            instance._asset_data['_hair_guide_cloth'] = {'package': CONTENT, 'role': role}
        instance._import_task, instance._options = task, object()
        instance.ensure_hair_tool_uv_precision = lambda _: self.calls.append(('uv',))
        instance.ensure_hair_tool_nanite = lambda _: self.calls.append(('nanite',))
        instance.audit_hair_tool_payload = lambda _: self.calls.append(('audit',))
        return instance

    def test_sim_and_render_hold_owner_through_postprocessing_and_task_completion(self):
        for role in ('sim', 'render'):
            with self.subTest(role=role):
                importer = self.setup_importer(role=role)
                self.assertEqual(importer.run_import(), [MESH+'.Back_GuideSim'])
                self.assertEqual([c[0] for c in self.calls], ['begin', 'import', 'uv', 'nanite', 'audit', 'wait_objects', 'end'])
                self.assertFalse(self.guard.active)
                self.assertEqual(self.guard.suppressed, [MESH, MESH])
                self.assertEqual(self.guard.ordinary, ['/Game/Body/OriginalBody'])
                self.assertTrue(self.calls[-1][-1])

    def test_ordinary_import_never_requires_the_new_native_api(self):
        importer = self.setup_importer(marker=False, missing_api=True)
        importer.run_import()
        self.assertEqual([c[0] for c in self.calls], ['import', 'uv', 'nanite', 'audit'])
        self.assertTrue(importer._import_task.async_)
        self.assertEqual(self.guard.ordinary, [MESH, '/Game/Body/OriginalBody'])

    def test_old_dll_is_rejected_before_import(self):
        importer = self.setup_importer(missing_api=True)
        with self.assertRaisesRegex(RuntimeError, 'guard is not loaded'):
            importer.run_import()
        self.assertFalse(self.calls)

    def test_pending_manual_refresh_is_preserved_and_no_import_or_end_runs(self):
        importer = self.setup_importer()
        self.guard.pending.add(MESH)
        with self.assertRaisesRegex(RuntimeError, 'admission failed'):
            importer.run_import()
        self.assertEqual([c[0] for c in self.calls], ['begin'])
        self.assertEqual(self.guard.pending, {MESH})

    def test_marker_validation_fails_before_any_native_call(self):
        cases = (None, {}, {'package': CONTENT, 'role': 'body'},
            {'package': 'not-a-content-id', 'role': 'sim'},
            {'package': CONTENT, 'role': 'sim', 'extra': True})
        for marker in cases:
            with self.subTest(marker=marker):
                importer = self.setup_importer()
                importer._asset_data['_hair_guide_cloth'] = marker
                with self.assertRaises(RuntimeError):
                    importer.run_import()
                self.assertFalse(self.calls)

    def test_marker_on_non_skeletal_target_is_rejected(self):
        importer = self.setup_importer()
        importer._asset_data['_asset_type'] = 'Skeleton'
        with self.assertRaisesRegex(RuntimeError, 'marker'):
            importer.run_import()
        self.assertFalse(self.calls)

    def test_separate_lod_rpc_is_explicitly_unsupported_before_import(self):
        importer = self.setup_importer()
        importer._asset_data['lods'] = {'1': 'later.fbx'}
        with self.assertRaisesRegex(RuntimeError, 'separate LOD RPCs require explicit owner propagation'):
            importer.run_import()
        self.assertFalse(self.calls)

    def test_import_exception_still_waits_and_ends_matching_owner_as_failed(self):
        importer = self.setup_importer(import_failure=True)
        with self.assertRaisesRegex(ValueError, 'factory failed'):
            importer.run_import()
        self.assertEqual([c[0] for c in self.calls], ['begin', 'import', 'wait_objects', 'end'])
        self.assertEqual(self.calls[0][2], self.calls[-1][2])
        self.assertIs(self.calls[-1][-1], False)
        self.assertFalse(self.guard.active)
        self.assertEqual(self.guard.suppressed, [MESH, MESH])

    def test_post_import_validation_error_keeps_guard_until_cleanup(self):
        importer = self.setup_importer()
        def reject(_):
            raise ValueError('payload mismatch')
        importer.audit_hair_tool_payload = reject
        with self.assertRaisesRegex(ValueError, 'payload mismatch'):
            importer.run_import()
        self.assertEqual([c[0] for c in self.calls][-2:], ['wait_objects', 'end'])
        self.assertIs(self.calls[-1][-1], False)
        self.assertFalse(self.guard.active)

    def test_wrong_or_empty_import_result_is_not_success_and_scope_still_ends(self):
        for paths in ([], ['/Game/Other/Other.Other']):
            with self.subTest(paths=paths):
                importer = self.setup_importer(paths=paths)
                with self.assertRaisesRegex(RuntimeError, 'exact mesh'):
                    importer.run_import()
                self.assertIs(self.calls[-1][-1], False)
                self.assertFalse(self.guard.active)

    def test_completion_failure_or_pending_result_retains_scope_for_recovery(self):
        for options in ({'completion_failure': True}, {'completion_pending': True}):
            with self.subTest(options=options):
                importer = self.setup_importer(**options)
                with self.assertRaisesRegex(RuntimeError, 'scope retained'):
                    importer.run_import()
                self.assertTrue(self.guard.active)
                self.assertNotIn('end', [c[0] for c in self.calls])
                self.assertEqual(importer._managed_hair_import_receipts[-1]['stage'], 'completion_unproven')

    def test_native_end_failure_is_not_a_success_or_automatic_retry(self):
        importer = self.setup_importer()
        self.guard.end_failure = True
        with self.assertRaisesRegex(RuntimeError, 'could not end safely'):
            importer.run_import()
        self.assertTrue(self.guard.active)
        self.assertEqual([c[0] for c in self.calls].count('begin'), 1)
        self.assertEqual([c[0] for c in self.calls].count('end'), 1)

    def test_import_and_cleanup_errors_both_survive(self):
        importer = self.setup_importer(import_failure=True)
        self.guard.end_failure = True
        with self.assertRaisesRegex(RuntimeError, 'factory failed.*cleanup also requires recovery'):
            importer.run_import()
        self.assertTrue(self.guard.active)

    def test_each_call_has_a_new_uuid_and_logs_the_same_owner_in_begin_and_end(self):
        importer = self.setup_importer()
        importer.run_import()
        first = self.calls[0][2]
        importer.run_import()
        owners = [c[2] for c in self.calls if c[0] == 'begin']
        self.assertEqual(len(set(owners)), 2)
        self.assertEqual(str(uuid.UUID(first)), first)
        receipts = [json.loads(s.partition(':')[2]) for s in self.logs]
        self.assertEqual(receipts[0]['native']['request_id'], receipts[1]['native']['request_id'])


if __name__ == '__main__':
    unittest.main()
