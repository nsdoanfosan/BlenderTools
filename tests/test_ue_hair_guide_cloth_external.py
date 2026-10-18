"""External Dataflow references must not turn Cloth duplication into graph edits.

This is an object-reference/persistence model, not a Chaos simulation. In
particular duplicate_asset(Cloth) deliberately RETAINS an external graph
reference. Only duplicate_asset(Dataflow) produces an independent external graph,
and saving a Cloth deliberately does NOT save that separate graph package.
"""
import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


PATH = Path(__file__).parents[1] / 'src/addons/send2ue/resources/pipeline/ue_hair_guide_cloth.py'
SPEC = importlib.util.spec_from_file_location('ue_hair_guide_cloth_external_test', PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def package_path(path):
    return path.split('.', 1)[0]


class Package:
    def __init__(self, path):
        self.path = path
        self.dirty = False

    def get_path_name(self):
        return self.path

    def get_name(self):
        return self.path.rsplit('/', 1)[-1]

    def get_outer(self):
        return None


class Object:
    def __init__(self, path, outer):
        self.path = path
        self.outer = outer

    def get_path_name(self):
        return self.path

    def get_name(self):
        return self.path.rsplit(':', 1)[-1].rsplit('.', 1)[-1]

    def get_outer(self):
        return self.outer

    def get_outermost(self):
        return self.get_package()

    def get_package(self):
        current = self
        while not isinstance(current, Package):
            current = current.outer
        return current

    def get_typed_outer(self, cls):
        current = self.outer
        while current is not None:
            if isinstance(current, cls):
                return current
            current = current.get_outer()
        return None

    def modify(self):
        self.get_package().dirty = True
        return True


class SerializedValues(dict):
    """Native struct/property-bag values expose export_text to Python."""
    def export_text(self):
        return json.dumps(self, sort_keys=True, separators=(',', ':'))


def property_bag_text(values, type_hash):
    if values is None:
        return '(Value=None)'
    def native_value(value):
        if isinstance(value, bool):
            return 'True' if value else 'False'
        return json.dumps(value, separators=(',', ':'))
    payload = ','.join(name + '=' + native_value(value) for name, value in values.items())
    return '(Value=/Engine/Transient.PropertyBag_' + type_hash + '(' + payload + '))'


class Instance:
    def __init__(self, graph, owner):
        self.owner = owner
        self.override_bag_type = 'A000000000000001'
        self.preview_bag_type = 'B000000000000001'
        self.overridden_variable_guids = ('0123456789ABCDEF0123456789ABCDEF',)
        self.values = {
            'dataflow_asset': graph,
            'dataflow_terminal': 'ClothAssetTerminal',
            'variable_overrides': SerializedValues(GustScale=7.125, UseArtistWind=False),
            'preview_blueprint_variable_overrides': SerializedValues(PreviewWind=31.),
        }

    def get_editor_property(self, name):
        if name in ('owner', 'preview_blueprint_variable_overrides'):
            raise PermissionError('Native UPROPERTY is not editor-readable: ' + name)
        return self.values[name]

    def set_editor_property(self, name, value):
        if name in ('owner', 'preview_blueprint_variable_overrides'):
            raise PermissionError('Native UPROPERTY is not editor-writable: ' + name)
        if name == 'dataflow_asset' and self.owner.harness.rebind_fails:
            self.owner.harness.rebind_fails = False
            raise RuntimeError('injected Dataflow rebind failure')
        if (name == 'dataflow_asset' and self.owner.harness.restore_binding_fails and
                '_Dataflow_' in self.values[name].get_path_name() and
                '_Dataflow_' not in value.get_path_name()):
            raise RuntimeError('injected Dataflow old-reference restoration failure')
        self.values[name] = value
        if name == 'dataflow_asset' and self.owner.harness.regenerate_property_bag_types_on_bind:
            self.owner.harness.property_bag_generation += 1
            generation = self.owner.harness.property_bag_generation
            self.override_bag_type = f'{generation:016X}'
            self.preview_bag_type = f'{generation + 1024:016X}'
        if name == 'dataflow_asset' and '_Dataflow_' in value.get_path_name():
            if self.owner.harness.override_value_corruption_on_bind:
                self.values['variable_overrides']['GustScale'] = 7.25
                self.owner.harness.override_value_corruption_on_bind = False
            if self.owner.harness.override_guid_corruption_on_bind:
                self.overridden_variable_guids = ('FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF',)
                self.owner.harness.override_guid_corruption_on_bind = False
        if (name == 'dataflow_asset' and self.owner.harness.context_corruption_on_bind and
                '_Dataflow_' in value.get_path_name()):
            self.values['dataflow_terminal'] = 'InjectedCorruptTerminal'
            self.owner.harness.context_corruption_on_bind = False
        self.owner.modify()

    def export_text(self):
        graph = self.values['dataflow_asset']
        return '(' + ','.join((
            "DataflowAsset=/Script/DataflowEngine.Dataflow'" + graph.path + "'" if graph else 'DataflowAsset=None',
            'DataflowTerminal=' + json.dumps(self.values['dataflow_terminal']),
            'VariableOverrides=(Variables=' + property_bag_text(self.values['variable_overrides'], self.override_bag_type) +
                ',OverriddenVariableGuids=(' + ','.join(self.overridden_variable_guids) + '))',
            'PreviewBlueprintVariableOverrides=' + property_bag_text(
                self.values['preview_blueprint_variable_overrides'], self.preview_bag_type),
            "Owner=/Script/ChaosClothAsset.ChaosClothAsset'" + self.owner.path + "'",
        )) + ')'

    def copy_for(self, owner, graph):
        result = Instance(graph, owner)
        for name, value in self.values.items():
            if name != 'dataflow_asset':
                result.values[name] = copy.deepcopy(value)
        result.override_bag_type = self.override_bag_type
        result.preview_bag_type = self.preview_bag_type
        result.overridden_variable_guids = self.overridden_variable_guids
        return result


class Cloth(Object):
    def __init__(self, harness, path, graph=None):
        package = Package(package_path(path))
        super().__init__(package.path + '.' + package.get_name(), package)
        self.harness = harness
        self.metadata = {}
        self.instance = Instance(graph, self)
        self.collection = {'binding_revision': 'unbuilt'}

    def get_editor_property(self, name):
        if name == 'dataflow_instance':
            return self.instance
        raise AttributeError(name)

    def set_editor_property(self, name, value):
        if name != 'dataflow_instance':
            raise AttributeError(name)
        self.instance = value
        self.instance.owner = self
        self.modify()


class Node(Object):
    def __init__(self, graph, name, node_type, properties, guid):
        super().__init__(graph.path + ':' + name, graph)
        self.name = name
        self.node_type = node_type
        self.properties = copy.deepcopy(properties)
        self.guid = guid


class Graph(Object):
    def __init__(self, path, outer, nodes=None, connections=None):
        super().__init__(path, outer)
        self.nodes = {}
        self.connections = copy.deepcopy(connections or [])
        self.context = {'variables': SerializedValues(GustScale=1.1, UseArtistWind=True),
                        'preview': {'Blueprint': '/Game/Fixtures/BP_ArtistPreview'}}
        for index, (name, (typ, props)) in enumerate((nodes or {}).items(), 1):
            self.nodes[name] = Node(self, name, typ, props, f'{index:032x}')

    def get_editor_property(self, name):
        if name == 'variables':
            raise PermissionError('Use the null-safe native ListVariables tool')
        return self.context[name]

    def snapshot(self):
        return {
            'nodes': {name: {'type': node.node_type, 'properties': copy.deepcopy(node.properties),
                             'guid': node.guid} for name, node in self.nodes.items()},
            'connections': copy.deepcopy(self.connections),
            'context': copy.deepcopy(self.context),
        }

    def clone(self, path, outer):
        result = Graph(path, outer)
        for name, node in self.nodes.items():
            result.nodes[name] = Node(result, name, node.node_type, node.properties, node.guid)
        result.connections = copy.deepcopy(self.connections)
        result.context = copy.deepcopy(self.context)
        return result


class DataflowTool:
    def __init__(self, harness):
        self.harness = harness

    def call_method(self, operation, args=()):
        if operation == 'GetGraphStructure':
            graph, = args
            return json.dumps({'nodes': [{'name': n.name, 'guid': n.guid, 'type': n.node_type}
                for n in graph.nodes.values()], 'connections': graph.connections})
        if operation == 'GetNodeInfo':
            node, = args
            props = copy.deepcopy(node.properties)
            if node.node_type == 'FChaosClothAssetImportNode':
                ref = props['ClothAsset']
                props['ClothAsset'] = "/Script/ChaosClothAsset.ChaosClothAsset'" + ref + '.' + ref.rsplit('/', 1)[-1] + "'"
                props['ImportLod'] = str(props['ImportLod'])
            return json.dumps({'name': node.name, 'guid': node.guid, 'type': node.node_type,
                'properties': props, 'inputPins': [{'name': 'CollectionLods[' + str(i) + ']'} for i in range(3)]
                if 'TerminalNode' in node.node_type else [{'name': 'Collection'}],
                'outputPins': [{'name': 'Collection'}]})
        if operation == 'ListVariables':
            values = args[0].context['variables'] or {}
            return json.dumps([{'name': name, 'type': 'Bool' if isinstance(value, bool) else
                'Float' if isinstance(value, float) else 'String',
                'value': ('True' if value else 'False') if isinstance(value, bool) else str(value)}
                for name, value in values.items()])
        if operation in ('RemoveNode', 'AddNode'):
            graph = args[0]
        else:
            graph = args[0].get_typed_outer(Graph)
        self.harness.mutations.append((operation, graph))
        graph.modify()
        if operation == 'RemoveNode':
            node = args[1]
            del graph.nodes[node.name]
            graph.connections = [e for e in graph.connections
                if node.name not in (e['fromNode'], e['toNode'])]
        elif operation == 'AddNode':
            _, typ, name, properties, _, _ = args
            node = Node(graph, name, typ, json.loads(properties), f'{100 + len(graph.nodes):032x}')
            graph.nodes[name] = node
            return node
        elif operation == 'UpdateNode':
            props = json.loads(args[1])
            if isinstance(props.get('VertexWeights'), list):
                props['VertexWeights'] = '(' + ','.join(map(str, props['VertexWeights'])) + ')'
            args[0].properties.update(props)
        elif operation == 'ConnectNodePins':
            a, output, b, input_pin = args
            graph.connections = [e for e in graph.connections
                if (e['toNode'], e['toPin']) != (b.name, input_pin)]
            graph.connections.append(dict(fromNode=a.name, fromPin=output,
                toNode=b.name, toPin=input_pin))
        elif operation == 'DisconnectNodePins':
            a, output, b, input_pin = args
            graph.connections = [e for e in graph.connections if
                (e['fromNode'], e['fromPin'], e['toNode'], e['toPin']) !=
                (a.name, output, b.name, input_pin)]
        else:
            raise AssertionError('Unexpected Dataflow operation: ' + operation)
        return True


def configuration():
    """Synthetic nondefault authoring values; no claim about a live asset."""
    return {
        'ArtistMaxDistance': ('FChaosClothAssetSimulationMaxDistanceConfigNode',
            {'MaxDistance': {'Low': 8., 'High': 61., 'WeightMap': 'MaxDistance'}}),
        'ArtistAerodynamics': ('FChaosClothAssetSimulationAerodynamicsConfigNode_v2',
            {'Drag': {'Low': .113, 'High': .671, 'WeightMap': 'MaxDistance'},
             'Lift': {'Low': .037, 'High': .283, 'WeightMap': 'MaxDistance'}}),
        'ArtistDamping': ('FChaosClothAssetSimulationDampingConfigNode',
            {'DampingCoefficientWeighted': {'Low': .071, 'High': .219, 'WeightMap': 'MaxDistance'}}),
        'ArtistSolver': ('FChaosClothAssetSimulationSolverConfigNode',
            {'NumIterations': 16, 'MaxNumIterations': 23, 'NumSubsteps': 3}),
        'ArtistHairResponse': ('FCodexHairResponseConfigNode',
            {'bEnableHairResponse': True, 'bEnableHairParentResponse': False, 'HairGustTimeScale': 2.7}),
        'ArtistGarmentWind': ('FCodexGarmentWindConfigNode',
            {'bEnabled': False, 'AirSpeedAtUnitAmplitude': 405., 'ResponseExponent': .77,
             'MaxAirSpeed': 1370., 'Gust': .83, 'Flutter': .44, 'MeanSoftCap': 575., 'HighFlutter': .22}),
    }


class ExternalGraphHarness:
    template = '/Game/Fixtures/Template'
    source_path = '/Game/Fixtures/BuildSource'
    target = '/Game/Fixtures/CA_Output'
    old_binding = '/Game/Fixtures/OldBinding'
    new_binding = '/Game/Fixtures/NewBinding'
    binding_key = 'Send2UE.HairGuideCloth.BindingData'
    owner = M.VERSION + ':/Game/Fixtures/SK_Render'

    def __init__(self, existing=True, external_source=True, external_final=True):
        self.assets = {}
        self.saved_cloths = {}
        self.saved_graphs = {}
        self.mutations = []
        self.evaluations = []
        self.duplicates = []
        self.created = {}
        self.delete_attempts = []
        self.save_attempts = []
        self.bad_graph_duplicate = False
        self.graph_clone_shares_nodes = False
        self.graph_clone_changes_properties = False
        self.graph_duplicate_fails = False
        self.graph_partial_duplicate = None
        self.rebind_fails = False
        self.restore_binding_fails = False
        self.context_corruption_on_bind = False
        self.regenerate_property_bag_types_on_bind = True
        self.property_bag_generation = 0
        self.override_value_corruption_on_bind = False
        self.override_guid_corruption_on_bind = False
        self.graph_save_fails = False
        self.graph_save_failure_predicate = None
        self.fail_target_save_once = False
        self.fail_target_saves_remaining = 0
        self.target_delete_fails = False
        self.stage_save_fails = False
        self.bad_stage = False
        self.source = self.make_cloth(self.source_path, 'SourceInput',
            'FChaosClothAssetSkeletalMeshImportNode_v2', {}, external_source)
        self.source.collection = self.binding_collection(self.new_binding)
        self.final = None
        if existing:
            self.final = self.make_cloth(self.target, 'GeneratorBindings',
                'FChaosClothAssetImportNode', {'ClothAsset': self.old_binding, 'ImportLod': 0}, external_final)
            self.add_artist_configuration(self.final)
            self.final.metadata = {M.OWNER: self.owner, M.CONTENT: 'old-content', self.binding_key: self.old_binding}
            self.final.collection = self.binding_collection(self.old_binding)
            self.final.collection['weight_maps'] = [{'name': 'MaxDistance', 'values': [.125, .625, .875]}]
        self.tool = DataflowTool(self)
        self.unreal = SimpleNamespace(
            Dataflow=Graph, DataflowEdNode=Node, DataflowAgentToolset=DataflowTool,
            ObjectIterator=lambda cls: iter([node for obj in self.assets.values() if isinstance(obj, Graph)
                for node in obj.nodes.values()] if cls is Node else
                [obj for obj in self.assets.values() if isinstance(obj, cls)]),
            get_default_object=lambda cls: self.tool,
            load_asset=self.load, is_valid=lambda obj: obj is not None,
            EditorAssetLibrary=SimpleNamespace(duplicate_asset=self.duplicate,
                does_asset_exist=lambda path: package_path(path) in self.assets,
                get_metadata_tag=lambda asset, key: asset.metadata.get(key, ''),
                set_metadata_tag=lambda asset, key, value: asset.metadata.__setitem__(key, value),
                save_loaded_asset=self.save, load_asset=self.load, delete_asset=self.delete),
            CodexClothToolsLibrary=SimpleNamespace(
                bake_vertex_color_weight_map=lambda *args: json.dumps({'success': True,
                    'unmatched_sim_vertices': 0, 'snap_distance_max_cm': 0.}),
                dump_cloth_collection_colors=self.dump,
                dump_dataflow_nodes=self.dump_graph),
            DataflowBlueprintLibrary=SimpleNamespace(evaluate_terminal_node_by_name=self.evaluate))
        for obj in list(self.assets.values()):
            self.save(obj)
        self.save_attempts.clear()

    def make_cloth(self, path, input_name, input_type, input_props, external):
        cloth = Cloth(self, path)
        if external:
            graph_package = Package(path + '_ArtistDataflow')
            graph_path = graph_package.path + '.' + graph_package.get_name()
            outer = graph_package
        else:
            graph_path = cloth.path + ':EmbeddedDataflow'
            outer = cloth
        graph = Graph(graph_path, outer, {
            input_name: (input_type, input_props),
            'ClothAssetTerminal': ('FChaosClothAssetTerminalNode', {})},
            [dict(fromNode=input_name, fromPin='Collection', toNode='ClothAssetTerminal',
                  toPin='CollectionLods[' + str(i) + ']') for i in range(3)])
        cloth.instance.values['dataflow_asset'] = graph
        self.assets[path] = cloth
        self.assets[graph.path if not external else graph_package.path] = graph
        return cloth

    def graph(self, cloth):
        return cloth.instance.get_editor_property('dataflow_asset')

    def add_artist_configuration(self, cloth):
        graph = self.graph(cloth)
        nodes = configuration()
        nodes = {'ArtistPaint': ('FChaosClothAssetWeightMapNode', {
            'MeshTarget': 'Simulation', 'MapOverrideType': 'ReplaceAll',
            'Snapshots': '(ActiveSnapshot=-1)', 'bIsFrozen': 'False',
            'OutputName': '(StringValue="MaxDistance")', 'VertexWeights': '(0.125,0.625,0.875)'})} | nodes
        for index, (name, (typ, props)) in enumerate(nodes.items(), 20):
            graph.nodes[name] = Node(graph, name, typ, props, f'{index:032x}')
        chain = ['GeneratorBindings'] + list(nodes) + ['ClothAssetTerminal']
        graph.connections = [dict(fromNode=a, fromPin='Collection', toNode=b,
            toPin='Collection' if b != 'ClothAssetTerminal' else 'CollectionLods[0]')
            for a, b in zip(chain, chain[1:])]
        graph.connections += [dict(fromNode=chain[-2], fromPin='Collection',
            toNode='ClothAssetTerminal', toPin='CollectionLods[' + str(i) + ']') for i in (1, 2)]
        graph.connections.append(dict(fromNode='ArtistPaint', fromPin='OutputName.StringValue',
            toNode='ArtistAerodynamics', toPin='Drag.WeightMap'))

    def peer(self, cloth):
        result = Cloth(self, package_path(cloth.path) + '_Peer', self.graph(cloth))
        self.assets[package_path(result.path)] = result
        return result

    def load(self, path):
        return self.assets.get(path) or self.assets.get(package_path(path))

    def duplicate(self, source, destination):
        original = self.load(source)
        if original is None:
            raise AssertionError('Unknown duplicate source: ' + source)
        self.duplicates.append((original, destination))
        if isinstance(original, Graph):
            if self.graph_duplicate_fails:
                return None
            if self.bad_graph_duplicate:
                return original
            package = Package(destination)
            duplicate = original.clone(destination + '.' + package.get_name(), package)
            if self.graph_clone_shares_nodes:
                duplicate.nodes = original.nodes
            if self.graph_clone_changes_properties:
                duplicate.nodes[next(iter(duplicate.nodes))].properties['InjectedUnexpectedProperty'] = 51
            self.assets[destination] = duplicate
            self.created[destination] = duplicate
            if self.graph_partial_duplicate:
                # A failed native call can leave a registered/saved destination
                # even when no object is returned to the caller.
                self.save(duplicate)
                if self.graph_partial_duplicate == 'return_none':
                    return None
                raise RuntimeError('injected partial Dataflow duplication failure')
            return duplicate
        result = Cloth(self, destination)
        graph = self.graph(original)
        # THIS distinction is the regression: external references survive Cloth
        # duplication, while a truly owned embedded UObject is copied with it.
        if graph.get_typed_outer(Cloth) is original:
            graph = graph.clone(result.path + ':EmbeddedDataflow', result)
            self.assets[graph.path] = graph
        result.instance = original.instance.copy_for(result, graph)
        result.collection = copy.deepcopy(original.collection)
        self.assets[destination] = result
        self.created[destination] = result
        return result

    def delete(self, path):
        asset = self.load(path)
        self.delete_attempts.append(asset)
        if asset is None:
            return False
        if (self.target_delete_fails and isinstance(asset, Cloth) and
                package_path(asset.path) == self.target):
            return False
        for key, value in list(self.assets.items()):
            if value is asset or (isinstance(value, Graph) and value.get_typed_outer(Cloth) is asset):
                del self.assets[key]
        if isinstance(asset, Graph):
            self.saved_graphs.pop(asset.path, None)
        else:
            self.saved_cloths.pop(package_path(asset.path), None)
        return True

    def context(self, cloth):
        return {name: copy.deepcopy(value) for name, value in cloth.instance.values.items()
                if name != 'dataflow_asset'} | {
                    'overridden_variable_guids': cloth.instance.overridden_variable_guids}

    def snapshot_cloth(self, cloth):
        graph = self.graph(cloth)
        return {'graph_path': graph.path, 'metadata': copy.deepcopy(cloth.metadata),
                'context': self.context(cloth), 'collection': copy.deepcopy(cloth.collection),
                'embedded': graph.snapshot() if graph.get_typed_outer(Cloth) is cloth else None}

    def save(self, asset, *args, **kwargs):
        self.save_attempts.append(asset)
        if isinstance(asset, Graph):
            if self.graph_save_fails or (self.graph_save_failure_predicate and
                    self.graph_save_failure_predicate(asset)):
                return False
            self.saved_graphs[asset.path] = asset.snapshot()
        else:
            if self.stage_save_fails and package_path(asset.path).endswith('_FinalStage'):
                return False
            if self.fail_target_save_once and package_path(asset.path) == self.target:
                self.fail_target_save_once = False
                return False
            if self.fail_target_saves_remaining and package_path(asset.path) == self.target:
                self.fail_target_saves_remaining -= 1
                return False
            self.saved_cloths[package_path(asset.path)] = self.snapshot_cloth(asset)
        asset.get_package().dirty = False
        return True

    def reload_snapshot(self, path):
        saved = copy.deepcopy(self.saved_cloths[path])
        saved['graph'] = saved.pop('embedded') or copy.deepcopy(self.saved_graphs[saved['graph_path']])
        return saved

    def binding_collection(self, binding):
        return {'binding_revision': binding, 'built_mapping': {
            'sim_positions': [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]],
            'sim_triangles': [[0, 1, 2]], 'kinematic_sim_indices': [0]},
            'weight_maps': [], 'sim_float_attributes': []}

    def evaluate(self, graph, terminal, asset):
        self.evaluations.append(asset)
        imports = [n for n in graph.nodes.values() if n.node_type == 'FChaosClothAssetImportNode']
        if imports:
            asset.collection = self.binding_collection(imports[0].properties['ClothAsset'])
            paint = graph.nodes.get('ArtistPaint')
            if paint:
                asset.collection['weight_maps'] = [{'name': 'MaxDistance',
                    'values': [float(x) for x in paint.properties['VertexWeights'].strip('()').split(',')]}]

    def dump(self, path):
        if path in (self.old_binding, self.new_binding):
            collection = self.binding_collection(path)
        else:
            asset = self.load(path)
            collection = copy.deepcopy(asset.collection)
            if self.bad_stage and package_path(path).endswith('_FinalStage'):
                collection['binding_revision'] = 'injected-bad-stage'
            # Artist map values are read directly from the actual shared graph.
            paint = self.graph(asset).nodes.get('ArtistPaint')
            if paint:
                collection['weight_maps'] = [{'name': 'MaxDistance',
                    'values': [float(x) for x in paint.properties['VertexWeights'].strip('()').split(',')]}]
        return json.dumps({'success': True, 'collections': [collection]})

    def dump_graph(self, path):
        graph = self.load(path)
        if isinstance(graph, Cloth):
            graph = self.graph(graph)
        return json.dumps({'nodes': [{'name': n.name, 'type': n.node_type,
            'props': copy.deepcopy(n.properties)} for n in graph.nodes.values()],
            'connections': graph.connections})

    def build(self):
        record = {'cloth_template_asset_path': self.source_path,
                  'sim_asset_path': '/Game/Fixtures/Sim', 'render_asset_path': '/Game/Fixtures/Render',
                  'physics_asset_path': '/Game/Fixtures/BodyPhysics'}
        # _build_source requires exactly one MaxDistance node in its template.
        graph = self.graph(self.source)
        if 'ArtistMaxDistance' not in graph.nodes:
            node = Node(graph, 'ArtistMaxDistance', 'FChaosClothAssetSimulationMaxDistanceConfigNode',
                        {'MaxDistance': {'Low': 8., 'High': 61., 'WeightMap': 'MaxDistance'}}, 'f' * 32)
            graph.nodes[node.name] = node
            graph.connections = [dict(fromNode='SourceInput', fromPin='Collection',
                toNode=node.name, toPin='Collection')] + [dict(fromNode=node.name, fromPin='Collection',
                toNode='ClothAssetTerminal', toPin='CollectionLods[' + str(i) + ']') for i in range(3)]
        return M._build_source(self.unreal, record, '/Game/Fixtures/NewBuild')

    def publish(self):
        return M._publish(self.unreal, self.source, self.new_binding, self.target,
                          self.owner, 'new-content')


