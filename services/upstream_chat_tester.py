from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from services.chat_code import parse_extra_body_template


class UpstreamChatTestError(RuntimeError):
    pass


def normalize_base_url(base_url: str) -> str:
    url = str(base_url or "").rstrip("/")
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/chat/completions"


def build_payload(profile: dict[str, Any]) -> dict[str, Any]:
    chat_config = profile.get("chat_request_config") or {}
    payload: dict[str, Any] = {
        "model": profile.get("model") or "",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Hello"},
        ],
        "stream": False,
    }
    reasoning = str(chat_config.get("reasoning_level") or "").strip().lower()
    if reasoning and reasoning != "none":
        payload["reasoning_effort"] = reasoning
    extra_template = str(chat_config.get("extra_body_template") or "").strip()
    if extra_template:
        # OpenAI SDK's extra_body is merged into the outgoing JSON body.
        payload.update(parse_extra_body_template(extra_template))
    return payload


def test_upstream_chat(profile: dict[str, Any]) -> dict[str, Any]:
    target = normalize_base_url(str(profile.get("base_url") or ""))
    body = json.dumps(build_payload(profile), ensure_ascii=False).encode("utf-8")
    req = request.Request(
        target,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {profile.get('api_key') or ''}",
            "Accept": "application/json",
            "User-Agent": "guangming-ai-workbench/0.1",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=45) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise UpstreamChatTestError(f"上游请求失败：HTTP {exc.code} {detail[:500]}") from exc
    except Exception as exc:  # noqa: BLE001
        raise UpstreamChatTestError(f"上游请求失败：{exc}") from exc

    message = ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        content = first.get("message") if isinstance(first.get("message"), dict) else {}
        message = str(content.get("content") or "").strip()

    return {
        "ok": True,
        "path": "上游 Chat Completions",
        "message": message or "测试成功，但未返回可显示的正文内容。",
        "assistant_text": message,
        "source": "代码编辑区",
    }
