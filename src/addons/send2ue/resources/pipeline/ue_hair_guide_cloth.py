"""Build Chaos Cloth from a verified Send2UE Hair Tool generator packet.

The packet owns topology, guide membership and simulation G. A cloth template
supplies physics settings only. No remeshing, paint generation or render-G mask
is performed here. The final asset imports a validated standard cloth collection.
"""

import hashlib
import importlib.util
import itertools
import json
import math
from pathlib import Path
import re
import sys
import uuid
from collections import defaultdict

OWNER = "Send2UE.HairGuideCloth.Owner"
CONTENT = "Send2UE.HairGuideCloth.Content"
VERSION = "send2ue.hair_guide_cloth.v1"


def _sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _path(value):
    value = str(value or "").split(".", 1)[0]
    # Keep the names/mounts already chosen by ordinary Send2UE, including
    # Unicode asset names. Unreal itself owns asset naming and class validation.
    if not value.startswith("/") or "\x00" in value or any(part in ("", ".", "..") for part in value[1:].split("/")):
        raise ValueError("Expected an Unreal asset package path: " + value)
    return value


def _native(result):
    def checked(payload, call_failed=False):
        if not isinstance(payload, dict):
            raise RuntimeError("Native cloth operation did not return an object receipt: " + str(result)[:1500])
        if call_failed or payload.get("success") is False:
            raise RuntimeError("Native cloth operation failed: " + json.dumps(payload, ensure_ascii=False)[:1500])
        return payload

    if isinstance(result, str):
        return checked(json.loads(result))
    for value in result or []:
        if isinstance(value, str) and value.startswith("{"):
            return checked(json.loads(value), call_failed=result[0] is False)
    raise RuntimeError("Native cloth operation did not return its receipt: " + str(result)[:1500])


def _target(record):
    folder, name = record["render_asset_path"].rsplit("/", 1)
    return _path(record.get("cloth_asset_path") or folder + "/CA_" + re.sub(r"^(?:SK|SM)_", "", name) + "_GuideCloth")


def validate_record(record):
    """Validate real file hashes and per-vertex provenance before asset writes."""
    if not isinstance(record, dict) or record.get("version") not in (1, 2):
        raise ValueError("Unsupported hair guide cloth handoff version")
    out = dict(record)
    filename = Path(record["manifest_path"]).resolve(strict=True)
    if _sha(filename) != record.get("manifest_sha256"):
        raise ValueError("Hair generator manifest changed after export")
    packet = json.loads(filename.read_text(encoding="utf8"))
    simulation_enabled = record.get("simulation_enabled", True)
    if record["version"] == 2:
        if (packet.get("version") != 2 or type(simulation_enabled) is not bool or
                packet.get("simulation_enabled") is not simulation_enabled):
            raise ValueError("Hair simulation mode differs from its export manifest")
    elif simulation_enabled is not True:
        raise ValueError("Render-only hair requires the explicit version 2 export contract")
    if any(part.get("provenance") == "render_as_sim_no_guide" for part in packet.get("parts", [])):
        raise ValueError("Legacy render-as-simulation export needs re-export with the render-only policy")
    out["simulation_enabled"] = simulation_enabled
    keys = ["render_asset_path"]
    if simulation_enabled:
        keys += ["sim_asset_path", "cloth_template_asset_path", "body_mesh_asset_path", "physics_asset_path"]
    for key in keys:
        out[key] = _path(record.get(key))
    for role in ("sim", "render"):
        fbx = record.get(role + "_fbx")
        if fbx and _sha(Path(fbx["path"]).resolve(strict=True)) != fbx["sha256"]:
            raise ValueError(role + " FBX changed after export")
        mesh = packet["meshes"][role]
        vertices, triangles, owners = mesh["vertices"], mesh["triangles"], mesh["guide_ids"]
        if role == "sim" and not simulation_enabled:
            if vertices or triangles or owners or mesh.get("weights") or record.get("sim_asset_path") or fbx:
                raise ValueError("Render-only export unexpectedly contains simulation geometry")
            continue
        if not vertices or not triangles or len(owners) != len(vertices):
            raise ValueError(role + " has incomplete topology or guide provenance")
        if any(type(owner) is not int or owner < (-1 if role == "render" else 0) for owner in owners):
            raise ValueError(role + " has invalid generator ownership")
        if any(len(p) != 3 or any(not math.isfinite(v) for v in p) for p in vertices):
            raise ValueError(role + " has invalid positions")
        for tri in triangles:
            if len(tri) != 3 or any(type(i) is not int or not 0 <= i < len(vertices) for i in tri):
                raise ValueError(role + " has invalid triangle indices")
            if len({owners[i] for i in tri}) != 1:
                raise ValueError(role + " triangle crosses generator ownership")
        if role == "sim":
            weights = mesh["weights"]
            if len(weights) != len(vertices) or any(not math.isfinite(w) or not 0 <= w <= 1 for w in weights):
                raise ValueError("Simulation source G is incomplete")
    render = packet["meshes"]["render"]
    render_only = [index for index, owner in enumerate(render["guide_ids"]) if owner == -1]
    if render_only:
        sources = packet.get("render_only_sources", [])
        if (record["version"] != 2 or render.get("render_only_vertex_indices") != render_only or
                not sources or any(source.get("status") != "no_guide_after_search" or
                                   source.get("simulation_enabled") is not False for source in sources)):
            raise ValueError("Render-only vertices require an explicit completed no-guide search")
    elif render.get("render_only_vertex_indices") or packet.get("render_only_sources"):
        raise ValueError("Render-only provenance does not match the exported vertices")
    guided = {owner for owner in render["guide_ids"] if owner != -1}
    if not guided.issubset(set(packet["meshes"]["sim"]["guide_ids"])):
        raise ValueError("Render strands refer to missing simulation guides")
    if not simulation_enabled and guided:
        raise ValueError("Render-only export still contains guided simulation vertices")
    out["manifest_path"] = str(filename)
    return out, packet