class ExternalGraphTests(unittest.TestCase):
    def assert_original_untouched(self, h, graph, before, peer):
        self.assertEqual(graph.snapshot(), before)
        self.assertIs(h.graph(peer), graph)
        self.assertFalse(graph.get_package().dirty)
        self.assertFalse(any(target is graph for _, target in h.mutations),
                         'No native mutation may target the artist/peer shared graph')
        self.assertNotIn(graph, h.save_attempts)
        self.assertNotIn(graph, h.delete_attempts)

    def test_fixture_retains_external_references_and_copies_owned_embedded_graphs(self):
        for external in (True, False):
            with self.subTest(external=external):
                h = ExternalGraphHarness(external_source=external)
                duplicate = h.duplicate(h.source.path, '/Game/Fixtures/Duplicate')
                if external:
                    self.assertIs(h.graph(duplicate), h.graph(h.source))
                else:
                    self.assertIsNot(h.graph(duplicate), h.graph(h.source))
                    self.assertIs(h.graph(duplicate).get_typed_outer(Cloth), duplicate)

    def test_build_external_template_keeps_original_peer_and_context(self):
        h = ExternalGraphHarness()
        # Establish the authored input before taking the protected snapshot.
        graph = h.graph(h.source)
        node = Node(graph, 'ArtistMaxDistance', 'FChaosClothAssetSimulationMaxDistanceConfigNode',
                    {'MaxDistance': {'Low': 8., 'High': 61., 'WeightMap': 'MaxDistance'}}, 'f' * 32)
        graph.nodes[node.name] = node
        graph.connections = [dict(fromNode='SourceInput', fromPin='Collection',
            toNode=node.name, toPin='Collection')] + [dict(fromNode=node.name, fromPin='Collection',
            toNode='ClothAssetTerminal', toPin='CollectionLods[' + str(i) + ']') for i in range(3)]
        h.save(graph)
        h.save_attempts.clear()
        before = graph.snapshot(); peer = h.peer(h.source); context = h.context(h.source)
        asset, _ = h.build()
        self.assert_original_untouched(h, graph, before, peer)
        self.assertIsNot(h.graph(asset), graph)
        self.assertEqual(h.context(asset), context)
        loaded = h.reload_snapshot('/Game/Fixtures/NewBuild')
        self.assertIn('SimImport', loaded['graph']['nodes'])
        self.assertNotIn('SourceInput', loaded['graph']['nodes'])
        self.assertEqual(loaded['context'], context)

    def test_stage_and_first_publication_preserve_external_source_and_are_independent(self):
        h = ExternalGraphHarness(existing=False)
        graph = h.graph(h.source); before = graph.snapshot(); peer = h.peer(h.source)
        h.publish()
        self.assert_original_untouched(h, graph, before, peer)
        stage = h.created[h.source_path + '_FinalStage']; final = h.load(h.target)
        self.assertIsNot(h.graph(stage), graph)
        self.assertIsNot(h.graph(final), graph)
        self.assertIsNot(h.graph(stage), h.graph(final))
        for asset in (stage, final):
            self.assertEqual(h.context(asset), h.context(h.source))
            # A successful publisher may remove its temporary stage. Final must
            # still be independent, serializable and reloadable after cleanup.
            if asset is stage and package_path(asset.path) not in h.saved_cloths:
                continue
            loaded = h.reload_snapshot(package_path(asset.path))
            self.assertEqual(set(loaded['graph']['nodes']), {'GeneratorBindings', 'ClothAssetTerminal'})
            self.assertEqual(loaded['graph']['nodes']['GeneratorBindings']['properties']['ClothAsset'], h.new_binding)
            self.assertEqual(loaded['metadata'][h.binding_key], h.new_binding)

    def test_failed_stage_verification_does_not_touch_external_source(self):
        h = ExternalGraphHarness(); h.bad_stage = True
        graph = h.graph(h.source); before = graph.snapshot(); peer = h.peer(h.source)
        final_before = h.snapshot_cloth(h.final)
        with self.assertRaisesRegex(RuntimeError, 'Final import changed'):
            h.publish()
        self.assert_original_untouched(h, graph, before, peer)
        self.assertEqual(h.snapshot_cloth(h.final), final_before)

    def test_reexport_detaches_external_target_and_preserves_artist_wind8_paint_and_all_lod_pins(self):
        h = ExternalGraphHarness(external_source=False)
        graph = h.graph(h.final); before = graph.snapshot(); peer = h.peer(h.final)
        context = h.context(h.final)
        h.publish()
        self.assert_original_untouched(h, graph, before, peer)
        self.assertIsNot(h.graph(h.final), graph)
        current = h.graph(h.final).snapshot()
        for name, row in before['nodes'].items():
            if name != 'GeneratorBindings':
                self.assertEqual(current['nodes'][name], row)
        self.assertEqual(current['connections'], before['connections'])
        self.assertEqual(h.context(h.final), context)
        self.assertEqual(h.final.metadata[h.binding_key], h.new_binding)
        loaded = h.reload_snapshot(h.target)
        self.assertEqual(loaded['graph'], current)
        self.assertEqual(loaded['graph']['nodes']['GeneratorBindings']['properties']['ClothAsset'], h.new_binding)
        self.assertIs(loaded['graph']['nodes']['ArtistGarmentWind']['properties']['bEnabled'], False)

    def test_rejected_artist_import_is_not_detached_or_modified(self):
        h = ExternalGraphHarness(external_source=False)
        graph = h.graph(h.final)
        graph.nodes['GeneratorBindings'].properties['ClothAsset'] = '/Game/Fixtures/ArtistReplacement'
        before = graph.snapshot(); peer = h.peer(h.final)
        with self.assertRaisesRegex(ValueError, 'Import values have user changes'):
            h.publish()
        self.assert_original_untouched(h, graph, before, peer)
        self.assertIs(h.graph(h.final), graph)
        self.assertFalse(any(original is graph for original, _ in h.duplicates))

    def test_failed_reexport_save_restores_original_reference_and_saved_metadata(self):
        h = ExternalGraphHarness(external_source=False)
        graph = h.graph(h.final); before = graph.snapshot(); peer = h.peer(h.final)
        old_cloth = h.snapshot_cloth(h.final)
        h.fail_target_save_once = True
        with self.assertRaisesRegex(RuntimeError, 'could not be saved'):
            h.publish()
        self.assert_original_untouched(h, graph, before, peer)
        self.assertIs(h.graph(h.final), graph)
        self.assertEqual(h.snapshot_cloth(h.final), old_cloth)
        self.assertEqual(h.saved_cloths[h.target], old_cloth)

    def test_failed_reexport_isolation_keeps_original_reference_metadata_and_peer(self):
        for fault in ('bad_graph_duplicate', 'graph_clone_shares_nodes', 'graph_clone_changes_properties',
                      'graph_duplicate_fails', 'rebind_fails', 'graph_save_fails'):
            with self.subTest(fault=fault):
                h = ExternalGraphHarness(external_source=False)
                graph = h.graph(h.final); before = graph.snapshot(); peer = h.peer(h.final)
                old_cloth = h.snapshot_cloth(h.final)
                setattr(h, fault, True)
                with self.assertRaises((RuntimeError, ValueError)):
                    h.publish()
                self.assert_original_untouched(h, graph, before, peer)
                self.assertIs(h.graph(h.final), graph)
                self.assertEqual(h.snapshot_cloth(h.final), old_cloth)
                self.assertEqual(h.saved_cloths[h.target], old_cloth)

    def test_two_reexports_keep_original_peer_and_preserve_all_artist_configuration(self):
        h = ExternalGraphHarness(external_source=False)
        original = h.graph(h.final); before = original.snapshot(); peer = h.peer(h.final)
        for binding in (h.new_binding, '/Game/Fixtures/NextBinding'):
            h.new_binding = binding
            h.publish()
            self.assert_original_untouched(h, original, before, peer)
            current = h.graph(h.final).snapshot()
            for name, row in before['nodes'].items():
                if name != 'GeneratorBindings':
                    self.assertEqual(current['nodes'][name], row)
            self.assertEqual(current['connections'], before['connections'])
            loaded = h.reload_snapshot(h.target)
            self.assertEqual(loaded['graph'], current)
            self.assertEqual(loaded['metadata'][h.binding_key], binding)
            h.old_binding = binding

    def test_existing_build_or_stage_destination_is_never_overwritten_or_cleaned_up(self):
        for phase, path in (('build', '/Game/Fixtures/NewBuild'),
                            ('stage', ExternalGraphHarness.source_path + '_FinalStage')):
            with self.subTest(phase=phase):
                h = ExternalGraphHarness(external_source=False)
                old = h.make_cloth(path, 'ArtistOriginal', 'FChaosClothAssetImportNode',
                    {'ClothAsset': '/Game/Fixtures/ArtistOriginalBinding', 'ImportLod': 0}, True)
                graph = h.graph(old); before = graph.snapshot(); peer = h.peer(old)
                h.save(graph); h.save(old); h.save_attempts.clear()
                old_cloth = h.snapshot_cloth(old)
                with self.assertRaises((RuntimeError, ValueError)):
                    h.build() if phase == 'build' else h.publish()
                self.assert_original_untouched(h, graph, before, peer)
                self.assertIs(h.load(path), old)
                self.assertNotIn(old, h.delete_attempts)
                self.assertEqual(h.snapshot_cloth(old), old_cloth)
                self.assertEqual(h.saved_cloths[path], old_cloth)

    def test_failed_graph_duplication_rebind_or_save_never_mutates_original(self):
        for fault in ('bad_graph_duplicate', 'graph_clone_shares_nodes', 'graph_clone_changes_properties',
                      'graph_duplicate_fails', 'rebind_fails', 'graph_save_fails'):
            with self.subTest(fault=fault):
                h = ExternalGraphHarness(existing=False)
                graph = h.graph(h.source); before = graph.snapshot(); peer = h.peer(h.source)
                setattr(h, fault, True)
                with self.assertRaises((RuntimeError, ValueError)):
                    h.publish()
                self.assert_original_untouched(h, graph, before, peer)
                self.assertFalse(h.target in h.saved_cloths)

    def test_graph_save_failure_after_native_edits_cannot_publish_a_stale_graph_reference(self):
        h = ExternalGraphHarness(existing=False)
        graph = h.graph(h.source); before = graph.snapshot(); peer = h.peer(h.source)
        # The initial independent copy can save, but the later edited graph
        # cannot. Saving only its Cloth would otherwise publish a stale import.
        h.graph_save_failure_predicate = lambda item: 'GeneratorBindings' in item.nodes
        with self.assertRaises((RuntimeError, ValueError)):
            h.publish()
        self.assert_original_untouched(h, graph, before, peer)
        self.assertNotIn(h.target, h.saved_cloths)
        self.assertNotIn(h.source_path + '_FinalStage', h.saved_cloths)

    def test_failed_first_final_save_and_delete_retains_its_graph_and_reports_cleanup_failure(self):
        h = ExternalGraphHarness(existing=False)
        original = h.graph(h.source); before = original.snapshot(); peer = h.peer(h.source)
        h.fail_target_save_once = True
        h.target_delete_fails = True
        with patch.object(M, '_apply_hair_guide_cloth', side_effect=lambda record: h.publish()), \
                redirect_stdout(io.StringIO()):
            receipt = M.apply_hair_guide_cloth({'render_asset_path': '/Game/Fixtures/SK_Render'})
        self.assertFalse(receipt['verified'])
        self.assertEqual(receipt['status'], 'cloth_not_updated')
        self.assertIn('could not be published', receipt['detail'])
        self.assertIn('remaining dependencies retained', receipt['detail'])
        self.assertIn(h.target, receipt['detail'])
        surviving = h.load(h.target)
        self.assertIsNotNone(surviving)
        dependency = h.graph(surviving)
        self.assertIs(h.load(dependency.path), dependency)
        self.assertIn(dependency.path, h.saved_graphs)
        self.assertNotIn(dependency, h.delete_attempts,
                         'Force deletion must stop before a surviving Cloth dependency')
        self.assertEqual(h.delete_attempts, [surviving])
        self.assertNotIn(h.target, h.saved_cloths)
        self.assert_original_untouched(h, original, before, peer)

    def test_failed_update_and_rollback_saves_report_both_failures_without_touching_shared_graph(self):
        h = ExternalGraphHarness(external_source=False)
        original = h.graph(h.final); before = original.snapshot(); peer = h.peer(h.final)
        old_cloth = h.snapshot_cloth(h.final)
        h.fail_target_saves_remaining = 2
        with patch.object(M, '_apply_hair_guide_cloth', side_effect=lambda record: h.publish()), \
                redirect_stdout(io.StringIO()):
            receipt = M.apply_hair_guide_cloth({'render_asset_path': '/Game/Fixtures/SK_Render'})
        self.assertFalse(receipt['verified'])
        self.assertEqual(receipt['status'], 'cloth_not_updated')
        self.assertIn('Updated cloth could not be saved', receipt['detail'])
        self.assertIn('Cloth rollback failed', receipt['detail'])
        self.assertIn('Restored Cloth could not be saved', receipt['detail'])
        self.assertIn(h.target, receipt['detail'])
        self.assertIs(h.graph(h.final), original)
        self.assertEqual(h.snapshot_cloth(h.final), old_cloth)
        self.assertEqual(h.saved_cloths[h.target], old_cloth)
        self.assertEqual(sum(asset is h.final for asset in h.save_attempts), 2)
        self.assert_original_untouched(h, original, before, peer)

    def assert_dirty_external_graph_rejected_before_any_write(self, target_graph):
        h = ExternalGraphHarness()
        record = {'version': 1, 'simulation_enabled': True,
            'sim_asset_path': '/Game/Fixtures/SK_Sim', 'render_asset_path': '/Game/Fixtures/SK_Render',
            'cloth_template_asset_path': h.source_path, 'body_mesh_asset_path': '/Game/Fixtures/SK_Body',
            'physics_asset_path': '/Game/Fixtures/PA_Body', 'cloth_asset_path': h.target,
            'manifest_sha256': 'new-content'}
        for name in ('sim_asset_path', 'render_asset_path', 'body_mesh_asset_path', 'physics_asset_path'):
            path = record[name]
            h.assets[path] = Cloth(h, path)
        selected = h.final if target_graph else h.source
        graph = h.graph(selected)
        before = graph.snapshot(); old_cloth = h.snapshot_cloth(h.final)
        graph.get_package().dirty = True
        self.assertFalse(selected.get_package().dirty, 'Only the external graph package is dirty')
        h.unreal.EditorLoadingAndSavingUtils = SimpleNamespace(
            get_dirty_content_packages=lambda: [graph.get_package()])
        builder = Mock(side_effect=AssertionError('Dirty graph must be rejected before build'))
        with patch.dict('sys.modules', {'unreal': h.unreal}), \
                patch.object(M, 'validate_record', return_value=(record, {})), \
                patch.object(M, 'build_in_editor', builder), redirect_stdout(io.StringIO()):
            receipt = M.apply_hair_guide_cloth(record)
        self.assertFalse(receipt['verified'])
        self.assertEqual(receipt['status'], 'cloth_not_updated')
        self.assertIn('Cloth Dataflow has unsaved edits', receipt['detail'])
        self.assertIn(graph.path, receipt['detail'])
        builder.assert_not_called()
        self.assertEqual(h.mutations, [])
        self.assertEqual(h.duplicates, [])
        self.assertEqual(h.save_attempts, [])
        self.assertEqual(h.delete_attempts, [])
        self.assertEqual(graph.snapshot(), before)
        self.assertEqual(h.snapshot_cloth(h.final), old_cloth)
        self.assertIs(h.graph(selected), graph)
        self.assertTrue(graph.get_package().dirty)

    def test_dirty_external_template_graph_with_clean_cloth_stops_before_any_write(self):
        self.assert_dirty_external_graph_rejected_before_any_write(target_graph=False)

    def test_dirty_external_target_graph_with_clean_cloth_stops_before_any_write(self):
        self.assert_dirty_external_graph_rejected_before_any_write(target_graph=True)

    def test_partial_graph_duplicate_none_or_raise_cleans_only_its_destination(self):
        for existing in (False, True):
            for outcome in ('return_none', 'raise'):
                with self.subTest(existing=existing, outcome=outcome):
                    h = ExternalGraphHarness(existing=existing, external_source=not existing)
                    protected = h.final if existing else h.source
                    graph = h.graph(protected); before = graph.snapshot(); peer = h.peer(protected)
                    h.graph_partial_duplicate = outcome
                    with self.assertRaises((RuntimeError, ValueError)):
                        h.publish()
                    self.assert_original_untouched(h, graph, before, peer)
                    self.assertIs(h.graph(protected), graph)
                    self.assertFalse(any('_Dataflow_' in path for path in h.assets))
                    self.assertFalse(any('_Dataflow_' in path for path in h.saved_graphs))
                    self.assertNotIn(h.source_path + '_FinalStage', h.assets)
                    if existing:
                        self.assertNotIn(h.final, h.evaluations)
                        self.assertNotIn(h.final, h.save_attempts)

    def test_uncertain_existing_target_reference_or_context_restore_never_evaluates_or_saves_target(self):
        for failure in ('reference', 'context'):
            with self.subTest(failure=failure):
                h = ExternalGraphHarness(external_source=False)
                graph = h.graph(h.final); before = graph.snapshot(); peer = h.peer(h.final)
                saved = copy.deepcopy(h.saved_cloths[h.target])
                if failure == 'reference':
                    h.graph_clone_changes_properties = True
                    h.restore_binding_fails = True
                else:
                    h.context_corruption_on_bind = True
                with patch.object(M, '_apply_hair_guide_cloth', side_effect=lambda record: h.publish()), \
                        redirect_stdout(io.StringIO()):
                    receipt = M.apply_hair_guide_cloth({'render_asset_path': '/Game/Fixtures/SK_Render'})
                self.assertFalse(receipt['verified'])
                self.assertEqual(receipt['status'], 'cloth_not_updated')
                self.assertIn('Dataflow isolation rollback failed', receipt['detail'])
                self.assertIn(h.target, receipt['detail'])
                self.assertNotIn(h.final, h.evaluations)
                self.assertNotIn(h.final, h.save_attempts)
                self.assertEqual(h.saved_cloths[h.target], saved)
                self.assert_original_untouched(h, graph, before, peer)


