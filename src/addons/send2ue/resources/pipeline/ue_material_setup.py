"""
UE Material Setup (JSON 기반 텍스처 직접 import)
================================================
send2ue post_import extension(send2ue_material_pipeline.py)이 import 직후
process_mesh(asset_path) 를 RPC 로 호출한다. 수동 실행도 가능.

블렌더 애드온이 남긴 JSON 사이드카(exports/<mesh_name>.json)를 읽어:
  1. 각 머티리얼의 텍스처를 디스크 파일에서 /Game/Textures 로 직접 import
     → FBX 가 못 나르는 MetallicRoughness 포함 모든 맵이 확실히 들어온다.
  2. 텍스처 종류별로 sRGB / 노멀맵 압축을 설정.
  3. Create or load a preset material instance, then assign shared texture data.
  4. JSON 의 slot_index 로 메쉬 슬롯에 MI 할당.

가드: 머티리얼명에서 'M_' 를 뗀 것이 메쉬명과 정확히 같거나 '메쉬명_' 으로 시작할 때만 처리.
"""

import json
import os
import re
import unreal

# ─── 설정 ────────────────────────────────────────────────────────────────────
DEFAULT_MASTER_PRESET = "prop"
MASTER_PRESETS = {
    "prop": {
        "master": "/Game/Material/Mesh/M_Prop_Master",
        "mi_folder": "/Game/Material/Mesh/MI_Prop_Master",
        "assignment": "flat",
    },
    "layer": {
        "master": "/Game/Material/Layer/M_LayerBlend",
        "mi_folder": "/Game/Material/Layer",
        "assignment": "layer",
    },
    "cloth": {
        "master": "/Game/Material/Layer/M_LayerBlend",
        "mi_folder": "/Game/Material/Layer",
        "assignment": "layer",
    },
    "asset_surface": {
        "master": "/Game/Material/AssetSurface/M_AssetSurface_Master",
        "mi_folder": "/Game/Material/AssetSurface/MI",
        "assignment": "layer",
    },
    "coat": {
        "master": "/Game/Material/Mesh/M_Coat_Fabric_Substrate_Master",
        "mi_folder": "/Game/Material/Mesh/MI_Coat_Master",
        "assignment": "coat_flat",
    },
}
# 반투명(유리) 머티리얼은 전용 MI 를 만들지 않고 이 공유 글래스 MI 를 슬롯에 직접 할당한다.
# (Megascan 글래스를 프로젝트로 localize 한 인스턴스. 부모 M_MS_Glass_Material, TRANSLUCENT)
GLASS_MI_PATH        = "/Game/Material/Mesh/MI_Prop_Master/MI_Prop_Glass_01"
TEXTURES_FOLDER      = "/Game/Textures"
EXPORT_DIR           = r"C:/Users/PARK/Documents/UE_Blender_Pipeline/exports"
JSON_SEARCH_ROOTS    = [
    r"C:/Users/PARK/OneDrive/Forestportfolio",
]
# /Game/Meshes/<rel> 은 디스크의 JSON_SEARCH_ROOTS[0]/<rel> 에 1:1 대응(send2ue 자동경로 규칙).
# 이를 이용해 mesh_path 로부터 해당 프롭 폴더만 좁혀 JSON 을 찾는다 → 3만개 트리 전체 walk 회피.
GAME_MESHES_PREFIX   = "/Game/Meshes/"

# Shared surface-layer texture parameter names (JSON 의 param 과 동일해야 연결됨)
KNOWN_PARAMS = {"Albedo", "Extra", "Normal", "Height", "Transmission", "Emissive"}
LAYER_PARAM_BY_LEGACY_PARAM = {
    "BaseColor": "Albedo",
    "MetallicRoughness": "Extra",
    "Roughness": "Extra",
    "Metallic": "Extra",
    "Occlusion": "Extra",
    "Normal": "Normal",
    "Height": "Height",
    "Alpha": "Transmission",
    "Emissive": "Emissive",
    "Texture": "Albedo",
}
FLAT_PARAM_BY_LAYER_PARAM = {
    "Albedo": "BaseColor",
    "Extra": "MetallicRoughness",
    "Normal": "Normal",
    "Emissive": "Emissive",
}
COAT_PARAM_BY_LAYER_PARAM = {
    "Albedo": "BaseColor",
    "Extra": "ORM",
    "Normal": "Normal",
}

# import 되는 텍스처의 인게임 최대 해상도 캡(param 별). 목록에 없으면 DEFAULT 적용.
MAX_TEXTURE_SIZE_BY_PARAM = {
    "Albedo": 2048,   # 2K
    "BaseColor": 2048,   # legacy JSON
    "Normal":    2048,   # 2K
}
DEFAULT_MAX_TEXTURE_SIZE = 1024   # MetallicRoughness/Emissive/기타 → 1K
ENABLE_VIRTUAL_TEXTURE_STREAMING = True
# import 되는 StaticMesh 를 자동으로 Nanite 로 등록할지(반투명 머티리얼 메쉬는 자동 제외).
ENABLE_NANITE = True
# ─────────────────────────────────────────────────────────────────────────────


