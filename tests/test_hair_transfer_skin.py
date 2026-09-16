"""Explicit source transfer must work on rigs without a head bone."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).parents[1] / 'src/addons/send2ue/core/hair_tool_export.py'


class TransferSkinTests(unittest.TestCase):
    def setup_binding(self, transfer=True):
        tree = ast.parse(SOURCE.read_text(encoding='utf8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_bind_export_skin')
        rig = NS(data=NS(bones=[NS(name='Tail', use_deform=True)]))
        modifier = NS(type='ARMATURE', object=rig)
        obj = NS(ue_unique_transfer_weights=transfer,
                 vdt_object_props=NS(transfer_source=NS(name='Base')),
                 select_set=Mock(), modifiers=[modifier],
                 vertex_groups=[NS(index=0, name='Tail')],
                 data=NS(vertices=[NS(groups=[NS(group=0, weight=1.0)])]))
        original_active = object()
        context = NS(selected_objects=[], view_layer=NS(objects=NS(active=original_active)))
        native = Mock(return_value={'FINISHED'})
        bpy = NS(context=context, ops=NS(object=NS(select_all=Mock(), vdt_pointer_transfer_weights=native)))
        head = Mock(side_effect=AssertionError('Head lookup must not run for explicit transfer'))
        namespace = {'bpy': bpy, '_get_head_bone_name': head}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        return namespace['_bind_export_skin'], obj, rig, native, context, original_active, head

    def test_explicit_transfer_accepts_tail_rig_and_restores_selection_context(self):
        bind, obj, rig, native, context, original, head = self.setup_binding()
        bind(obj, rig)
        native.assert_called_once()
        head.assert_not_called()
        self.assertIs(context.view_layer.objects.active, original)

    def test_cancelled_transfer_never_falls_back_to_head(self):
        bind, obj, rig, native, context, original, head = self.setup_binding()
        native.return_value = {'CANCELLED'}
        with self.assertRaisesRegex(RuntimeError, 'did not finish'):
            bind(obj, rig)
        head.assert_not_called()
        self.assertIs(context.view_layer.objects.active, original)

    def test_missing_source_is_not_ignored(self):
        bind, obj, rig, native, *_ = self.setup_binding()
        obj.vdt_object_props.transfer_source = None
        with self.assertRaisesRegex(RuntimeError, 'source is missing'):
            bind(obj, rig)
        native.assert_not_called()

    def test_unweighted_transfer_result_is_rejected(self):
        bind, obj, rig, *_ = self.setup_binding()
        obj.data.vertices[0].groups[0].weight = 0.0
        with self.assertRaisesRegex(RuntimeError, 'unweighted'):
            bind(obj, rig)


if __name__ == '__main__':
    unittest.main()
