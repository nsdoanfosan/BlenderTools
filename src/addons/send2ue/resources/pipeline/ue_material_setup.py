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
import time
import unreal


def _candidate_contract_paths():
    override = os.environ.get("SUBSTANCE_TOOLS_PIPELINE_CONTRACT")
    if override:
        yield override

    here = os.path.abspath(__file__)
    current = os.path.dirname(here)
    while current and os.path.dirname(current) != current:
        yield os.path.join(current, "pipeline_contract.json")
        yield os.path.join(current, "substance-tools", "pipeline_contract.json")
        yield os.path.join(current, "substance_tools", "pipeline_contract.json")
        current = os.path.dirname(current)

    appdata = os.environ.get("APPDATA")
    if appdata:
        blender_root = os.path.join(appdata, "Blender Foundation", "Blender")
        if os.path.isdir(blender_root):
            for version in sorted(os.listdir(blender_root), reverse=True):
                yield os.path.join(
                    blender_root,
                    version,
                    "scripts",
                    "addons",
                    "substance_tools",
                    "pipeline_contract.json",
                )


def _pipeline_contract():
    for path in _candidate_contract_paths():
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
    return {}


def _contract_path_mapping():
    return _pipeline_contract().get("unreal_path_mapping", {}).get("current_default", {})

# ─── 설정 ────────────────────────────────────────────────────────────────────
DEFAULT_MASTER_PRESET = "prop"
MASTER_PRESETS = {
    "prop": {
        "master": "/Game/Material/AssetSurface/Master/M_AssetSurface_Master",
        "mi_folder": "/Game/Material/AssetSurface/MI/Surface",
        "assignment": "asset_surface_flat",
        "virtual_textures": True,
    },
    "layer": {
        "master": "/Game/Material/AssetSurface/Master/M_LayerBlend",
        "mi_folder": "/Game/Material/AssetSurface/MI/LayerBlend",
        "assignment": "material_layer_instance",
        "layer_parent": "/Game/Material/AssetSurface/Master/MaterialLayer/MY_Mesh_UV0",
        "layer_instance_folder": "/Game/Material/AssetSurface/MYI/LayerBlend",
        "layer_instance_strip_prefixes": ["LayerBlend_"],
        "layer_texture_remap": {
            "Albedo": "Albedo",
            "Extra": "Extra",
            "Normal": "Normal",
            "Height": "Height",
            "Transmission": "Transmission",
        },
        "virtual_textures": True,
    },
    "cloth": {
        "master": "/Game/Material/AssetSurface/Master/M_Coat_Fabric_Substrate_Master",
        "mi_folder": "/Game/Material/AssetSurface/MI/Cloth",
        "assignment": "material_layer_instance",
        "layer_parent": "/Game/Material/AssetSurface/Master/MaterialLayer/MY_Cloth",
        "layer_instance_folder": "/Game/Material/AssetSurface/MYI/Cloth",
        "layer_texture_remap": {
            "Albedo": "BaseColor",
            "Extra": "ORM",
            "Normal": "Normal",
            "Sheen Color": "Fuzz Color Map",
            "Sheen Opacity": "Fuzz Mask",
            "Sheen Roughness": "Fuzz Roughness Map",
        },
        "virtual_textures": True,
    },
    "asset_surface": {
        "master": "/Game/Material/AssetSurface/Master/M_AssetSurface_Master",
        "mi_folder": "/Game/Material/AssetSurface/MI/Surface",
        "assignment": "asset_surface_flat",
        "virtual_textures": True,
    },
    "coat": {
        "master": "/Game/Material/AssetSurface/Master/M_Coat_Fabric_Substrate_Master",
        "mi_folder": "/Game/Material/AssetSurface/MI/Cloth",
        "assignment": "material_layer_instance",
        "layer_parent": "/Game/Material/AssetSurface/Master/MaterialLayer/MY_Cloth",
        "layer_instance_folder": "/Game/Material/AssetSurface/MYI/Cloth",
        "layer_texture_remap": {
            "Albedo": "BaseColor",
            "Extra": "ORM",
            "Normal": "Normal",
            "Sheen Color": "Fuzz Color Map",
            "Sheen Opacity": "Fuzz Mask",
            "Sheen Roughness": "Fuzz Roughness Map",
        },
        "virtual_textures": True,
    },
    "hair": {
        "master": "/Game/CC_Shaders/HairShader/RL_Hair",
        "mi_folder": "/Game/Material/AssetSurface/MI/Hair",
        "assignment": "none",
        "virtual_textures": False,
        "create_if_missing": False,
        "exclude_path_fragments": ["/Game/Material/AssetSurface/"],
    },
    "tree": {
        "master": "/Game/Material/Tree/AssetTree/Master/M_TreeAsset_Master",
        "mi_folder": "/Game/Material/Tree/AssetTree/MI",
        "assignment": "material_layer_instance",
        "layer_parent": "/Game/Material/Tree/AssetTree/Master/MaterialLayer/MY_Tree_Bark",
        "layer_instance_folder": "/Game/Material/Tree/AssetTree/MYI",
        "layer_parents_by_name": {
            "bark": "/Game/Material/Tree/AssetTree/Master/MaterialLayer/MY_Tree_Bark",
            "branch": "/Game/Material/Tree/AssetTree/Master/MaterialLayer/MY_Tree_Branch",
            "leaf": "/Game/Material/Tree/AssetTree/Master/MaterialLayer/MY_Tree_Leaf",
        },
        "layer_texture_remap": {
            "Albedo": "Albedo",
            "Extra": "Extra",
            "Normal": "Normal",
            "Height": "Height",
            "Transmission": "Transmission",
        },
        "virtual_textures": True,
    },
}
# 반투명(유리) 머티리얼은 전용 MI 를 만들지 않고 이 공유 글래스 MI 를 슬롯에 직접 할당한다.
# (Megascan 글래스를 프로젝트로 localize 한 인스턴스. 부모 M_MS_Glass_Material, TRANSLUCENT)
GLASS_MI_PATH        = "/Game/Material/AssetSurface/MI/MI_Prop_Glass_01"
TEXTURES_FOLDER      = "/Game/Textures"
EXPORT_DIR           = r"C:/Users/PARK/Documents/UE_Blender_Pipeline/exports"
_PATH_MAPPING        = _contract_path_mapping()
_LOCAL_ANCHOR        = str(_PATH_MAPPING.get("local_anchor") or "Forestportfolio").strip("/\\")
JSON_SEARCH_ROOTS    = [
    os.path.join(r"C:/Users/PARK/OneDrive", _LOCAL_ANCHOR),
]
_D_DRIVE_ANCHOR      = os.path.join(r"D:/OneDrive", _LOCAL_ANCHOR)
if os.path.isdir(_D_DRIVE_ANCHOR) and _D_DRIVE_ANCHOR not in JSON_SEARCH_ROOTS:
    JSON_SEARCH_ROOTS.append(_D_DRIVE_ANCHOR)
# /Game/Meshes/<rel> 은 디스크의 JSON_SEARCH_ROOTS[0]/<rel> 에 1:1 대응(send2ue 자동경로 규칙).
# 이를 이용해 mesh_path 로부터 해당 프롭 폴더만 좁혀 JSON 을 찾는다 → 3만개 트리 전체 walk 회피.
GAME_MESHES_PREFIX   = f"{str(_PATH_MAPPING.get('unreal_anchor') or '/Game/Meshes').rstrip('/')}/"
DELETE_IMPORTED_SOURCE_MATERIALS = False
DELETE_IMPORTED_SOURCE_TEXTURES = False

