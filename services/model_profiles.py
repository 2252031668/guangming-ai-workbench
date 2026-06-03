from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


MODEL_PROFILES_FILENAME = "model_profiles.json"
LEGACY_CODEX_FILENAME = "codex.local.json"
SCHEMA_VERSION = 3
PROFILE_MODE_NATIVE = "responses_native"
PROFILE_MODE_BRIDGE = "chat_via_bridge"
DEFAULT_REASONING_EFFORT = "high"


class ModelProfileError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_positive_int(value: Any) -> int:
    text = normalize_text(value)
    if not text:
        return 0
    try:
        parsed = int(text)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def profiles_path(repo_dir: Path) -> Path:
    return repo_dir / "config" / MODEL_PROFILES_FILENAME


def legacy_config_path(repo_dir: Path) -> Path:
    return repo_dir / "config" / LEGACY_CODEX_FILENAME


def default_bridge_capabilities(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw or {}
    return {
        "thinking_toggle_supported": bool(raw.get("thinking_toggle_supported", True)),
        "thinking_default_enabled": bool(raw.get("thinking_default_enabled", True)),
        "reasoning_level_mapping_supported": bool(raw.get("reasoning_level_mapping_supported", False)),
        "reasoning_level_mapping_enabled": bool(raw.get("reasoning_level_mapping_enabled", False)),
        "upstream_protocol": "openai_chat",
    }


def default_chat_request_config(
    raw: dict[str, Any] | None = None,
    *,
    fallback_reasoning: str = DEFAULT_REASONING_EFFORT,
    bridge_capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from services.chat_code import normalize_chat_request_config

    raw = raw or {}
    bridge_capabilities = bridge_capabilities or default_bridge_capabilities(raw.get("bridge_capabilities"))
    current = raw.get("chat_request_config") if isinstance(raw.get("chat_request_config"), dict) else {}
    merged = dict(current)
    if "thinking_enabled" not in merged:
        merged["thinking_enabled"] = bool(bridge_capabilities.get("thinking_default_enabled", True))
    if not normalize_text(merged.get("reasoning_level")):
        merged["reasoning_level"] = normalize_text(
            raw.get("reasoning_effort_default") or raw.get("reasoning_effort") or fallback_reasoning
        ).lower()
    if "extra_body_template" not in merged and raw.get("extra_body_template") is not None:
        merged["extra_body_template"] = raw.get("extra_body_template")
    return normalize_chat_request_config(merged, fallback_reasoning=fallback_reasoning)


def profile_display_name(raw: dict[str, Any]) -> str:
    name = normalize_text(raw.get("name"))
    if name:
        return name
    model = normalize_text(raw.get("model"))
    if model:
        return model
    return "未命名模型"


def normalize_profile(raw: dict[str, Any], index: int = 0) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    mode = raw.get("mode")
    mode = PROFILE_MODE_BRIDGE if mode == PROFILE_MODE_BRIDGE else PROFILE_MODE_NATIVE
    profile_id = normalize_text(raw.get("id")) or f"profile-{uuid4().hex[:10]}"
    reasoning_effort = (
        normalize_text(raw.get("reasoning_effort_default") or raw.get("reasoning_effort") or DEFAULT_REASONING_EFFORT)
        or DEFAULT_REASONING_EFFORT
    )
    bridge_capabilities = default_bridge_capabilities(raw.get("bridge_capabilities"))
    chat_request_config = default_chat_request_config(
        raw,
        fallback_reasoning=reasoning_effort,
        bridge_capabilities=bridge_capabilities,
    )
    if mode == PROFILE_MODE_BRIDGE:
        bridge_capabilities["thinking_default_enabled"] = bool(chat_request_config["thinking_enabled"])
        bridge_capabilities["reasoning_level_mapping_enabled"] = chat_request_config["reasoning_level"] not in {"", "none"}
    profile = {
        "id": profile_id,
        "name": profile_display_name(raw),
        "note": normalize_text(raw.get("note")),
        "api_key": normalize_text(raw.get("api_key")),
        "base_url": normalize_text(raw.get("base_url")).rstrip("/"),
        "model": normalize_text(raw.get("model")),
        "mode": mode,
        "disable_response_storage": bool(raw.get("disable_response_storage", True)),
        "reasoning_effort_default": reasoning_effort,
        "context_window": normalize_positive_int(raw.get("context_window")),
        "max_output_tokens": normalize_positive_int(raw.get("max_output_tokens")),
        "bridge_capabilities": bridge_capabilities,
        "chat_request_config": chat_request_config,
        "created_at": normalize_text(raw.get("created_at")) or now_iso(),
        "updated_at": normalize_text(raw.get("updated_at")) or now_iso(),
        "sort_order": int(raw.get("sort_order") or index),
    }
    return profile


def empty_native_profile() -> dict[str, Any]:
    return normalize_profile(
        {
            "name": "OpenAI Responses",
            "note": "",
            "api_key": "",
            "base_url": "https://api.openai.com",
            "model": "gpt-5.4",
            "mode": PROFILE_MODE_NATIVE,
            "reasoning_effort_default": DEFAULT_REASONING_EFFORT,
        }
    )


def migrate_legacy_profile(repo_dir: Path) -> dict[str, Any] | None:
    legacy_path = legacy_config_path(repo_dir)
    if not legacy_path.exists():
        return None
    raw = read_json(legacy_path, {})
    if not isinstance(raw, dict):
        return None
    provider = normalize_text(raw.get("model_provider"))
    mode = PROFILE_MODE_NATIVE if provider != "custom-chat-bridge" else PROFILE_MODE_BRIDGE
    name = normalize_text(raw.get("profile_name")) or normalize_text(raw.get("model")) or "默认模型"
    profile = normalize_profile(
        {
            "name": name,
            "note": "由旧版 codex.local.json 自动迁移",
            "api_key": raw.get("api_key", ""),
            "base_url": raw.get("base_url", ""),
            "model": raw.get("model", ""),
            "mode": mode,
            "reasoning_effort_default": raw.get("reasoning_effort", DEFAULT_REASONING_EFFORT),
            "disable_response_storage": raw.get("disable_response_storage", True),
        }
    )
    return profile


def default_profiles_state(repo_dir: Path) -> dict[str, Any]:
    migrated = migrate_legacy_profile(repo_dir)
    profile = migrated or empty_native_profile()
    return {
        "schema_version": SCHEMA_VERSION,
        "active_profile_id": profile["id"],
        "profiles": [profile],
    }


def normalize_profiles_state(raw: Any, repo_dir: Path) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return default_profiles_state(repo_dir)
    profiles = raw.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        return default_profiles_state(repo_dir)
    source_schema_version = int(raw.get("schema_version") or 0)
    normalized_profiles = [normalize_profile(item, index=index) for index, item in enumerate(profiles) if isinstance(item, dict)]
    if not normalized_profiles:
        return default_profiles_state(repo_dir)
    if source_schema_version < 3:
        for profile in normalized_profiles:
            if profile.get("mode") != PROFILE_MODE_BRIDGE:
                continue
            # Version 2 briefly wrote these as implicit bridge defaults. Treat
            # that exact pair as "unset" so users only get limits they chose.
            if profile.get("context_window") == 32768 and profile.get("max_output_tokens") == 4096:
                profile["context_window"] = 0
                profile["max_output_tokens"] = 0
    active_id = normalize_text(raw.get("active_profile_id"))
    if not any(item["id"] == active_id for item in normalized_profiles):
        active_id = normalized_profiles[0]["id"]
    return {
        "schema_version": SCHEMA_VERSION,
        "active_profile_id": active_id,
        "profiles": normalized_profiles,
    }


def ensure_model_profiles(repo_dir: Path) -> dict[str, Any]:
    path = profiles_path(repo_dir)
    normalized = normalize_profiles_state(read_json(path, None), repo_dir)
    write_json(path, normalized)
    return normalized


def load_model_profiles(repo_dir: Path) -> dict[str, Any]:
    return ensure_model_profiles(repo_dir)


def save_model_profiles(repo_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_profiles_state(state, repo_dir)
    write_json(profiles_path(repo_dir), normalized)
    return normalized


def list_model_profiles(repo_dir: Path) -> list[dict[str, Any]]:
    state = load_model_profiles(repo_dir)
    return deepcopy(state["profiles"])


def get_active_profile_id(repo_dir: Path) -> str:
    state = load_model_profiles(repo_dir)
    return state["active_profile_id"]


def get_model_profile(repo_dir: Path, profile_id: str) -> dict[str, Any] | None:
    profile_id = normalize_text(profile_id)
    for profile in load_model_profiles(repo_dir)["profiles"]:
        if profile["id"] == profile_id:
            return deepcopy(profile)
    return None


def get_active_model_profile(repo_dir: Path) -> dict[str, Any]:
    state = load_model_profiles(repo_dir)
    active_id = state["active_profile_id"]
    profile = get_model_profile(repo_dir, active_id)
    if profile is None:
        raise ModelProfileError("当前没有可用的模型配置。")
    return profile


def set_active_model_profile(repo_dir: Path, profile_id: str) -> dict[str, Any]:
    state = load_model_profiles(repo_dir)
    profile_id = normalize_text(profile_id)
    if not any(profile["id"] == profile_id for profile in state["profiles"]):
        raise ModelProfileError("要启用的模型配置不存在。")
    state["active_profile_id"] = profile_id
    return save_model_profiles(repo_dir, state)


def validate_profile_payload(payload: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ModelProfileError("模型配置格式不正确。")
    candidate = deepcopy(existing or {})
    candidate.update(payload)
    profile = normalize_profile(candidate)
    if not profile["name"]:
        raise ModelProfileError("请填写显示名称。")
    if not profile["api_key"]:
        raise ModelProfileError("请填写 API Key。")
    if not profile["base_url"]:
        raise ModelProfileError("请填写 API 请求地址。")
    if not re.match(r"^https?://", profile["base_url"], flags=re.I):
        raise ModelProfileError("API 请求地址必须以 http:// 或 https:// 开头。")
    if not profile["model"]:
        raise ModelProfileError("请填写模型名。")
    return profile


def create_model_profile(repo_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    state = load_model_profiles(repo_dir)
    profile = validate_profile_payload(payload)
    profile["id"] = normalize_text(payload.get("id")) or f"profile-{uuid4().hex[:10]}"
    profile["created_at"] = now_iso()
    profile["updated_at"] = now_iso()
    profile["sort_order"] = len(state["profiles"])
    state["profiles"].append(profile)
    saved = save_model_profiles(repo_dir, state)
    return next(item for item in saved["profiles"] if item["id"] == profile["id"])


def update_model_profile(repo_dir: Path, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    state = load_model_profiles(repo_dir)
    for index, current in enumerate(state["profiles"]):
        if current["id"] != profile_id:
            continue
        profile = validate_profile_payload(payload, existing=current)
        profile["id"] = current["id"]
        profile["created_at"] = current.get("created_at") or now_iso()
        profile["updated_at"] = now_iso()
        profile["sort_order"] = current.get("sort_order", index)
        state["profiles"][index] = profile
        saved = save_model_profiles(repo_dir, state)
        return next(item for item in saved["profiles"] if item["id"] == profile_id)
    raise ModelProfileError("要更新的模型配置不存在。")


def delete_model_profile(repo_dir: Path, profile_id: str) -> dict[str, Any]:
    state = load_model_profiles(repo_dir)
    if len(state["profiles"]) <= 1:
        raise ModelProfileError("至少需要保留一条模型配置。")
    removed = False
    kept: list[dict[str, Any]] = []
    for profile in state["profiles"]:
        if profile["id"] == profile_id:
            removed = True
            continue
        kept.append(profile)
    if not removed:
        raise ModelProfileError("要删除的模型配置不存在。")
    for index, profile in enumerate(kept):
        profile["sort_order"] = index
    state["profiles"] = kept
    if state["active_profile_id"] == profile_id:
        state["active_profile_id"] = kept[0]["id"]
    return save_model_profiles(repo_dir, state)


def active_profile_summary(repo_dir: Path) -> dict[str, Any]:
    profile = get_active_model_profile(repo_dir)
    return {
        "id": profile["id"],
        "name": profile["name"],
        "model": profile["model"],
        "base_url": profile["base_url"],
        "mode": profile["mode"],
        "mode_label": "使用本地路由" if profile["mode"] == PROFILE_MODE_BRIDGE else "原生 Responses",
    }