class BuildDependencyBoundaryTests(unittest.TestCase):
    """The outer build must honor commit/recovery state from its inner work.

    The stub models only native assets already created and registered in the
    caller's cleanup ledger. It leaves the actual wrapper and deletion routine
    under test; it does not recreate mesh import or Chaos simulation.
    """
    source = '/Game/Request/CA_BuildSource'
    binding = '/Game/Request/CA_BindingData'

    def run_failure(self, *, retain=False, committed=False):
        h = ExternalGraphHarness(existing=False, external_source=False)
        made = {}
        error = M._RetainDependenciesError('injected uncertain rollback') if retain else \
            OSError('injected receipt write failure') if committed else RuntimeError('injected precommit failure')

        def inner(record, output, created, commit_state):
            for path in (self.source, self.binding):
                asset = h.make_cloth(path, 'RequestInput', 'FChaosClothAssetImportNode',
                    {'ClothAsset': h.new_binding, 'ImportLod': 0}, False)
                made[path] = asset
                h.save(asset)
                created.append(path)
            if committed:
                final = h.make_cloth(h.target, 'GeneratorBindings', 'FChaosClothAssetImportNode',
                    {'ClothAsset': self.binding, 'ImportLod': 0}, False)
                made[h.target] = final
                h.save(final)
                commit_state[0] = True
            raise error

        with patch.dict('sys.modules', {'unreal': h.unreal}), \
                patch.object(M, '_build_in_editor', side_effect=inner):
            with self.assertRaises(type(error)) as raised:
                M.build_in_editor({'fixture': 'cleanup-ledger'}, 'unused-fixture-output')
        self.assertIs(raised.exception, error, 'The original recovery/receipt error must remain explicit')
        return h, made

    def test_uncertain_local_recovery_propagates_without_outer_source_or_binding_deletion(self):
        h, made = self.run_failure(retain=True)
        self.assertEqual(h.delete_attempts, [])
        for path in (self.source, self.binding):
            self.assertIs(h.load(path), made[path])
            self.assertIn(path, h.saved_cloths)

    def test_ordinary_uncommitted_failure_cleans_only_request_source_and_binding(self):
        h, made = self.run_failure()
        self.assertEqual(h.delete_attempts, [made[self.binding], made[self.source]])
        for path in (self.source, self.binding):
            self.assertIsNone(h.load(path))
            self.assertNotIn(path, h.saved_cloths)
        self.assertIs(h.load(h.source_path), h.source)

    def test_receipt_failure_after_commit_keeps_published_final_and_its_dependencies(self):
        h, made = self.run_failure(committed=True)
        self.assertEqual(h.delete_attempts, [])
        for path in (self.source, self.binding, h.target):
            self.assertIs(h.load(path), made[path])
            self.assertIn(path, h.saved_cloths)
        reference = h.graph(made[h.target]).nodes['GeneratorBindings'].properties['ClothAsset']
        self.assertEqual(reference, self.binding)
        self.assertIsNotNone(h.load(reference))


