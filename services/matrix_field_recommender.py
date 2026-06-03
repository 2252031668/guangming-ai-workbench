from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, TextInput
from openai_codex.generated.v2_all import ReasoningSummary

from services.codex_runner import build_config_overrides, friendly_codex_error, load_runtime_config, run_thread_turn_with_diagnostics


def paper_context_line(paper: dict[str, Any]) -> str:
    authors = " / ".join(paper.get("authors") or [])
    keywords = " / ".join(paper.get("keywords") or [])
    return (
        f"- {paper.get('title', '')} ({paper.get('year', '')}, {paper.get('venue', '')})\n"
        f"  作者：{authors or '未知'}\n"
        f"  关键词：{keywords or '无'}\n"
        f"  中文摘要：{paper.get('abstract_zh') or paper.get('abstract') or '无'}"
    )


def parse_json_array(text: str) -> list[dict[str, str]]:
    source = (text or "").strip()
    if source.startswith("```"):
        source = re.sub(r"^```(?:json)?", "", source, flags=re.IGNORECASE).strip()
        source = re.sub(r"```$", "", source).strip()
    try:
        data = json.loads(source)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", source, flags=re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, list):
        raise ValueError("AI 推荐字段结果不是 JSON 数组")
    fields: list[dict[str, str]] = []
    for item in data[:8]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        rule = str(item.get("rule") or "").strip()
        if name and rule:
            fields.append({"name": name, "rule": rule})
    return fields


def recommend_matrix_fields(
    *,
    repo_dir: Path,
    project_dir: Path,
    papers: list[dict[str, Any]],
    existing_fields: list[dict[str, Any]],
) -> list[dict[str, str]]:
    config = load_runtime_config(repo_dir)
    provider = config.get("model_provider") or "custom"
    codex_home = repo_dir / "instance" / "codex-home-web"
    codex_home.mkdir(parents=True, exist_ok=True)

    existing = " / ".join(field.get("name", "") for field in existing_fields if field.get("name")) or "无"
    paper_lines = "\n\n".join(paper_context_line(paper) for paper in papers[:24]) or "当前没有可用论文。"
    prompt = f"""
你是“光明 AI 学术工作台”的文献矩阵字段设计助手。
请基于当前论文集合，为后续综述写作推荐 3 到 6 个有价值的文献矩阵字段。

已有字段：{existing}

论文信息：
{paper_lines}

要求：
1. 不要重复已有字段或语义高度相同的字段。
2. 字段要适合综述写作中的方法比较、内容核对和章节组织。
3. 每个字段必须包含 name 和 rule。
4. rule 必须写成“判断依据和格式要求”，例如输出布尔值、分类范围、字数限制、证据要求。
5. 只输出 JSON 数组，不要 Markdown，不要解释。

输出格式：
[
  {{
    "name": "任务类型",
    "rule": "判断论文处理的任务类型；输出 1-3 个短语，例如任务规划、双臂操作、装配执行。"
  }}
]
""".strip()

    codex_config = CodexConfig(
        cwd=str(project_dir),
        env={"CODEX_HOME": str(codex_home)},
        config_overrides=build_config_overrides(config, reasoning_effort="medium"),
    )
    with Codex(codex_config) as codex:
        codex.login_api_key(config["api_key"])
        thread = codex.thread_start(
            cwd=str(project_dir),
            sandbox=Sandbox.full_access,
            approval_mode=ApprovalMode.deny_all,
            model=config["model"],
            model_provider=provider,
            ephemeral=True,
        )
        result = run_thread_turn_with_diagnostics(
            thread,
            [TextInput(prompt)],
            summary=ReasoningSummary(root="concise"),
        )
    if not result.final_response:
        detail = result.diagnostics.get("error") or "Codex turn 已完成，但没有收到任何 assistant 文本。"
        raise RuntimeError(friendly_codex_error(RuntimeError(detail)))
    return parse_json_array(result.final_response)
