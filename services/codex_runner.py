from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, SkillInput, TextInput
from openai_codex.generated.v2_all import (
    AgentMessageDeltaNotification,
    AgentMessageThreadItem,
    CommandExecutionThreadItem,
    ItemCompletedNotification,
    ReasoningSummary,
    ReasoningSummaryTextDeltaNotification,
    ReasoningTextDeltaNotification,
    ThreadItem,
    ThreadTokenUsage,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    TurnStatus,
)
from services.bridge_manager import BridgeError, start_bridge
from services.model_profiles import (
    PROFILE_MODE_BRIDGE,
    get_active_model_profile,
)


class CodexConfigError(RuntimeError):
    pass


SEARCH_MODE_QUICK = "quick"
SEARCH_MODE_DEEP = "deep"
SEARCH_MODE_LABELS = {
    SEARCH_MODE_QUICK: "快速检索",
    SEARCH_MODE_DEEP: "深度检索",
}

MINIMAL_CONNECTIVITY_PROMPT = "请只回答“正常”两个字。"


@dataclass(slots=True)
class CodexTurnDiagnostics:
    turn_id: str
    status: str
    error: str
    final_response: str
    delta_text: str
    items: list[ThreadItem]
    usage: ThreadTokenUsage | None
    diagnostics: dict[str, Any]

    def to_api_payload(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "turn_status": self.status,
            "assistant_text": self.final_response,
            "usage": model_to_json(self.usage),
            "diagnostics": self.diagnostics,
        }


