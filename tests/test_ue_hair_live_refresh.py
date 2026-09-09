"""Stateful scene seam: asset reload must change the existing simulation proxy."""
import importlib.util
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

path = Path(__file__).parents[1]/'src/addons/send2ue/resources/pipeline/ue_hair_guide_cloth.py'
spec = importlib.util.spec_from_file_location('live_refresh_test_module', path)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


class Asset:
    def __init__(self, name, owner='', generation=1):
        self.name, self.owner, self.generation = name, owner, generation
    def get_path_name(self): return self.name + '.' + self.name.rsplit('/', 1)[1]


class Renderer:
    @classmethod
    def static_class(cls): return cls
    def __init__(self, actor, name, asset=None):
        self.actor, self.name, self.asset = actor, name, asset
        self.props = dict(component_tags=[], visible=True, hidden_in_game=False,
                          leader_pose_component='body', visibility_based_anim_tick_option='always',
                          forced_lod_model=1, override_materials=['user_hair_material'])
        self.parent, self.transform = 'body', ('offset', 1, 2, 3)
        actor.components.append(self)
    def get_name(self): return self.name
    def get_owner(self): return self.actor
    def get_path_name(self): return self.actor.name + ':' + self.name
    def get_editor_property(self, key): return self.props[key]
    def set_editor_property(self, key, value, **kwargs):
        if key == 'creation_method': raise AttributeError('Protected native property')
        self.props[key] = value
        if key == 'component_tags': self.props['visible'] = True
    def set_visibility(self, value, children): self.props['visible'] = value
    def set_hidden_in_game(self, value, children): self.props['hidden_in_game'] = value
    def get_skinned_asset(self): return self.asset
    def get_attach_parent(self): return self.parent
    def get_attach_socket_name(self): return 'head_socket'
    def get_relative_transform(self): return self.transform
    def attach_to_component(self, parent, *args): self.parent = parent
    def set_relative_transform(self, transform, *args): self.transform = transform
    def set_leader_pose_component(self, leader): self.props['leader_pose_component'] = leader
    def set_skeletal_mesh_asset(self, asset):
        if self.asset != asset: self.props['override_materials'] = []
        self.asset = asset
    def call_method(self, method, args):
        assert method == 'K2_DestroyComponent'
        self.actor.components.remove(self)
        if self in self.actor.instances: self.actor.instances.remove(self)


class Cloth(Renderer):
    def __init__(self, *args):
        super().__init__(*args)
        self.enabled, self.suspended = True, False
        self.config_generation = self.proxy_generation = self.asset.generation
        self.rebuilds = 0
    def get_editor_property(self, key):
        if key == 'enable_simulation': return self.enabled
        if key == 'suspend_simulation': return self.suspended
        return super().get_editor_property(key)
    def get_asset(self): return self.asset
    def set_asset(self, asset): self.asset = asset  # Same-identity set deliberately cannot refresh the proxy.
    def reset_config_properties(self): self.config_generation = self.asset.generation
    def recreate_cloth_simulation_proxy(self):
        self.proxy_generation = self.config_generation
        self.rebuilds += 1
    def is_simulation_enabled(self): return self.enabled
    def is_simulation_suspended(self): return self.suspended
    def set_enable_simulation(self, value): self.enabled = value
    @property
    def suspend_simulation(self): return self.suspended  # UE 5.8 reflected bool shadows method.
    def call_method(self, method, args):
        if method == 'SuspendSimulation': self.suspended = True
        elif method == 'ResumeSimulation': self.suspended = False
        else: return super().call_method(method, args)


class Actor:
    def __init__(self, name): self.name, self.components, self.instances = name, [], []
    def modify(self): return True
    def get_editor_property(self, key):
        raise AttributeError('Protected native property: ' + key)
    def set_editor_property(self, key, value, **kwargs):
        raise AttributeError('Protected native property: ' + key)
    def get_components_by_class(self, cls): return [c for c in self.components if isinstance(c, cls)]
    def call_method(self, method, args):
        raise AssertionError('Helper must use native editor instance authoring')


class SceneReference:
    def __init__(self, name='body'): self.name = name
    def get_name(self): return self.name
    def get_owner(self): return None


class SubobjectSubsystem:
    def __init__(self): self.creations, self.on_create = 0, None
    def k2_gather_subobject_data_for_instance(self, actor): return [actor]
    def add_new_subobject(self, params):
        assert params.new_class is Renderer
        assert params.blueprint_context is None
        assert params.skip_mark_blueprint_modified
        assert not params.conform_transform_to_parent
        actor = params.parent_handle
        helper = Renderer(actor, 'generated_' + str(len(actor.components)))
        actor.instances.append(helper)  # Native subsystem's AddInstanceComponent behavior.
        if self.on_create: self.on_create(actor)
        self.creations += 1
        return helper, ''


