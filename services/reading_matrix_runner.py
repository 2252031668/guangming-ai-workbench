from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, TextInput
from openai_codex.generated.v2_all import ReasoningSummary

from services.codex_runner import build_config_overrides, read_json


def build_matrix_prompt(
    *,
    paper: dict[str, Any],
    fields: list[dict[str, Any]],
    pdf_path: str,
) -> str:
    field_lines = "\n".join(
        f'- {field["field_id"]}: {field["name"]}。判断依据和格式要求：{field["rule"]}'
        for field in fields
        if field.get("enabled", True)
    )
    authors = " / ".join(paper.get("authors") or [])
    return f"""
你正在为“光明 AI 学术工作台”生成单篇论文的文献矩阵。本轮只处理下面列出的目标字段，不要生成未列出的字段。

论文信息：
- paper_id: {paper.get("paper_id", "")}
- 标题: {paper.get("title", "")}
- 作者: {authors}
- 年份: {paper.get("year", "")}
- 来源: {paper.get("venue", "")}
- DOI: {paper.get("doi") or "无"}
- 本地 PDF 路径: {pdf_path}
- 中文摘要: {paper.get("abstract_zh") or "无"}
- 英文摘要: {paper.get("abstract") or "无"}

本轮目标矩阵字段：
{field_lines}

任务要求：
1. 必须优先读取本地 PDF 内容，再结合论文元数据和摘要补充判断。
2. 按每个矩阵字段的判断依据和格式要求，生成适合文献综述整理的中文结果。
3. 如果 PDF 中找不到足够证据，不要编造，写明“未在当前 PDF 中找到明确证据”并给出可确认的有限信息。
4. 只输出一个 JSON 对象，不要 Markdown，不要代码块，不要解释。
5. JSON 结构必须为：
{{
  "fields": {{
    "field_id": {{
      "value": "中文结果"
    }}
  }}
}}
""".strip()


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("文献矩阵结果不是 JSON 对象")
    return data


def run_reading_matrix_for_paper(
    *,
    repo_dir: Path,
    project_dir: Path,
    paper: dict[str, Any],
    fields: list[dict[str, Any]],
    pdf_path: str,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    config = read_json(repo_dir / "config" / "codex.local.json")
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
        emit("正在启动文献矩阵智能体。")
        codex.login_api_key(config["api_key"])
        thread = codex.thread_start(
            cwd=str(project_dir),
            sandbox=Sandbox.full_access,
            approval_mode=ApprovalMode.deny_all,
            model=config["model"],
            model_provider=provider,
            ephemeral=True,
        )
        result = thread.run(
            [
                TextInput(
                    build_matrix_prompt(
                        paper=paper,
                        fields=fields,
                        pdf_path=pdf_path,
                    )
                )
            ],
            approval_mode=ApprovalMode.deny_all,
            sandbox=Sandbox.full_access,
            summary=ReasoningSummary(root="concise"),
        )

    emit("智能体已返回文献矩阵结果，正在校验 JSON。")
    return parse_json_object(result.final_response or "")