def normalize_ownership(built, packet, tolerance_cm=0.0002):
    """Resolve import splits/welds to exact source neighborhoods, never nearest guide.

    A neighborhood with conflicting guide IDs or G is rejected. Geometry matching
    only restores source vertex identity after FBX import; it never chooses which
    guide owns a strand.
    """
    result, receipt = {}, {}
    for role in ("sim", "render"):
        source = packet["meshes"][role]
        positions = [[p[0] * 100, -p[1] * 100, p[2] * 100] for p in source["vertices"]]
        buckets = defaultdict(list)
        cell = lambda p: tuple(math.floor(v / tolerance_cm) for v in p)
        for index, point in enumerate(positions):
            buckets[cell(point)].append(index)
        gids, source_indices, errors = [], [], []
        for point in built[role + "_positions"]:
            key = cell(point)
            candidates = []
            for delta in itertools.product((-1, 0, 1), repeat=3):
                for index in buckets.get(tuple(key[d] + delta[d] for d in range(3)), ()):
                    error = math.dist(point, positions[index])
                    if error <= tolerance_cm:
                        candidates.append((error, index))
            if not candidates:
                raise ValueError(role + " imported vertex has no exact source identity")
            owners = {source["guide_ids"][i] for _, i in candidates}
            if len(owners) != 1:
                raise ValueError(role + " coincident source vertices have ambiguous guide identity")
            if role == "sim" and len({round(source["weights"][i], 6) for _, i in candidates}) != 1:
                raise ValueError("Coincident simulation vertices have conflicting source G")
            error, index = min(candidates)
            gids.append(source["guide_ids"][index]); source_indices.append(index); errors.append(error)
        result[role + "_positions"] = built[role + "_positions"]
        result[role + "_triangles"] = built[role + "_triangles"]
        result[role + "_guide_ids"] = gids
        result[role + "_source_indices"] = source_indices
        receipt[role] = {"maximum_import_error_cm": max(errors), "ambiguous": 0, "unmatched": 0}
        if role == "sim":
            expected = [source["weights"][i] for i in source_indices]
            actual = built["sim_weights"]
            if len(actual) != len(expected) or any(abs(a - b) > 1e-6 for a, b in zip(actual, expected)):
                raise ValueError("Chaos MaxDistance map differs from exported simulation G")
            result["sim_weights"] = actual
    for tri in result["sim_triangles"]:
        if len({result["sim_guide_ids"][i] for i in tri}) != 1:
            raise ValueError("Chaos import welded different source guides")
    result["provenance"] = {"weight_source": "simulation_source_own_g", "render_g_used": False,
                             "lineage": "generator_carried_id", "source_matching": receipt,
                             "no_guide_policy": "render_only_skinning"}
    # Imported splits retain the sentinel from exact source identity. The native
    # repair must remove these vertices' initial unrestricted proxy influence.
    result["render_only_vertex_indices"] = [i for i, gid in enumerate(result["render_guide_ids"]) if gid == -1]
    return result