class CodexTurnError(RuntimeError):
    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CodexConfigError(f"codex.local.json 配置文件不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalize_search_mode(value: str | None) -> str:
    return SEARCH_MODE_QUICK if value == SEARCH_MODE_QUICK else SEARCH_MODE_DEEP


def search_mode_label(value: str | None) -> str:
    return SEARCH_MODE_LABELS.get(normalize_search_mode(value), SEARCH_MODE_LABELS[SEARCH_MODE_DEEP])


def model_to_json(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True, mode="json")
    return value


def item_root(item: ThreadItem) -> Any:
    return item.root if hasattr(item, "root") else item


def item_summary(item: ThreadItem) -> dict[str, Any]:
    root = item_root(item)
    item_type = str(getattr(root, "type", type(root).__name__))
    summary: dict[str, Any] = {
        "type": item_type,
        "id": str(getattr(root, "id", "")),
    }
    if isinstance(root, AgentMessageThreadItem):
        summary["phase"] = root.phase.value if root.phase else ""
        summary["text_preview"] = root.text[:500]
    elif isinstance(root, CommandExecutionThreadItem):
        summary["status"] = root.status.value
    return summary


def final_response_from_items(items: list[ThreadItem]) -> str:
    last_unknown_phase = ""
    for item in reversed(items):
        root = item_root(item)
        if not isinstance(root, AgentMessageThreadItem):
            continue
        phase = root.phase.value if root.phase else ""
        if phase == "final_answer":
            return root.text or ""
        if not phase and not last_unknown_phase:
            last_unknown_phase = root.text or ""
    return last_unknown_phase


def collect_codex_turn_with_diagnostics(
    stream: Iterator[Any],
    *,
    turn_id: str,
    on_event: Callable[[Any], None] | None = None,
) -> CodexTurnDiagnostics:
    completed: TurnCompletedNotification | None = None
    items: list[ThreadItem] = []
    usage: ThreadTokenUsage | None = None
    deltas: list[str] = []

    for event in stream:
        if on_event:
            on_event(event)
        payload = event.payload
        if isinstance(payload, AgentMessageDeltaNotification) and payload.turn_id == turn_id:
            deltas.append(payload.delta)
            continue
        if isinstance(payload, ItemCompletedNotification) and payload.turn_id == turn_id:
            items.append(payload.item)
            continue
        if isinstance(payload, ThreadTokenUsageUpdatedNotification) and payload.turn_id == turn_id:
            usage = payload.token_usage
            continue
        if isinstance(payload, TurnCompletedNotification) and payload.turn.id == turn_id:
            completed = payload

    if completed is None:
        diagnostics = {
            "turn_id": turn_id,
            "item_count": len(items),
            "delta_preview": "".join(deltas).strip()[:1000],
            "items": [item_summary(item) for item in items[-8:]],
        }
        raise CodexTurnError("没有收到 Codex turn completed 事件。", diagnostics)

    turn = completed.turn
    status = turn.status.value if hasattr(turn.status, "value") else str(turn.status)
    error = turn.error.message if turn.error is not None and turn.error.message else ""
    text_from_items = final_response_from_items(items)
    delta_text = "".join(deltas).strip()
    final_response = (text_from_items or delta_text).strip()
    diagnostics = {
        "turn_id": turn_id,
        "status": status,
        "error": error,
        "item_count": len(items),
        "agent_delta_chars": len(delta_text),
        "items": [item_summary(item) for item in items[-8:]],
    }
    if turn.status == TurnStatus.failed:
        raise CodexTurnError(error or f"Codex turn failed: {status}", diagnostics)
    return CodexTurnDiagnostics(
        turn_id=turn_id,
        status=status,
        error=error,
        final_response=final_response,
        delta_text=delta_text,
        items=items,
        usage=usage,
        diagnostics=diagnostics,
    )


def run_thread_turn_with_diagnostics(
    thread: Any,
    turn_input: Any,
    *,
    approval_mode: ApprovalMode = ApprovalMode.deny_all,
    sandbox: Sandbox = Sandbox.full_access,
    summary: ReasoningSummary | None = None,
    on_event: Callable[[Any], None] | None = None,
) -> CodexTurnDiagnostics:
    turn = thread.turn(
        turn_input,
        approval_mode=approval_mode,
        sandbox=sandbox,
        summary=summary,
    )
    stream = turn.stream()
    try:
        return collect_codex_turn_with_diagnostics(stream, turn_id=turn.id, on_event=on_event)
    finally:
        stream.close()


def is_context_limit_error(message: str) -> bool:
    text = str(message or "").lower()
    needles = [
        "max_seq_len",
        "context_length_exceeded",
        "input tokens",
        "maximum context",
        "context window",
        "too many tokens",
        "tokens has exceeded",
        "exceeded max",
    ]
    return any(item in text for item in needles)


def friendly_codex_error(exc: Exception) -> str:
    message = str(exc)
    if is_context_limit_error(message):
        return f"当前线程历史或本轮材料超过上游模型上下文窗口：{message}"
    return message


def wait_for_tcp_port(host: str, port: int, *, timeout_seconds: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.2)
    if last_error:
        raise last_error


def build_config_overrides(config: dict[str, Any], *, reasoning_effort: str | None = None) -> tuple[str, ...]:
    provider = config.get("model_provider") or "custom"
    base_url = str(config["base_url"]).rstrip("/") + "/"
    effort = reasoning_effort or config.get("reasoning_effort", "high")
    overrides = [
        f'model_provider="{provider}"',
        f'model="{config["model"]}"',
        f'model_reasoning_effort="{effort}"',
        f'disable_response_storage={str(config.get("disable_response_storage", True)).lower()}',
        f'model_providers.{provider}.name="{provider}"',
        f'model_providers.{provider}.wire_api="{config.get("wire_api", "responses")}"',
        f"model_providers.{provider}.requires_openai_auth=true",
        f'model_providers.{provider}.base_url="{base_url}"',
    ]
    if int(config.get("context_window") or 0) > 0:
        overrides.append(f'model_context_window={int(config["context_window"])}')
    if int(config.get("max_output_tokens") or 0) > 0:
        overrides.append(f'model_max_output_tokens={int(config["max_output_tokens"])}')
    return tuple(overrides)


def profile_runtime_config(repo_dir: Path, profile: dict[str, Any], *, bridge_trace_enabled: bool = False) -> dict[str, Any]:
    mode = profile.get("mode")
    base_url = str(profile.get("base_url") or "").rstrip("/")
    api_key = str(profile.get("api_key") or "")
    provider = f"profile_{profile.get('id', 'custom').replace('-', '_')}"
    runtime = {
        "profile_id": profile.get("id"),
        "profile_name": profile.get("name"),
        "mode": mode,
        "api_key": api_key,
        "base_url": base_url,
        "model": profile.get("model") or "",
        "model_provider": provider,
        "wire_api": "responses",
        "reasoning_effort": profile.get("reasoning_effort_default") or "high",
        "context_window": int(profile.get("context_window") or 0),
        "max_output_tokens": int(profile.get("max_output_tokens") or 0),
        "disable_response_storage": bool(profile.get("disable_response_storage", True)),
        "bridge": None,
    }
    if mode == PROFILE_MODE_BRIDGE:
        bridge = start_bridge(repo_dir, profile, trace_enabled=bridge_trace_enabled)
        runtime["api_key"] = bridge.auth_token
        runtime["base_url"] = bridge.base_url
        runtime["bridge"] = {
            "port": bridge.port,
            "config_path": str(bridge.config_path),
            "runtime_dir": str(bridge.runtime_dir),
        }
    return runtime


def load_runtime_config(repo_dir: Path) -> dict[str, Any]:
    profile = get_active_model_profile(repo_dir)
    return profile_runtime_config(repo_dir, profile)


def run_connectivity_probe(
    *,
    repo_dir: Path,
    working_dir: Path,
    profile_payload: dict[str, Any],
) -> dict[str, Any]:
    profile = dict(profile_payload)
    runtime = profile_runtime_config(repo_dir, profile, bridge_trace_enabled=True)
    provider = runtime.get("model_provider") or "custom"
    codex_home = repo_dir / "instance" / "codex-home-web"
    codex_home.mkdir(parents=True, exist_ok=True)
    codex_config = CodexConfig(
        cwd=str(working_dir),
        env={"CODEX_HOME": str(codex_home)},
        config_overrides=build_config_overrides(runtime, reasoning_effort=runtime.get("reasoning_effort")),
    )
    try:
        with Codex(codex_config) as codex:
            codex.login_api_key(runtime["api_key"])
            thread = codex.thread_start(
                cwd=str(working_dir),
                sandbox=Sandbox.full_access,
                approval_mode=ApprovalMode.deny_all,
                model=runtime["model"],
                model_provider=provider,
                ephemeral=True,
            )
            result = run_thread_turn_with_diagnostics(
                thread,
                [TextInput(MINIMAL_CONNECTIVITY_PROMPT)],
                summary=ReasoningSummary(root="concise"),
            )
            if not result.final_response:
                payload = result.to_api_payload()
                payload.update(
                    {
                        "ok": False,
                        "path": "本地桥接" if runtime.get("mode") == PROFILE_MODE_BRIDGE else "原生 Responses",
                        "message": "Codex turn 已完成，但没有收到任何 assistant 文本。",
                    }
                )
                return payload
        return {
            "ok": True,
            "path": "本地桥接" if runtime.get("mode") == PROFILE_MODE_BRIDGE else "原生 Responses",
            "message": f"测试成功：{result.final_response}",
            **result.to_api_payload(),
        }
    except BridgeError:
        raise
    except CodexTurnError as exc:
        return {
            "ok": False,
            "path": "本地桥接" if runtime.get("mode") == PROFILE_MODE_BRIDGE else "原生 Responses",
            "message": friendly_codex_error(exc),
            "diagnostics": exc.diagnostics,
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": "本地桥接" if runtime.get("mode") == PROFILE_MODE_BRIDGE else "原生 Responses",
            "message": friendly_codex_error(exc),
        }


def run_bridge_responses_probe(
    *,
    repo_dir: Path,
    profile_payload: dict[str, Any],
) -> dict[str, Any]:
    profile = dict(profile_payload)
    if profile.get("mode") != PROFILE_MODE_BRIDGE:
        return {"ok": False, "path": "Moon Bridge Responses", "message": "原生 Responses 模式不需要本地路由测试。"}
    bridge = start_bridge(repo_dir, profile, trace_enabled=True)
    try:
        wait_for_tcp_port("127.0.0.1", bridge.port)
    except Exception as exc:
        return {
            "ok": False,
            "path": "Moon Bridge Responses",
            "message": f"Moon Bridge 已启动但端口暂未就绪：{friendly_codex_error(exc)}",
            "bridge_runtime": {"port": bridge.port, "config_path": str(bridge.config_path)},
        }
    body = json.dumps(
        {
            "model": profile.get("model") or "",
            "input": MINIMAL_CONNECTIVITY_PROMPT,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        last_error: Exception | None = None
        raw = ""
        for attempt in range(30):
            try:
                request = urllib.request.Request(
                    f"{bridge.base_url.rstrip('/')}/responses",
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {bridge.auth_token}",
                    },
                )
                with urllib.request.urlopen(request, timeout=45) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                break
            except urllib.error.HTTPError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == 29:
                    raise
                time.sleep(0.5)
        if not raw and last_error:
            raise last_error
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "path": "Moon Bridge Responses",
            "message": friendly_codex_error(RuntimeError(detail or str(exc))),
            "bridge_runtime": {"port": bridge.port, "config_path": str(bridge.config_path)},
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": "Moon Bridge Responses",
            "message": friendly_codex_error(exc),
            "bridge_runtime": {"port": bridge.port, "config_path": str(bridge.config_path)},
        }
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"raw": raw}
    output_text = str(data.get("output_text") or "").strip() if isinstance(data, dict) else ""
    if not output_text and isinstance(data, dict):
        output_parts: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("text"):
                    output_parts.append(str(content.get("text")))
        output_text = "".join(output_parts).strip()
    return {
        "ok": bool(output_text),
        "path": "Moon Bridge Responses",
        "message": f"测试成功：{output_text}" if output_text else "Moon Bridge 返回成功，但 Responses 响应中没有 output_text。",
        "assistant_text": output_text,
        "diagnostics": {
            "response_keys": list(data.keys()) if isinstance(data, dict) else [],
            "raw_preview": raw[:1000],
        },
        "bridge_runtime": {"port": bridge.port, "config_path": str(bridge.config_path)},
    }


