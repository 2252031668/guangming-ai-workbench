# Guangming AI Workbench

面向科研综述写作的本地 Python Web 工作台。系统围绕一个本地项目目录组织文献检索、知识库管理、论文研读、文献矩阵和综述写作，让 AI 对话、结构化 JSON、PDF、BibTeX 和 Markdown 产物都落到可追踪的本地文件中。

## 功能概览

- 项目管理：创建、切换、删除本地研究项目，并初始化项目文件结构。
- 文献检索：支持快速检索与深度检索，深度检索通过仓库内 `academic-search-only` skill 显式注入 Codex SDK。
- 候选文献池：检索结果先写入 `search_runs/`，再由后端归一化、去重并合并到 `candidate_papers.json`。
- 知识库：支持候选文献导入、手动导入文献、标签、备注、PDF 下载/上传、PDF 查找、BibTeX 补全与导出。
- 论文研读：支持本地 PDF 阅读、连续对话、截图提问、粘贴图片提问和文献矩阵展示。
- 文献矩阵：支持项目级字段配置、AI 推荐字段、逐篇增量生成 `papers/<paper_id>/reading.json`。
- 综述写作：四阶段工作台，覆盖拟定主题、大纲生成、内容核对和综述生成；写作对话跨阶段保留记忆。

## 实现原理

- Flask 提供 Web 页面、JSON API 和后台任务入口。
- 项目数据以 `workspace/projects/<project_id>/` 为边界，主要用 JSON 表保存状态。
- 单篇论文资产保存在 `papers/<paper_id>/`，包括 `metadata.json`、`paper.pdf`、`bibtex.bib`、`reading.json` 等。
- 搜索、知识库问答、论文研读、文献矩阵、导入补全和综述写作均通过 Codex SDK 后台任务执行。
- Codex SDK 的运行缓存保存在 `instance/codex-home-web/`，该目录只保存本机运行状态、会话和缓存，不进入版本库。
- 仓库内 skill 作为源码保存在 `skills/academic-search-only/`，深度检索通过 `SkillInput` 显式传入 `SKILL.md` 的绝对路径，不依赖全局用户目录。

## 目录结构

```text
guangming-ai-workbench/
  app.py
  pyproject.toml
  uv.lock
  config/
    codex.example.json
    codex.local.json       # 本地私有配置，已 gitignore
  services/
  templates/
  static/
  docs/
  skills/
    academic-search-only/
  instance/                # Codex SDK 本地运行缓存，已 gitignore
  workspace/
    projects/              # 本地项目数据，已 gitignore
```

每个研究项目都会在 `workspace/projects/<时间戳-项目名>/` 下生成独立目录。完整项目文件结构见 [文件结构设计、表格字段与系统实现](docs/文件结构设计_表格字段_系统实现.md)。

## 环境配置

推荐使用 Python 3.12 和 `uv`。

```bash
uv sync
```

如需运行 Codex SDK 任务流：

```bash
uv sync --extra codex
```

复制配置模板并填写自己的模型服务配置：

```powershell
Copy-Item config/codex.example.json config/codex.local.json
```

`config/codex.local.json` 示例字段：

```json
{
  "api_key": "sk-your-api-key",
  "base_url": "https://api.openai.com/",
  "model": "gpt-5.4",
  "model_provider": "project-openai",
  "wire_api": "responses",
  "reasoning_effort": "high",
  "disable_response_storage": true
}
```

`codex.local.json` 已加入 `.gitignore`，不要提交真实密钥。

Windows PowerShell 5.1 如遇中文乱码，可在当前终端先切换 UTF-8：

```powershell
chcp 65001
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
```

## 运行方式

```bash
uv run python app.py
```

或在已创建的虚拟环境中运行：

```powershell
.\.venv\Scripts\python.exe app.py
```

打开：

```text
http://127.0.0.1:5000
```

## 文档入口

- [产品需求与验收标准](docs/需求文档_产品需求与验收标准.md)
- [页面原型说明与组件清单](docs/页面原型说明与组件清单.md)
- [文件结构设计、表格字段与系统实现](docs/文件结构设计_表格字段_系统实现.md)

## 注意事项

- `instance/` 是本机 Codex SDK 运行缓存，保留在仓库根目录但不提交。
- `workspace/projects/` 是本地项目数据目录，不提交到 git。
- `skills/academic-search-only/` 是深度检索 skill 的源码目录，需要随仓库同步。
- `config/codex.local.json` 包含私有 API 配置，不提交到 git。