def _render_only_receipt(record, packet):
    """Ordinary imported render geometry remains available without a cloth build.

    This exporter does not assign actor components. A previous generated asset
    is retained as an asset; it is never returned as the current cloth output.
    """
    return {"verified": True, "version": record["version"], "status": "render_only",
            "simulation_enabled": False, "cloth_asset_path": None,
            "render_asset_path": record["render_asset_path"],
            "manifest_sha256": record["manifest_sha256"], "sim_vertices": 0,
            "render_vertices": len(packet["meshes"]["render"]["vertices"]),
            "render_only_vertices": len(packet["meshes"]["render"]["vertices"]),
            "guide_count": 0, "no_guide_policy": "render_only_skinning"}


def _verify_render_only_binding(receipt, expected_count):
    if expected_count and (receipt.get("render_only_vertices") != expected_count or
            receipt.get("render_only_skinning_blend_one") is not True or
            receipt.get("render_only_effective_sim_influences") != 0 or
            receipt.get("render_only_dormant_mapping_indices_valid") is not True or
            receipt.get("simulation_geometry_preserved") is not True):
        raise RuntimeError("Native helper did not verify render-only skinning for unguided geometry")


def _graph(unreal, asset):
    graph = asset.get_editor_property("dataflow_instance").get_editor_property("dataflow_asset")
    if not graph:
        raise ValueError("Cloth physics template requires an editable Dataflow graph")
    tool = unreal.get_default_object(unreal.DataflowAgentToolset)
    call = lambda name, *args: tool.call_method(name, args=args)
    structure = json.loads(call("GetGraphStructure", graph))
    names = {n["guid"].replace("-", "").upper(): n["name"] for n in structure["nodes"]}
    nodes, infos = {}, {}
    for node in unreal.ObjectIterator(unreal.DataflowEdNode):
        if node.get_typed_outer(unreal.Dataflow) == graph:
            info = json.loads(call("GetNodeInfo", node))
            name = names.get(info["guid"].replace("-", "").upper())
            if name:
                nodes[name], infos[name] = node, info
    return graph, call, structure, nodes, infos