def compact_codex_thread(*, repo_dir: Path, working_dir: Path, thread_id: str) -> dict[str, Any]:
    config = load_runtime_config(repo_dir)
    provider = config.get("model_provider") or "custom"
    codex_home = repo_dir / "instance" / "codex-home-web"
    codex_home.mkdir(parents=True, exist_ok=True)
    codex_config = CodexConfig(
        cwd=str(working_dir),
        env={"CODEX_HOME": str(codex_home)},
        config_overrides=build_config_overrides(config, reasoning_effort=config.get("reasoning_effort", "high")),
    )
    with Codex(codex_config) as codex:
        codex.login_api_key(config["api_key"])
        thread = codex.thread_resume(
            thread_id,
            cwd=str(working_dir),
            sandbox=Sandbox.full_access,
            approval_mode=ApprovalMode.deny_all,
            model=config["model"],
            model_provider=provider,
        )
        thread.compact()
    return {"ok": True, "thread_id": thread_id, "message": "已请求 Codex 压缩当前线程记忆。"}


def quick_result_schema(run_id: str, user_request: str) -> str:
    return f"""
{{
  "run_id": "{run_id}",
  "user_request": "{user_request}",
  "search_mode": "quick",
  "status": "success",
  "results": [
    {{
      "title": "论文标题必填",
      "authors": ["作者1", "作者2"],
      "year": 2024,
      "venue": "会议或期刊",
      "paper_url": "https://...",
      "doi": "10....",
      "abstract_zh": "一两句中文摘要",
      "pdf_url": "https://..."
    }}
  ]
}}
""".strip()


