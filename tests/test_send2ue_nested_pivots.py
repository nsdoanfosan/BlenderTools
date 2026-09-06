"""Regression coverage for the narrow nested export-pivot boundary."""

import ast
from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys
import types
import unittest


ADDON = Path(__file__).resolve().parents[1] / 'src' / 'addons' / 'send2ue'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PIVOTS = load_module('tested_nested_pivots', ADDON / 'core' / 'nested_pivots.py')
BEVEL = load_module('tested_bevel_modifier_export', ADDON / 'core' / 'bevel_modifier_export.py')


class Object:
    def __init__(self, name, object_type='EMPTY', parent=None, collection=None):
        self.name = name
        self.type = object_type
        self.parent = parent
        self.children = []
        self.users_collection = [collection] if collection is not None else []
        self.instance_type = 'NONE'
        self.instance_collection = None
        self.active_shape_key = None
        self.modifiers = []
        self.selected = False
        if parent:
            parent.children.append(self)

    def select_set(self, value):
        self.selected = value


class TestNestedPivotBoundary(unittest.TestCase):
    def setUp(self):
        self.export = object()
        self.root = Object('window_wood_single_02', collection=self.export)
        self.frame = Object('window_02014', 'MESH', self.root, self.export)
        self.frame_two = Object('window_02014.001', 'MESH', self.root, self.export)
        self.glass = Object('window_wood_single_02_glass', parent=self.root, collection=self.export)
        self.glass_mesh = Object('window_02008', 'MESH', self.glass, self.export)
        self.glass_two = Object('window_02013', 'MESH', self.glass, self.export)

    def test_actual_window_units_have_one_common_root(self):
        self.assertIs(PIVOTS.get_assembly_root(self.root, self.export), self.root)
        self.assertIs(PIVOTS.get_assembly_root(self.glass, self.export), self.root)
        self.assertEqual(list(PIVOTS.iter_assembly_pivots(self.root, self.export)), [self.root, self.glass])

    def test_glass_geometry_and_helpers_never_enter_root_selection(self):
        collision = Object('UCX_window_02008_00', 'MESH', self.glass_mesh, self.export)
        selected = [self.frame, self.frame_two, self.glass_mesh, self.glass_two, collision]
        for obj in selected:
            obj.select_set(True)
        PIVOTS.prune_nested_pivot_selection(self.root, self.export, selected)
        self.assertEqual([obj.name for obj in selected if obj.selected], [self.frame.name, self.frame_two.name])
        self.assertEqual(PIVOTS.nested_pivot_descendants(self.glass, self.export), set())

    def test_arbitrary_depth_preserves_each_export_unit(self):
        latch = Object('window_latch', parent=self.glass, collection=self.export)
        latch_mesh = Object('latch_visual', 'MESH', latch, self.export)
        self.assertIs(PIVOTS.get_assembly_root(latch, self.export), self.root)
        self.assertIn(latch_mesh, PIVOTS.nested_pivot_descendants(self.root, self.export))
        self.assertEqual(PIVOTS.nested_pivot_descendants(self.glass, self.export), {latch, latch_mesh})
        self.assertEqual(list(PIVOTS.iter_assembly_pivots(self.root, self.export)), [self.root, self.glass, latch])

    def test_standalone_asset_preserves_legacy_selection(self):
        self.root.children.remove(self.glass)
        self.glass.parent = None
        self.assertIsNone(PIVOTS.get_assembly_root(self.root, self.export))
        self.assertEqual(PIVOTS.nested_pivot_descendants(self.root, self.export), set())

    def test_non_export_child_empty_does_not_activate(self):
        self.glass.users_collection = []
        self.assertIsNone(PIVOTS.get_assembly_root(self.root, self.export))
        self.assertEqual(PIVOTS.nested_pivot_descendants(self.root, self.export), set())

    def test_nested_work_collection_is_not_explicit_export_membership(self):
        self.glass.users_collection = [object()]
        self.assertFalse(PIVOTS.is_export_pivot(self.glass, self.export))

    def test_both_pivots_require_their_own_direct_export_meshes(self):
        self.frame.users_collection = []
        self.frame_two.users_collection = []
        self.assertIsNone(PIVOTS.get_assembly_root(self.glass, self.export))
        self.assertEqual(PIVOTS.nested_pivot_descendants(self.root, self.export), set())

    def test_gpro_collection_instances_are_never_assembly_pivots(self):
        for field, value in [('instance_collection', object()), ('instance_type', 'COLLECTION')]:
            with self.subTest(field=field):
                original = getattr(self.glass, field)
                setattr(self.glass, field, value)
                self.assertIsNone(PIVOTS.get_assembly_root(self.root, self.export))
                self.assertEqual(PIVOTS.nested_pivot_descendants(self.root, self.export), set())
                setattr(self.glass, field, original)

    def test_collision_socket_and_skeletal_children_do_not_create_units(self):
        for prefix in ('UCX_', 'UBX_', 'UCP_', 'USP_', 'SOCKET_'):
            with self.subTest(prefix=prefix):
                self.glass_mesh.name = prefix + 'one'
                self.glass_two.name = prefix + 'two'
                self.assertFalse(PIVOTS.is_export_pivot(self.glass, self.export))
        self.glass_mesh.name = 'glass_one'
        self.glass_two.name = 'glass_two'
        self.glass_mesh.active_shape_key = object()
        self.glass_two.modifiers = [types.SimpleNamespace(type='ARMATURE', object=object())]
        self.assertFalse(PIVOTS.is_export_pivot(self.glass, self.export))

    def test_socket_empty_and_armature_crossing_do_not_activate(self):
        self.glass.name = 'SOCKET_glass'
        self.assertIsNone(PIVOTS.get_assembly_root(self.root, self.export))
        self.glass.name = 'glass'
        self.root.type = 'ARMATURE'
        self.assertIsNone(PIVOTS.get_assembly_root(self.glass, self.export))

    def test_export_pivot_pair_under_any_armature_ancestor_remains_legacy(self):
        rig = Object('existing_rig', 'ARMATURE', collection=self.export)
        helper = Object('ordinary_rig_helper', parent=rig)
        for parent in [rig, helper]:
            with self.subTest(parent=parent.name):
                self.root.parent = parent
                self.assertIsNone(PIVOTS.get_assembly_root(self.root, self.export))
                self.assertIsNone(PIVOTS.get_assembly_root(self.glass, self.export))
                self.assertEqual(PIVOTS.nested_pivot_descendants(self.root, self.export), set())

    def _load_extension(self):
        objects = [self.root, self.frame, self.frame_two, self.glass, self.glass_mesh, self.glass_two]
        test_case = self

        class Context:
            @property
            def selected_objects(self):
                return [obj for obj in objects if obj.selected]

        bpy = types.ModuleType('bpy')
        bpy.props = types.SimpleNamespace(EnumProperty=lambda **kwargs: None)
        bpy.data = types.SimpleNamespace(
            objects={obj.name: obj for obj in objects}, collections={'Export': self.export}
        )
        bpy.context = Context()
        utilities = types.ModuleType('send2ue.core.utilities')
        calls = []

        def select_all_children(parent, *_args, **_kwargs):
            calls.append(parent)
            for child in parent.children:
                if child.type == 'MESH':
                    child.select_set(True)
                select_descendants(child)

        def select_descendants(parent):
            for child in parent.children:
                if child.type == 'MESH':
                    child.select_set(True)
                select_descendants(child)

        utilities.select_all_children = select_all_children
        utilities.select_asset_collisions = lambda *_args: None
        # The unchanged real combine filter must still schedule one mesh per pivot.
        tree = ast.parse((ADDON / 'core' / 'utilities.py').read_text(encoding='utf-8'))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'get_unique_parent_mesh_objects')
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<unique-parent-filter>', 'exec'), utilities.__dict__)
        core = types.ModuleType('send2ue.core')
        core.utilities = utilities
        core.nested_pivots = PIVOTS
        extension = types.ModuleType('send2ue.core.extension')

        class ExtensionBase:
            def update_asset_data(self, update):
                test_case.asset_data.update(update)

        extension.ExtensionBase = ExtensionBase
        constants = types.ModuleType('send2ue.constants')
        constants.BlenderTypes = types.SimpleNamespace(MESH='MESH')
        constants.ToolInfo = types.SimpleNamespace(EXPORT_COLLECTION=types.SimpleNamespace(value='Export'))
        constants.UnrealTypes = types.SimpleNamespace(STATIC_MESH='StaticMesh')
        replacements = {
            'bpy': bpy, 'send2ue': types.ModuleType('send2ue'), 'send2ue.core': core,
            'send2ue.core.extension': extension, 'send2ue.constants': constants,
        }
        previous = {key: sys.modules.get(key) for key in replacements}
        sys.modules.update(replacements)
        try:
            module = load_module('tested_combine_extension', ADDON / 'resources' / 'extensions' / 'combine_assets.py')
        finally:
            for key, value in previous.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value
        return module, calls

    def test_combine_extension_exports_two_disjoint_named_meshes(self):
        module, _ = self._load_extension()
        extension = module.CombineAssetsExtension()
        extension.combine = 'child_meshes'
        _, filtered, _ = extension.filter_objects([], [self.frame, self.frame_two, self.glass_mesh, self.glass_two], [])
        self.assertEqual(filtered, [self.frame, self.glass_mesh])
        for representative, expected, pivot in [
            (self.frame, [self.frame, self.frame_two], self.root),
            (self.glass_mesh, [self.glass_mesh, self.glass_two], self.glass),
        ]:
            for obj in [self.frame, self.frame_two, self.glass_mesh, self.glass_two]:
                obj.selected = False
            self.asset_data = {
                '_asset_type': 'StaticMesh', '_mesh_object_name': representative.name,
                'file_path': '/tmp/unit.fbx', 'asset_folder': '/Game/Meshes/',
            }
            extension.pre_mesh_export(self.asset_data, None)
            self.assertEqual(module.bpy.context.selected_objects, expected)
            self.assertEqual(self.asset_data['asset_path'], '/Game/Meshes/' + pivot.name)
            self.assertEqual(self.asset_data['empty_object_name'], pivot.name)
            self.assertTrue(self.asset_data['_nested_pivot_origin'])

    def test_legacy_off_skeletal_and_instancer_modes_retain_behavior(self):
        module, calls = self._load_extension()
        extension = module.CombineAssetsExtension()
        self.asset_data = {
            '_asset_type': 'StaticMesh', '_mesh_object_name': self.frame.name,
            'file_path': '/tmp/unit.fbx', 'asset_folder': '/Game/Meshes/',
        }
        extension.combine = 'off'
        extension.pre_mesh_export(self.asset_data, None)
        self.assertEqual(calls, [])
        self.assertNotIn('_nested_pivot_origin', self.asset_data)
        extension.combine = 'child_meshes'
        self.glass.instance_collection = object()
        extension.pre_mesh_export(self.asset_data, None)
        self.assertEqual(module.bpy.context.selected_objects, [self.frame, self.frame_two, self.glass_mesh, self.glass_two])
        self.assertNotIn('_nested_pivot_origin', self.asset_data)
        self.glass.instance_collection = None
        self.asset_data['_asset_type'] = 'SkeletalMesh'
        extension.pre_mesh_export(self.asset_data, None)
        self.assertNotIn('_nested_pivot_origin', self.asset_data)

    def test_final_export_selection_rejects_child_collider_reselected_after_hooks(self):
        # A naming mistake must never make the child collider part of the
        # parent's mesh. The boundary also retains it when its owning unit
        # selected it explicitly, without changing legacy collision naming.
        collider = Object('UCX_window_02014_00', 'MESH', self.glass_mesh, self.export)
        all_objects = [self.root, self.frame, self.frame_two, self.glass, self.glass_mesh, self.glass_two, collider]
        tree = ast.parse((ADDON / 'core' / 'export.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'export_mesh')
        function.decorator_list = []
        code = compile(ast.Module(body=[function], type_ignores=[]), '<actual-export-mesh>', 'exec')

        for pivot, representative, expected_meshes, assembly in [
            (self.root, self.frame, [self.frame, self.frame_two], True),
            (self.glass, self.glass_mesh, [self.glass_mesh, self.glass_two, collider], True),
            (self.root, self.frame, [self.frame, self.frame_two, collider], False),
        ]:
            with self.subTest(pivot=pivot.name, assembly=assembly):
                class Context:
                    @property
                    def selected_objects(self):
                        return [obj for obj in all_objects if obj.selected]

                asset_data = {'empty_object_name': pivot.name}
                if assembly:
                    asset_data['_nested_pivot_origin'] = True
                context = Context()
                context.evaluated_depsgraph_get = lambda: types.SimpleNamespace(update=lambda: None)
                context.window_manager = types.SimpleNamespace(send2ue=types.SimpleNamespace(asset_data={'unit': asset_data}))
                bpy = types.SimpleNamespace(context=context, data=types.SimpleNamespace(
                    objects={obj.name: obj for obj in all_objects}, collections={'Export': self.export},
                ))
                exported = []
                phases = []

                def run_extension_tasks(task):
                    if task == 'pre':
                        for obj in pivot.children:
                            if obj.type == 'MESH':
                                obj.select_set(True)
                        if pivot == self.glass:
                            collider.select_set(True)
                        phases.append('pre')

                def select_collisions(*_args):
                    self.assertEqual(phases, ['pre'])
                    collider.select_set(True)
                    phases.append('collisions')

                utilities = types.SimpleNamespace(
                    deselect_all_objects=lambda: [obj.select_set(False) for obj in all_objects],
                    get_asset_name=lambda name, _properties: name,
                    select_asset_collisions=select_collisions,
                    disable_particles=lambda *_args: {}, restore_particles=lambda *_args: None,
                    get_mesh_unreal_type=lambda *_args: 'StaticMesh',
                )
                namespace = {
                    'bpy': bpy, 'utilities': utilities, 'nested_pivots': PIVOTS,
                    'bevel_modifier_export': BEVEL,
                    'extension': types.SimpleNamespace(run_extension_tasks=run_extension_tasks),
                    'ExtensionTasks': types.SimpleNamespace(
                        PRE_MESH_EXPORT=types.SimpleNamespace(value='pre'),
                        POST_MESH_EXPORT=types.SimpleNamespace(value='post'),
                    ),
                    'UnrealTypes': types.SimpleNamespace(STATIC_MESH='StaticMesh'),
                    'ToolInfo': types.SimpleNamespace(EXPORT_COLLECTION=types.SimpleNamespace(value='Export')),
                    'set_parent_rig_selection': lambda *_args: None,
                    'realize_selected_geometry_node_instances': nullcontext,
                    'compensate_negative_scale_winding': lambda **_kwargs: nullcontext(),
                    'export_file': lambda *_args: exported.extend(context.selected_objects),
                }
                exec(code, namespace)
                namespace['export_mesh']('unit', representative, None)
                self.assertEqual(exported, expected_meshes)


class TestNestedPivotFbxOrigin(unittest.TestCase):
    def test_only_assembly_flag_overrides_disabled_global_origin(self):
        class Matrix:
            def __init__(self, location):
                self.location = location

            def to_translation(self):
                return self.location

            def __matmul__(self, other):
                return [value * 100 for value in other]

        for filename in ('fbx_b3.py', 'fbx_b4.py'):
            tree = ast.parse((ADDON / 'core' / 'io' / filename).read_text(encoding='utf-8'))
            function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'fbx_data_object_elements')
            branch = next(
                node for node in ast.walk(function)
                if isinstance(node, ast.If)
                and ast.unparse(node.test) == "ob_obj.type == 'MESH'"
            )
            code = compile(ast.Module(body=branch.body, type_ignores=[]), filename, 'exec')
            for enabled in (False, True):
                with self.subTest(filename=filename, assembly=enabled):
                    asset_data = {
                        '_asset_type': 'StaticMesh', '_mesh_object_name': 'visual',
                        'empty_object_name': 'pivot',
                    }
                    if enabled:
                        asset_data['_nested_pivot_origin'] = True
                    bpy = types.SimpleNamespace(
                        context=types.SimpleNamespace(
                            scene=types.SimpleNamespace(send2ue=types.SimpleNamespace(use_object_origin=False)),
                            window_manager=types.SimpleNamespace(send2ue=types.SimpleNamespace(asset_id='unit', asset_data={'unit': asset_data})),
                        ),
                        data=types.SimpleNamespace(objects={
                            'visual': types.SimpleNamespace(name='visual', matrix_world=Matrix([11, 22, 33])),
                            'pivot': types.SimpleNamespace(matrix_world=Matrix([10, 20, 30])),
                        }),
                    )
                    namespace = {
                        'bpy': bpy, 'Vector': list, 'SCALE_FACTOR': 100,
                        'loc': [1100, 2200, 3300],
                        'ob_obj': types.SimpleNamespace(name='visual'),
                        'scene_data': types.SimpleNamespace(settings=types.SimpleNamespace(global_matrix=Matrix(None))),
                    }
                    exec(code, namespace)
                    self.assertEqual(namespace['loc'], [100, 200, 300] if enabled else [1100, 2200, 3300])
                    self.assertFalse(bpy.context.scene.send2ue.use_object_origin)


if __name__ == '__main__':
    unittest.main()