def _build_source(unreal, record, path):
    asset = unreal.EditorAssetLibrary.duplicate_asset(record["cloth_template_asset_path"], path)
    if not asset:
        raise RuntimeError("Failed to duplicate the cloth physics template")
    graph, call, structure, nodes, infos = _graph(unreal, asset)
    terminals = [n for n, info in infos.items() if "TerminalNode" in info["type"]]
    if len(terminals) != 1:
        raise ValueError("Physics template must have one cloth terminal")
    terminal = terminals[0]
    incoming = {(e["toNode"], e["toPin"]): e["fromNode"] for e in structure["connections"]
                if e["fromPin"] == "Collection"}
    chain, current, seen = [], incoming.get((terminal, "CollectionLods[0]")), set()
    while current:
        if current in seen:
            raise ValueError("Cloth template contains a cycle")
        seen.add(current); chain.append(current)
        current = incoming.get((current, "Collection"))
    chain.reverse()
    config = [n for n in chain if infos[n]["type"].startswith("FChaosClothAssetSimulation")
              and "Config" in infos[n]["type"]]
    maxima = [n for n in config if "MaxDistanceConfig" in infos[n]["type"]]
    if len(maxima) != 1:
        raise ValueError("Physics template requires one MaxDistance config on LOD0")
    for name, node in list(nodes.items()):
        if name not in config and name != terminal:
            call("RemoveNode", graph, node)
            del nodes[name]
    def add(typ, name, props):
        node = call("AddNode", graph, typ, name, json.dumps(props), -900 + len(nodes) * 220, 0)
        if not node:
            raise RuntimeError("Could not create cloth node " + name)
        nodes[name] = node
        return name
    def link(a, b, output="Collection", input="Collection"):
        if not call("ConnectNodePins", nodes[a], output, nodes[b], input):
            raise RuntimeError("Could not connect " + a + " → " + b)
    for role in ("sim", "render"):
        add("FChaosClothAssetSkeletalMeshImportNode_v2", role.title() + "Import", {
            "SkeletalMesh": record[role + "_asset_path"], "bImportSimMesh": role == "sim",
            "bImportRenderMesh": role == "render", "bImportSingleSection": False,
            "bSetPhysicsAsset": False, "UVChannel": -1})
    add("FChaosClothAssetMergeClothCollectionsNode_v2", "MergeGuideAndRender", {})
    link("SimImport", "MergeGuideAndRender", input="Collections[0]")
    link("RenderImport", "MergeGuideAndRender", input="Collections[1]")
    add("FChaosClothAssetWeightMapNode", "GuideOwnG", {
        "OutputName": {"StringValue": "MaxDistance"}, "MeshTarget": "Simulation", "MapOverrideType": "ReplaceAll"})
    add("FChaosClothAssetSetPhysicsAssetNode", "BodyPhysics", {"PhysicsAsset": record["physics_asset_path"]})
    add("FChaosClothAssetProxyDeformerNode_v3", "EmbeddingInput", {
        "bUseMultipleInfluences": False, "bPreserveRenderTangents": True})
    add("FChaosClothAssetSkinningBlendNode", "SimulationKinematicBlend", {
        "KinematicVertices3D": {"StringValue": "KinematicVertices3D"}, "bUseSmoothTransition": True})
    # Both FBXs carry the exporter's own skeleton and skin weights. A body
    # closest-point transfer here would overwrite them around the jaw/face.
    order = ["MergeGuideAndRender", "GuideOwnG", "BodyPhysics"] + config + ["EmbeddingInput", "SimulationKinematicBlend"]
    for a, b in zip(order, order[1:]):
        link(a, b)
    link("GuideOwnG", maxima[0], "OutputName.StringValue", "MaxDistance.WeightMap")
    link(maxima[0], "SimulationKinematicBlend", "KinematicVertices3D", "KinematicVertices3D.StringValue")
    terminal_inputs = [p["name"] for p in infos[terminal]["inputPins"] if p["name"].startswith("CollectionLods[")]
    for pin in terminal_inputs:
        link(order[-1], terminal, input=pin)
    # The initial unrestricted embedding is a build input only. It is never
    # published: the native repair replaces EVERY vertex using generator IDs.
    baked = _native(unreal.CodexClothToolsLibrary.bake_vertex_color_weight_map(
        path, record["sim_asset_path"], "GuideOwnG", "G", 1., False, True))
    if baked.get("unmatched_sim_vertices", 1) or baked.get("snap_distance_max_cm", 1.) > .001:
        raise ValueError("Simulation G could not be transferred exactly")
    audit = _native(unreal.CodexClothToolsLibrary.dump_cloth_collection_colors(path))
    if not unreal.EditorAssetLibrary.save_loaded_asset(asset):
        raise RuntimeError("Could not save the cloth build source")
    return asset, audit