# 텍스처 재import 회피용 mtime 캐시 (텍스처 asset path -> 소스파일 mtime).
# send 마다 OneDrive 에서 모든 텍스처를 다시 읽지 않도록, 소스가 안 바뀌었고 에셋이 이미
# 있으면 import 를 건너뛴다. 소스를 수정하면 mtime 이 바뀌어 자동으로 재import 된다.
TEXTURE_IMPORT_CACHE = os.path.join(EXPORT_DIR, "_texture_import_cache.json")


def _load_texture_cache() -> dict:
    try:
        with open(TEXTURE_IMPORT_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_texture_cache(cache: dict):
    try:
        os.makedirs(os.path.dirname(TEXTURE_IMPORT_CACHE), exist_ok=True)
        with open(TEXTURE_IMPORT_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception as e:
        _warn(f"텍스처 캐시 저장 실패(무시): {e}")


def _log(msg):
    unreal.log(f"[Blender Pipeline] {msg}")


def _warn(msg):
    unreal.log_warning(f"[Blender Pipeline] {msg}")


def _mesh_path_to_disk_folder(mesh_path: str):
    """import 된 메쉬의 /Game/Meshes 경로를 디스크 프롭 폴더로 역산. 매핑 밖이면 None.
    예: /Game/Meshes/00_common/prop/Prop_X → <root>/00_common/prop"""
    if not mesh_path or not mesh_path.startswith(GAME_MESHES_PREFIX) or not JSON_SEARCH_ROOTS:
        return None
    rel = mesh_path[len(GAME_MESHES_PREFIX):]
    rel_folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    return os.path.join(JSON_SEARCH_ROOTS[0], rel_folder.replace("/", os.sep))


def _walk_for_json(roots, filename):
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            if filename in filenames:
                found.append(os.path.join(dirpath, filename))
    return found


def _find_json_path(mesh_name: str, mesh_path: str = None):
    # 빠른 경로: mesh_path 로 프롭 폴더를 역산해 그 작은 서브트리만 walk(3만개 OneDrive 트리 전체 walk 회피).
    # 못 찾으면 기존처럼 JSON_SEARCH_ROOTS 전체로 폴백한다.
    filename = f"{mesh_name}.json"
    candidates = []

    disk_folder = _mesh_path_to_disk_folder(mesh_path)
    if disk_folder and os.path.isdir(disk_folder):
        candidates = _walk_for_json([disk_folder], filename)

    if not candidates:
        candidates = _walk_for_json([r for r in JSON_SEARCH_ROOTS if os.path.isdir(r)], filename)

    legacy_path = os.path.join(EXPORT_DIR, filename)
    if os.path.exists(legacy_path):
        candidates.append(legacy_path)

    if not candidates:
        return None
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _load_json(mesh_name: str, explicit_path: str = None, mesh_path: str = None):
    # extension 이 정확한 JSON 경로를 넘겨주면 walk 를 건너뛴다. 없으면 mesh_path 로 폴더를 좁힌다.
    if explicit_path and os.path.exists(explicit_path):
        path = explicit_path
    else:
        path = _find_json_path(mesh_name, mesh_path)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _log(f"JSON sidecar: {path}")
        return data
    except Exception as e:
        _warn(f"JSON 읽기 실패 ({mesh_name}.json): {e}")
        return None


def _set_texture_property_if_changed(tex, property_name: str, value) -> bool:
    try:
        current = tex.get_editor_property(property_name)
    except Exception:
        return False
    if current == value:
        return False
    tex.set_editor_property(property_name, value)
    return True


def _configure_imported_texture(tex, param: str) -> bool:
    changed = False

    if param == "Normal":
        changed |= _set_texture_property_if_changed(tex, "srgb", False)
        changed |= _set_texture_property_if_changed(
            tex,
            "compression_settings",
            unreal.TextureCompressionSettings.TC_NORMALMAP,
        )
    elif param in {"Extra", "MetallicRoughness", "Roughness", "Metallic", "Occlusion"}:
        changed |= _set_texture_property_if_changed(tex, "srgb", False)
        changed |= _set_texture_property_if_changed(
            tex,
            "compression_settings",
            unreal.TextureCompressionSettings.TC_MASKS,
        )
    else:
        changed |= _set_texture_property_if_changed(tex, "srgb", True)

    max_size = MAX_TEXTURE_SIZE_BY_PARAM.get(param, DEFAULT_MAX_TEXTURE_SIZE)
    if max_size:
        changed |= _set_texture_property_if_changed(tex, "max_texture_size", max_size)

    if ENABLE_VIRTUAL_TEXTURE_STREAMING:
        try:
            before = bool(tex.get_editor_property("virtual_texture_streaming"))
        except Exception:
            before = False
        if not before:
            if hasattr(tex, "set_virtual_texture_streaming"):
                tex.set_virtual_texture_streaming(True)
            else:
                tex.set_editor_property("virtual_texture_streaming", True)
            changed = True

    return changed


def _import_texture(
    file_path: str,
    asset_name: str,
    param: str,
    tex_cache: dict = None,
    force_reimport: bool = False,
):
    """디스크 텍스처를 TEXTURES_FOLDER 로 직접 import 하고 종류별 설정 적용. asset path 반환.

    tex_cache 가 주어지면, 소스파일 mtime 이 캐시와 같고 에셋이 이미 있을 때 import 를 건너뛴다.
    """
    if not asset_name:
        _warn(f"  텍스처 asset_name 없음 — skip ({file_path})")
        return None
    if not file_path or not os.path.exists(file_path):
        _warn(f"  텍스처 파일 없음: {asset_name} ({file_path})")
        return None

    full_path = f"{TEXTURES_FOLDER}/{asset_name}"

    # mtime 캐시 히트면 재import 생략(OneDrive 재읽기/하이드레이션 방지). getmtime 은 메타데이터만
    # 읽으므로 클라우드 파일을 받아오지 않는다.
    try:
        source_mtime = os.path.getmtime(file_path)
    except OSError:
        source_mtime = None
    if unreal.EditorAssetLibrary.does_asset_exist(full_path) and not force_reimport:
        tex = unreal.load_asset(full_path)
        if tex is not None and _configure_imported_texture(tex, param):
            unreal.EditorAssetLibrary.save_asset(full_path)
        if tex_cache is not None and source_mtime is not None:
            tex_cache[full_path] = source_mtime
        _log(f"  texture exists, import skipped: {asset_name} ({param})")
        return full_path

    if (tex_cache is not None and source_mtime is not None
            and tex_cache.get(full_path) == source_mtime
            and unreal.EditorAssetLibrary.does_asset_exist(full_path)
            and not force_reimport):
        tex = unreal.load_asset(full_path)
        if tex is not None and _configure_imported_texture(tex, param):
            unreal.EditorAssetLibrary.save_asset(full_path)
        return full_path

    task = unreal.AssetImportTask()
    task.set_editor_property('filename', file_path)
    task.set_editor_property('destination_path', TEXTURES_FOLDER)
    task.set_editor_property('destination_name', asset_name)
    task.set_editor_property('automated', True)
    task.set_editor_property('replace_existing', bool(force_reimport))
    task.set_editor_property('save', True)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    tex = unreal.load_asset(full_path)
    if tex is None:
        _warn(f"  텍스처 import 실패: {asset_name}")
        return None

    _configure_imported_texture(tex, param)

    unreal.EditorAssetLibrary.save_asset(full_path)
    if tex_cache is not None and source_mtime is not None:
        tex_cache[full_path] = source_mtime
    _log(f"  텍스처 import: {asset_name} ({param})")
    return full_path


def _set_nanite(mesh, enabled: bool) -> bool:
    """StaticMesh 의 Nanite 활성 상태를 enabled 로 맞춘다. 변경이 있었으면 True."""
    nanite = mesh.get_editor_property("nanite_settings")
    if bool(nanite.get_editor_property("enabled")) == enabled:
        return False
    nanite.set_editor_property("enabled", enabled)
    mesh.set_editor_property("nanite_settings", nanite)
    _log(f"  Nanite {'활성화' if enabled else '비활성화(반투명)'}")
    return True


def _sync_browser_to_mesh(mesh_path: str):
    """Content Browser 를 import 된 메쉬로 이동/선택시킨다.
    (텍스처 import 가 마지막이라 브라우저가 /Game/Textures 로 튀는 것을 되돌림)"""
    try:
        unreal.EditorAssetLibrary.sync_browser_to_objects([mesh_path])
    except Exception as e:
        _warn(f"  브라우저 동기화 실패(무시): {e}")


def _is_translucent(data) -> bool:
    """JSON 머티리얼 중 하나라도 반투명이면 True. (블렌더 애드온이 'translucent' 플래그를 씀)"""
    if not data:
        return False
    return any(entry.get("translucent") for entry in data.get("materials", []))


def _same_asset(a, b) -> bool:
    return a is not None and b is not None and a.get_path_name() == b.get_path_name()


def _master_preset(data: dict, entry: dict = None) -> dict:
    entry = entry or {}
    key = (
        entry.get("material_master")
        or entry.get("master_material")
        or entry.get("master_preset")
        or data.get("material_master")
        or data.get("master_material")
        or data.get("master_preset")
        or DEFAULT_MASTER_PRESET
    )
    key = str(key or DEFAULT_MASTER_PRESET).strip().lower()
    preset = MASTER_PRESETS.get(key)
    if preset is None:
        _warn(f"unknown material master preset '{key}', using '{DEFAULT_MASTER_PRESET}'")
        key = DEFAULT_MASTER_PRESET
        preset = MASTER_PRESETS[key]
    result = dict(preset)
    result["key"] = key
    return result


def _load_master_material(preset: dict):
    master_path = preset["master"]
    master_mat = unreal.load_asset(master_path)
    if master_mat is None:
        _warn(f"마스터 머티리얼 없음: {master_path}")
    return master_mat


def _create_or_load_mi(asset_tools, master_mat, mat_base: str, mi_folder: str):
    mi_name = f"MI_{mat_base}"
    mi_path = f"{mi_folder}/{mi_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(mi_path):
        mi = unreal.load_asset(mi_path)
        parent_changed = False
        try:
            current_parent = mi.get_editor_property("parent")
        except Exception:
            current_parent = None
        if not _same_asset(current_parent, master_mat):
            unreal.MaterialEditingLibrary.set_material_instance_parent(mi, master_mat)
            _log(f"  MI parent 변경: {mi_name} -> {master_mat.get_path_name()}")
            parent_changed = True
        return mi, mi_path, False, parent_changed, "existing"

    copy_from_path = _derive_number_suffix_copy_source(mi_path)
    if copy_from_path:
        mi_folder_path, _mi_name = mi_path.rsplit("/", 1)
        source = unreal.load_asset(copy_from_path)
        copied = None
        try:
            copied = unreal.EditorAssetLibrary.duplicate_asset(copy_from_path, mi_path)
        except Exception as e:
            _warn(f"  MI suffix copy failed, trying AssetTools: {copy_from_path} -> {mi_path} ({e})")
        if copied is None and source is not None:
            try:
                copied = asset_tools.duplicate_asset(mi_name, mi_folder_path, source)
            except Exception as e:
                _warn(f"  MI suffix AssetTools copy failed: {copy_from_path} -> {mi_path} ({e})")
        if copied is not None:
            _log(f"  MI copy: {copy_from_path} -> {mi_path}")
            return copied, mi_path, True, False, "copy"

    unreal.EditorAssetLibrary.make_directory(mi_folder)
    factory = unreal.MaterialInstanceConstantFactoryNew()
    mi = asset_tools.create_asset(
        mi_name, mi_folder, unreal.MaterialInstanceConstant, factory
    )
    if mi is None:
        _warn(f"MI 생성 실패: {mi_name}")
        return None, mi_path, False, False, "missing"
    unreal.MaterialEditingLibrary.set_material_instance_parent(mi, master_mat)
    _log(f"  MI 생성: {mi_name}")
    return mi, mi_path, True, False, "new"


def _entry_target_material_path(entry: dict):
    for key in (
        "target_material_path",
        "material_instance_path",
        "unreal_material_path",
    ):
        value = str(entry.get(key) or "").strip()
        if value:
            return value.split(".")[0]
    return None


def _entry_copy_source_material_path(entry: dict):
    for key in (
        "copy_from_material_path",
        "source_material_path",
    ):
        value = str(entry.get(key) or "").strip()
        if value:
            return value.split(".")[0]
    return None


def _derive_number_suffix_copy_source(target_path: str):
    if not target_path or "/" not in target_path:
        return None
    target_folder, target_name = target_path.rsplit("/", 1)
    match = re.match(r"(.+)_\d+$", target_name)
    if not match:
        return None
    source_path = f"{target_folder}/{match.group(1)}"
    if unreal.EditorAssetLibrary.does_asset_exist(source_path):
        return source_path
    return None


def _load_or_copy_target_material(
    asset_tools,
    target_path: str,
    copy_from_path: str = None,
    master_mat=None,
):
    if unreal.EditorAssetLibrary.does_asset_exist(target_path):
        return unreal.load_asset(target_path), target_path, False, "existing"

    if not copy_from_path:
        copy_from_path = _derive_number_suffix_copy_source(target_path)

    if not copy_from_path:
        if master_mat is None:
            _warn(f"  target material missing and no master: {target_path}")
            return None, target_path, False, "missing"
        target_folder, target_name = target_path.rsplit("/", 1)
        unreal.EditorAssetLibrary.make_directory(target_folder)
        factory = unreal.MaterialInstanceConstantFactoryNew()
        mi = asset_tools.create_asset(
            target_name,
            target_folder,
            unreal.MaterialInstanceConstant,
            factory,
        )
        if mi is None:
            _warn(f"  target material create failed: {target_path}")
            return None, target_path, False, "missing"
        unreal.MaterialEditingLibrary.set_material_instance_parent(mi, master_mat)
        unreal.EditorAssetLibrary.save_asset(target_path, only_if_is_dirty=False)
        _log(f"  MI create: {target_path}")
        return mi, target_path, True, "new"
    if not unreal.EditorAssetLibrary.does_asset_exist(copy_from_path):
        _warn(f"  copy source material missing: {copy_from_path} -> {target_path}")
        return None, target_path, False, "missing"

    target_folder, target_name = target_path.rsplit("/", 1)
    unreal.EditorAssetLibrary.make_directory(target_folder)

    copied = None
    try:
        copied = unreal.EditorAssetLibrary.duplicate_asset(copy_from_path, target_path)
    except Exception as e:
        _warn(f"  duplicate_asset failed, trying AssetTools: {copy_from_path} -> {target_path} ({e})")

    if copied is None:
        source = unreal.load_asset(copy_from_path)
        try:
            copied = asset_tools.duplicate_asset(target_name, target_folder, source)
        except Exception as e:
            _warn(f"  AssetTools duplicate failed: {copy_from_path} -> {target_path} ({e})")
            copied = None

    if copied is None:
        _warn(f"  target material copy failed: {copy_from_path} -> {target_path}")
        return None, target_path, False, "missing"

    unreal.EditorAssetLibrary.save_asset(target_path, only_if_is_dirty=False)
    _log(f"  MI copy: {copy_from_path} -> {target_path}")
    return copied, target_path, True, "copy"


def _static_material_name_values(static_material):
    names = []
    for prop in ("material_slot_name", "imported_material_slot_name"):
        try:
            value = static_material.get_editor_property(prop)
        except Exception:
            value = None
        if value:
            names.append(str(value))
    try:
        material = static_material.get_editor_property("material_interface")
    except Exception:
        material = None
    if material is not None:
        try:
            names.append(material.get_name())
        except Exception:
            pass
    return names


def _entry_slot_match_names(entry: dict, mat_name: str):
    names = []
    for value in (
        mat_name,
        entry.get("slot_name"),
        entry.get("material_slot_name"),
        entry.get("imported_slot_name"),
        entry.get("imported_material_slot_name"),
        entry.get("original_material_name"),
    ):
        value = str(value or "").strip()
        if value and value not in names:
            names.append(value)
    return names


def _slot_index_for_entry(mesh, entry: dict, mat_name: str):
    candidates = _entry_slot_match_names(entry, mat_name)
    if not candidates:
        return None
    candidate_set = set(candidates)
    candidate_folded = {name.casefold() for name in candidates}
    static_materials = mesh.get_editor_property("static_materials")
    for i, static_material in enumerate(static_materials):
        slot_names = _static_material_name_values(static_material)
        if any(name in candidate_set for name in slot_names):
            return i
    for i, static_material in enumerate(static_materials):
        slot_names = _static_material_name_values(static_material)
        if any(str(name).casefold() in candidate_folded for name in slot_names):
            return i
    return None


def _slot_index_for_material_name(mesh, mat_name: str):
    """메쉬의 실제 슬롯 중 import 된 머티리얼명이 mat_name 인 슬롯 인덱스. 없으면 None.
    Empty 결합(combine child meshes) export 처럼 JSON 의 slot_index 가 실제 결합 슬롯 순서와
    다를 수 있으므로, 슬롯을 이름으로 매칭해 정확한 인덱스를 찾는다."""
    return _slot_index_for_entry(mesh, {"name": mat_name}, mat_name)


def _assign_slot(mesh, slot_index: int, material) -> bool:
    """메쉬 슬롯에 머티리얼 할당. FBX import 결과 슬롯 수가 JSON 과 달라도 안전하게 가드."""
    static_materials = mesh.get_editor_property("static_materials")
    if slot_index < 0 or slot_index >= len(static_materials):
        _warn(f"  슬롯 인덱스 {slot_index} 가 메쉬 슬롯 수({len(static_materials)})를 벗어남 — 할당 skip")
        return False
    # 이미 같은 머티리얼이 할당돼 있으면 변경 없음 → 메쉬 재저장을 피한다.
    current = static_materials[slot_index].get_editor_property("material_interface")
    if current is not None and material is not None and current.get_path_name() == material.get_path_name():
        return False
    mesh.set_material(slot_index, material)
    return True


def _surface_layer_param(param: str) -> str:
    return LAYER_PARAM_BY_LEGACY_PARAM.get(str(param or ""), str(param or ""))


def _assign_layer_zero_textures(mi, param_tex_map: dict) -> bool:
    """Fallback for stale editors: Python can address only material layer index 0."""
    changed = False
    association = unreal.MaterialParameterAssociation.LAYER_PARAMETER
    for param, tex_path in param_tex_map.items():
        tex = unreal.load_asset(tex_path)
        if tex is None:
            continue
        try:
            current = unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(
                mi,
                param,
                association,
            )
        except Exception:
            current = None
        if current is not None and current.get_path_name() == tex.get_path_name():
            continue
        unreal.MaterialEditingLibrary.set_material_instance_texture_parameter_value(
            mi,
            param,
            tex,
            association,
        )
        _log(f"  layer[0] {param} -> {tex_path.split('/')[-1]} (python fallback)")
        changed = True
    return changed


def _entry_layers(entry: dict):
    layers = entry.get("layers") or []
    normalized = []
    for layer_index, layer in enumerate(layers):
        textures = []
        for texture in layer.get("textures", []):
            item = dict(texture)
            item["param"] = _surface_layer_param(item.get("param"))
            textures.append(item)
        normalized.append(
            {
                "name": str(layer.get("name") or f"Layer_{layer_index + 1:02d}"),
                "index": int(layer.get("index", layer_index)),
                "textures": textures,
            }
        )
    if normalized:
        return normalized

    textures = []
    for texture in entry.get("textures", []):
        item = dict(texture)
        item["param"] = _surface_layer_param(item.get("param"))
        textures.append(item)
    return [{"name": "Base", "index": 0, "textures": textures}] if textures else []


def _import_layer_textures(layers, tex_cache: dict, force_reimport: bool = False):
    layer_maps = []
    for layer in layers:
        param_tex_map = {}
        for texture in layer.get("textures", []):
            param = texture.get("param")
            tex_path = _import_texture(
                texture.get("file"),
                texture.get("asset_name"),
                param,
                tex_cache,
                force_reimport=force_reimport,
            )
            if tex_path and param in KNOWN_PARAMS:
                param_tex_map[param] = tex_path
        layer_maps.append(
            {
                "name": layer.get("name", "Base"),
                "index": int(layer.get("index", len(layer_maps))),
                "textures": param_tex_map,
            }
        )
    return layer_maps


def reimport_textures_from_json(json_path: str) -> int:
    json_path = os.path.abspath(str(json_path or ""))
    if not os.path.isfile(json_path):
        _warn(f"texture reimport JSON missing: {json_path}")
        return 0
    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        _warn(f"texture reimport JSON read failed: {json_path} ({exc})")
        return 0

    tex_cache = _load_texture_cache()
    imported = 0
    seen = set()
    for entry in data.get("materials", []):
        for layer in _entry_layers(entry):
            for texture in layer.get("textures", []):
                asset_name = texture.get("asset_name")
                param = texture.get("param")
                key = (asset_name, param)
                if key in seen:
                    continue
                seen.add(key)
                tex_path = _import_texture(
                    texture.get("file"),
                    asset_name,
                    param,
                    tex_cache,
                    force_reimport=True,
                )
                if tex_path:
                    imported += 1
    _save_texture_cache(tex_cache)
    _log(f"texture reimport complete: {imported} texture(s)")
    return imported


def _assign_surface_layer_textures(mi, layer_maps) -> bool:
    """Assign imported textures to a material instance using shared layer params.

    The Unreal Python API can address layer parameters by association but not by
    layer index. If the Codex C++ helper is available it handles indexed layer
    parameters; otherwise this falls back to the first layer only.
    """
    helper = getattr(unreal, "CodexMaterialToolsLibrary", None)
    if helper and hasattr(helper, "set_material_instance_layer_texture_parameter_value"):
        changed = False
        for layer in layer_maps:
            for param, tex_path in layer.get("textures", {}).items():
                tex = unreal.load_asset(tex_path)
                if tex is None:
                    continue
                if helper.set_material_instance_layer_texture_parameter_value(
                    mi,
                    str(param),
                    tex,
                    int(layer.get("index", 0)),
                ):
                    _log(
                        f"  layer[{int(layer.get('index', 0))}] {param} -> "
                        f"{tex_path.split('/')[-1]}"
                    )
                    changed = True
        return changed

    if len(layer_maps) > 1 or any(int(layer.get("index", 0)) != 0 for layer in layer_maps):
        _warn("  indexed layer helper missing; assigning only layer[0] textures")
    first_layer = next(
        (layer.get("textures", {}) for layer in layer_maps if int(layer.get("index", 0)) == 0),
        {},
    )
    return _assign_layer_zero_textures(mi, first_layer)


def _first_layer_textures(layer_maps) -> dict:
    return next(
        (layer.get("textures", {}) for layer in layer_maps if int(layer.get("index", 0)) == 0),
        {},
    )


def _assign_flat_textures(mi, layer_maps, param_map: dict, label: str) -> bool:
    changed = False
    for layer_param, tex_path in _first_layer_textures(layer_maps).items():
        flat_param = param_map.get(layer_param)
        if not flat_param:
            continue
        tex = unreal.load_asset(tex_path)
        if tex is None:
            continue
        try:
            current = unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(
                mi, flat_param
            )
        except Exception:
            current = None
        if current is not None and current.get_path_name() == tex.get_path_name():
            continue
        unreal.MaterialEditingLibrary.set_material_instance_texture_parameter_value(
            mi, flat_param, tex
        )
        _log(f"  {label} {flat_param} <- {tex_path.split('/')[-1]}")
        changed = True
    return changed


def _assign_master_textures(mi, layer_maps, assignment: str) -> bool:
    if assignment == "layer":
        return _assign_surface_layer_textures(mi, layer_maps)
    if assignment == "coat_flat":
        return _assign_flat_textures(mi, layer_maps, COAT_PARAM_BY_LAYER_PARAM, "coat")
    return _assign_flat_textures(mi, layer_maps, FLAT_PARAM_BY_LAYER_PARAM, "prop")


def _material_instance_base_name(mat_name: str) -> str:
    if mat_name.startswith("M_"):
        return mat_name[2:]
    if mat_name.startswith("MI_"):
        return mat_name[3:]
    return mat_name


def _source_asset_names(data: dict):
    material_names = []
    texture_names = []

    def add_texture_names(tex: dict):
        asset_name = str(tex.get("asset_name", ""))
        if asset_name:
            texture_names.append(asset_name)
        file_path = str(tex.get("file", ""))
        if file_path:
            source_name = os.path.splitext(os.path.basename(file_path))[0]
            if source_name:
                texture_names.append(source_name)

    for entry in data.get("materials", []):
        mat_name = str(entry.get("name", ""))
        if mat_name:
            material_names.append(mat_name)
        for tex in entry.get("textures", []):
            add_texture_names(tex)
        for layer in entry.get("layers", []):
            for tex in layer.get("textures", []):
                add_texture_names(tex)
    return material_names, texture_names


def _delete_asset_if_type(asset_path: str, allowed_class_names: set) -> bool:
    if not unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        return False
    asset = unreal.load_asset(asset_path)
    if asset is None:
        return False
    class_name = asset.get_class().get_name()
    if class_name not in allowed_class_names:
        _log(f"  cleanup skip: {asset_path} ({class_name})")
        return False
    if unreal.EditorAssetLibrary.delete_asset(asset_path):
        _log(f"  cleanup delete: {asset_path}")
        return True
    _warn(f"  cleanup delete failed: {asset_path}")
    return False


def _cleanup_imported_source_assets(mesh_path: str, data: dict):
    mesh_folder = mesh_path.rsplit("/", 1)[0]
    material_names, texture_names = _source_asset_names(data)

    deleted = 0
    for name in material_names:
        asset_path = f"{mesh_folder}/{name}"
        if asset_path == mesh_path:
            continue
        if _delete_asset_if_type(asset_path, {"Material", "MaterialInstanceConstant"}):
            deleted += 1

    for name in texture_names:
        asset_path = f"{mesh_folder}/{name}"
        if asset_path == mesh_path:
            continue
        if _delete_asset_if_type(asset_path, {"Texture2D"}):
            deleted += 1

    if deleted:
        # NOTE(perf): delete_asset 가 각 패키지를 디스크에서 즉시 지우고, 메쉬/MI/텍스처는 위에서
        #   이미 개별 save 됐으므로 여기서 추가로 flush 할 게 없다. 예전엔 save_directory(
        #   only_if_is_dirty=False)로 import 폴더 전체를 강제 재저장했는데, 모든 메쉬가 같은 폴더
        #   (/Game/untitled_category/untitled_asset)로 들어오는 데다 메쉬마다 호출돼서 폴더가
        #   쌓일수록 export 가 O(N^2) 로 느려졌다. 그래서 폴더 통째 save 는 하지 않는다.
        _log(f"  cleanup complete: {deleted} source asset(s) removed from {mesh_folder}")
    else:
        _log(f"  cleanup: no source material/texture assets found in {mesh_folder}")


def process_mesh(mesh_path: str, master_mat=None, json_path: str = None) -> bool:
    """단일 StaticMesh 를 JSON 기반으로 처리. 변경이 있었으면 True.

    json_path: send2ue extension 이 넘겨주는 JSON 절대경로(있으면 OneDrive walk 생략).
    """
    mesh_path = mesh_path.split(".")[0]
    mesh = unreal.load_asset(mesh_path)
    if not isinstance(mesh, unreal.StaticMesh):
        return False

    mesh_name = mesh_path.rsplit("/", 1)[-1]
    data = _load_json(mesh_name, json_path, mesh_path)

    # Nanite: import 되는 StaticMesh 에 켜되, 반투명 머티리얼 메쉬는 끈다.
    # (JSON 이 없으면 불투명으로 가정 → 켬. 반투명으로 판정되면 이미 켜져 있어도 끈다.)
    if ENABLE_NANITE and _set_nanite(mesh, not _is_translucent(data)):
        unreal.EditorAssetLibrary.save_asset(mesh_path)

    if data is None:
        _warn(f"JSON 사이드카 없음: {mesh_name}.json — skip (블렌더에서 Rename 버튼을 눌렀나요?)")
        return False

    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    changed = False
    json_mesh_name = str(data.get("mesh_name", ""))
    if json_mesh_name and json_mesh_name != mesh_name:
        _warn(f"JSON mesh_name mismatch: asset={mesh_name}, json={json_mesh_name}; using JSON data")

    # 텍스처 재import 회피 캐시(메쉬마다 reload 되므로 디스크에서 읽고, 바뀌면 끝에 저장).
    tex_cache = _load_texture_cache()
    tex_cache_before = dict(tex_cache)

    for entry in data.get("materials", []):
        mat_name = str(entry.get("name", ""))
        target_material_path = _entry_target_material_path(entry)
        legacy_generated_mi_flow = mat_name.startswith("M_")
        if not target_material_path and not legacy_generated_mi_flow:
            continue

        # 실제 슬롯을 머티리얼 이름으로 매칭(Empty 결합 export 대응). 없으면 JSON slot_index 로 폴백.
        slot_index = _slot_index_for_entry(mesh, entry, mat_name)
        if slot_index is None:
            if entry.get("slot_match_required"):
                _warn(f"  slot not found for target-only material '{mat_name}' -> skip")
                continue
            slot_index = int(entry.get("slot_index", 0))

        # 반투명(유리): 전용 MI/텍스처 없이 공유 글래스 MI 를 슬롯에 바로 할당.
        if entry.get("translucent"):
            glass_mi = unreal.load_asset(GLASS_MI_PATH)
            if glass_mi is None:
                _warn(f"  글래스 MI 없음: {GLASS_MI_PATH} — 슬롯[{slot_index}] skip")
                continue
            if _assign_slot(mesh, slot_index, glass_mi):
                _log(f"  슬롯[{slot_index}] '{mat_name}' → 글래스 MI 할당(공유)")
                changed = True
            continue

        preset = _master_preset(data, entry)
        if target_material_path:
            copy_from_path = _entry_copy_source_material_path(entry)
            selected_master = master_mat or _load_master_material(preset)
            if selected_master is None and not copy_from_path:
                continue
            mi, mi_path, mi_created, mi_source = _load_or_copy_target_material(
                asset_tools,
                target_material_path,
                copy_from_path,
                selected_master,
            )
            if mi is None:
                continue
            _log(
                f"  slot[{slot_index}] '{mat_name}' -> {mi_path} "
                f"(master: {preset['key']})"
            )
            params_changed = False
            if mi_source == "new":
                layers = _entry_layers(entry)
                layer_maps = _import_layer_textures(layers, tex_cache)
                params_changed = _assign_master_textures(mi, layer_maps, preset["assignment"])
            if mi_created or params_changed:
                unreal.EditorAssetLibrary.save_asset(mi_path)
                changed = True
            if _assign_slot(mesh, slot_index, mi):
                changed = True
            continue

        mat_base = _material_instance_base_name(mat_name)
        selected_master = master_mat or _load_master_material(preset)
        if selected_master is None:
            continue

        _log(
            f"  슬롯[{slot_index}] '{mat_name}' 처리 "
            f"(base: {mat_base}, master: {preset['key']})"
        )

        # 1. 텍스처 직접 import (소스가 안 바뀌었으면 캐시 히트로 skip)
        layers = None
        layer_maps = None

        # 2. MI 생성/로드
        mi, mi_path, mi_created, parent_changed, mi_source = _create_or_load_mi(
            asset_tools,
            selected_master,
            mat_base,
            preset["mi_folder"],
        )
        if mi is None:
            continue

        # 3. Assign textures using the selected master material contract.
        params_changed = False
        if mi_source == "new":
            layers = _entry_layers(entry)
            layer_maps = _import_layer_textures(layers, tex_cache)
            params_changed = _assign_master_textures(mi, layer_maps, preset["assignment"])
        if mi_created or parent_changed or params_changed:
            unreal.EditorAssetLibrary.save_asset(mi_path)
            changed = True

        # 4. 슬롯에 MI 할당
        if _assign_slot(mesh, slot_index, mi):
            changed = True

    # 이번 처리에서 새로 import 된 텍스처가 있으면 캐시 갱신
    if tex_cache != tex_cache_before:
        _save_texture_cache(tex_cache)

    _cleanup_imported_source_assets(mesh_path, data)

    if changed:
        unreal.EditorAssetLibrary.save_asset(mesh_path)
        _log(f"메쉬 '{mesh_name}' 완료")

    # 마지막으로 브라우저를 메쉬로 돌려 선택 상태로 끝낸다(텍스처 폴더로 튀는 것 방지).
    _sync_browser_to_mesh(mesh_path)
    return changed


def process_meshes(mesh_paths):
    count = 0
    for p in mesh_paths:
        if process_mesh(p):
            count += 1
    _log(f"일괄 처리 완료: {count} 메쉬 변경")


def run(import_folder: str = "/Game/Meshes/00_common"):
    """수동 폴백: 폴더(재귀) 안 모든 StaticMesh 를 처리."""
    asset_registry = unreal.AssetRegistryHelpers.get_asset_registry()
    mesh_filter = unreal.ARFilter(
        class_names=["StaticMesh"],
        package_paths=[import_folder],
        recursive_paths=True,
    )
    meshes = asset_registry.get_assets(mesh_filter)
    if not meshes:
        _warn(f"'{import_folder}' 에서 StaticMesh 없음")
        return
    process_meshes([str(m.package_name) for m in meshes])