class LiveRefreshTests(unittest.TestCase):
    def setUp(self):
        self.record = dict(render_asset_path='/Mount/Hair/SK_Render', simulation_enabled=True)
        self.owner = M.VERSION + ':' + self.record['render_asset_path']
        self.render = Asset(self.record['render_asset_path'])
        self.asset = Asset(M._target(self.record), self.owner)
        self.actor = Actor('/Map:Actor')
        self.cloth = Cloth(self.actor, 'hair', self.asset)
        self.other = Cloth(self.actor, 'other', Asset('/Mount/Hair/Other', 'foreign'))
        self.body = SceneReference()
        self.cloth.parent = self.body
        self.cloth.props['leader_pose_component'] = self.body
        self.subobjects = SubobjectSubsystem()
        self.unreal = SimpleNamespace(ChaosClothComponent=Cloth, SkeletalMeshComponent=Renderer,
            ActorComponent=Renderer, SubobjectDataSubsystem=SubobjectSubsystem,
            AddNewSubobjectParams=SimpleNamespace,
            SubobjectDataBlueprintFunctionLibrary=SimpleNamespace(get_data=lambda h: h,
                get_associated_object=lambda data: data, is_handle_valid=lambda h: h is not None),
            get_engine_subsystem=lambda cls: self.subobjects,
            EditorActorSubsystem=object, Transform=lambda: (), AttachmentRule=SimpleNamespace(KEEP_RELATIVE='keep'),
            get_editor_subsystem=lambda cls: SimpleNamespace(get_all_level_actors=lambda: [self.actor]),
            load_asset=lambda p: self.render,
            EditorAssetLibrary=SimpleNamespace(get_metadata_tag=lambda asset, tag: asset.owner))
        self.patch = patch.dict('sys.modules', {'unreal': self.unreal})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def refresh(self, enabled=True):
        self.record['simulation_enabled'] = enabled
        return M._refresh_live_components(self.record, self.asset if enabled else self.render)

    def test_two_same_object_reimports_propagate_new_generations_to_proxy(self):
        self.cloth.enabled, self.cloth.suspended = False, True
        original = (self.cloth.parent, self.cloth.transform, dict(self.cloth.props))
        for generation in (2, 3):
            self.asset.generation = generation  # Reload updated the UObject, not its live proxy.
            self.assertNotEqual(self.cloth.proxy_generation, generation)
            result = self.refresh()
            self.assertEqual(self.cloth.proxy_generation, generation)
            self.assertEqual(result[0]['status'], 'simulation_refreshed')
            self.assertFalse(self.cloth.enabled)
            self.assertTrue(self.cloth.suspended)
        self.assertEqual(self.cloth.rebuilds, 2)
        self.assertEqual((self.cloth.parent, self.cloth.transform, self.cloth.props), original)
        self.assertEqual(self.other.rebuilds, 0)

    def test_owner_metadata_matches_previous_output_path_without_matching_foreign_cloth(self):
        self.asset.name = '/Mount/Hair/PreviousOutput'
        self.refresh()
        self.assertEqual(self.cloth.rebuilds, 1)
        self.assertEqual(self.other.rebuilds, 0)

    def test_render_only_twice_then_sim_restores_user_renderer_visibility_and_cloth_flags(self):
        renderer = Renderer(self.actor, 'source_render', self.render)
        renderer.parent = self.body
        renderer.props['leader_pose_component'] = self.body
        renderer.props.update(visible=False, hidden_in_game=True)
        self.refresh(False)
        self.refresh(False)
        self.assertEqual(len(self.actor.components), 3)
        self.assertFalse(self.cloth.enabled)
        self.assertFalse(self.cloth.props['visible'])
        self.assertTrue(renderer.props['visible'])
        self.asset.generation = 2
        self.refresh(True)
        self.assertTrue(self.cloth.enabled)
        self.assertFalse(self.cloth.suspended)
        self.assertTrue(self.cloth.props['visible'])
        self.assertFalse(renderer.props['visible'])
        self.assertTrue(renderer.props['hidden_in_game'])
        self.assertEqual(self.cloth.props['component_tags'], [])
        self.assertEqual(self.cloth.proxy_generation, 2)

    def test_owned_helper_is_idempotent_and_only_helper_removed_on_return(self):
        self.refresh(False)
        helper = next(c for c in self.actor.components if type(c) is Renderer)
        self.assertEqual(helper.parent, self.cloth.parent)
        self.assertEqual(helper.transform, self.cloth.transform)
        self.assertEqual(helper.props['override_materials'], ['user_hair_material'])
        self.assertEqual(self.actor.instances, [helper])
        self.refresh(False)
        self.assertEqual(len(self.actor.components), 3)
        self.assertEqual(self.subobjects.creations, 1)
        self.refresh(True)
        self.assertEqual(self.actor.components, [self.cloth, self.other])
        self.assertEqual(self.actor.instances, [])

    def test_hidden_cloth_stays_hidden_after_return_from_render_only_tag_cleanup(self):
        self.cloth.props['visible'] = False
        self.refresh(False)
        self.refresh(True)
        self.assertFalse(self.cloth.props['visible'])
        self.assertEqual(self.actor.instances, [])

    def test_native_instance_creation_reacquires_reconstructed_cloth_parent_and_leader(self):
        body = Renderer(self.actor, 'body_component')
        self.cloth.parent = body
        self.cloth.props['leader_pose_component'] = body
        old_cloth = self.cloth
        replacements = {}
        def reconstruct(actor):
            for component in (body, old_cloth):
                replacement = copy.copy(component)
                replacement.props = copy.deepcopy(component.props)
                actor.components[actor.components.index(component)] = replacement
                replacements[component.name] = replacement
            current = replacements['hair']
            current.parent = replacements['body_component']
            current.props['leader_pose_component'] = replacements['body_component']
        self.subobjects.on_create = reconstruct
        self.refresh(False)
        current = replacements['hair']
        helper = self.actor.instances[0]
        self.assertTrue(old_cloth.enabled)
        self.assertFalse(current.enabled)
        self.assertIs(helper.parent, replacements['body_component'])
        self.assertIs(helper.props['leader_pose_component'], replacements['body_component'])
        self.assertEqual(helper.transform, old_cloth.transform)
        self.assertEqual(helper.props['override_materials'], ['user_hair_material'])
        self.asset.generation = 2
        self.refresh(True)
        self.assertTrue(current.enabled)
        self.assertEqual(current.proxy_generation, 2)
        self.assertEqual(self.actor.instances, [])


if __name__ == '__main__': unittest.main()