def _publish(unreal, source_asset, binding_path, target, owner, content):
    # Verify a new two-node graph before touching a previously published asset.
    stage_path = source_asset.get_path_name().split(".", 1)[0] + "_FinalStage"
    stage = unreal.EditorAssetLibrary.duplicate_asset(source_asset.get_path_name(), stage_path)
    graph, call, _, nodes, infos = _graph(unreal, stage)
    terminal = next(n for n, i in infos.items() if "TerminalNode" in i["type"])
    for name, node in nodes.items():
        if name != terminal:
            call("RemoveNode", graph, node)
    imported = call("AddNode", graph, "FChaosClothAssetImportNode", "GeneratorBindings",
                    json.dumps({"ClothAsset": binding_path, "ImportLod": 0}), -300, 0)
    for pin in infos[terminal]["inputPins"]:
        if pin["name"].startswith("CollectionLods["):
            if not call("ConnectNodePins", imported, "Collection", nodes[terminal], pin["name"]):
                raise RuntimeError("Failed to connect the validated cloth import")
    unreal.DataflowBlueprintLibrary.evaluate_terminal_node_by_name(graph, terminal, stage)
    before = _native(unreal.CodexClothToolsLibrary.dump_cloth_collection_colors(binding_path))
    after = _native(unreal.CodexClothToolsLibrary.dump_cloth_collection_colors(stage_path))
    if before["collections"][0] != after["collections"][0]:
        raise RuntimeError("Final import changed the validated cloth collection")
    binding_key = "Send2UE.HairGuideCloth.BindingData"
    for key, value in ((OWNER, owner), (CONTENT, content), (binding_key, binding_path)):
        unreal.EditorAssetLibrary.set_metadata_tag(stage, key, value)
    if not unreal.EditorAssetLibrary.save_loaded_asset(stage):
        raise RuntimeError("Validated cloth staging asset could not be saved")
    if not unreal.EditorAssetLibrary.does_asset_exist(target):
        final = unreal.EditorAssetLibrary.duplicate_asset(stage_path, target)
        if not final:
            raise RuntimeError("Validated cloth could not be published")
        # Package UMetaData is not guaranteed to follow UObject duplication.
        for key, value in ((OWNER, owner), (CONTENT, content), (binding_key, binding_path)):
            unreal.EditorAssetLibrary.set_metadata_tag(final, key, value)
        if not unreal.EditorAssetLibrary.save_loaded_asset(final):
            raise RuntimeError("Validated cloth could not be published")
    else:
        final = unreal.load_asset(target)
        if unreal.EditorAssetLibrary.get_metadata_tag(final, OWNER) != owner:
            raise ValueError("Cloth output belongs to another asset: " + target)
        old_content = unreal.EditorAssetLibrary.get_metadata_tag(final, CONTENT)
        old_binding = unreal.EditorAssetLibrary.get_metadata_tag(final, binding_key)
        fg, fc, _, fn, fi = _graph(unreal, final)
        imports = [n for n, info in fi.items() if info["type"] == "FChaosClothAssetImportNode"]
        terminals = [n for n, info in fi.items() if "TerminalNode" in info["type"]]
        if len(fn) != 2 or len(imports) != 1 or len(terminals) != 1 or not old_binding:
            raise ValueError("Existing cloth graph has user changes; it remains unchanged")
        # GetNodeInfo exports actual property values as Unreal text, e.g.
        # /Script/ChaosClothAsset.ChaosClothAsset'/Game/Hair/Data.Data'. Metadata
        # alone cannot prove that the artist left this two-node graph unchanged.
        previous_import = fi[imports[0]].get("properties", {})
        reference = previous_import.get("ClothAsset")
        if isinstance(reference, str):
            reference = reference.strip()
            typed_reference = re.fullmatch(r"[A-Za-z0-9_./]+'([^']+)'", reference)
            if typed_reference:
                reference = typed_reference.group(1)
        expected_references = (old_binding, old_binding + "." + old_binding.rsplit("/", 1)[-1])
        if (reference not in expected_references or
                str(previous_import.get("ImportLod", "")).strip() != "0"):
            raise ValueError("Existing cloth Import values have user changes; it remains unchanged")
        try:
            fc("UpdateNode", fn[imports[0]], json.dumps({"ClothAsset": binding_path, "ImportLod": 0}))
            unreal.DataflowBlueprintLibrary.evaluate_terminal_node_by_name(fg, terminals[0], final)
            actual = _native(unreal.CodexClothToolsLibrary.dump_cloth_collection_colors(target))
            if actual["collections"][0] != before["collections"][0]:
                raise RuntimeError("Published cloth differs from its verified collection")
            unreal.EditorAssetLibrary.set_metadata_tag(final, CONTENT, content)
            unreal.EditorAssetLibrary.set_metadata_tag(final, binding_key, binding_path)
            if not unreal.EditorAssetLibrary.save_loaded_asset(final):
                raise RuntimeError("Updated cloth could not be saved")
        except Exception:
            fc("UpdateNode", fn[imports[0]], json.dumps({"ClothAsset": old_binding, "ImportLod": 0}))
            unreal.DataflowBlueprintLibrary.evaluate_terminal_node_by_name(fg, terminals[0], final)
            unreal.EditorAssetLibrary.set_metadata_tag(final, CONTENT, old_content)
            unreal.EditorAssetLibrary.set_metadata_tag(final, binding_key, old_binding)
            unreal.EditorAssetLibrary.save_loaded_asset(final)
            raise
    return after