def deep_result_schema(run_id: str, user_request: str) -> str:
    return f"""
{{
  "run_id": "{run_id}",
  "user_request": "{user_request}",
  "search_mode": "deep",
  "status": "success",
  "results": [
    {{
      "title": "论文标题必填",
      "authors": ["作者1", "作者2", "作者3"],
      "year": 2024,
      "venue": "会议或期刊",
      "paper_url": "https://...",
      "doi": "10....",
      "abstract": "英文原摘要",
      "keywords": ["关键词1", "关键词2", "关键词3", "关键词4"],
      "abstract_zh": "根据英文摘要翻译并简化后的中文摘要",
      "pdf_url": "https://..."
    }}
  ]
}}
""".strip()


def build_quick_search_prompt(user_request: str, run_id: str, max_results: int = 8) -> str:
    return f"""
你正在执行“快速检索”模式。

重要约束：
1. 用户输入的是完整检索要求，不一定只是主题词，里面可能包含年份、数量、偏好、举例、重点方向等条件。你必须先理解用户要求，再检索。
2. 快速模式强调速度与代表性，不追求字段极度完整。

任务要求：
1. 根据下面这段用户检索要求，快速找出高相关论文。
2. 优先满足用户明确提出的限制条件，例如年份、数量、领域、顶会偏好、是否先给代表论文。
3. 如果用户明确要求数量，优先满足该数量；否则默认返回 {max_results} 篇以内。
4. 每条结果的 title 、 year和paper_url 必须填写；authors 只保留前两个作者； doi / pdf_url 若找不到就填空字符串。
5. abstract_zh 必须写成简洁中文，一到两句即可，不要照搬英文。

用户检索要求：
{user_request}

输出要求：
1. 只把本次检索结果写入 search_runs/{run_id}.json。
2. 结果文件必须是纯 JSON，不要输出 Markdown 包裹。
3. JSON 字段结构必须严格遵守下面这个格式：
{quick_result_schema(run_id, user_request)}

完成 JSON 写入后，请在最终回复里只用中文给用户一句简洁总结：
“推荐优先阅读哪几篇 + 原因 + 共找到多少篇”。
不要提本地路径、脚本路径、skill、内部执行过程。
""".strip()


