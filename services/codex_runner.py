from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, SkillInput, TextInput
from openai_codex._run import _collect_turn_result
from openai_codex.generated.v2_all import (
    AgentMessageDeltaNotification,
    CommandExecutionThreadItem,
    ItemCompletedNotification,
    ReasoningSummary,
    ReasoningSummaryTextDeltaNotification,
    ReasoningTextDeltaNotification,
)


class CodexConfigError(RuntimeError):
    pass


SEARCH_MODE_QUICK = "quick"
SEARCH_MODE_DEEP = "deep"
SEARCH_MODE_LABELS = {
    SEARCH_MODE_QUICK: "快速检索",
    SEARCH_MODE_DEEP: "深度检索",
}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CodexConfigError(f"codex.local.json 配置文件不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalize_search_mode(value: str | None) -> str:
    return SEARCH_MODE_QUICK if value == SEARCH_MODE_QUICK else SEARCH_MODE_DEEP


def search_mode_label(value: str | None) -> str:
    return SEARCH_MODE_LABELS.get(normalize_search_mode(value), SEARCH_MODE_LABELS[SEARCH_MODE_DEEP])


def build_config_overrides(config: dict[str, Any], *, reasoning_effort: str | None = None) -> tuple[str, ...]:
    provider = config.get("model_provider") or "custom"
    base_url = str(config["base_url"]).rstrip("/") + "/"
    effort = reasoning_effort or config.get("reasoning_effort", "high")
    return (
        f'model_provider="{provider}"',
        f'model="{config["model"]}"',
        f'model_reasoning_effort="{effort}"',
        f'disable_response_storage={str(config.get("disable_response_storage", True)).lower()}',
        f'model_providers.{provider}.name="{provider}"',
        f'model_providers.{provider}.wire_api="{config.get("wire_api", "responses")}"',
        f"model_providers.{provider}.requires_openai_auth=true",
        f'model_providers.{provider}.base_url="{base_url}"',
    )


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
    config = read_json(repo_dir / "config" / "codex.local.json")
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

    def observed_stream():
        for event in stream:
            observe_event(event)
            yield event

    return _collect_turn_result(observed_stream(), turn_id=turn_id)
