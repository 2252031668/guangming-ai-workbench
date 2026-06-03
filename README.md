# 光明 AI 文献综述工作台

一个面向科研综述写作的本地 Python Web 工作台。它把文献检索、知识库管理、论文研读、文献矩阵和综述写作组织到同一个本地项目目录中，并通过 Codex SDK 驱动 AI 任务，让检索记录、PDF、BibTeX、结构化 JSON 和 Markdown 产物都可追踪、可复用。

## 主要功能

- 文献检索：支持自然语言检索需求、快速检索和深度检索，并把候选论文统一写入项目候选池。
- 知识库：管理已导入论文的元数据、标签、备注、PDF、BibTeX 和阅读矩阵状态。
- 论文研读：在同一页面查看论文信息、PDF、阅读矩阵，并围绕当前论文连续问答。
- 综述写作：围绕本地 CSV、文献矩阵、章节映射和 Markdown 草稿逐步生成综述。
- 多模型设置：在左下角齿轮入口管理模型配置，支持新增、编辑、删除、切换和连通测试。
- 本地路由：内置 Moon Bridge，可把 OpenAI Chat Completions 供应商桥接成 Codex SDK 可用的 Responses 链路。

## 环境准备

推荐使用 Python 3.12 和 `uv`。

```bash
uv sync
```

`openai-codex` 已是必选依赖，不需要再执行 `uv sync --extra codex`。

启动工作台：

```bash
uv run python app.py
```

如果已经在仓库内创建过虚拟环境，也可以在 Windows PowerShell 中运行：

```powershell
.\.venv\Scripts\python.exe app.py
```

然后打开：

```text
http://127.0.0.1:5000
```

如果 PowerShell 5.1 显示中文乱码，可先切换 UTF-8：

```powershell
chcp 65001
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
```

## 模型设置

进入页面后，点击左侧栏左下角的齿轮图标打开“模型设置”。每一条模型配置对应一个具体模型，包含显示名称、备注、API Key、Base URL、模型名和运行模式。

支持两种模式：

- 原生 Responses：适合 OpenAI Responses API 与 Codex 原生支持的 GPT 系列模型。
- 使用本地路由：适合 DeepSeek、Kimi、Qwen 等 OpenAI Chat Completions 风格供应商，通过 Moon Bridge 转成 Responses 链路供 Codex SDK 使用。

桥接模式下会显示“测试代码（路由前）”，它是发往上游 Chat Completions 的真实请求示例。用户可以编辑 `api_key`、`base_url`、`model`、`reasoning_effort` 和 `extra_body`，再点击测试运行验证上游请求结构。

模型设置保存在：

```text
config/model_profiles.json
```

该文件包含 API Key，已加入 `.gitignore`，不要提交到仓库。

## Moon Bridge

仓库直接内置 Windows x64 预编译二进制：

```text
tools/moonbridge.exe
```

Windows 用户不需要安装 Go，也不需要自行编译 Moon Bridge。桥接运行时配置、日志和本地状态写入：

```text
instance/bridge/
```

`instance/` 是本地运行目录，已加入 `.gitignore`。

如果需要本地开发或重新编译 Moon Bridge，可以把源码和构建脚本放在：

```text
vendor/
```

`vendor/` 仅作为本地开发目录，已加入 `.gitignore`，不会上传仓库。

## 目录结构

```text
guangming-ai-workbench/
  app.py
  pyproject.toml
  uv.lock
  config/
    codex.example.json
    codex.local.json          # 旧版本地配置，已忽略
    model_profiles.json       # 新版模型配置，已忽略
  services/
    model_profiles.py
    bridge_manager.py
    chat_code.py
    upstream_chat_tester.py
  templates/
  static/
  tools/
    moonbridge.exe            # 仓库内置 Moon Bridge
  docs/
  skills/
    academic-search-only/
  instance/                   # 本地运行状态、日志、Codex home，已忽略
  workspace/
    projects/                 # 本地项目数据，已忽略
  vendor/                     # 本地 Moon Bridge 开发目录，已忽略
```

## 本地数据与安全

- `config/model_profiles.json` 和 `config/codex.local.json` 会保存私有模型配置，不提交。
- `instance/` 会保存运行日志、桥接配置、Codex home 和本地缓存，不提交。
- `workspace/projects/` 是本地项目数据目录，不提交。
- `tools/moonbridge.exe` 是面向用户交付的预编译路由程序，需要提交。

## 开发检查

提交前建议运行：

```powershell
$files = @('app.py') + (Get-ChildItem -Path services -Filter *.py | ForEach-Object { $_.FullName })
.\.venv\Scripts\python.exe -m py_compile $files
node --check static\js\app.js
node --check static\js\model-settings.js
git diff --check
```
