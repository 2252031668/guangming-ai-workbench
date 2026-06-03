from __future__ import annotations

import ast
import json
import pprint
from copy import deepcopy
from typing import Any

from services.model_profiles import normalize_text


class ChatCodeError(RuntimeError):
    pass


DEFAULT_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant"},
    {"role": "user", "content": "Hello"},
]


def default_extra_body(thinking_enabled: bool) -> str:
    if not thinking_enabled:
        return ""
    return json.dumps({"thinking": {"type": "enabled"}}, ensure_ascii=False, indent=2)


def normalize_chat_request_config(raw: dict[str, Any] | None = None, *, fallback_reasoning: str = "high") -> dict[str, Any]:
    raw = raw or {}
    reasoning_level = normalize_text(raw.get("reasoning_level") or fallback_reasoning or "high").lower()
    if reasoning_level not in {"", "none", "low", "medium", "high"}:
        reasoning_level = "high"
    thinking_enabled = bool(raw.get("thinking_enabled", True))
    extra_body_template = raw.get("extra_body_template")
    if isinstance(extra_body_template, dict):
        extra_body_template = json.dumps(extra_body_template, ensure_ascii=False, indent=2)
    extra_body_template = str(extra_body_template or "").strip()
    if not extra_body_template and thinking_enabled:
        extra_body_template = default_extra_body(True)
    return {
        "thinking_enabled": thinking_enabled,
        "reasoning_level": reasoning_level if reasoning_level else "none",
        "extra_body_template": extra_body_template,
    }


def parse_extra_body_template(text: str) -> dict[str, Any]:
    source = str(text or "").strip()
    if not source:
        return {}
    try:
        value = json.loads(source)
    except json.JSONDecodeError as exc:
        raise ChatCodeError(f"extra_body 不是合法 JSON：{exc.msg}") from exc
    if not isinstance(value, dict):
        raise ChatCodeError("extra_body 必须是 JSON 对象。")
    return value


def generate_chat_code(profile: dict[str, Any]) -> str:
    config = normalize_chat_request_config(
        profile.get("chat_request_config"),
        fallback_reasoning=profile.get("reasoning_effort_default") or "high",
    )
    extra_body_value = parse_extra_body_template(config["extra_body_template"]) if config["extra_body_template"] else {}
    reasoning_lines = ""
    if config["reasoning_level"] not in {"", "none"}:
        reasoning_lines += f'    reasoning_effort="{config["reasoning_level"]}",\n'
    if extra_body_value:
        extra_body_literal = pprint.pformat(extra_body_value, width=88, sort_dicts=False)
        extra_body_literal = extra_body_literal.replace("\n", "\n    ")
        reasoning_lines += f"    extra_body={extra_body_literal}\n"

    return (
        "import os\n"
        "from openai import OpenAI\n\n"
        "client = OpenAI(\n"
        f'    api_key="{profile.get("api_key", "")}",\n'
        f'    base_url="{profile.get("base_url", "")}")\n\n'
        "response = client.chat.completions.create(\n"
        f'    model="{profile.get("model", "")}",\n'
        "    messages=[\n"
        '        {"role": "system", "content": "You are a helpful assistant"},\n'
        '        {"role": "user", "content": "Hello"},\n'
        "    ],\n"
        "    stream=False,\n"
        f"{reasoning_lines}"
        ")\n\n"
        "print(response.choices[0].message.content)\n"
    )


def _literal_value(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except Exception as exc:  # noqa: BLE001
        raise ChatCodeError("代码中存在无法解析的值，请保持模板参数为字符串、字典、列表或布尔值。") from exc


def _keyword_map(call: ast.Call) -> dict[str, ast.AST]:
    result: dict[str, ast.AST] = {}
    for keyword in call.keywords:
        if keyword.arg:
            result[keyword.arg] = keyword.value
    return result


def parse_chat_code(code: str, base_profile: dict[str, Any]) -> dict[str, Any]:
    source = str(code or "").strip()
    if not source:
        raise ChatCodeError("代码内容为空，无法测试。")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ChatCodeError(f"代码格式无法解析：第 {exc.lineno} 行存在语法错误。") from exc

    client_call: ast.Call | None = None
    create_call: ast.Call | None = None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "OpenAI":
                client_call = node
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "create":
                create_call = node

    if client_call is None or create_call is None:
        raise ChatCodeError("未找到 OpenAI(...) 或 client.chat.completions.create(...) 调用。")

    client_kwargs = _keyword_map(client_call)
    create_kwargs = _keyword_map(create_call)
    profile = deepcopy(base_profile)
    chat_config = normalize_chat_request_config(
        profile.get("chat_request_config"),
        fallback_reasoning=profile.get("reasoning_effort_default") or "high",
    )

    if "api_key" in client_kwargs:
        profile["api_key"] = str(_literal_value(client_kwargs["api_key"]))
    if "base_url" in client_kwargs:
        profile["base_url"] = str(_literal_value(client_kwargs["base_url"]))
    if "model" in create_kwargs:
        profile["model"] = str(_literal_value(create_kwargs["model"]))
    if "reasoning_effort" in create_kwargs:
        chat_config["reasoning_level"] = str(_literal_value(create_kwargs["reasoning_effort"])).strip().lower() or "none"
    else:
        chat_config["reasoning_level"] = "none"

    if "extra_body" in create_kwargs:
        extra_body = _literal_value(create_kwargs["extra_body"])
        if not isinstance(extra_body, dict):
            raise ChatCodeError("extra_body 必须是 Python 字典。")
        chat_config["extra_body_template"] = json.dumps(extra_body, ensure_ascii=False, indent=2)
        chat_config["thinking_enabled"] = bool(extra_body)
    else:
        chat_config["extra_body_template"] = ""
        chat_config["thinking_enabled"] = False

    profile["chat_request_config"] = chat_config
    if chat_config["reasoning_level"] not in {"", "none"}:
        profile["reasoning_effort_default"] = chat_config["reasoning_level"]
    return profile


def apply_form_chat_settings(profile: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(profile)
    chat_config = normalize_chat_request_config(
        updated.get("chat_request_config"),
        fallback_reasoning=updated.get("reasoning_effort_default") or "high",
    )
    if not chat_config["thinking_enabled"]:
        chat_config["extra_body_template"] = ""
    elif not chat_config["extra_body_template"]:
        chat_config["extra_body_template"] = default_extra_body(True)
    updated["chat_request_config"] = chat_config
    return updated