def build_in_editor(record, output_directory):
    """Run in a cold native worker, or an editor with the current helper loaded."""
    import unreal
    record, packet = validate_record(record)
    output = Path(output_directory); output.mkdir(parents=True, exist_ok=True)
    if not record["simulation_enabled"]:
        receipt = _render_only_receipt(record, packet)
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
        return receipt
    if not callable(getattr(unreal.CodexClothToolsLibrary, "repair_cloth_proxy_bindings", None)):
        raise RuntimeError("Current CodexClothTools native binding helper is not loaded")
    unreal.SystemLibrary.execute_console_command(None, "Editor.AsyncSkinnedAssetCompilation 0")
    sim_mesh = unreal.load_asset(record["sim_asset_path"])
    render_mesh = unreal.load_asset(record["render_asset_path"])
    body_mesh = unreal.load_asset(record["body_mesh_asset_path"])
    if (sim_mesh.get_editor_property("skeleton") != render_mesh.get_editor_property("skeleton") or
            sim_mesh.get_editor_property("skeleton") != body_mesh.get_editor_property("skeleton")):
        raise ValueError("Guide and render imports do not share the export skeleton")
    folder, render_name = record["render_asset_path"].rsplit("/", 1)
    stem = re.sub(r"^(?:SK|SM)_", "", render_name)
    target = _target(record)
    owner = VERSION + ":" + record["render_asset_path"]
    if unreal.EditorAssetLibrary.does_asset_exist(target):
        existing = unreal.load_asset(target)
        if unreal.EditorAssetLibrary.get_metadata_tag(existing, OWNER) != owner:
            raise ValueError("Refusing to replace an unrelated cloth asset: " + target)
    content = record["manifest_sha256"]
    revision = content[:12] + "_" + uuid.uuid4().hex[:8]
    build_folder = folder + "/HairGuideCloth/" + stem + "/" + revision
    source_path, binding_path = build_folder + "/CA_BuildSource", build_folder + "/CA_BindingData"
    source, initial = _build_source(unreal, record, source_path)
    ownership = normalize_ownership(initial["collections"][0]["built_mapping"], packet)
    # Sharing a USkeleton does not guarantee identical imported reference-pose
    # scales. The native helper uses the actual leader's reference skeleton,
    # after checking bone identity, without transferring or repainting weights.
    ownership["reference_skeletal_mesh_asset_path"] = record["body_mesh_asset_path"]
    ownership_path = output / "ownership.json"
    ownership_path.write_text(json.dumps(ownership), encoding="utf8")
    preview = _native(unreal.CodexClothToolsLibrary.repair_cloth_proxy_bindings(
        source_path, binding_path, str(ownership_path), False))
    (output / "binding_preview.json").write_text(json.dumps(preview, indent=2), encoding="utf8")
    render_only_count = len(ownership["render_only_vertex_indices"])
    _verify_render_only_binding(preview, render_only_count)
    repaired = _native(unreal.CodexClothToolsLibrary.repair_cloth_proxy_bindings(
        source_path, binding_path, str(ownership_path), True))
    _verify_render_only_binding(repaired, render_only_count)
    binding = unreal.load_asset(binding_path)
    if not binding or not unreal.EditorAssetLibrary.save_loaded_asset(binding):
        raise RuntimeError("Validated binding collection could not be saved")
    final = _publish(unreal, source, binding_path, target, owner, content)
    native = final["collections"][0]["built_mapping"]
    receipt = {"verified": True, "version": record["version"], "cloth_asset_path": target,
               "simulation_enabled": True, "no_guide_policy": "render_only_skinning",
               "sim_asset_path": record["sim_asset_path"], "render_asset_path": record["render_asset_path"],
               "binding_asset_path": binding_path, "build_source_asset_path": source_path,
               "manifest_sha256": content, "source_matching": ownership["provenance"]["source_matching"],
               "sim_vertices": len(native["sim_positions"]), "render_vertices": len(native["render_positions"]),
               "guide_count": len(set(ownership["sim_guide_ids"])), "remesh": False,
               "render_only_vertices": render_only_count,
               "weight_source": "simulation_source_own_g",
               "reference_skeletal_mesh_asset_path": record["body_mesh_asset_path"],
               "native_binding": repaired}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
    return receipt


