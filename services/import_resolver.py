from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, TextInput
from openai_codex.generated.v2_all import ReasoningSummary

from services.codex_runner import build_config_overrides, friendly_codex_error, load_runtime_config, run_thread_turn_with_diagnostics


def parse_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
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
        raise ValueError("导入补全结果不是 JSON 对象")
    return data


def import_schema() -> str:
    return """
{
  "status": "success",
  "title": "论文完整标题，必填",
  "authors": ["作者1", "作者2"],
  "year": 2024,
  "venue": "会议或期刊，找不到填空字符串",
  "doi": "10.xxxx，找不到填空字符串",
  "paper_url": "论文详情页或官方页面，找不到填空字符串",
  "abstract": "英文摘要，找不到填空字符串",
  "abstract_zh": "中文摘要，一到三句，找不到填空字符串",
  "keywords": ["关键词1", "关键词2", "关键词3", "关键词4"],
  "pdf_url": "合法开放 PDF 链接，找不到填空字符串",
  "error": ""
}
""".strip()


def build_import_prompt(draft: dict[str, Any], pdf_path: str = "") -> str:
    input_type = draft.get("input_type") or "text"
    raw_input = draft.get("raw_input") or ""
    pdf_hint = f"\n本地 PDF 路径：{pdf_path}" if pdf_path else ""
    source_instruction = (
        "用户提供的是 DOI 或论文完整题名，请根据该输入补全论文元数据。"
        if input_type == "text"
        else "用户上传了 PDF，请优先读取本地 PDF 内容，从 PDF 中提取论文元数据。"
    )
    return f"""
你正在为“光明 AI 学术工作台”的“导入文献”功能补全文献信息。

输入类型：{input_type}
用户输入：{raw_input}{pdf_hint}

任务要求：
1. {source_instruction}
2. 只补全学术论文元数据，不要写入任何本地文件。
3. 如果是 PDF，但 PDF 是扫描版、损坏、加密或无法抽取文本，请返回 status="failed" 并在 error 中说明原因。
4. title 是必填字段；如果无法可靠确定 title，则返回 status="failed"。
5. authors 返回字符串数组；keywords 最多 4 个。
6. pdf_url 只能填写合法开放 PDF 链接；找不到就填空字符串，不要使用 Sci-Hub 或任何绕过付费墙的来源。
7. 只输出一个 JSON 对象，不要 Markdown，不要代码块，不要解释。

JSON 格式必须严格符合：
{import_schema()}
""".strip()


def run_import_resolution(
    *,
    repo_dir: Path,
    project_dir: Path,
    draft: dict[str, Any],
    pdf_path: str = "",
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
        emit("正在启动导入补全智能体。")
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
            [TextInput(build_import_prompt(draft, pdf_path=pdf_path))],
            summary=ReasoningSummary(root="concise"),
        )

    if not result.final_response:
        detail = result.diagnostics.get("error") or "Codex turn 已完成，但没有收到任何 assistant 文本。"
        raise RuntimeError(friendly_codex_error(RuntimeError(detail)))
    emit("导入补全智能体已返回结果，正在校验 JSON。")
    return parse_json_object(result.final_response)