def build_deep_search_prompt(user_request: str, run_id: str, max_results: int = 8) -> str:
    return f"""
你正在执行“深度检索”模式。

重要约束：
1. 用户输入的是完整检索要求，不一定只是主题词，里面可能包含年份、数量、重点、示例、场景偏好、会议期刊偏好等条件。你必须先理解用户要求，再制定检索策略。
2. academic-search-only skill 已通过 SkillInput 显式注入到本次 turn。
3. 不要在最终用户回复中暴露本地路径、脚本路径、skill、内部实现细节。

任务要求：
1. 直接按照已注入的 academic-search-only skill 指令深入检索高相关论文。
2. 优先满足用户明确提出的限制条件，例如年份范围、数量要求、重点方向、是否优先开放 PDF。
3. 如果用户明确要求数量，优先满足该数量；否则默认返回 {max_results} 篇以内。
4. 每条结果必须填写 title、authors、year、paper_url
5. doi,尽量要有；abstract 必须保留英文原摘要；abstract_zh 必须根据英文摘要翻译并简化为几句中文；keywords 最多保留前 4 个；pdf_url 如果是开放PDF尽量填写，没有或者不开放就填空字符串。

用户检索要求：
{user_request}

输出要求：
1. 只把本次检索结果写入 search_runs/{run_id}.json。
2. 结果文件必须是纯 JSON，不要输出 Markdown 包裹。
3. JSON 字段结构必须严格遵守下面这个格式字段：
{deep_result_schema(run_id, user_request)}

完成 JSON 写入后，请在最终回复里只用中文给用户一句简洁总结：
“推荐优先阅读哪几篇 + 原因 + 共找到多少篇”。
不要提本地路径、脚本路径、skill、内部执行过程。
""".strip()


def build_turn_inputs(*, repo_dir: Path, user_request: str, run_id: str, search_mode: str, max_results: int) -> list[Any]:
    if search_mode == SEARCH_MODE_QUICK:
        return [TextInput(build_quick_search_prompt(user_request, run_id, max_results=max_results))]
    return [
        SkillInput(
            name="academic-search-only",
            path=str((repo_dir / "skills" / "academic-search-only" / "SKILL.md").resolve()),
        ),
        TextInput(
            build_deep_search_prompt(
                user_request,
                run_id,
                max_results=max_results,
            )
        ),
    ]