# Shared surface-layer texture parameter names (JSON 의 param 과 동일해야 연결됨)
KNOWN_PARAMS = {
    "Albedo",
    "Extra",
    "Normal",
    "Height",
    "Transmission",
    "Emissive",
    "Sheen Color",
    "Sheen Opacity",
    "Sheen Roughness",
    "Moss Blend Mask",
}
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
    "SheenColor": "Sheen Color",
    "SheenOpacity": "Sheen Opacity",
    "SheenRoughness": "Sheen Roughness",
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
DEFAULT_CLOTH_TEXTURE_PARAM_BY_LAYER_PARAM = {
    "Albedo": "BaseColor",
    "Extra": "ORM",
    "Normal": "Normal",
    "Sheen Color": "Fuzz Color Map",
    "Sheen Opacity": "Fuzz Mask",
    "Sheen Roughness": "Fuzz Roughness Map",
}


def _cloth_texture_param_by_layer_param():
    contract = _pipeline_contract()
    handoff_remap = (
        contract.get("unreal_handoff_sidecar", {})
        .get("cloth_master_param_remap", {})
    )
    material_layer_remap = handoff_remap.get("material_layer", {})
    legacy_remap = (
        contract.get("unreal_material_json", {})
        .get("cloth_master_param_remap", {})
    )
    mapping = (
        material_layer_remap.get("texture_remap")
        or handoff_remap.get("texture_remap")
        or legacy_remap.get("material_layer", {}).get("texture_remap")
        or legacy_remap.get("texture_remap")
    )
    if isinstance(mapping, dict) and mapping:
        merged = dict(DEFAULT_CLOTH_TEXTURE_PARAM_BY_LAYER_PARAM)
        merged.update({str(key): str(value) for key, value in mapping.items()})
        return merged
    return dict(DEFAULT_CLOTH_TEXTURE_PARAM_BY_LAYER_PARAM)


CLOTH_TEXTURE_PARAM_BY_LAYER_PARAM = _cloth_texture_param_by_layer_param()
ASSET_SURFACE_PARAM_BY_LAYER_PARAM = {
    "Albedo": "Albedo",
    "Extra": "Extra",
    "Normal": "Normal",
    "Height": "Height",
    "Transmission": "Transmission",
    "Moss Blend Mask": "Moss Blend Mask",
}

# Send to Unreal keeps the source texture stem in the asset name.  These
# suffixes are the source of truth for the tree texture set, even when an
# older sidecar uses a generic/legacy parameter name.
TEXTURE_PARAM_BY_NAME_SUFFIX = (
    ("_color", "Albedo"),
    ("_extra", "Extra"),
    ("_height", "Height"),
    ("_normal", "Normal"),
    ("_opacity", "Opacity"),
    ("_subsurface", "Subsurface"),
)

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
ENABLE_SKELETAL_NANITE_VOXELIZE = True
DYNAMIC_WIND_JSON_SUFFIX = "_dynamic_wind_import_from_megaplant_groups.json"
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


def _texture_param_from_name(file_path=None, asset_name=None):
    """Return the canonical import role for a known texture-name suffix."""
    for value in (file_path, asset_name):
        if not value:
            continue
        stem = os.path.splitext(os.path.basename(str(value).replace("\\", "/")))[0].lower()
        for suffix, role in TEXTURE_PARAM_BY_NAME_SUFFIX:
            if stem.endswith(suffix):
                return role
    return None


def _effective_texture_param(param: str, file_path=None, asset_name=None) -> str:
    # A recognized filename suffix wins over a legacy JSON role. This keeps
    # existing MYI/MI JSON contracts working while fixing tree map imports.
    return _texture_param_from_name(file_path, asset_name) or str(param or "")


def _configure_imported_texture(
    tex,
    param: str,
    virtual_texture_streaming=None,
    file_path=None,
    asset_name=None,
) -> bool:
    changed = False
    param = _effective_texture_param(param, file_path, asset_name)

    if param == "Normal":
        changed |= _set_texture_property_if_changed(tex, "srgb", False)
        changed |= _set_texture_property_if_changed(
            tex,
            "compression_settings",
            unreal.TextureCompressionSettings.TC_NORMALMAP,
        )
    elif param in {
        "Extra",
        "MetallicRoughness",
        "Roughness",
        "Metallic",
        "Occlusion",
        "Sheen Opacity",
        "Sheen Roughness",
    }:
        changed |= _set_texture_property_if_changed(tex, "srgb", False)
        changed |= _set_texture_property_if_changed(
            tex,
            "compression_settings",
            unreal.TextureCompressionSettings.TC_MASKS,
        )
    elif param in {"Height", "Opacity", "Alpha", "Transmission"}:
        changed |= _set_texture_property_if_changed(tex, "srgb", False)
        changed |= _set_texture_property_if_changed(
            tex,
            "compression_settings",
            unreal.TextureCompressionSettings.TC_GRAYSCALE,
        )
    elif param == "Subsurface":
        changed |= _set_texture_property_if_changed(tex, "srgb", True)
    else:
        changed |= _set_texture_property_if_changed(tex, "srgb", True)

    max_size = MAX_TEXTURE_SIZE_BY_PARAM.get(param, DEFAULT_MAX_TEXTURE_SIZE)
    if max_size:
        changed |= _set_texture_property_if_changed(tex, "max_texture_size", max_size)

    if virtual_texture_streaming is None:
        virtual_texture_streaming = ENABLE_VIRTUAL_TEXTURE_STREAMING

    try:
        before = bool(tex.get_editor_property("virtual_texture_streaming"))
    except Exception:
        before = None
    if before is not None and before != bool(virtual_texture_streaming):
        if hasattr(tex, "set_virtual_texture_streaming"):
            tex.set_virtual_texture_streaming(bool(virtual_texture_streaming))
        else:
            tex.set_editor_property("virtual_texture_streaming", bool(virtual_texture_streaming))
        changed = True

    return changed


