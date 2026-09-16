"""Strict consumer rules for content-addressed SpeedTree prototype identity."""

import hashlib
import json
import re


CONTENT_KIND = "speedtree_embedded_cutout_content"
IDENTITY_KIND = "speedtree_prototype_content"
LINEAGE_KIND = "speedtree_prototype_lineage_content"
BLENDER_FBX_CONTENT_KIND = "speedtree_blender_fbx_payload_content"
SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CONTENT_KEYS = {
    "kind",
    "schema_version",
    "vertex_count",
    "triangle_count",
    "position_sha256",
    "normal_sha256",
    "uv_sha256",
    "topology_sha256",
}
_IDENTITY_KEYS = {"kind", "schema_version", "algorithm", "digest"}


def canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def validate_content(value):
    if not isinstance(value, dict) or set(value) != _CONTENT_KEYS:
        raise ValueError("prototype content has an unknown or incomplete schema")
    if value.get("kind") != CONTENT_KIND or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("prototype content kind/schema_version is unsupported")
    if (
        not isinstance(value.get("vertex_count"), int)
        or isinstance(value.get("vertex_count"), bool)
        or value["vertex_count"] < 3
        or not isinstance(value.get("triangle_count"), int)
        or isinstance(value.get("triangle_count"), bool)
        or value["triangle_count"] < 1
    ):
        raise ValueError("prototype content counts are invalid")
    for key in ("position_sha256", "normal_sha256", "uv_sha256", "topology_sha256"):
        if not isinstance(value.get(key), str) or not _SHA256_RE.fullmatch(value[key]):
            raise ValueError(f"prototype content {key} is not lowercase sha256")
    return dict(value)


def identity_for_content(content):
    content = validate_content(content)
    return {
        "kind": IDENTITY_KIND,
        "schema_version": SCHEMA_VERSION,
        "algorithm": "sha256",
        "digest": hashlib.sha256(canonical_json_bytes(content)).hexdigest(),
    }


def validate_identity(value, expected_kind=IDENTITY_KIND):
    if not isinstance(value, dict) or set(value) != _IDENTITY_KEYS:
        raise ValueError("prototype identity has an unknown or incomplete schema")
    if value.get("kind") != expected_kind or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("prototype identity kind/schema_version is unsupported")
    if value.get("algorithm") != "sha256":
        raise ValueError("prototype identity algorithm is unsupported")
    if not isinstance(value.get("digest"), str) or not _SHA256_RE.fullmatch(value["digest"]):
        raise ValueError("prototype identity digest is not lowercase sha256")
    return dict(value)


def file_content_identity(path, kind):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return {
        "kind": str(kind),
        "schema_version": SCHEMA_VERSION,
        "algorithm": "sha256",
        "digest": digest.hexdigest(),
    }


def validate_file_content_identity(value, kind):
    return validate_identity(value, expected_kind=str(kind))


def validate_pair(identity, content):
    identity = validate_identity(identity)
    content = validate_content(content)
    if identity != identity_for_content(content):
        raise ValueError("prototype identity does not match current content evidence")
    return identity, content


def lineage_identity(member_identities):
    members = [validate_identity(value) for value in member_identities]
    if not members:
        raise ValueError("prototype lineage has no content identities")
    members = sorted(members, key=lambda value: value["digest"])
    if len({value["digest"] for value in members}) != len(members):
        raise ValueError("prototype lineage contains duplicate content identities")
    payload = {
        "kind": "speedtree_prototype_lineage_members",
        "schema_version": SCHEMA_VERSION,
        "members": members,
    }
    return {
        "kind": LINEAGE_KIND,
        "schema_version": SCHEMA_VERSION,
        "algorithm": "sha256",
        "digest": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    }


def validate_lineage(identity, member_identities):
    identity = validate_identity(identity, expected_kind=LINEAGE_KIND)
    if identity != lineage_identity(member_identities):
        raise ValueError("prototype lineage identity does not match its members")
    return identity
