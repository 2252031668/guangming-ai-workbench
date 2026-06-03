from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from openai_codex import ApprovalMode, Codex, CodexConfig, LocalImageInput, Sandbox, TextInput
from openai_codex.generated.v2_all import ReasoningSummary

from services.codex_runner import build_config_overrides, friendly_codex_error, load_runtime_config, run_thread_turn_with_diagnostics


def format_single_paper_context(project_dir: Path, paper: dict[str, Any]) -> str:
    pdf_path = str(paper.get("pdf_path") or "").strip()
    pdf_exists = bool(pdf_path and (project_dir / pdf_path).exists())
    return "\n".join(
        [
            f"paper_id: {paper.get('paper_id', '')}",
            f"标题: {paper.get('title', '')}",
            f"作者: {' / '.join(paper.get('authors') or [])}",
            f"年份: {paper.get('year', '')}",
            f"来源: {paper.get('venue', '')}",
            f"DOI: {paper.get('doi') or '无'}",
            f"paper_url: {paper.get('paper_url') or '无'}",
            f"pdf_url: {paper.get('pdf_url') or '无'}",
            f"本地 PDF 路径: {pdf_path or '无'}",
            f"本地 PDF 是否存在: {'是' if pdf_exists else '否'}",
            f"标签: {' / '.join(paper.get('tags') or []) or '无'}",
            f"备注: {paper.get('notes') or '无'}",
            f"中文摘要: {paper.get('abstract_zh') or '无'}",
            f"英文摘要: {paper.get('abstract') or '无'}",
        ]
    )


def build_reading_chat_prompt(
    *,
    project_dir: Path,
    paper: dict[str, Any],
    user_question: str,
    include_paper_context: bool,
    image_count: int = 0,
) -> str:
    paper_context = (
        format_single_paper_context(project_dir, paper)
        if include_paper_context
        else "本轮继续沿用当前 thread 中已经提供过的同一篇论文上下文和前文对话记忆。"
    )
    context_rule = (
        "本轮已经提供当前论文上下文。"
        if include_paper_context
        else "本轮不要假装重新读取了新的论文；如果需要引用论文内容，请基于当前 thread 已有上下文。"
    )
    image_rule = (
        f"用户本轮附加了 {image_count} 张当前论文阅读过程中的图片，请你了解图片内容，并结合用户问题回答。"
        if image_count
        else "用户本轮没有附加图片。"
    )
    return f"""
你是“光明 AI 学术工作台”的单篇论文研读助手，正在帮助用户阅读当前论文。

重要要求：
1. 你正在同一个 thread 中持续对话，需要结合前文记忆回答。
2. 本轮只围绕当前这篇论文回答，不要扩展到项目中其他论文，除非用户明确要求比较。
3. 如果本地 PDF 路径存在，你可以读取该 PDF 辅助回答，并优先依据 PDF 内容。
4. 如果本地 PDF 不存在，则基于给出的元数据、paper_url 和 pdf_url 回答。
5. 如果证据不足，要明确说明，不要编造论文中没有的结论。
6. 回答使用中文。
7. {context_rule}
8. {image_rule}

当前论文：
{paper_context}

用户问题：
{user_question}
""".strip()


def run_reading_chat_turn(
    *,
    repo_dir: Path,
    project_dir: Path,
    thread_id: str | None,
    paper: dict[str, Any],
    user_question: str,
    include_paper_context: bool = True,
    image_paths: list[str] | None = None,
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
            emit("正在恢复单篇研读对话线程。")
            thread = codex.thread_resume(
                thread_id,
                cwd=str(project_dir),
                sandbox=Sandbox.full_access,
                approval_mode=ApprovalMode.deny_all,
                model=config["model"],
                model_provider=provider,
            )
        else:
            emit("正在创建单篇研读对话线程。")
            thread = codex.thread_start(
                cwd=str(project_dir),
                sandbox=Sandbox.full_access,
                approval_mode=ApprovalMode.deny_all,
                model=config["model"],
                model_provider=provider,
                ephemeral=False,
            )

        local_image_paths = [str(path) for path in (image_paths or []) if str(path).strip()]
        turn_input = [
            TextInput(
                build_reading_chat_prompt(
                    project_dir=project_dir,
                    paper=paper,
                    user_question=user_question,
                    include_paper_context=include_paper_context,
                    image_count=len(local_image_paths),
                )
            ),
            *[LocalImageInput(path) for path in local_image_paths],
        ]
        result = run_thread_turn_with_diagnostics(
            thread,
            turn_input,
            summary=ReasoningSummary(root="concise"),
        )

    if not result.final_response:
        detail = result.diagnostics.get("error") or "Codex turn 已完成，但没有收到任何 assistant 文本。"
        raise RuntimeError(friendly_codex_error(RuntimeError(detail)))
    return {
        "thread_id": thread.id,
        "assistant_message": result.final_response,
        "turn_id": result.turn_id,
        "turn_status": result.status,
        "usage": result.to_api_payload().get("usage"),
        "diagnostics": result.diagnostics,
    }
