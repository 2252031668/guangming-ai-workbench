from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, TextInput
from openai_codex.generated.v2_all import (
    AgentMessageDeltaNotification,
    CommandExecutionThreadItem,
    ItemCompletedNotification,
    ReasoningSummary,
    ReasoningSummaryTextDeltaNotification,
    ReasoningTextDeltaNotification,
)

from services.codex_runner import (
    build_config_overrides,
    collect_codex_turn_with_diagnostics,
    friendly_codex_error,
    load_runtime_config,
)


STAGE_GUIDES = {
    "topic": "当前阶段是“拟定主题”。你需要基于 CSV 中的已选文献和已选主题状态，帮助用户形成合适的综述主题；如果文献不足，请给出可复制到文献检索页的完整快速检索要求；如果需要更多矩阵信息，请建议新增文献矩阵字段、判断依据和格式要求。",
    "outline": "当前阶段是“大纲生成”。你需要基于 CSV、已选主题、前序讨论和本地大纲文件，帮助用户生成、比较或修改综述大纲。用户修改的大纲以本地 outline.md 为准。",
    "mapping": "当前阶段是“内容核对”。你必须先读取最新 outline.md，以没有下级子节的叶子小节作为分配依据；再基于 writing_sources.csv、文献矩阵和每篇论文的 paper_dir，为当前叶子小节生成小节-文献映射记录；如果缺少文献，请给出可复制到文献检索页的完整快速检索要求。",
    "draft": "当前阶段是“综述生成”。你需要基于 CSV、outline.md、writing_section_mappings.json 和用户要求，直接查看并编辑本地 survey.md。右侧回复只说明你做了什么、还需要用户确认什么，不要把完整正文复制到聊天气泡。",
}


ACTION_SCHEMA = """
<guangming_actions>{
  "topic_options": [
    {"id": "A", "title": "可选综述主题", "reason": "为什么适合"}
  ],
  "search_prompts": [
    {"label": "补充检索", "request": "完整检索要求，可以直接粘贴到文献检索框", "reason": "为什么需要补充检索"}
  ],
  "matrix_field_suggestions": [
    {"name": "字段名称", "rule": "判断依据和格式要求，例如输出布尔值/分类/字数限制", "reason": "为什么需要这个字段"}
  ],
  "writing_mappings": [
    {"section_id": "section-id", "paper_id": "paper-xxxx", "citation_role": "核心证据/背景定义/方法对比/实验支撑/挑战展望/辅助证据", "writing_note": "这篇文献在当前小节中具体写什么", "evidence_detail": "可写入正文的真实方法、实验、数据或论据细节", "missing_detail": "仍需从 PDF 或资料补查的内容"}
  ]
}</guangming_actions>
""".strip()


def clean_action_items(items: Any, allowed_keys: set[str]) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        row = {key: str(item.get(key) or "").strip() for key in allowed_keys}
        if any(row.values()):
            cleaned.append(row)
    return cleaned


def normalize_actions(data: Any) -> dict[str, list[dict[str, str]]]:
    if not isinstance(data, dict):
        data = {}
    return {
        "topic_options": clean_action_items(data.get("topic_options"), {"id", "title", "reason"}),
        "search_prompts": clean_action_items(data.get("search_prompts"), {"label", "request", "reason"}),
        "matrix_field_suggestions": clean_action_items(data.get("matrix_field_suggestions"), {"name", "rule", "reason"}),
        "writing_mappings": clean_action_items(data.get("writing_mappings"), {"section_id", "paper_id", "citation_role", "writing_note", "evidence_detail", "missing_detail"}),
    }


def extract_actions(text: str) -> tuple[str, dict[str, list[dict[str, str]]]]:
    pattern = re.compile(r"<guangming_actions>\s*(\{.*?\})\s*</guangming_actions>", flags=re.DOTALL)
    match = pattern.search(text or "")
    if not match:
        return (text or "").strip(), normalize_actions({})
    display_text = pattern.sub("", text or "").strip()
    try:
        actions = normalize_actions(json.loads(match.group(1)))
    except json.JSONDecodeError:
        actions = normalize_actions({})
    return display_text, actions