def _import_texture(
    file_path: str,
    asset_name: str,
    param: str,
    tex_cache: dict = None,
    force_reimport: bool = False,
    virtual_texture_streaming=None,
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
        if tex is not None and _configure_imported_texture(
            tex,
            param,
            virtual_texture_streaming,
            file_path,
            asset_name,
        ):
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
        if tex is not None and _configure_imported_texture(
            tex,
            param,
            virtual_texture_streaming,
            file_path,
            asset_name,
        ):
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

    _configure_imported_texture(
        tex,
        param,
        virtual_texture_streaming,
        file_path,
        asset_name,
    )

    unreal.EditorAssetLibrary.save_asset(full_path)
    if tex_cache is not None and source_mtime is not None:
        tex_cache[full_path] = source_mtime
    _log(f"  텍스처 import: {asset_name} ({param})")
    return full_path


def _nanite_shape_preservation_voxelize():
    for enum_name in ("NaniteShapePreservation", "ENaniteShapePreservation"):
        enum_type = getattr(unreal, enum_name, None)
        if enum_type is None:
            continue
        for value_name in ("VOXELIZE", "Voxelize", "voxelize"):
            value = getattr(enum_type, value_name, None)
            if value is not None:
                return value
        for value_name in dir(enum_type):
            if "voxel" in value_name.lower():
                value = getattr(enum_type, value_name, None)
                if value is not None:
                    return value
    return None


def _set_nanite_shape_preservation(nanite, value) -> bool:
    if value is None:
        return False
    try:
        if nanite.get_editor_property("shape_preservation") == value:
            return False
    except Exception:
        pass
    try:
        nanite.set_editor_property("shape_preservation", value)
        return True
    except Exception as exc:
        _warn(f"  Nanite Shape Preservation Voxelize set failed: {exc}")
        return False


def _notify_nanite_settings_changed(mesh):
    for method_name in ("notify_nanite_settings_changed", "post_edit_change"):
        method = getattr(mesh, method_name, None)
        if callable(method):
            try:
                method()
            except Exception:
                pass


def _set_nanite(mesh, enabled: bool, shape_preservation=None) -> bool:
    """Set mesh Nanite settings. Returns True when any value changed."""
    nanite = mesh.get_editor_property("nanite_settings")
    changed = False
    if bool(nanite.get_editor_property("enabled")) != enabled:
        nanite.set_editor_property("enabled", enabled)
        changed = True
    if enabled:
        changed = _set_nanite_shape_preservation(nanite, shape_preservation) or changed
    if not changed:
        return False
    mesh.set_editor_property("nanite_settings", nanite)
    _notify_nanite_settings_changed(mesh)
    if enabled and shape_preservation is not None:
        _log("  Nanite enabled + Shape Preservation Voxelize")
    else:
        _log("  Nanite enabled" if enabled else "  Nanite disabled")
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


def _entry_name_blob(entry: dict) -> str:
    values = []
    for key in (
        "name",
        "slot_name",
        "material_slot_name",
        "imported_slot_name",
        "imported_material_slot_name",
        "original_material_name",
    ):
        value = str(entry.get(key) or "").strip()
        if value:
            values.append(value)
    return " ".join(values).casefold()


def _tree_part_key(entry: dict):
    blob = _entry_name_blob(entry)
    if any(token in blob for token in ("leaf", "leaves", "foliage")):
        return "leaf"
    if any(token in blob for token in ("branch", "twig")):
        return "branch"
    if any(token in blob for token in ("bark", "trunk", "stump")):
        return "bark"
    return None


def _is_tree_asset_path(mesh_path: str) -> bool:
    normalized = str(mesh_path or "").replace("\\", "/").casefold()
    return "/tree/" in normalized or "/trees/" in normalized


def _master_preset(data: dict, entry: dict = None, mesh_path: str = "") -> dict:
    entry = entry or {}
    tree_part = _tree_part_key(entry)
    if tree_part or _is_tree_asset_path(mesh_path):
        key = "tree"
    else:
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
    if tree_part:
        result["tree_part"] = tree_part
    return result


def _uses_tree_material_preset(data: dict, mesh_path: str) -> bool:
    if _is_tree_asset_path(mesh_path):
        return True
    if not data:
        return False
    return any(
        _master_preset(data, entry, mesh_path).get("key") == "tree"
        for entry in data.get("materials", [])
        if isinstance(entry, dict)
    )


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


def _entry_create_if_missing(entry: dict, preset: dict) -> bool:
    value = entry.get("create_if_missing", preset.get("create_if_missing", True))
    if isinstance(value, str):
        return value.strip().casefold() not in {"0", "false", "no", "off"}
    return bool(value)


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
    create_if_missing: bool = True,
):
    if unreal.EditorAssetLibrary.does_asset_exist(target_path):
        return unreal.load_asset(target_path), target_path, False, "existing"

    if not copy_from_path:
        copy_from_path = _derive_number_suffix_copy_source(target_path)

    if not copy_from_path:
        if not create_if_missing:
            _warn(f"  target material missing; creation disabled: {target_path}")
            return None, target_path, False, "missing"
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


def _supported_mesh_classes():
    classes = []
    for class_name in ("StaticMesh", "SkeletalMesh"):
        mesh_class = getattr(unreal, class_name, None)
        if mesh_class is not None:
            classes.append(mesh_class)
    return tuple(classes)


def _mesh_material_entries(mesh):
    for property_name in ("static_materials", "materials"):
        try:
            entries = mesh.get_editor_property(property_name)
        except Exception:
            continue
        return property_name, entries
    return None, []


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


def _entry_slot_display_name(entry: dict, mat_name: str):
    for value in (
        entry.get("slot_name"),
        entry.get("material_slot_name"),
        entry.get("imported_material_slot_name"),
        mat_name,
    ):
        value = str(value or "").strip()
        if value:
            return value
    return mat_name


def _slot_index_for_entry(mesh, entry: dict, mat_name: str):
    candidates = _entry_slot_match_names(entry, mat_name)
    if not candidates:
        return None
    candidate_set = set(candidates)
    candidate_folded = {name.casefold() for name in candidates}
    _property_name, material_entries = _mesh_material_entries(mesh)
    for i, material_entry in enumerate(material_entries):
        slot_names = _static_material_name_values(material_entry)
        if any(name in candidate_set for name in slot_names):
            return i
    for i, material_entry in enumerate(material_entries):
        slot_names = _static_material_name_values(material_entry)
        if any(str(name).casefold() in candidate_folded for name in slot_names):
            return i
    return None


def _slot_index_for_material_name(mesh, mat_name: str):
    """메쉬의 실제 슬롯 중 import 된 머티리얼명이 mat_name 인 슬롯 인덱스. 없으면 None.
    Empty 결합(combine child meshes) export 처럼 JSON 의 slot_index 가 실제 결합 슬롯 순서와
    다를 수 있으므로, 슬롯을 이름으로 매칭해 정확한 인덱스를 찾는다."""
    return _slot_index_for_entry(mesh, {"name": mat_name}, mat_name)


def _rename_material_slot(mesh, slot_index: int, slot_name: str) -> bool:
    if not slot_name:
        return False
    property_name, material_entries = _mesh_material_entries(mesh)
    if not property_name or slot_index < 0 or slot_index >= len(material_entries):
        return False

    material_entry = material_entries[slot_index]
    changed = False
    for prop in ("material_slot_name", "imported_material_slot_name"):
        try:
            current = str(material_entry.get_editor_property(prop))
        except Exception:
            continue
        if current == slot_name:
            continue
        try:
            material_entry.set_editor_property(prop, slot_name)
        except Exception as exc:
            _warn(f"  slot name update failed: {prop} -> {slot_name} ({exc})")
            continue
        changed = True

    if not changed:
        return False
    try:
        mesh.set_editor_property(property_name, material_entries)
    except Exception as exc:
        _warn(f"  material slot array update failed: {property_name} ({exc})")
        return False
    return True


def _is_skeletal_mesh(mesh) -> bool:
    skeletal_mesh_class = getattr(unreal, "SkeletalMesh", None)
    return skeletal_mesh_class is not None and isinstance(mesh, skeletal_mesh_class)


def _mesh_relative_disk_folder_candidates(mesh_path: str):
    if not mesh_path or not mesh_path.startswith(GAME_MESHES_PREFIX):
        return []
    rel = mesh_path[len(GAME_MESHES_PREFIX):]
    rel_folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    candidates = []
    for root in JSON_SEARCH_ROOTS:
        if root and os.path.isdir(root):
            candidates.append(os.path.join(root, rel_folder.replace("/", os.sep)))
    return candidates