def run_literature_search(
    *,
    repo_dir: Path,
    project_dir: Path,
    run_id: str,
    user_request: str,
    search_mode: str,
    max_results: int = 10,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    search_mode = normalize_search_mode(search_mode)
    config = load_runtime_config(repo_dir)
    provider = config.get("model_provider") or "custom"
    reasoning_effort = "medium" if search_mode == SEARCH_MODE_QUICK else config.get("reasoning_effort", "high")
    codex_home = repo_dir / "instance" / "codex-home-web"
    codex_home.mkdir(parents=True, exist_ok=True)

    run_record_path = project_dir / "search_runs" / f"{run_id}.json"
    run_record_path.parent.mkdir(parents=True, exist_ok=True)

    codex_config = CodexConfig(
        cwd=str(project_dir),
        env={"CODEX_HOME": str(codex_home)},
        config_overrides=build_config_overrides(config, reasoning_effort=reasoning_effort),
    )

    def emit(message: str) -> None:
        if progress:
            progress(message)

    with Codex(codex_config) as codex:
        emit(f"智能体已启动，准备执行{search_mode_label(search_mode)}。")
        codex.login_api_key(config["api_key"])
        emit("认证完成，正在创建本次检索线程。")
        thread = codex.thread_start(
            cwd=str(project_dir),
            sandbox=Sandbox.full_access,
            approval_mode=ApprovalMode.deny_all,
            model=config["model"],
            model_provider=provider,
            ephemeral=True,
        )
        turn = thread.turn(
            build_turn_inputs(
                repo_dir=repo_dir,
                user_request=user_request,
                run_id=run_id,
                search_mode=search_mode,
                max_results=max_results,
            ),
            approval_mode=ApprovalMode.deny_all,
            sandbox=Sandbox.full_access,
            summary=ReasoningSummary(root="concise"),
        )
        if search_mode == SEARCH_MODE_QUICK:
            emit("智能体已接收任务，开始执行快速检索。")
        else:
            emit("智能体已接收任务，开始执行深度检索。")
        stream = turn.stream()
        try:
            result = collect_turn_result_with_progress(stream, turn_id=turn.id, progress=emit, search_mode=search_mode)
        finally:
            stream.close()

    return {
        "assistant_message": result.final_response or "",
        "run_record_path": run_record_path,
        "run_record_exists": run_record_path.exists(),
        "search_mode": search_mode,
        "turn_id": result.turn_id,
        "turn_status": result.status,
        "usage": result.to_api_payload().get("usage"),
        "diagnostics": result.diagnostics,
    }


def collect_turn_result_with_progress(stream, *, turn_id: str, progress: Callable[[str], None], search_mode: str):
    recent_agent_text = ""
    recent_reasoning_text = ""

    def emit_once(message: str) -> None:
        progress(message)

    def item_root(item):
        return item.root if hasattr(item, "root") else item

    def observe_event(event) -> None:
        nonlocal recent_agent_text, recent_reasoning_text
        payload = event.payload
        if isinstance(payload, AgentMessageDeltaNotification) and payload.turn_id == turn_id:
            recent_agent_text = (recent_agent_text + payload.delta)[-160:]
            if len(recent_agent_text.strip()) >= 40:
                emit_once(f"智能体正在组织回复：{recent_agent_text.strip()}")
                recent_agent_text = ""
            return
        if isinstance(payload, ReasoningSummaryTextDeltaNotification) and payload.turn_id == turn_id:
            recent_reasoning_text = (recent_reasoning_text + payload.delta)[-180:]
            if len(recent_reasoning_text.strip()) >= 35:
                emit_once(f"思考推理：{recent_reasoning_text.strip()}")
                recent_reasoning_text = ""
            return
        if isinstance(payload, ReasoningTextDeltaNotification) and payload.turn_id == turn_id:
            if search_mode == SEARCH_MODE_QUICK:
                emit_once("智能体正在快速整理检索线索。")
            else:
                emit_once("智能体正在分析用户要求并组织深度检索策略。")
            return
        if isinstance(payload, ItemCompletedNotification) and payload.turn_id == turn_id:
            item = item_root(payload.item)
            if isinstance(item, CommandExecutionThreadItem):
                emit_once(f"已执行检索辅助命令，状态：{item.status.value}。")

    return collect_codex_turn_with_diagnostics(stream, turn_id=turn_id, on_event=observe_event)