def build_writing_prompt(
    *,
    stage: str,
    user_question: str,
    csv_path: str,
    outline_path: str,
    survey_path: str,
    selected_topic: str,
    include_context: bool,
    outline_changed: bool,
    draft_changed: bool,
) -> str:
    stage_guide = STAGE_GUIDES.get(stage, STAGE_GUIDES["topic"])
    context_rule = (
        "本轮需要重新关注提供的 CSV、outline.md 和 survey.md 路径。"
        if include_context
        else "本轮沿用当前 thread 中已经提供过的项目写作上下文；只有当用户问题需要时再读取本地文件。"
    )
    outline_rule = "用户修改过 outline.md，本轮必须以最新 outline.md 为准。" if outline_changed else "outline.md 未检测到新的用户修改。"
    draft_rule = "用户修改过 survey.md，本轮必须尊重最新 survey.md。" if draft_changed else "survey.md 未检测到新的用户修改。"
    topic_rule = f"当前用户已选择的综述主题是：{selected_topic}" if selected_topic else "当前还没有用户确认的综述主题。"
    return f"""
你是“光明 AI 学术工作台”的综述写作助手，正在同一个 thread 中跨阶段帮助用户完成本地文献综述。
阶段任务：{stage_guide}

重要要求：
1. 你必须围绕当前项目工作，不要修改无关文件。
2. 当前写作 CSV 路径：{csv_path}
3. 当前大纲 Markdown 路径：{outline_path}
4. 当前综述 Markdown 路径：{survey_path}
4a. 当前小节-文献映射 JSON 路径：outputs/writing/writing_section_mappings.json；第四阶段生成正文时必须优先按这个文件中的小节级备注组织引用和论据。
5. {topic_rule}
6. {context_rule}
7. {outline_rule}
8. {draft_rule}
9. 第四阶段如果需要生成正文，必须直接编辑 survey.md；聊天回复只保留沟通摘要，不要输出完整文章。
10. 正文引用第一版使用数字引用，例如 [1]、[2]，并在文末生成“参考文献”列表。
11. 如果信息不足，要先在面向用户的回复中清楚说明缺口是什么、为什么会影响后续写作，再给出后续检索提示词或文献矩阵字段建议。
12. 如果你建议继续检索，必须在正文中解释“为什么要检索”和“希望补到什么类型的证据”，并给出完整、可直接执行的检索要求，不要只给关键词片段。
13. 如果你建议新增文献矩阵字段，必须在正文中解释“为什么需要这些字段”和“这些字段会服务哪个写作判断”，并同时给出字段名、判断依据和格式要求，例如布尔值、分类范围、字数限制或输出格式。
14. 如果你给出主题候选，请使用 A/B/C/D 这样的选项 id，并给出简短理由。
15. 内容核对阶段必须以最新 outline.md 的叶子小节为准：如果 `1` 下面有 `1.1 / 1.2`，只处理 `1.1 / 1.2`，不要再把 `1` 当成独立小节分配；只有没有下级子节的章节才作为分配单元。必须在动作块的 writing_mappings 中返回当前小节的 section_id、paper_id、citation_role、writing_note、evidence_detail、missing_detail，由后端写入 writing_section_mappings.json；不要只在正文里描述。
16. 内容核对阶段正文可以说明分配逻辑，但不要声称“我已经手动编辑了 CSV”。系统会根据 writing_mappings 自动写入；如果无法给出 writing_mappings，必须明确告诉用户“尚未写入”。
17. 跳转检索和跳转文献矩阵不是固定流程。只有当你已经在正文中明确建议用户补充检索或新增矩阵字段时，才在动作块中填入对应数组；如果当前信息已经足够，就保持对应数组为空，不要生成不必要的按钮。
18. 回复末尾必须附加一个可解析动作块；如果没有对应内容，数组留空。动作块格式如下：
{ACTION_SCHEMA}

用户本轮请求：{user_question}
""".strip()