class NativeContextAccessTests(unittest.TestCase):
    def test_protected_instance_fields_are_exported_but_cannot_be_read_or_written_directly(self):
        h = ExternalGraphHarness()
        instance = h.final.instance
        for name in ('owner', 'preview_blueprint_variable_overrides'):
            with self.subTest(name=name):
                with self.assertRaises(PermissionError):
                    instance.get_editor_property(name)
                with self.assertRaises(PermissionError):
                    instance.set_editor_property(name, None)
        context = M._graph_context(h.final)
        self.assertIn(h.final.path, context['owner'])
        self.assertIn('PreviewWind', context['preview'])

    def test_null_property_bag_uses_list_variables_without_direct_graph_getter(self):
        h = ExternalGraphHarness()
        graph = h.graph(h.final)
        graph.context['variables'] = None
        with self.assertRaises(PermissionError):
            graph.get_editor_property('variables')
        self.assertEqual(M._graph_snapshot(h.unreal, h.final)['variables'], [])

    def test_struct_parser_preserves_nested_graph_and_owner_values_with_commas(self):
        overrides = r'''(Variables=(Graph="/Game/Artist/Graph.Graph",Owner="nested, owner",Text="DataflowAsset=/Game/Nested,Owner=keep"),OverriddenVariableGuids=(A,B))'''
        preview = r'''(Values=(Label="preview, owner",Graph="/Game/Preview/DF.DF"))'''
        owner = "/Script/ChaosClothAsset.ChaosClothAsset'/Game/Cloth/CA_Main.CA_Main'"
        fields = M._struct_text_fields('(DataflowAsset=/Game/Old.DF,DataflowTerminal="Artist, Terminal",'
            'VariableOverrides=' + overrides + ',PreviewBlueprintVariableOverrides=' + preview + ',Owner=' + owner + ')')
        self.assertEqual(fields, {'DataflowAsset': '/Game/Old.DF', 'DataflowTerminal': '"Artist, Terminal"',
            'VariableOverrides': overrides, 'PreviewBlueprintVariableOverrides': preview, 'Owner': owner})

    def test_struct_parser_preserves_escaped_quotes_brackets_and_nested_field_like_text(self):
        overrides = r'''(Variables=(Message="keep, \"quoted Owner=artist\" and [brackets] {braces}",Path="/Game/Artist/Graph.Graph",Literal="DataflowAsset=still nested"),Ids=("A,B","C=D"))'''
        preview = r'''(Label="Owner=\"preview,keep\"",EscapedSlash="a\\b",Nested=(Value="(x,y)"))'''
        owner = '"/Script/ChaosClothAsset.ChaosClothAsset\'/Game/Cloth/CA_Main.CA_Main\'"'
        fields = M._struct_text_fields('(Owner=' + owner + ',VariableOverrides=' + overrides +
            ',DataflowTerminal=ClothAssetTerminal,PreviewBlueprintVariableOverrides=' + preview +
            ',DataflowAsset="/Game/Original.Graph")')
        self.assertEqual(fields['VariableOverrides'], overrides)
        self.assertEqual(fields['PreviewBlueprintVariableOverrides'], preview)
        self.assertEqual(fields['Owner'], owner)
        self.assertEqual(fields['DataflowAsset'], '"/Game/Original.Graph"')
        self.assertEqual(len(fields), 5)

    def test_property_bag_type_regeneration_keeps_values_and_rejects_changed_payload_or_guid(self):
        h = ExternalGraphHarness(external_source=False)
        before = M._graph_context(h.final)
        old_type = h.final.instance.override_bag_type
        guids = h.final.instance.overridden_variable_guids
        h.publish()
        self.assertNotEqual(h.final.instance.override_bag_type, old_type)
        self.assertEqual(M._graph_context(h.final), before)
        self.assertEqual(h.final.instance.values['variable_overrides'], {'GustScale': 7.125, 'UseArtistWind': False})
        self.assertEqual(h.final.instance.overridden_variable_guids, guids)
        for fault in ('override_value_corruption_on_bind', 'override_guid_corruption_on_bind'):
            with self.subTest(fault=fault):
                changed = ExternalGraphHarness(external_source=False)
                graph = changed.graph(changed.final); original = graph.snapshot(); peer = changed.peer(changed.final)
                saved = copy.deepcopy(changed.saved_cloths[changed.target])
                setattr(changed, fault, True)
                with self.assertRaisesRegex(RuntimeError, 'preserve graph/context'):
                    changed.publish()
                self.assertNotIn(changed.final, changed.evaluations)
                self.assertNotIn(changed.final, changed.save_attempts)
                self.assertEqual(changed.saved_cloths[changed.target], saved)
                self.assertEqual(graph.snapshot(), original)
                self.assertIs(changed.graph(peer), graph)

    def test_property_bag_normalization_preserves_matching_paths_inside_artist_strings(self):
        payload = r'''(GustScale=7.125,UseArtistWind=False,Note="/Engine/Transient.PropertyBag_AB12(Owner=\"artist, keep\")",Graph="/Game/Artist/Graph.Graph")'''
        first = '(Value=/Engine/Transient.PropertyBag_0123AB' + payload + ')'
        regenerated = '(Value=/Engine/Transient.PropertyBag_FF00' + payload + ')'
        expected = '(Value=<PropertyBag>' + payload + ')'
        self.assertEqual(M._property_bag_context(first), expected)
        self.assertEqual(M._property_bag_context(regenerated), expected)
        changed_artist_path = first.replace('PropertyBag_AB12(', 'PropertyBag_AB13(')
        self.assertNotEqual(M._property_bag_context(changed_artist_path), expected)
        authored_type = '(Value=/Game/Artist.PropertyBag_0123AB' + payload + ')'
        self.assertEqual(M._property_bag_context(authored_type), authored_type)
        self.assertEqual(M._property_bag_context('(Value=None)'), '(Value=None)')


if __name__ == '__main__':
    unittest.main()
