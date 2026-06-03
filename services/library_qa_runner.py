from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, TextInput
from openai_codex.generated.v2_all import ReasoningSummary

from services.codex_runner import build_config_overrides, friendly_codex_error, load_runtime_config, run_thread_turn_with_diagnostics


def format_paper_context(project_dir: Path, papers: list[dict[str, Any]]) -> str:
    if not papers:
        return "本轮没有勾选文献。"

    blocks: list[str] = []
    for index, paper in enumerate(papers, start=1):
        pdf_path = str(paper.get("pdf_path") or "").strip()
        pdf_exists = bool(pdf_path and (project_dir / pdf_path).exists())
        blocks.append(
            "\n".join(
                [
                    f"[{index}] paper_id: {paper.get('paper_id', '')}",
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
        )
    return "\n\n".join(blocks)


def build_library_qa_prompt(
    project_dir: Path,
    papers: list[dict[str, Any]],
    user_question: str,
    *,
    include_paper_context: bool,
) -> str:
    paper_context = (
        format_paper_context(project_dir, papers)
        if include_paper_context
        else "本轮勾选文献与上次已注入上下文一致，请沿用当前  thread 中已有的文献上下文和对话记忆。"
    )
    context_rule = (
        "本轮已经重新提供勾选文献上下文。"
        if include_paper_context
        else "本轮不要假装重新读取了新的文献上下文；如需引用文献，请基于当前线程中已经提供过的同一组文献上下文。"
    )
    return f"""
你是“光明 AI 学术工作台”的知识库问答助手，正在和用户围绕当前项目文献进行多轮对话。

重要要求：
1. 你正在同一个 thread 中持续对话，需要结合前文记忆回答。
2. 本轮回答必须优先基于用户当前勾选的文献，不要把未勾选文献当成本轮依据。
3. 如果文献有本地 PDF 路径且文件存在，你可以读取该 PDF 辅助回答。
4. 如果本地 PDF 不存在，则基于给出的元数据、摘要、备注、paper_url 和 pdf_url 回答。
5. 如果证据不足，要明确说明。
6. 回答使用中文，结构清晰，尽量给出可用于综述整理的结论。
7. {context_rule}

本轮勾选文献：
{paper_context}

用户问题：
{user_question}
""".strip()


def run_library_qa_turn(
    *,
    repo_dir: Path,
    project_dir: Path,
    thread_id: str | None,
    user_question: str,
    selected_papers: list[dict[str, Any]],
    include_paper_context: bool = True,
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
            emit("正在恢复知识库问答线程。")
            thread = codex.thread_resume(
                thread_id,
                cwd=str(project_dir),
                sandbox=Sandbox.full_access,
                approval_mode=ApprovalMode.deny_all,
                model=config["model"],
                model_provider=provider,
            )
        else:
            emit("正在创建知识库问答线程。")
            thread = codex.thread_start(
                cwd=str(project_dir),
                sandbox=Sandbox.full_access,
                approval_mode=ApprovalMode.deny_all,
                model=config["model"],
                model_provider=provider,
                ephemeral=False,
            )

        result = run_thread_turn_with_diagnostics(
            thread,
            [
                TextInput(
                    build_library_qa_prompt(
                        project_dir,
                        selected_papers,
                        user_question,
                        include_paper_context=include_paper_context,
                    )
                )
            ],
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