def collect_writing_turn_result_with_progress(stream, *, turn_id: str, progress: Callable[[str], None]):
    recent_agent_text = ""
    recent_reasoning_text = ""
    reasoning_text_seen = False

    def emit(message: str) -> None:
        progress(message)

    def item_root(item):
        return item.root if hasattr(item, "root") else item

    def observe_event(event) -> None:
        nonlocal recent_agent_text, recent_reasoning_text, reasoning_text_seen
        payload = event.payload
        if isinstance(payload, AgentMessageDeltaNotification) and payload.turn_id == turn_id:
            recent_agent_text = (recent_agent_text + payload.delta)[-180:]
            if len(recent_agent_text.strip()) >= 48:
                emit(f"智能体正在组织回复：{recent_agent_text.strip()}")
                recent_agent_text = ""
            return
        if isinstance(payload, ReasoningSummaryTextDeltaNotification) and payload.turn_id == turn_id:
            recent_reasoning_text = (recent_reasoning_text + payload.delta)[-220:]
            if len(recent_reasoning_text.strip()) >= 40:
                emit(f"思考推理：{recent_reasoning_text.strip()}")
                recent_reasoning_text = ""
            return
        if isinstance(payload, ReasoningTextDeltaNotification) and payload.turn_id == turn_id:
            if not reasoning_text_seen:
                emit("智能体正在分析当前阶段任务与本地写作材料。")
                reasoning_text_seen = True
            return
        if isinstance(payload, ItemCompletedNotification) and payload.turn_id == turn_id:
            item = item_root(payload.item)
            if isinstance(item, CommandExecutionThreadItem):
                emit(f"已执行本地辅助命令，状态：{item.status.value}。")

    return collect_codex_turn_with_diagnostics(stream, turn_id=turn_id, on_event=observe_event)


def run_writing_turn(
    *,
    repo_dir: Path,
    project_dir: Path,
    thread_id: str | None,
    stage: str,
    user_question: str,
    csv_path: str,
    outline_path: str,
    survey_path: str,
    selected_topic: str = "",
    include_context: bool,
    outline_changed: bool,
    draft_changed: bool,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    config = load_runtime_config(repo_dir)
    provider = config.get("model_provider") or "custom"
    codex_home = repo_dir / "instance" / "codex-home-web"
    codex_home.mkdir(parents=True, exist_ok=True)

    codex_config = CodexConfig(
        cwd=str(project_dir),
        env={"CODEX_HOME": str(codex_home)},
        config_overrides=build_config_overrides(config, reasoning_effort=config.get("reasoning_effort", "high")),
    )

    def emit(message: str) -> None:
        if progress:
            progress(message)

    with Codex(codex_config) as codex:
        codex.login_api_key(config["api_key"])
        if thread_id:
            emit("正在恢复综述写作对话线程。")
            thread = codex.thread_resume(
                thread_id,
                cwd=str(project_dir),
                sandbox=Sandbox.full_access,
                approval_mode=ApprovalMode.deny_all,
                model=config["model"],
                model_provider=provider,
            )
        else:
            emit("正在创建综述写作对话线程。")
            thread = codex.thread_start(
                cwd=str(project_dir),
                sandbox=Sandbox.full_access,
                approval_mode=ApprovalMode.deny_all,
                model=config["model"],
                model_provider=provider,
                ephemeral=False,
            )

        turn = thread.turn(
            [
                TextInput(
                    build_writing_prompt(
                        stage=stage,
                        user_question=user_question,
                        csv_path=csv_path,
                        outline_path=outline_path,
                        survey_path=survey_path,
                        selected_topic=selected_topic,
                        include_context=include_context,
                        outline_changed=outline_changed,
                        draft_changed=draft_changed,
                    )
                )
            ],
            approval_mode=ApprovalMode.deny_all,
            sandbox=Sandbox.full_access,
            summary=ReasoningSummary(root="concise"),
        )
        emit("智能体已接收任务，开始处理当前写作阶段。")
        stream = turn.stream()
        try:
            result = collect_writing_turn_result_with_progress(stream, turn_id=turn.id, progress=emit)
        finally:
            stream.close()

    if not result.final_response:
        detail = result.diagnostics.get("error") or "Codex turn 已完成，但没有收到任何 assistant 文本。"
        raise RuntimeError(friendly_codex_error(RuntimeError(detail)))
    display_text, actions = extract_actions(result.final_response)
    return {
        "thread_id": thread.id,
        "assistant_message": display_text,
        "actions": actions,
        "turn_id": result.turn_id,
        "turn_status": result.status,
        "usage": result.to_api_payload().get("usage"),
        "diagnostics": result.diagnostics,
    }