def _dynamic_wind_json_from_data(data: dict):
    data = data or {}
    for key in ("dynamic_wind_json", "dynamic_wind_json_path", "wind_json", "wind_json_path"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    wind = data.get("wind")
    if isinstance(wind, dict):
        for key in ("json", "json_path", "path"):
            value = str(wind.get(key) or "").strip()
            if value:
                return value
    return None


def _dynamic_wind_candidate_dirs(mesh_path: str, json_path: str = None):
    dirs = []
    if json_path:
        current = os.path.dirname(os.path.abspath(json_path))
        for _ in range(6):
            if not current or current in dirs:
                break
            dirs.extend([
                current,
                os.path.join(current, "JSON"),
                os.path.join(current, "fbx", "JSON"),
            ])
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    for folder in _mesh_relative_disk_folder_candidates(mesh_path):
        dirs.extend([
            folder,
            os.path.join(folder, "JSON"),
            os.path.join(folder, "fbx"),
            os.path.join(folder, "fbx", "JSON"),
        ])

    seen = set()
    result = []
    for folder in dirs:
        normalized = os.path.normcase(os.path.abspath(folder))
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(folder)
    return result


def _find_dynamic_wind_json(data: dict, mesh_path: str, mesh_name: str, json_path: str = None):
    explicit = _dynamic_wind_json_from_data(data)
    dirs = _dynamic_wind_candidate_dirs(mesh_path, json_path)
    if explicit:
        explicit = os.path.expandvars(os.path.expanduser(explicit))
        if os.path.isabs(explicit) and os.path.isfile(explicit):
            return explicit
        for folder in dirs:
            candidate = os.path.join(folder, explicit)
            if os.path.isfile(candidate):
                return candidate

    filename = f"{mesh_name}{DYNAMIC_WIND_JSON_SUFFIX}"
    for folder in dirs:
        candidate = os.path.join(folder, filename)
        if os.path.isfile(candidate):
            return candidate
    return None


def _import_dynamic_wind_if_available(mesh, mesh_path: str, mesh_name: str, data: dict, json_path: str = None) -> bool:
    if not _is_skeletal_mesh(mesh) or not _is_tree_asset_path(mesh_path):
        return False
    helper = getattr(unreal, "CodexDynamicWindImportLibrary", None)
    if not helper or not hasattr(helper, "import_dynamic_wind_json_to_skeletal_mesh"):
        _warn("  DynamicWind import helper missing; tree wind data skipped")
        return False
    wind_json = _find_dynamic_wind_json(data, mesh_path, mesh_name, json_path)
    if not wind_json:
        _warn(f"  DynamicWind JSON not found for tree skeletal mesh: {mesh_name}")
        return False
    try:
        result = helper.import_dynamic_wind_json_to_skeletal_mesh(mesh, wind_json)
    except Exception as exc:
        _warn(f"  DynamicWind import failed: {wind_json} ({exc})")
        return False
    _log(f"  DynamicWind <- {wind_json} ({result})")
    return True


def _new_skeletal_material_entry(slot_name: str, material, old_entry=None):
    entry = unreal.SkeletalMaterial()
    entry.set_editor_property("material_interface", material)
    entry.set_editor_property("material_slot_name", slot_name)
    if old_entry is not None:
        for prop in ("uv_channel_data", "overlay_material_interface"):
            try:
                entry.set_editor_property(prop, old_entry.get_editor_property(prop))
            except Exception:
                pass
    return entry


def _assign_skeletal_slot(mesh, slot_index: int, material, slot_name: str = None) -> bool:
    property_name, material_entries = _mesh_material_entries(mesh)
    if property_name != "materials" or slot_index < 0 or slot_index >= len(material_entries):
        return False

    slot_name = str(slot_name or "").strip()
    if not slot_name:
        slot_name = str(material_entries[slot_index].get_editor_property("material_slot_name"))

    current_material = material_entries[slot_index].get_editor_property("material_interface")
    current_slot = str(material_entries[slot_index].get_editor_property("material_slot_name"))
    if (
        current_slot == slot_name
        and current_material is not None
        and material is not None
        and current_material.get_path_name() == material.get_path_name()
    ):
        return False

    new_entries = list(material_entries)
    new_entries[slot_index] = _new_skeletal_material_entry(
        slot_name,
        material,
        material_entries[slot_index],
    )
    mesh.set_editor_property("materials", new_entries)
    return True


def _normalize_skeletal_material_slots(mesh, assignments: dict) -> bool:
    if not _is_skeletal_mesh(mesh) or not assignments:
        return False

    property_name, material_entries = _mesh_material_entries(mesh)
    if property_name != "materials":
        return False

    ordered = [
        (slot_index, slot_name, material)
        for slot_index, (slot_name, material) in sorted(assignments.items())
    ]

    unchanged = len(material_entries) == len(ordered)
    if unchanged:
        for new_index, (_old_index, slot_name, material) in enumerate(ordered):
            current_entry = material_entries[new_index]
            try:
                current_slot = str(current_entry.get_editor_property("material_slot_name"))
                current_material = current_entry.get_editor_property("material_interface")
            except Exception:
                unchanged = False
                break
            if current_slot != slot_name:
                unchanged = False
                break
            if bool(current_material) != bool(material):
                unchanged = False
                break
            if (
                current_material is not None
                and material is not None
                and current_material.get_path_name() != material.get_path_name()
            ):
                unchanged = False
                break
    if unchanged:
        return False

    new_entries = []
    for old_index, slot_name, material in ordered:
        old_entry = material_entries[old_index] if 0 <= old_index < len(material_entries) else None
        new_entries.append(_new_skeletal_material_entry(slot_name, material, old_entry))

    mesh.set_editor_property("materials", new_entries)
    return True


def _set_material_interface(mesh, slot_index: int, material) -> bool:
    property_name, material_entries = _mesh_material_entries(mesh)
    if not property_name or slot_index < 0 or slot_index >= len(material_entries):
        return False

    current = material_entries[slot_index].get_editor_property("material_interface")
    if (
        current is not None
        and material is not None
        and current.get_path_name() == material.get_path_name()
    ):
        return False

    if hasattr(mesh, "set_material"):
        mesh.set_material(slot_index, material)
        return True

    material_entries[slot_index].set_editor_property("material_interface", material)
    mesh.set_editor_property(property_name, material_entries)
    return True


def _assign_slot(mesh, slot_index: int, material, slot_name: str = None) -> bool:
    """메쉬 슬롯에 머티리얼 할당. FBX import 결과 슬롯 수가 JSON 과 달라도 안전하게 가드."""
    _property_name, material_entries = _mesh_material_entries(mesh)
    if _is_skeletal_mesh(mesh):
        return _assign_skeletal_slot(mesh, slot_index, material, slot_name)
    if slot_index < 0 or slot_index >= len(material_entries):
        _warn(f"  슬롯 인덱스 {slot_index} 가 메쉬 슬롯 수({len(material_entries)})를 벗어남 — 할당 skip")
        return False
    material_changed = _set_material_interface(mesh, slot_index, material)
    slot_renamed = _rename_material_slot(mesh, slot_index, slot_name)
    return material_changed or slot_renamed


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


def _import_layer_textures(
    layers,
    tex_cache: dict,
    force_reimport: bool = False,
    virtual_texture_streaming=None,
):
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
                virtual_texture_streaming=virtual_texture_streaming,
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


def _texture_parameter_name(parameter_value):
    try:
        info = parameter_value.get_editor_property("parameter_info")
        return str(info.get_editor_property("name"))
    except Exception:
        try:
            return str(parameter_value.get_editor_property("parameter_name"))
        except Exception:
            return ""


def _prune_texture_parameter_overrides(mi, keep_names: set, update: bool = True) -> bool:
    try:
        values = list(mi.get_editor_property("texture_parameter_values"))
    except Exception:
        return False
    kept = [
        value
        for value in values
        if _texture_parameter_name(value) in keep_names
    ]
    if len(kept) == len(values):
        return False
    mi.set_editor_property("texture_parameter_values", kept)
    if update:
        try:
            unreal.MaterialEditingLibrary.update_material_instance(mi)
        except Exception:
            pass
    _log(
        "  stale texture overrides pruned: "
        + ", ".join(
            _texture_parameter_name(value)
            for value in values
            if _texture_parameter_name(value) not in keep_names
        )
    )
    return True


def _layer_instance_base_name(mat_base: str, preset: dict, entry: dict) -> str:
    material_layer = entry.get("material_layer") if isinstance(entry.get("material_layer"), dict) else {}
    for value in (
        material_layer.get("instance_name"),
        entry.get("material_layer_instance_name"),
        entry.get("layer_instance_name"),
    ):
        value = str(value or "").strip()
        if value:
            return value[4:] if value.startswith("MYI_") else value

    base = str(mat_base or "").strip()
    for prefix in preset.get("layer_instance_strip_prefixes", []):
        prefix = str(prefix or "")
        if prefix and base.startswith(prefix):
            base = base[len(prefix):]
            break
    return base


def _layer_instance_path(mat_base: str, preset: dict, entry: dict):
    material_layer = entry.get("material_layer") if isinstance(entry.get("material_layer"), dict) else {}
    for key in ("instance_path", "path"):
        value = str(material_layer.get(key) or "").strip()
        if value:
            return value.split(".")[0]
    for key in (
        "material_layer_instance_path",
        "layer_instance_path",
        "target_layer_instance_path",
    ):
        value = str(entry.get(key) or "").strip()
        if value:
            return value.split(".")[0]

    folder = str(entry.get("layer_instance_folder") or preset.get("layer_instance_folder") or "").rstrip("/")
    if not folder:
        return None
    base = _layer_instance_base_name(mat_base, preset, entry)
    if not base:
        return None
    return f"{folder}/MYI_{base}"


def _layer_parent_path(preset: dict, entry: dict):
    material_layer = entry.get("material_layer") if isinstance(entry.get("material_layer"), dict) else {}
    for key in ("parent", "parent_layer"):
        value = str(material_layer.get(key) or "").strip()
        if value:
            return value.split(".")[0]
    for key in ("material_layer_parent", "layer_parent", "parent_layer"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value.split(".")[0]
    tree_part = str(preset.get("tree_part") or "").casefold()
    layer_parents = preset.get("layer_parents_by_name")
    if tree_part and isinstance(layer_parents, dict):
        value = str(layer_parents.get(tree_part) or "").strip()
        if value:
            return value.split(".")[0]
    value = str(preset.get("layer_parent") or "").strip()
    return value.split(".")[0] if value else None


def _as_float(value):
    try:
        return float(value)
    except Exception:
        return None


def _linear_color(value):
    if isinstance(value, dict):
        channels = [_as_float(value.get(key)) for key in ("r", "g", "b", "a")]
        if channels[3] is None:
            channels[3] = 1.0
    elif isinstance(value, (list, tuple)):
        channels = [_as_float(item) for item in value[:4]]
        if len(channels) == 3:
            channels.append(1.0)
    else:
        return None
    if len(channels) != 4 or any(channel is None for channel in channels):
        return None
    return unreal.LinearColor(*channels)


def _parameter_source_dicts(entry: dict):
    entry = entry or {}
    material_layer = entry.get("material_layer") if isinstance(entry.get("material_layer"), dict) else {}
    for source in (material_layer, entry):
        yield source
        wind = source.get("wind")
        if isinstance(wind, dict):
            yield wind


def _layer_scalar_params(entry: dict) -> dict:
    params = {}
    for source in _parameter_source_dicts(entry):
        for key in ("scalar_parameters", "scalars", "scalar_params", "wind_scalars"):
            values = source.get(key)
            if isinstance(values, dict):
                for name, value in values.items():
                    parsed = _as_float(value)
                    if parsed is not None:
                        params[str(name)] = parsed
        for name, value in source.items():
            if "wind" not in str(name).casefold():
                continue
            parsed = _as_float(value)
            if parsed is not None:
                params[str(name)] = parsed
    return params


def _layer_vector_params(entry: dict) -> dict:
    params = {}
    for source in _parameter_source_dicts(entry):
        for key in ("vector_parameters", "vectors", "vector_params", "wind_vectors"):
            values = source.get(key)
            if isinstance(values, dict):
                for name, value in values.items():
                    parsed = _linear_color(value)
                    if parsed is not None:
                        params[str(name)] = parsed
        for name, value in source.items():
            if "wind" not in str(name).casefold():
                continue
            parsed = _linear_color(value)
            if parsed is not None:
                params[str(name)] = parsed
    return params


def _layer_texture_remap(preset: dict, entry: dict) -> dict:
    material_layer = entry.get("material_layer") if isinstance(entry.get("material_layer"), dict) else {}
    mapping = (
        material_layer.get("texture_remap")
        or entry.get("layer_texture_remap")
        or entry.get("material_layer_texture_remap")
    )
    if not isinstance(mapping, dict):
        mapping = preset.get("layer_texture_remap", {})
    return {str(key): str(value) for key, value in dict(mapping).items() if key and value}


def _call_create_or_update_layer_instance(helper, parent_layer, layer_path, texture_params, scalar_params=None, vector_params=None):
    result = helper.create_or_update_material_layer_instance(
        parent_layer,
        layer_path,
        texture_params,
        scalar_params or {},
        vector_params or {},
        False,
        True,
    )
    if isinstance(result, tuple):
        ok = bool(result[0]) if result else False
        errors = result[2] if len(result) > 2 else []
        return ok, errors
    return bool(result), []


_NORMALIZED_MATERIAL_LAYER_ASSETS = set()


def _normalize_material_layer_asset(helper, method_name: str, asset_path: str, label: str):
    cache_key = (method_name, asset_path)
    if cache_key in _NORMALIZED_MATERIAL_LAYER_ASSETS:
        return
    method = getattr(helper, method_name, None)
    if method is None:
        raise RuntimeError(f"CodexMaterialTools {label} normalization helper missing")

    result = method(asset_path)
    report_text = ""
    errors = []
    returned_ok = None
    if isinstance(result, tuple):
        if result and isinstance(result[0], bool):
            returned_ok = bool(result[0])
            if len(result) > 1:
                report_text = str(result[1] or "")
            if len(result) > 2:
                errors = [str(item) for item in (result[2] or [])]
        elif result and isinstance(result[0], str):
            report_text = result[0]
            if len(result) > 1:
                errors = [str(item) for item in (result[1] or [])]
    elif isinstance(result, str):
        report_text = result
    elif isinstance(result, bool):
        returned_ok = result

    try:
        report = json.loads(report_text) if report_text else {}
    except Exception:
        report = {}
    report_ok = bool(report.get("ok", returned_ok))
    if not report_ok or errors:
        detail = " | ".join(errors) or report_text or "unknown normalization failure"
        raise RuntimeError(f"{label} normalization failed: {asset_path} ({detail})")

    _NORMALIZED_MATERIAL_LAYER_ASSETS.add(cache_key)
    changed = (
        report.get("removed_placeholder_count", 0)
        or report.get("removed_set_declaration_count", 0)
        or report.get("removed_get_declaration_count", 0)
        or report.get("restored_tree_input_count", 0)
    )
    if changed:
        _log(f"  {label} normalized: {asset_path}")


def _call_set_material_instance_background_layer(helper, mi, layer_asset):
    if hasattr(helper, "set_material_instance_background_layer_report"):
        result = helper.set_material_instance_background_layer_report(mi, layer_asset)
        ok = False
        report_json = ""
        errors = []
        if isinstance(result, tuple):
            if result and isinstance(result[0], bool):
                ok = bool(result[0])
                report_json = str(result[1] if len(result) > 1 else "")
                if len(result) > 2:
                    errors = [str(item) for item in (result[2] or [])]
            elif result and isinstance(result[0], str):
                report_json = result[0]
                if len(result) > 1:
                    errors = [str(item) for item in (result[1] or [])]
        elif isinstance(result, str):
            report_json = result
        else:
            ok = bool(result)
        if report_json:
            try:
                report = json.loads(report_json)
                errors.extend(str(item) for item in (report.get("errors") or []))
                ok = bool(report.get("ok", ok) or report.get("desired_is_set"))
            except Exception as exc:
                return False, [f"background layer report parse failed: {exc}"]
        return ok, errors
    if hasattr(helper, "set_material_instance_background_layer_with_errors"):
        result = helper.set_material_instance_background_layer_with_errors(mi, layer_asset)
        if isinstance(result, tuple):
            changed = bool(result[0]) if result else False
            errors = result[1] if len(result) > 1 else []
            return changed, list(errors or [])
        return bool(result), []
    return bool(helper.set_material_instance_background_layer(mi, layer_asset)), []


def _assign_material_layer_instance(mi, mat_base: str, layer_maps, preset: dict, entry: dict) -> bool:
    helper = getattr(unreal, "CodexMaterialToolsLibrary", None)
    if not helper or not hasattr(helper, "create_or_update_material_layer_instance"):
        _warn("  CodexMaterialTools layer instance helper missing; MYI assignment skipped")
        return False
    if not (
        hasattr(helper, "set_material_instance_background_layer_report")
        or hasattr(helper, "set_material_instance_background_layer_with_errors")
        or hasattr(helper, "set_material_instance_background_layer")
    ):
        _warn("  CodexMaterialTools background layer helper missing; MYI assignment skipped")
        return False

    parent_layer = _layer_parent_path(preset, entry)
    layer_path = _layer_instance_path(mat_base, preset, entry)
    if not parent_layer or not layer_path:
        _warn("  material layer instance path is incomplete; MYI assignment skipped")
        return False

    _normalize_material_layer_asset(
        helper,
        "normalize_material_layer_placeholders",
        str(preset.get("master") or ""),
        "material master",
    )
    _normalize_material_layer_asset(
        helper,
        "normalize_material_function_attribute_nodes",
        parent_layer,
        "material layer function",
    )

    remap = _layer_texture_remap(preset, entry)
    texture_params = {}
    for layer_param, tex_path in _first_layer_textures(layer_maps).items():
        target_param = remap.get(layer_param)
        if target_param and tex_path:
            texture_params[target_param] = tex_path
    scalar_params = _layer_scalar_params(entry)
    vector_params = _layer_vector_params(entry)
    if scalar_params or vector_params:
        _log(
            "  material layer parameters: "
            f"{len(scalar_params)} scalar(s), {len(vector_params)} vector(s)"
        )

    ok, errors = _call_create_or_update_layer_instance(
        helper,
        parent_layer,
        layer_path,
        texture_params,
        scalar_params,
        vector_params,
    )
    if not ok:
        _warn(f"  MYI create/update failed: {layer_path}")
        for error in errors or []:
            _warn(f"    {error}")
        return False

    layer_asset = unreal.load_asset(layer_path)
    if layer_asset is None:
        _warn(f"  MYI load failed after create/update: {layer_path}")
        return False

    # Remove stale flat/layer overrides before the C++ helper persists the MI.
    # Do not trigger a live material preview update here; UE 5.8 can assert
    # while compiling a newly-created Material Layer Instance thumbnail.
    overrides_pruned = _prune_texture_parameter_overrides(mi, set(), update=False)
    changed, background_errors = _call_set_material_instance_background_layer(
        helper, mi, layer_asset
    )
    if background_errors:
        _warn(f"  background MYI assignment failed: {layer_path}")
        for error in background_errors:
            _warn(f"    {error}")
        return False
    if not changed:
        _warn(f"  background MYI assignment not verified: {layer_path}")
        return False
    changed = overrides_pruned or changed
    _log(f"  background MYI <- {layer_path}")
    return changed


def _assign_master_textures(mi, layer_maps, assignment: str, preset: dict = None, entry: dict = None, mat_base: str = "") -> bool:
    preset = preset or {}
    entry = entry or {}
    if assignment == "none":
        return False
    if assignment == "layer":
        return _assign_surface_layer_textures(mi, layer_maps)
    if assignment == "material_layer_instance":
        return _assign_material_layer_instance(mi, mat_base, layer_maps, preset, entry)
    if assignment == "asset_surface_flat":
        return _assign_flat_textures(mi, layer_maps, ASSET_SURFACE_PARAM_BY_LAYER_PARAM, "asset_surface")
    if assignment == "coat_flat":
        return _assign_flat_textures(mi, layer_maps, COAT_PARAM_BY_LAYER_PARAM, "coat")
    return _assign_flat_textures(mi, layer_maps, FLAT_PARAM_BY_LAYER_PARAM, "prop")


def _material_instance_base_name(mat_name: str) -> str:
    if mat_name.startswith("M_"):
        return mat_name[2:]
    if mat_name.startswith("MI_"):
        return mat_name[3:]
    return mat_name


def _hair_target_material_name(mat_name: str, entry: dict) -> str:
    for key in ("material_instance_name", "target_material_name", "unreal_material_name"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    return f"MI_{_material_instance_base_name(str(mat_name or ''))}"


def _hair_target_material_path(mat_name: str, entry: dict, preset: dict):
    folder = str(preset.get("mi_folder") or "").rstrip("/")
    target_name = _hair_target_material_name(mat_name, entry)
    if not folder or not target_name:
        return None
    target_path = _entry_target_material_path(entry)
    if target_path:
        normalized_target = str(target_path).replace("\\", "/").casefold()
        normalized_folder = f"{folder}/".replace("\\", "/").casefold()
        if normalized_target.startswith(normalized_folder):
            return target_path
        _warn(
            f"  ignoring non-hair target material path for hair '{mat_name}': "
            f"{target_path}"
        )
    return f"{folder}/{target_name}"


def _asset_path_excluded(path: str, fragments) -> bool:
    normalized = str(path or "").replace("\\", "/").casefold()
    return any(str(fragment or "").casefold() in normalized for fragment in fragments or [])


def _asset_data_class_name(asset_data):
    for property_name in ("asset_class_path", "asset_class"):
        try:
            value = getattr(asset_data, property_name)
        except Exception:
            continue
        if value:
            text = str(value)
            return text.rsplit("/", 1)[-1].rsplit(".", 1)[-1].strip("'\"")
    return ""


def _asset_paths_named(asset_names, excluded_fragments=None, allowed_classes=None):
    wanted = {str(name or "").casefold() for name in asset_names if str(name or "").strip()}
    if not wanted:
        return []
    excluded_fragments = excluded_fragments or []
    allowed_classes = {str(name).casefold() for name in allowed_classes or []}
    asset_registry = unreal.AssetRegistryHelpers.get_asset_registry()
    results = []
    try:
        assets = asset_registry.get_assets_by_path("/Game", recursive=True)
    except TypeError:
        assets = asset_registry.get_assets_by_path("/Game", True)
    for asset_data in assets:
        name = str(asset_data.asset_name)
        if name.casefold() not in wanted:
            continue
        if allowed_classes and _asset_data_class_name(asset_data).casefold() not in allowed_classes:
            continue
        path = str(asset_data.package_name)
        if _asset_path_excluded(path, excluded_fragments):
            continue
        results.append(path)
    return sorted(results, key=lambda path: ("/Materials/" not in path, path.casefold()))


def _hair_source_material_paths(mat_name: str, entry: dict, preset: dict):
    base_name = _material_instance_base_name(str(mat_name or ""))
    source_names = [
        base_name,
        f"{base_name}_Inst",
        f"{base_name}_LWHQ_Inst",
    ]
    return _asset_paths_named(
        source_names,
        preset.get("exclude_path_fragments"),
        allowed_classes={"MaterialInstanceConstant"},
    )


def _asset_base_material_path(asset):
    if asset is None or not hasattr(asset, "get_base_material"):
        return ""
    try:
        base_material = asset.get_base_material()
    except Exception:
        return ""
    try:
        return str(base_material.get_path_name())
    except Exception:
        return str(base_material or "")


def _wrong_generated_hair_material_paths(mat_name: str, entry: dict, preset: dict, target_path: str):
    target_name = _hair_target_material_name(mat_name, entry)
    candidates = set()

    entry_target_path = _entry_target_material_path(entry)
    if entry_target_path and entry_target_path != target_path:
        candidates.add(entry_target_path)

    for path in _asset_paths_named(
        [target_name],
        allowed_classes={"MaterialInstanceConstant"},
    ):
        if path == target_path:
            continue
        if _asset_path_excluded(path, preset.get("exclude_path_fragments")):
            candidates.add(path)

    wrong_paths = []
    for path in sorted(candidates):
        asset = unreal.load_asset(path)
        base_path = _asset_base_material_path(asset).replace("\\", "/").casefold()
        if "/game/material/assetsurface/" in base_path:
            wrong_paths.append(path)
    return wrong_paths


def _delete_wrong_generated_hair_materials(mat_name: str, entry: dict, preset: dict, target_path: str):
    deleted = []
    for wrong_path in _wrong_generated_hair_material_paths(mat_name, entry, preset, target_path):
        if not unreal.EditorAssetLibrary.does_asset_exist(wrong_path):
            continue
        if unreal.EditorAssetLibrary.delete_asset(wrong_path):
            deleted.append(wrong_path)
            _log(f"  deleted wrong generated hair MI: {wrong_path}")
        else:
            _warn(f"  wrong generated hair MI delete failed: {wrong_path}")
    return deleted


def _load_or_migrate_hair_material(asset_tools, mat_name: str, entry: dict, preset: dict):
    target_path = _hair_target_material_path(mat_name, entry, preset)
    if not target_path:
        _warn(f"  hair material target path incomplete for '{mat_name}'")
        return None, None

    source_paths = [path for path in _hair_source_material_paths(mat_name, entry, preset) if path != target_path]
    if source_paths:
        source_path = source_paths[0]
        if unreal.EditorAssetLibrary.does_asset_exist(target_path):
            if not unreal.EditorAssetLibrary.delete_asset(target_path):
                _warn(f"  existing wrong hair MI delete failed: {target_path}")
                return None, None
            _log(f"  deleted wrong generated hair MI: {target_path}")
        if not unreal.EditorAssetLibrary.rename_asset(source_path, target_path):
            _warn(f"  hair MI move/rename failed: {source_path} -> {target_path}")
            return None, None
        _log(f"  hair MI moved: {source_path} -> {target_path}")
        _delete_wrong_generated_hair_materials(mat_name, entry, preset, target_path)
        return unreal.load_asset(target_path), target_path

    if unreal.EditorAssetLibrary.does_asset_exist(target_path):
        _delete_wrong_generated_hair_materials(mat_name, entry, preset, target_path)
        return unreal.load_asset(target_path), target_path

    _warn(f"  hair material instance source missing for '{mat_name}' -> target {target_path}")
    return None, None


def _source_asset_names(data: dict):
    cleanup = data.get("cleanup")
    if isinstance(cleanup, dict):
        material_names = _unique_string_list(cleanup.get("source_material_names", []))
        texture_names = _unique_string_list(cleanup.get("source_texture_names", []))
        if material_names or texture_names:
            return material_names, texture_names

    material_names = []
    texture_names = []
    seen_material_names = set()
    seen_texture_names = set()

    def append_unique(target: list, seen: set, name: str):
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            target.append(name)

    def add_material_names(name: str):
        append_unique(material_names, seen_material_names, name)
        base_name = _material_instance_base_name(name)
        append_unique(material_names, seen_material_names, base_name)
        for prefix in ("LayerBlend_", "Prop_", "Coat_"):
            if base_name.startswith(prefix):
                append_unique(material_names, seen_material_names, base_name[len(prefix):])

    def add_texture_names(tex: dict):
        file_path = str(tex.get("file", ""))
        if file_path:
            source_name = os.path.splitext(os.path.basename(file_path))[0]
            append_unique(texture_names, seen_texture_names, source_name)
        else:
            asset_name = str(tex.get("asset_name", ""))
            append_unique(texture_names, seen_texture_names, asset_name)

    for entry in data.get("materials", []):
        add_material_names(str(entry.get("name", "")))
        add_material_names(str(entry.get("slot_name", "")))
        for tex in entry.get("textures", []):
            add_texture_names(tex)
        for layer in entry.get("layers", []):
            for tex in layer.get("textures", []):
                add_texture_names(tex)
    return material_names, texture_names


def _unique_string_list(values) -> list:
    unique = []
    seen = set()
    if not isinstance(values, list):
        return unique
    for value in values:
        name = str(value or "")
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            unique.append(name)
    return unique


def _asset_paths_by_name(folder_path: str) -> dict:
    assets = unreal.EditorAssetLibrary.list_assets(folder_path, recursive=False, include_folder=False)
    paths_by_name = {}
    for asset_path in (str(path) for path in assets):
        package_path = asset_path.split(".", 1)[0]
        asset_name = package_path.rsplit("/", 1)[-1]
        paths_by_name[asset_name.casefold()] = package_path
    return paths_by_name


def _delete_asset_if_type(asset_path: str, allowed_class_names: set) -> bool:
    if not unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        return False
    asset = unreal.load_asset(asset_path)
    if asset is None:
        return False
    class_name = asset.get_class().get_name()
    if class_name not in allowed_class_names:
        return False
    if unreal.EditorAssetLibrary.delete_asset(asset_path):
        return True
    _warn(f"  cleanup delete failed: {asset_path}")
    return False


def _cleanup_imported_source_assets(mesh_path: str, data: dict):
    started = time.perf_counter()
    mesh_folder = mesh_path.rsplit("/", 1)[0]
    material_names, texture_names = _source_asset_names(data)
    existing_asset_paths = _asset_paths_by_name(mesh_folder)

    deleted = 0
    skipped_materials = 0
    skipped_textures = 0
    if DELETE_IMPORTED_SOURCE_MATERIALS:
        for name in material_names:
            asset_path = existing_asset_paths.get(name.casefold())
            if not asset_path or asset_path == mesh_path:
                continue
            if _delete_asset_if_type(asset_path, {"Material", "MaterialInstanceConstant"}):
                deleted += 1
    else:
        skipped_materials = sum(
            1
            for name in material_names
            if (asset_path := existing_asset_paths.get(name.casefold())) and asset_path != mesh_path
        )

    if DELETE_IMPORTED_SOURCE_TEXTURES:
        for name in texture_names:
            asset_path = existing_asset_paths.get(name.casefold())
            if not asset_path or asset_path == mesh_path:
                continue
            if _delete_asset_if_type(asset_path, {"Texture2D"}):
                deleted += 1
    else:
        skipped_textures = sum(
            1
            for name in texture_names
            if (asset_path := existing_asset_paths.get(name.casefold())) and asset_path != mesh_path
        )

    elapsed = time.perf_counter() - started
    if deleted or skipped_materials or skipped_textures:
        # Source asset deletes trigger package deletion, source-control work, and shader churn.
        # Keep them available for manual maintenance, but do not run them during export by default.
        material_note = (
            f"; {skipped_materials} source material asset(s) left in place"
            if skipped_materials
            else ""
        )
        texture_note = (
            f"; {skipped_textures} source texture asset(s) left in place"
            if skipped_textures
            else ""
        )
        _log(
            f"  cleanup complete: {deleted} source asset(s) removed from {mesh_folder}"
            f"{material_note}{texture_note} ({elapsed:.2f}s)"
        )
    else:
        _log(f"  cleanup: no source material/texture assets found in {mesh_folder} ({elapsed:.2f}s)")


def preflight_mesh_materials(mesh_path: str, json_path: str = None) -> bool:
    """Normalize shared material-layer assets before Unreal touches an existing mesh.

    A skeletal-mesh reimport recompiles its currently assigned material instances
    during ImportAssetTasks.  Legacy SpeedTree layers therefore have to be repaired
    before the FBX import, not from post_import after it.
    """
    mesh_path = mesh_path.split(".")[0]
    mesh_name = mesh_path.rsplit("/", 1)[-1]
    data = _load_json(mesh_name, json_path, mesh_path)
    if not data:
        return False

    helper = getattr(unreal, "CodexMaterialToolsLibrary", None)
    if helper is None:
        raise RuntimeError("CodexMaterialTools material preflight helper missing")

    normalized = False
    for entry in data.get("materials", []):
        preset = _master_preset(data, entry, mesh_path)
        if preset.get("assignment") != "material_layer_instance":
            continue
        master_path = str(preset.get("master") or "")
        parent_layer = _layer_parent_path(preset, entry)
        if master_path:
            _normalize_material_layer_asset(
                helper,
                "normalize_material_layer_placeholders",
                master_path,
                "material master",
            )
            normalized = True
        if parent_layer:
            _normalize_material_layer_asset(
                helper,
                "normalize_material_function_attribute_nodes",
                parent_layer,
                "material layer function",
            )
            normalized = True
    return normalized


def process_mesh(mesh_path: str, master_mat=None, json_path: str = None) -> bool:
    """단일 StaticMesh/SkeletalMesh 를 JSON 기반으로 처리. 변경이 있었으면 True.

    json_path: send2ue extension 이 넘겨주는 JSON 절대경로(있으면 OneDrive walk 생략).
    """
    mesh_path = mesh_path.split(".")[0]
    mesh = unreal.load_asset(mesh_path)
    if not isinstance(mesh, _supported_mesh_classes()):
        return False

    mesh_name = mesh_path.rsplit("/", 1)[-1]
    data = _load_json(mesh_name, json_path, mesh_path)

    def save_mesh_asset():
        if _is_skeletal_mesh(mesh):
            helper = getattr(unreal, "CodexMaterialToolsLibrary", None)
            if not helper or not hasattr(helper, "save_asset_package_without_thumbnail"):
                raise RuntimeError(
                    "CodexMaterialTools safe skeletal-mesh save helper is missing"
                )
            if not helper.save_asset_package_without_thumbnail(mesh):
                raise RuntimeError(f"safe skeletal-mesh save failed: {mesh_path}")
            return
        unreal.EditorAssetLibrary.save_asset(mesh_path)

    # Nanite: import 되는 StaticMesh 에 켜되, 반투명 머티리얼 메쉬는 끈다.
    # (JSON 이 없으면 불투명으로 가정 → 켬. 반투명으로 판정되면 이미 켜져 있어도 끈다.)
    if ENABLE_NANITE:
        nanite_enabled = not _is_translucent(data)
        if isinstance(mesh, unreal.StaticMesh) and _set_nanite(mesh, nanite_enabled):
            save_mesh_asset()
        elif _is_skeletal_mesh(mesh):
            voxelize = (
                _nanite_shape_preservation_voxelize()
                if ENABLE_SKELETAL_NANITE_VOXELIZE
                and _uses_tree_material_preset(data, mesh_path)
                else None
            )
            if _set_nanite(mesh, nanite_enabled, voxelize):
                save_mesh_asset()

    if data is None:
        _warn(f"JSON 사이드카 없음: {mesh_name}.json — skip (블렌더에서 Rename 버튼을 눌렀나요?)")
        return False

    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    changed = False
    json_mesh_name = str(data.get("mesh_name", ""))
    if json_mesh_name and json_mesh_name != mesh_name:
        _warn(f"JSON mesh_name mismatch: asset={mesh_name}, json={json_mesh_name}; using JSON data")
    if _import_dynamic_wind_if_available(mesh, mesh_path, mesh_name, data, json_path):
        save_mesh_asset()
        changed = True

    # 텍스처 재import 회피 캐시(메쉬마다 reload 되므로 디스크에서 읽고, 바뀌면 끝에 저장).
    tex_cache = _load_texture_cache()
    tex_cache_before = dict(tex_cache)
    skeletal_slot_assignments = {}

    for entry in data.get("materials", []):
        mat_name = str(entry.get("name", ""))
        slot_name = _entry_slot_display_name(entry, mat_name)
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

        preset = _master_preset(data, entry, mesh_path)
        if preset.get("key") == "hair":
            mi, mi_path = _load_or_migrate_hair_material(asset_tools, mat_name, entry, preset)
            if mi is None:
                continue
            _log(f"  hair slot[{slot_index}] '{mat_name}' -> {mi_path}")
            if _is_skeletal_mesh(mesh):
                skeletal_slot_assignments[slot_index] = (slot_name, mi)
            if _assign_slot(mesh, slot_index, mi, slot_name):
                changed = True
            continue

        # 반투명(유리): 전용 MI/텍스처 없이 공유 글래스 MI 를 슬롯에 바로 할당.
        if entry.get("translucent"):
            glass_mi = unreal.load_asset(GLASS_MI_PATH)
            if glass_mi is None:
                _warn(f"  글래스 MI 없음: {GLASS_MI_PATH} — 슬롯[{slot_index}] skip")
                continue
            if _is_skeletal_mesh(mesh):
                skeletal_slot_assignments[slot_index] = (slot_name, glass_mi)
            if _assign_slot(mesh, slot_index, glass_mi, slot_name):
                _log(f"  슬롯[{slot_index}] '{mat_name}' → 글래스 MI 할당(공유)")
                changed = True
            continue

        preset = _master_preset(data, entry, mesh_path)
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
                create_if_missing=_entry_create_if_missing(entry, preset),
            )
            if mi is None:
                continue
            parent_changed = False
            if selected_master is not None:
                try:
                    current_parent = mi.get_editor_property("parent")
                except Exception:
                    current_parent = None
                if not _same_asset(current_parent, selected_master):
                    unreal.MaterialEditingLibrary.set_material_instance_parent(mi, selected_master)
                    parent_changed = True
                    _log(f"  MI parent update: {mi_path} -> {selected_master.get_path_name()}")
            _log(
                f"  slot[{slot_index}] '{mat_name}' -> {mi_path} "
                f"(master: {preset['key']})"
            )
            layers = _entry_layers(entry)
            layer_maps = _import_layer_textures(
                layers,
                tex_cache,
                virtual_texture_streaming=preset.get("virtual_textures"),
            )
            mat_base = _material_instance_base_name(mat_name)
            params_changed = _assign_master_textures(
                mi,
                layer_maps,
                preset["assignment"],
                preset=preset,
                entry=entry,
                mat_base=mat_base,
            )
            if (mi_created or parent_changed or params_changed) and preset["assignment"] != "material_layer_instance":
                unreal.EditorAssetLibrary.save_asset(mi_path)
                changed = True
            elif mi_created or parent_changed or params_changed:
                changed = True
            if _is_skeletal_mesh(mesh):
                skeletal_slot_assignments[slot_index] = (slot_name, mi)
            if _assign_slot(mesh, slot_index, mi, slot_name):
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
        layers = _entry_layers(entry)
        layer_maps = _import_layer_textures(
            layers,
            tex_cache,
            virtual_texture_streaming=preset.get("virtual_textures"),
        )
        params_changed = _assign_master_textures(
            mi,
            layer_maps,
            preset["assignment"],
            preset=preset,
            entry=entry,
            mat_base=mat_base,
        )
        if (mi_created or parent_changed or params_changed) and preset["assignment"] != "material_layer_instance":
            unreal.EditorAssetLibrary.save_asset(mi_path)
            changed = True
        elif mi_created or parent_changed or params_changed:
            changed = True

        # 4. 슬롯에 MI 할당
        if _is_skeletal_mesh(mesh):
            skeletal_slot_assignments[slot_index] = (slot_name, mi)
        if _assign_slot(mesh, slot_index, mi, slot_name):
            changed = True

    # 이번 처리에서 새로 import 된 텍스처가 있으면 캐시 갱신
    if _normalize_skeletal_material_slots(mesh, skeletal_slot_assignments):
        _log(f"  skeletal material slots normalized to JSON: {len(skeletal_slot_assignments)} slot(s)")
        changed = True

    if tex_cache != tex_cache_before:
        _save_texture_cache(tex_cache)

    _cleanup_imported_source_assets(mesh_path, data)

    if changed:
        save_mesh_asset()
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


def run(import_folder: str = None):
    """수동 폴백: 폴더(재귀) 안 모든 StaticMesh/SkeletalMesh 를 처리."""
    import_folder = import_folder or f"{GAME_MESHES_PREFIX.rstrip('/')}/00_common"
    asset_registry = unreal.AssetRegistryHelpers.get_asset_registry()
    mesh_filter = unreal.ARFilter(
        class_names=["StaticMesh", "SkeletalMesh"],
        package_paths=[import_folder],
        recursive_paths=True,
    )
    meshes = asset_registry.get_assets(mesh_filter)
    if not meshes:
        _warn(f"'{import_folder}' 에서 StaticMesh/SkeletalMesh 없음")
        return
    process_meshes([str(m.package_name) for m in meshes])