def _apply_hair_guide_cloth(record):
    """Ordinary Send2UE post-import entry. Require a completed verified receipt."""
    import unreal
    record, packet = validate_record(record)
    if not record["simulation_enabled"]:
        render = unreal.load_asset(record["render_asset_path"])
        if not render:
            raise ValueError("Imported render mesh is missing: " + record["render_asset_path"])
        if not unreal.EditorAssetLibrary.save_loaded_asset(render):
            raise RuntimeError("Imported render-only hair mesh could not be saved")
        return _render_only_receipt(record, packet)
    assets = []
    for key in ("sim_asset_path", "render_asset_path", "cloth_template_asset_path",
                "body_mesh_asset_path", "physics_asset_path"):
        asset = unreal.load_asset(record[key])
        if not asset:
            raise ValueError("Required imported asset is missing: " + record[key])
        assets.append(asset)
    dirty = {p.get_path_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()}
    target = _target(record)
    if unreal.EditorAssetLibrary.does_asset_exist(target):
        current = unreal.load_asset(target)
        expected_owner = VERSION + ":" + record["render_asset_path"]
        if unreal.EditorAssetLibrary.get_metadata_tag(current, OWNER) != expected_owner:
            raise ValueError("Existing cloth output belongs to another asset and remains unchanged")
        if current.get_outer().get_path_name() in dirty:
            raise ValueError("Existing cloth has unsaved edits and remains unchanged")
        assets.append(current)
    for asset in assets[2:]:
        if asset.get_outer().get_path_name() in dirty:
            raise ValueError("Cloth template/body has unsaved edits; save it before Send to Unreal: " + asset.get_path_name())
    for asset in assets[:2]:
        if not unreal.EditorAssetLibrary.save_loaded_asset(asset):
            raise RuntimeError("Imported hair mesh could not be saved")
    project = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.get_project_file_path())).resolve()
    run = project.parent / "Saved" / "HairGuideCloth" / uuid.uuid4().hex
    run.mkdir(parents=True)
    # Releasing package file handles allows the isolated native worker to write
    # new collections while the user's current level and editor stay open.
    unreal.EditorLoadingAndSavingUtils.fully_load_assets(assets)
    worker_path = project.parent / "Scripts" / "HairGuideCloth" / "native_worker.py"
    if not worker_path.is_file():
        raise RuntimeError("Install the project's Scripts/HairGuideCloth native worker first")
    spec = importlib.util.spec_from_file_location("send2ue_hair_native_worker", worker_path)
    worker = importlib.util.module_from_spec(spec); sys.modules[spec.name] = worker; spec.loader.exec_module(worker)
    script = run / "build.py"
    script.write_text("import importlib.util,sys\n"
                      "s=importlib.util.spec_from_file_location('send2ue_hair_builder'," + repr(str(Path(__file__).resolve())) + ")\n"
                      "m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)\n"
                      "m.build_in_editor(" + repr(record) + "," + repr(str(run)) + ")\n", encoding="utf8")
    engine = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.engine_dir())).resolve().parent
    result = worker.run_script(str(script), project_path=str(project), engine_root=str(engine),
                               log_path=str(run / "worker.log"), timeout=900)
    receipt_path = run / "receipt.json"
    if not result.get("ok") or not receipt_path.is_file():
        raise RuntimeError("Hair guide cloth build failed: " + str(result))
    receipt = json.loads(receipt_path.read_text(encoding="utf8"))
    unreal.AssetRegistryHelpers.get_asset_registry().scan_paths_synchronous(
        [receipt["cloth_asset_path"].rsplit("/", 1)[0]], force_rescan=True)
    final = unreal.load_asset(receipt["cloth_asset_path"])
    if not final:
        raise RuntimeError("New cloth asset was saved but could not be loaded")
    if not unreal.EditorLoadingAndSavingUtils.reload_packages([final.get_outer()], unreal.ReloadPackagesInteractionMode.ASSUME_NEGATIVE):
        raise RuntimeError("New cloth was saved but the editor could not reload it")
    final = unreal.load_asset(receipt["cloth_asset_path"])
    if (unreal.EditorAssetLibrary.get_metadata_tag(final, CONTENT) != receipt["manifest_sha256"] or
            unreal.EditorAssetLibrary.get_metadata_tag(final, "Send2UE.HairGuideCloth.BindingData") != receipt["binding_asset_path"]):
        raise RuntimeError("The editor still has an older cloth result loaded")
    return receipt


def apply_hair_guide_cloth(record):
    """Keep the ordinary export successful when optional cloth cannot be built.

    No extra fallback or paint rule is inferred from a failed validation. The
    already imported render mesh and the previous cloth remain available.
    """
    try:
        return _apply_hair_guide_cloth(record)
    except Exception as error:
        receipt = {"version": 1, "verified": False, "status": "cloth_not_updated",
                   "render_asset_path": record.get("render_asset_path") if isinstance(record, dict) else None,
                   "detail": str(error)}
        print("SEND2UE_HAIR_GUIDE_CLOTH_INFO:" + json.dumps(receipt, ensure_ascii=False))
        return receipt
