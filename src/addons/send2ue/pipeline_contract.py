import json
import os
from functools import lru_cache
from pathlib import Path


def _candidate_paths():
    override = os.environ.get("SUBSTANCE_TOOLS_PIPELINE_CONTRACT")
    if override:
        yield Path(override)

    here = Path(__file__).resolve()
    for parent in here.parents:
        yield parent / "pipeline_contract.json"
        yield parent / "substance-tools" / "pipeline_contract.json"
        yield parent / "substance_tools" / "pipeline_contract.json"

    appdata = os.environ.get("APPDATA")
    if appdata:
        blender_root = Path(appdata) / "Blender Foundation" / "Blender"
        for path in sorted(blender_root.glob("*/scripts/addons/substance_tools/pipeline_contract.json"), reverse=True):
            yield path


@lru_cache(maxsize=1)
def pipeline_contract():
    for path in _candidate_paths():
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def collection_name(key, default):
    return pipeline_contract().get("blender_collections", {}).get(key, default)


def unreal_path_mapping():
    return pipeline_contract().get("unreal_path_mapping", {}).get("current_default", {})
