from __future__ import annotations

import json
import re
import shutil
import threading
import urllib.request
import csv
import hashlib
from urllib.parse import urlparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for

from services.codex_runner import (
    CodexConfigError,
    SEARCH_MODE_DEEP,
    SEARCH_MODE_QUICK,
    normalize_search_mode,
    run_literature_search,
    search_mode_label,
)
from services.import_resolver import run_import_resolution
from services.library_qa_runner import run_library_qa_turn
from services.matrix_field_recommender import recommend_matrix_fields
from services.pdf_resolver import OpenPdfNotFoundError, resolve_open_pdf_url
from services.reading_chat_runner import run_reading_chat_turn
from services.reading_matrix_runner import run_reading_matrix_for_paper
from services.search_normalizer import candidate_keys, merge_search_run_into_candidates
from services.writing_runner import run_writing_turn


BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR / "workspace"
PROJECTS_DIR = WORKSPACE_DIR / "projects"

app = Flask(__name__)
app.secret_key = "guangming-local-workbench-dev"
SEARCH_TASK_LOCK = threading.Lock()
SEARCH_TASKS: dict[str, dict[str, Any]] = {}
SEARCH_TASK_TIMEOUT_MINUTES = 20
LIBRARY_QA_LOCK = threading.Lock()
LIBRARY_QA_TASKS: dict[str, dict[str, Any]] = {}
READING_MATRIX_LOCK = threading.Lock()
READING_MATRIX_TASKS: dict[str, dict[str, Any]] = {}
BIBTEX_LOCK = threading.Lock()
BIBTEX_TASKS: dict[str, dict[str, Any]] = {}
PDF_LOOKUP_LOCK = threading.Lock()
PDF_LOOKUP_TASKS: dict[str, dict[str, Any]] = {}
PDF_DOWNLOAD_LOCK = threading.Lock()
PDF_DOWNLOAD_TASKS: dict[str, dict[str, Any]] = {}
IMPORT_LOCK = threading.Lock()
IMPORT_TASKS: dict[str, dict[str, Any]] = {}
READING_CHAT_LOCK = threading.Lock()
READING_CHAT_TASKS: dict[str, dict[str, Any]] = {}
WRITING_LOCK = threading.Lock()
WRITING_TASKS: dict[str, dict[str, Any]] = {}

PROJECT_DIRECTORIES = [
    "papers",
    "search_runs",
    "outputs",
    "outputs/writing",
    "exports",
    "imports",
]
PROJECT_JSON_TABLES: dict[str, Any] = {
    "candidate_papers.json": [],
    "library_papers.json": [],
    "project_tags.json": [],
    "reading_notes.json": [],
    "search_chat.json": [],
    "search_tasks.json": [],
    "library_chat.json": [],
    "library_chat_state.json": {},
    "library_chat_tasks.json": [],
    "reading_matrix_fields.json": [],
    "reading_matrix_tasks.json": [],
    "reading_chat_tasks.json": [],
    "writing_state.json": {},
    "writing_chat.json": [],
    "writing_chat_state.json": {},
    "writing_chat_tasks.json": [],
    "bibtex_tasks.json": [],
    "pdf_lookup_tasks.json": [],
    "pdf_download_tasks.json": [],
    "import_drafts.json": [],
    "import_tasks.json": [],
}
BUILTIN_TAGS = ["重点", "方法类", "综述"]
DEFAULT_READING_MATRIX_FIELDS = [
    {
        "field_id": "research_question",
        "name": "研究问题",
        "rule": "判断论文要解决的核心科学问题或工程问题。",
        "order": 1,
        "enabled": True,
    },
    {
        "field_id": "method_idea",
        "name": "方法思路",
        "rule": "提炼论文的核心方法、模型结构、算法流程或系统设计思路。",
        "order": 2,
        "enabled": True,
    },
    {
        "field_id": "experiment_setup",
        "name": "实验设置",
        "rule": "概括论文使用的数据集、任务场景、实验指标、基线方法和主要实验配置。",
        "order": 3,
        "enabled": True,
    },
    {
        "field_id": "core_conclusion",
        "name": "核心结论",
        "rule": "总结论文得到的关键发现、实验结论、有效性证明和对综述写作有价值的结论。",
        "order": 4,
        "enabled": True,
    },
]
WRITING_STAGES = ["topic", "outline", "mapping", "draft"]
WRITING_STAGE_LABELS = {
    "topic": "拟定主题",
    "outline": "大纲生成",
    "mapping": "内容核对",
    "draft": "综述生成",
}
TAG_PALETTE = [
    ("#fff0e8", "#b95a28"),
    ("#eaf3ff", "#315fbe"),
    ("#eaf8ef", "#2f8b57"),
    ("#f2efff", "#6654c6"),
    ("#fff7db", "#9b6a10"),
    ("#e8f7f5", "#167a72"),
    ("#ffeaf1", "#b34368"),
    ("#edf0ff", "#4d5bbd"),
    ("#edf7df", "#5b7f1f"),
    ("#fff1d6", "#a45f16"),
    ("#e6f0f8", "#2d6c8f"),
    ("#f7edf4", "#96507c"),
]


def search_mode_title(value: str | None) -> str:
    return search_mode_label(normalize_search_mode(value))


def asset_url(path: str) -> str:
    file_path = BASE_DIR / "static" / path
    version = int(file_path.stat().st_mtime) if file_path.exists() else 0
    return url_for("static", filename=path, v=version)


SEARCH_CATALOG = [
    {
        "id": "paper-agent-science-2025",
        "title": "AI Agent for Scientific Discovery and Experiment Planning",
        "authors": "Zhang et al.",
        "year": "2025",
        "source": "arXiv",
        "abstract": "面向科研探索场景的 AI Agent 框架，强调任务分解、工具调用与实验规划能力。",
        "keywords": ["agent", "science", "planning"],
        "pdf_url": "",
    },
    {
        "id": "paper-nature-science-2024",
        "title": "AI for Science: Foundation Models in Scientific Research",
        "authors": "Wang et al.",
        "year": "2024",
        "source": "Nature",
        "abstract": "综述 AI for Science 的代表性方向，关注基础模型在材料、生命科学与实验设计中的作用。",
        "keywords": ["materials", "science", "discovery"],
        "pdf_url": "",
    },
    {
        "id": "paper-survey-workflow-2023",
        "title": "Human-AI Collaborative Workflow for Literature Review Writing",
        "authors": "Li et al.",
        "year": "2023",
        "source": "ACM",
        "abstract": "讨论人机协同完成文献检索、筛选、阅读与综述写作的工作流设计。",
        "keywords": ["survey", "workflow", "prompt"],
        "pdf_url": "",
    },
    {
        "id": "paper-local-knowledgebase-2024",
        "title": "Local Research Knowledge Base for Personal Academic Workbench",
        "authors": "Chen et al.",
        "year": "2024",
        "source": "IEEE",
        "abstract": "聚焦本地知识库在科研工作台中的组织方式，强调可追溯、可编辑与可复用。",
        "keywords": ["local", "knowledge-base", "research"],
        "pdf_url": "",
    },
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_tags(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    cleaned: list[str] = []
    for tag in tags:
        value = str(tag).strip()
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def tag_colors(tag: str, project_tags: list[str] | None = None) -> tuple[str, str]:
    if tag in BUILTIN_TAGS:
        return TAG_PALETTE[BUILTIN_TAGS.index(tag)]
    custom_tags = clean_tags(project_tags or [])
    if tag in custom_tags:
        return TAG_PALETTE[(len(BUILTIN_TAGS) + custom_tags.index(tag)) % len(TAG_PALETTE)]
    index = sum((position + 1) * ord(char) for position, char in enumerate(tag)) % len(TAG_PALETTE)
    return TAG_PALETTE[index]


def tag_style(tag: str, project_tags: list[str] | None = None) -> str:
    background, color = tag_colors(tag, project_tags)
    return f"--tag-bg:{background};--tag-color:{color};"


def default_reading_matrix_fields() -> list[dict[str, Any]]:
    now = now_iso()
    return [{**field, "created_at": now, "updated_at": now} for field in DEFAULT_READING_MATRIX_FIELDS]


def field_rule_hash(rule: str) -> str:
    return hashlib.sha256(str(rule or "").strip().encode("utf-8")).hexdigest()


def ensure_workspace() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def safe_project_slug(name: str) -> str:
    slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name.strip())
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-. ")
    return slug[:48] or "untitled-project"


def make_project_id(name: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = safe_project_slug(name)
    project_id = f"{timestamp}-{slug}"
    if project_dir(project_id).exists():
        project_id = f"{project_id}-{uuid4().hex[:6]}"
    return project_id


def project_dir(project_id: str) -> Path:
    ensure_workspace()
    projects_root = PROJECTS_DIR.resolve()
    root = (PROJECTS_DIR / project_id).resolve()
    if root == projects_root or not root.is_relative_to(projects_root):
        raise ValueError(f"invalid project path: {project_id}")
    return root


def project_meta_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def project_papers_path(project_id: str) -> Path:
    return project_dir(project_id) / "library_papers.json"


def project_tags_path(project_id: str) -> Path:
    return project_dir(project_id) / "project_tags.json"


def candidate_papers_path(project_id: str) -> Path:
    return project_dir(project_id) / "candidate_papers.json"


def search_runs_dir(project_id: str) -> Path:
    return project_dir(project_id) / "search_runs"


def search_chat_path(project_id: str) -> Path:
    return project_dir(project_id) / "search_chat.json"


def search_tasks_path(project_id: str) -> Path:
    return project_dir(project_id) / "search_tasks.json"


def library_chat_path(project_id: str) -> Path:
    return project_dir(project_id) / "library_chat.json"


def library_chat_state_path(project_id: str) -> Path:
    return project_dir(project_id) / "library_chat_state.json"


def library_chat_tasks_path(project_id: str) -> Path:
    return project_dir(project_id) / "library_chat_tasks.json"


def reading_matrix_fields_path(project_id: str) -> Path:
    return project_dir(project_id) / "reading_matrix_fields.json"


def reading_matrix_tasks_path(project_id: str) -> Path:
    return project_dir(project_id) / "reading_matrix_tasks.json"


def reading_chat_tasks_path(project_id: str) -> Path:
    return project_dir(project_id) / "reading_chat_tasks.json"


def bibtex_tasks_path(project_id: str) -> Path:
    return project_dir(project_id) / "bibtex_tasks.json"


def pdf_lookup_tasks_path(project_id: str) -> Path:
    return project_dir(project_id) / "pdf_lookup_tasks.json"


def pdf_download_tasks_path(project_id: str) -> Path:
    return project_dir(project_id) / "pdf_download_tasks.json"


def import_drafts_path(project_id: str) -> Path:
    return project_dir(project_id) / "import_drafts.json"


def import_tasks_path(project_id: str) -> Path:
    return project_dir(project_id) / "import_tasks.json"


def import_run_dir(project_id: str, run_id: str) -> Path:
    root = (project_dir(project_id) / "imports" / safe_project_slug(run_id)).resolve()
    imports_root = (project_dir(project_id) / "imports").resolve()
    if root == imports_root or not root.is_relative_to(imports_root):
        raise ValueError(f"invalid import run path: {run_id}")
    return root


def reading_notes_path(project_id: str) -> Path:
    return project_dir(project_id) / "reading_notes.json"


def writing_state_path(project_id: str) -> Path:
    return project_dir(project_id) / "writing_state.json"


def writing_chat_path(project_id: str) -> Path:
    return project_dir(project_id) / "writing_chat.json"


def writing_chat_state_path(project_id: str) -> Path:
    return project_dir(project_id) / "writing_chat_state.json"


def writing_chat_tasks_path(project_id: str) -> Path:
    return project_dir(project_id) / "writing_chat_tasks.json"


def writing_sources_relative_path() -> str:
    return "outputs/writing/writing_sources.csv"


def writing_section_mappings_relative_path() -> str:
    return "outputs/writing/writing_section_mappings.json"


def writing_outline_relative_path() -> str:
    return "outputs/writing/outline.md"


def writing_survey_relative_path() -> str:
    return "outputs/writing/survey.md"


def paper_asset_dir(project_id: str, paper_id: str) -> Path:
    root = (project_dir(project_id) / "papers" / safe_project_slug(paper_id)).resolve()
    papers_root = (project_dir(project_id) / "papers").resolve()
    if root == papers_root or not root.is_relative_to(papers_root):
        raise ValueError(f"invalid paper path: {paper_id}")
    return root


def paper_pdf_relative_path(paper_id: str) -> str:
    return f"papers/{safe_project_slug(paper_id)}/paper.pdf"


def paper_reading_relative_path(paper_id: str) -> str:
    return f"papers/{safe_project_slug(paper_id)}/reading.json"


def paper_reading_chat_relative_path(paper_id: str) -> str:
    return f"papers/{safe_project_slug(paper_id)}/reading_chat.json"


def paper_reading_chat_state_relative_path(paper_id: str) -> str:
    return f"papers/{safe_project_slug(paper_id)}/reading_chat_state.json"


def paper_reading_assets_relative_dir(paper_id: str) -> str:
    return f"papers/{safe_project_slug(paper_id)}/reading_assets"


def paper_bibtex_relative_path(paper_id: str) -> str:
    return f"papers/{safe_project_slug(paper_id)}/bibtex.bib"


def resolve_project_file(project_id: str, relative_path: str) -> Path:
    root = project_dir(project_id).resolve()
    path = Path(str(relative_path))
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"invalid project file path: {relative_path}")
    return path


def initialize_project_files(project_id: str, project: dict[str, Any]) -> None:
    root = project_dir(project_id)
    for child in PROJECT_DIRECTORIES:
        (root / child).mkdir(parents=True, exist_ok=True)
    write_json(project_meta_path(project_id), project)
    for filename, default in PROJECT_JSON_TABLES.items():
        write_json(root / filename, default)


def ensure_project_structure(project_id: str) -> None:
    root = project_dir(project_id)
    if not root.exists():
        return
    for child in PROJECT_DIRECTORIES:
        (root / child).mkdir(parents=True, exist_ok=True)
    for filename, default in PROJECT_JSON_TABLES.items():
        path = root / filename
        if not path.exists():
            write_json(path, default)


def update_project_timestamp(project_id: str) -> None:
    meta_path = project_meta_path(project_id)
    if not meta_path.exists():
        return
    project = read_json(meta_path, {})
    project["updated_at"] = now_iso()
    project.pop("stats", None)
    write_json(meta_path, project)


def create_project_workspace(name: str, topic: str) -> dict[str, Any]:
    ensure_workspace()
    project_id = make_project_id(name)
    project = {
        "id": project_id,
        "name": name,
        "project_name": name,
        "project_slug": safe_project_slug(name),
        "topic": topic,
        "description": topic,
        "active_task": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    initialize_project_files(project_id, project)
    return project


def list_projects() -> list[dict[str, Any]]:
    ensure_workspace()
    items: list[dict[str, Any]] = []
    for meta_file in PROJECTS_DIR.glob("*/project.json"):
        project = read_json(meta_file, {})
        project_id = project.get("id") or meta_file.parent.name
        project["id"] = project_id
        ensure_project_structure(project_id)
        papers = load_project_papers(project_id)
        items.append({**project, "stats": compute_stats(papers)})
    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return items


def load_project(project_id: str | None) -> dict[str, Any] | None:
    if not project_id:
        return None
    meta = project_meta_path(project_id)
    if not meta.exists():
        return None
    ensure_project_structure(project_id)
    project = read_json(meta, {})
    project["id"] = project.get("id") or project_id
    project["stats"] = compute_stats(load_project_papers(project_id))
    return project


def load_project_papers(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    path = project_papers_path(project_id)
    return read_json(path, [])


def load_project_tags(project_id: str | None) -> list[str]:
    if not project_id:
        return []
    return clean_tags(read_json(project_tags_path(project_id), []))


def save_project_tags(project_id: str, tags: list[str]) -> None:
    write_json(project_tags_path(project_id), clean_tags([tag for tag in tags if tag not in BUILTIN_TAGS]))


def collect_custom_tags_from_papers(papers: list[dict[str, Any]]) -> list[str]:
    tags: list[str] = []
    for paper in papers:
        tags.extend(paper.get("tags") or [])
    return [tag for tag in clean_tags(tags) if tag not in BUILTIN_TAGS]


def register_project_tags(project_id: str, tags: list[str]) -> list[str]:
    custom_tags = [tag for tag in clean_tags(tags) if tag not in BUILTIN_TAGS]
    current = load_project_tags(project_id)
    merged = clean_tags([*current, *custom_tags])
    if merged != current:
        save_project_tags(project_id, merged)
    return merged


def delete_project_tag(project_id: str, tag: str) -> bool:
    tag = tag.strip()
    if not tag or tag in BUILTIN_TAGS:
        return False

    current_tags = load_project_tags(project_id)
    if tag not in current_tags:
        return False
    save_project_tags(project_id, [item for item in current_tags if item != tag])

    papers = load_project_papers(project_id)
    changed = False
    for paper in papers:
        tags = paper.get("tags") or []
        if tag not in tags:
            continue
        paper["tags"] = [item for item in tags if item != tag]
        paper["updated_at"] = now_iso()
        ensure_paper_asset_files(project_id, paper)
        changed = True
    if changed:
        save_project_papers(project_id, papers)
    return True


def load_candidate_papers(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(candidate_papers_path(project_id), [])


def load_search_chat(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(search_chat_path(project_id), [])


def save_search_chat(project_id: str, messages: list[dict[str, Any]]) -> None:
    write_json(search_chat_path(project_id), messages)


def load_search_tasks(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(search_tasks_path(project_id), [])


def save_search_tasks(project_id: str, tasks: list[dict[str, Any]]) -> None:
    write_json(search_tasks_path(project_id), tasks)


def load_library_chat(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(library_chat_path(project_id), [])


def save_library_chat(project_id: str, messages: list[dict[str, Any]]) -> None:
    write_json(library_chat_path(project_id), messages)


def load_library_chat_state(project_id: str | None) -> dict[str, Any]:
    if not project_id:
        return {}
    return read_json(library_chat_state_path(project_id), {})


def save_library_chat_state(project_id: str, state: dict[str, Any]) -> None:
    write_json(library_chat_state_path(project_id), state)


def load_library_chat_tasks(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(library_chat_tasks_path(project_id), [])


def save_library_chat_tasks(project_id: str, tasks: list[dict[str, Any]]) -> None:
    write_json(library_chat_tasks_path(project_id), tasks)


def normalize_matrix_field(field: dict[str, Any], index: int) -> dict[str, Any]:
    name = str(field.get("name") or "未命名字段").strip()
    field_id = str(field.get("field_id") or safe_project_slug(name).lower() or f"field_{index + 1}").strip()
    rule = str(field.get("rule") or "").strip()
    now = now_iso()
    return {
        "field_id": field_id,
        "name": name,
        "rule": rule,
        "rule_hash": field_rule_hash(rule),
        "order": int(field.get("order") or index + 1),
        "enabled": bool(field.get("enabled", True)),
        "created_at": field.get("created_at") or now,
        "updated_at": field.get("updated_at") or now,
    }


def load_reading_matrix_fields(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    fields = read_json(reading_matrix_fields_path(project_id), [])
    if not fields:
        fields = default_reading_matrix_fields()
        save_reading_matrix_fields(project_id, fields, migrate=False)
    normalized = [normalize_matrix_field(field, index) for index, field in enumerate(fields)]
    normalized.sort(key=lambda item: item.get("order", 0))
    return normalized


def save_reading_matrix_fields(project_id: str, fields: list[dict[str, Any]], *, migrate: bool = True) -> list[dict[str, Any]]:
    old_fields = [
        normalize_matrix_field(field, index)
        for index, field in enumerate(read_json(reading_matrix_fields_path(project_id), []))
    ]
    normalized = [normalize_matrix_field({**field, "order": index + 1}, index) for index, field in enumerate(fields)]
    write_json(reading_matrix_fields_path(project_id), normalized)
    if migrate:
        migrate_all_reading_json(project_id, normalized, old_fields)
    return normalized


def load_reading_matrix_tasks(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(reading_matrix_tasks_path(project_id), [])


def save_reading_matrix_tasks(project_id: str, tasks: list[dict[str, Any]]) -> None:
    write_json(reading_matrix_tasks_path(project_id), tasks)


def load_reading_chat_tasks(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(reading_chat_tasks_path(project_id), [])


def save_reading_chat_tasks(project_id: str, tasks: list[dict[str, Any]]) -> None:
    write_json(reading_chat_tasks_path(project_id), tasks)


def load_writing_state(project_id: str | None) -> dict[str, Any]:
    if not project_id:
        return {}
    return read_json(writing_state_path(project_id), {})


def save_writing_state(project_id: str, state: dict[str, Any]) -> None:
    write_json(writing_state_path(project_id), state)
    update_project_timestamp(project_id)


def load_writing_chat(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(writing_chat_path(project_id), [])


def save_writing_chat(project_id: str, messages: list[dict[str, Any]]) -> None:
    write_json(writing_chat_path(project_id), messages)


def append_writing_chat_message(project_id: str, message: dict[str, Any]) -> None:
    messages = load_writing_chat(project_id)
    messages.append(message)
    save_writing_chat(project_id, messages)


def load_writing_chat_state(project_id: str | None) -> dict[str, Any]:
    if not project_id:
        return {}
    return read_json(writing_chat_state_path(project_id), {})


def save_writing_chat_state(project_id: str, state: dict[str, Any]) -> None:
    write_json(writing_chat_state_path(project_id), state)


def load_writing_chat_tasks(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(writing_chat_tasks_path(project_id), [])


def save_writing_chat_tasks(project_id: str, tasks: list[dict[str, Any]]) -> None:
    write_json(writing_chat_tasks_path(project_id), tasks)


def load_bibtex_tasks(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(bibtex_tasks_path(project_id), [])


def save_bibtex_tasks(project_id: str, tasks: list[dict[str, Any]]) -> None:
    write_json(bibtex_tasks_path(project_id), tasks)


def load_pdf_lookup_tasks(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(pdf_lookup_tasks_path(project_id), [])


def save_pdf_lookup_tasks(project_id: str, tasks: list[dict[str, Any]]) -> None:
    write_json(pdf_lookup_tasks_path(project_id), tasks)


def load_pdf_download_tasks(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(pdf_download_tasks_path(project_id), [])


def save_pdf_download_tasks(project_id: str, tasks: list[dict[str, Any]]) -> None:
    write_json(pdf_download_tasks_path(project_id), tasks)


def load_import_drafts(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(import_drafts_path(project_id), [])


def save_import_drafts(project_id: str, drafts: list[dict[str, Any]]) -> None:
    write_json(import_drafts_path(project_id), drafts)


def load_import_tasks(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    return read_json(import_tasks_path(project_id), [])


def save_import_tasks(project_id: str, tasks: list[dict[str, Any]]) -> None:
    write_json(import_tasks_path(project_id), tasks)


def upsert_reading_matrix_task(project_id: str, task: dict[str, Any]) -> None:
    tasks = load_reading_matrix_tasks(project_id)
    replaced = False
    for index, item in enumerate(tasks):
        if item.get("run_id") == task.get("run_id"):
            tasks[index] = {**item, **task}
            replaced = True
            break
    if not replaced:
        tasks.append(task)
    save_reading_matrix_tasks(project_id, tasks[-20:])


def append_reading_matrix_task_event(project_id: str, run_id: str, message: str, kind: str = "info") -> None:
    tasks = load_reading_matrix_tasks(project_id)
    for task in tasks:
        if task.get("run_id") != run_id:
            continue
        events = task.setdefault("events", [])
        if events and events[-1].get("message") == message:
            return
        events.append({"kind": kind, "message": message, "created_at": now_iso()})
        task["events"] = events[-40:]
        save_reading_matrix_tasks(project_id, tasks)
        return


def upsert_reading_chat_task(project_id: str, task: dict[str, Any]) -> None:
    tasks = load_reading_chat_tasks(project_id)
    replaced = False
    for index, item in enumerate(tasks):
        if item.get("run_id") == task.get("run_id"):
            tasks[index] = {**item, **task}
            replaced = True
            break
    if not replaced:
        tasks.append(task)
    save_reading_chat_tasks(project_id, tasks[-30:])


def append_reading_chat_task_event(project_id: str, run_id: str, message: str, kind: str = "info") -> None:
    tasks = load_reading_chat_tasks(project_id)
    for task in tasks:
        if task.get("run_id") != run_id:
            continue
        events = task.setdefault("events", [])
        if events and events[-1].get("message") == message:
            return
        events.append({"kind": kind, "message": message, "created_at": now_iso()})
        task["events"] = events[-30:]
        save_reading_chat_tasks(project_id, tasks)
        return


def upsert_writing_chat_task(project_id: str, task: dict[str, Any]) -> None:
    tasks = load_writing_chat_tasks(project_id)
    replaced = False
    for index, item in enumerate(tasks):
        if item.get("run_id") == task.get("run_id"):
            tasks[index] = {**item, **task}
            replaced = True
            break
    if not replaced:
        tasks.append(task)
    save_writing_chat_tasks(project_id, tasks[-30:])


def append_writing_chat_task_event(project_id: str, run_id: str, message: str, kind: str = "info") -> None:
    tasks = load_writing_chat_tasks(project_id)
    for task in tasks:
        if task.get("run_id") != run_id:
            continue
        events = task.setdefault("events", [])
        if events and events[-1].get("message") == message:
            return
        events.append({"kind": kind, "message": message, "created_at": now_iso()})
        task["events"] = events[-30:]
        save_writing_chat_tasks(project_id, tasks)
        return


def upsert_bibtex_task(project_id: str, task: dict[str, Any]) -> None:
    tasks = load_bibtex_tasks(project_id)
    replaced = False
    for index, item in enumerate(tasks):
        if item.get("run_id") == task.get("run_id"):
            tasks[index] = {**item, **task}
            replaced = True
            break
    if not replaced:
        tasks.append(task)
    save_bibtex_tasks(project_id, tasks[-20:])


def append_bibtex_task_event(project_id: str, run_id: str, message: str, kind: str = "info") -> None:
    tasks = load_bibtex_tasks(project_id)
    for task in tasks:
        if task.get("run_id") != run_id:
            continue
        events = task.setdefault("events", [])
        if events and events[-1].get("message") == message:
            return
        events.append({"kind": kind, "message": message, "created_at": now_iso()})
        task["events"] = events[-40:]
        save_bibtex_tasks(project_id, tasks)
        return


def upsert_pdf_lookup_task(project_id: str, task: dict[str, Any]) -> None:
    tasks = load_pdf_lookup_tasks(project_id)
    replaced = False
    for index, item in enumerate(tasks):
        if item.get("run_id") == task.get("run_id"):
            tasks[index] = {**item, **task}
            replaced = True
            break
    if not replaced:
        tasks.append(task)
    save_pdf_lookup_tasks(project_id, tasks[-20:])


def append_pdf_lookup_task_event(project_id: str, run_id: str, message: str, kind: str = "info") -> None:
    tasks = load_pdf_lookup_tasks(project_id)
    for task in tasks:
        if task.get("run_id") != run_id:
            continue
        events = task.setdefault("events", [])
        if events and events[-1].get("message") == message:
            return
        events.append({"kind": kind, "message": message, "created_at": now_iso()})
        task["events"] = events[-40:]
        save_pdf_lookup_tasks(project_id, tasks)
        return


def upsert_pdf_download_task(project_id: str, task: dict[str, Any]) -> None:
    tasks = load_pdf_download_tasks(project_id)
    replaced = False
    for index, item in enumerate(tasks):
        if item.get("run_id") == task.get("run_id"):
            tasks[index] = {**item, **task}
            replaced = True
            break
    if not replaced:
        tasks.append(task)
    save_pdf_download_tasks(project_id, tasks[-20:])


def append_pdf_download_task_event(project_id: str, run_id: str, message: str, kind: str = "info") -> None:
    tasks = load_pdf_download_tasks(project_id)
    for task in tasks:
        if task.get("run_id") != run_id:
            continue
        events = task.setdefault("events", [])
        if events and events[-1].get("message") == message:
            return
        events.append({"kind": kind, "message": message, "created_at": now_iso()})
        task["events"] = events[-40:]
        save_pdf_download_tasks(project_id, tasks)
        return


def upsert_import_task(project_id: str, task: dict[str, Any]) -> None:
    tasks = load_import_tasks(project_id)
    replaced = False
    for index, item in enumerate(tasks):
        if item.get("run_id") == task.get("run_id"):
            tasks[index] = {**item, **task}
            replaced = True
            break
    if not replaced:
        tasks.append(task)
    save_import_tasks(project_id, tasks[-10:])


def append_import_task_event(project_id: str, run_id: str, message: str, kind: str = "info") -> None:
    tasks = load_import_tasks(project_id)
    for task in tasks:
        if task.get("run_id") != run_id:
            continue
        events = task.setdefault("events", [])
        if events and events[-1].get("message") == message:
            return
        events.append({"kind": kind, "message": message, "created_at": now_iso()})
        task["events"] = events[-40:]
        save_import_tasks(project_id, tasks)
        return


def project_has_running_search(project_id: str) -> bool:
    recover_search_tasks(project_id)
    return any(task.get("status") == "running" for task in load_search_tasks(project_id))


def upsert_search_task(project_id: str, task: dict[str, Any]) -> None:
    tasks = load_search_tasks(project_id)
    replaced = False
    for index, item in enumerate(tasks):
        if item.get("run_id") == task.get("run_id"):
            tasks[index] = {**item, **task}
            replaced = True
            break
    if not replaced:
        tasks.append(task)
    save_search_tasks(project_id, tasks[-20:])


def append_search_task_event(project_id: str, run_id: str, message: str, kind: str = "info") -> None:
    tasks = load_search_tasks(project_id)
    for task in tasks:
        if task.get("run_id") != run_id:
            continue
        events = task.setdefault("events", [])
        if events and events[-1].get("message") == message:
            return
        events.append({"kind": kind, "message": message, "created_at": now_iso()})
        task["events"] = events[-30:]
        save_search_tasks(project_id, tasks)
        return


def upsert_library_chat_task(project_id: str, task: dict[str, Any]) -> None:
    tasks = load_library_chat_tasks(project_id)
    replaced = False
    for index, item in enumerate(tasks):
        if item.get("run_id") == task.get("run_id"):
            tasks[index] = {**item, **task}
            replaced = True
            break
    if not replaced:
        tasks.append(task)
    save_library_chat_tasks(project_id, tasks[-20:])


def append_library_chat_task_event(project_id: str, run_id: str, message: str, kind: str = "info") -> None:
    tasks = load_library_chat_tasks(project_id)
    for task in tasks:
        if task.get("run_id") != run_id:
            continue
        events = task.setdefault("events", [])
        if events and events[-1].get("message") == message:
            return
        events.append({"kind": kind, "message": message, "created_at": now_iso()})
        task["events"] = events[-30:]
        save_library_chat_tasks(project_id, tasks)
        return


def project_has_running_library_chat(project_id: str) -> bool:
    return any(task.get("status") == "running" for task in load_library_chat_tasks(project_id))


def library_chat_task_is_running(project_id: str, run_id: str) -> bool:
    return any(
        task.get("run_id") == run_id and task.get("status") == "running"
        for task in load_library_chat_tasks(project_id)
    )


def project_has_running_reading_matrix(project_id: str) -> bool:
    return any(task.get("status") == "running" for task in load_reading_matrix_tasks(project_id))


def reading_matrix_task_is_running(project_id: str, run_id: str) -> bool:
    return any(
        task.get("run_id") == run_id and task.get("status") == "running"
        for task in load_reading_matrix_tasks(project_id)
    )


def project_has_running_reading_chat(project_id: str, paper_id: str) -> bool:
    return any(
        task.get("paper_id") == paper_id and task.get("status") == "running"
        for task in load_reading_chat_tasks(project_id)
    )


def reading_chat_task_is_running(project_id: str, run_id: str) -> bool:
    return any(
        task.get("run_id") == run_id and task.get("status") == "running"
        for task in load_reading_chat_tasks(project_id)
    )


def project_has_running_writing_chat(project_id: str) -> bool:
    return any(task.get("status") == "running" for task in load_writing_chat_tasks(project_id))


def writing_chat_task_is_running(project_id: str, run_id: str) -> bool:
    return any(
        task.get("run_id") == run_id and task.get("status") == "running"
        for task in load_writing_chat_tasks(project_id)
    )


def project_has_running_bibtex(project_id: str) -> bool:
    return any(task.get("status") == "running" for task in load_bibtex_tasks(project_id))


def bibtex_task_is_running(project_id: str, run_id: str) -> bool:
    return any(
        task.get("run_id") == run_id and task.get("status") == "running"
        for task in load_bibtex_tasks(project_id)
    )


def project_has_running_pdf_lookup(project_id: str) -> bool:
    return any(task.get("status") == "running" for task in load_pdf_lookup_tasks(project_id))


def pdf_lookup_task_is_running(project_id: str, run_id: str) -> bool:
    return any(
        task.get("run_id") == run_id and task.get("status") == "running"
        for task in load_pdf_lookup_tasks(project_id)
    )


def project_has_running_pdf_download(project_id: str) -> bool:
    return any(task.get("status") == "running" for task in load_pdf_download_tasks(project_id))


def pdf_download_task_is_running(project_id: str, run_id: str) -> bool:
    return any(
        task.get("run_id") == run_id and task.get("status") == "running"
        for task in load_pdf_download_tasks(project_id)
    )


def project_has_running_import(project_id: str) -> bool:
    return any(task.get("status") == "running" for task in load_import_tasks(project_id))


def import_task_is_running(project_id: str, run_id: str) -> bool:
    return any(
        task.get("run_id") == run_id and task.get("status") == "running"
        for task in load_import_tasks(project_id)
    )


def load_paper_reading(project_id: str, paper_id: str | None) -> dict[str, Any]:
    if not project_id or not paper_id:
        return {}
    return read_json(resolve_project_file(project_id, paper_reading_relative_path(paper_id)), {})


def load_paper_reading_chat(project_id: str, paper_id: str | None) -> list[dict[str, Any]]:
    if not project_id or not paper_id:
        return []
    return read_json(resolve_project_file(project_id, paper_reading_chat_relative_path(paper_id)), [])


def save_paper_reading_chat(project_id: str, paper_id: str, messages: list[dict[str, Any]]) -> None:
    write_json(resolve_project_file(project_id, paper_reading_chat_relative_path(paper_id)), messages)


def append_paper_reading_chat_message(project_id: str, paper_id: str, message: dict[str, Any]) -> None:
    messages = load_paper_reading_chat(project_id, paper_id)
    messages.append(message)
    save_paper_reading_chat(project_id, paper_id, messages)


def load_paper_reading_chat_state(project_id: str, paper_id: str | None) -> dict[str, Any]:
    if not project_id or not paper_id:
        return {}
    return read_json(resolve_project_file(project_id, paper_reading_chat_state_relative_path(paper_id)), {})


def save_paper_reading_chat_state(project_id: str, paper_id: str, state: dict[str, Any]) -> None:
    write_json(resolve_project_file(project_id, paper_reading_chat_state_relative_path(paper_id)), state)


def reading_chat_attachment_url(project_id: str, paper_id: str, attachment: dict[str, Any]) -> str:
    filename = str(attachment.get("filename") or "").strip()
    if not filename:
        return ""
    return url_for("reading_chat_asset", project_id=project_id, paper_id=paper_id, filename=filename)


def serialize_reading_chat_messages(project_id: str, paper_id: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        attachments = []
        for attachment in message.get("attachments") or []:
            if not isinstance(attachment, dict):
                continue
            copied = dict(attachment)
            copied["url"] = reading_chat_attachment_url(project_id, paper_id, copied)
            attachments.append(copied)
        item["attachments"] = attachments
        serialized.append(item)
    return serialized


def save_reading_chat_uploads(project_id: str, paper_id: str, run_id: str, uploads: list[Any]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    allowed = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }
    target_dir = resolve_project_file(project_id, paper_reading_assets_relative_dir(paper_id))
    target_dir.mkdir(parents=True, exist_ok=True)
    for index, upload in enumerate(uploads[:6], start=1):
        if not upload or not getattr(upload, "filename", ""):
            continue
        content_type = str(getattr(upload, "content_type", "") or "").lower().split(";", 1)[0]
        ext = allowed.get(content_type)
        if not ext:
            filename_ext = Path(str(upload.filename)).suffix.lower().lstrip(".")
            ext = filename_ext if filename_ext in {"png", "jpg", "jpeg", "webp", "gif"} else ""
        if not ext:
            continue
        if ext == "jpeg":
            ext = "jpg"
        filename = f"{safe_project_slug(run_id)}-{index:02d}.{ext}"
        relative_path = f"{paper_reading_assets_relative_dir(paper_id)}/{filename}"
        target = resolve_project_file(project_id, relative_path)
        upload.save(target)
        attachments.append(
            {
                "type": "image",
                "filename": filename,
                "path": relative_path,
                "content_type": content_type or f"image/{ext}",
            }
        )
    return attachments


def migrate_reading_record(
    reading: dict[str, Any],
    paper_id: str,
    fields: list[dict[str, Any]],
    old_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current_fields = reading.get("fields") if isinstance(reading.get("fields"), dict) else {}
    old_by_id = {field.get("field_id"): field for field in (old_fields or [])}
    migrated_fields: dict[str, Any] = {}
    for field in fields:
        field_id = field["field_id"]
        old_value = current_fields.get(field_id, {}) if isinstance(current_fields, dict) else {}
        old_rule = str((old_by_id.get(field_id) or {}).get("rule") or "").strip()
        rule_changed = field_id in old_by_id and old_rule != str(field.get("rule") or "").strip()
        preserved_value = str(old_value.get("value") or "").strip() if isinstance(old_value, dict) else ""
        migrated_fields[field_id] = {
            "name": field["name"],
            "rule": field["rule"],
            "rule_hash": field.get("rule_hash") or field_rule_hash(field.get("rule", "")),
            "value": "" if rule_changed else preserved_value,
        }
    return {
        "paper_id": reading.get("paper_id") or paper_id,
        "generated_at": reading.get("generated_at") or "",
        "field_version": "auto",
        "fields": migrated_fields,
    }


def migrate_all_reading_json(project_id: str, fields: list[dict[str, Any]], old_fields: list[dict[str, Any]] | None = None) -> None:
    changed_papers: list[dict[str, Any]] = []
    for paper in load_project_papers(project_id):
        paper_id = paper.get("paper_id")
        if not paper_id:
            continue
        path = resolve_project_file(project_id, paper.get("structured_reading_json_path") or paper_reading_relative_path(paper_id))
        if not path.exists():
            continue
        reading = read_json(path, {})
        write_json(path, migrate_reading_record(reading, paper_id, fields, old_fields))
        paper["has_structured_reading"] = True
        paper["structured_reading_json_path"] = paper_reading_relative_path(paper_id)
        paper["updated_at"] = now_iso()
        changed_papers.append(paper)
    if changed_papers:
        papers = load_project_papers(project_id)
        changed_ids = {paper["paper_id"] for paper in changed_papers}
        for paper in papers:
            if paper.get("paper_id") in changed_ids:
                paper["has_structured_reading"] = True
                paper["structured_reading_json_path"] = paper_reading_relative_path(paper["paper_id"])
                paper["updated_at"] = now_iso()
                ensure_paper_asset_files(project_id, paper)
        save_project_papers(project_id, papers)


def save_generated_reading(project_id: str, paper: dict[str, Any], fields: list[dict[str, Any]], result: dict[str, Any]) -> None:
    paper_id = paper["paper_id"]
    raw_fields = result.get("fields") if isinstance(result.get("fields"), dict) else {}
    reading_path = resolve_project_file(project_id, paper_reading_relative_path(paper_id))
    existing = read_json(reading_path, {}) if reading_path.exists() else {}
    reading_fields = existing.get("fields") if isinstance(existing.get("fields"), dict) else {}
    for field in fields:
        field_id = field["field_id"]
        raw_value = raw_fields.get(field_id, {}) if isinstance(raw_fields, dict) else {}
        value = raw_value.get("value") if isinstance(raw_value, dict) else raw_value
        reading_fields[field_id] = {
            "name": field["name"],
            "rule": field["rule"],
            "rule_hash": field.get("rule_hash") or field_rule_hash(field.get("rule", "")),
            "value": str(value or "").strip(),
        }
    reading = {
        "paper_id": paper_id,
        "generated_at": now_iso(),
        "field_version": "auto",
        "fields": reading_fields,
    }
    write_json(reading_path, reading)

    papers = load_project_papers(project_id)
    for item in papers:
        if item.get("paper_id") != paper_id:
            continue
        item["has_structured_reading"] = True
        item["structured_reading_json_path"] = paper_reading_relative_path(paper_id)
        item["updated_at"] = now_iso()
        ensure_paper_asset_files(project_id, item)
        break
    save_project_papers(project_id, papers)


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_writing_stage(value: str | None) -> str:
    return value if value in WRITING_STAGES else WRITING_STAGES[0]


def default_writing_outline() -> str:
    return "\n".join(
        [
            "# 综述标题",
            "",
            "## 1. 研究背景",
            "- 说明研究主题、问题来源和综述意义。",
            "",
            "## 2. 相关工作脉络",
            "- 按任务、方法或时间线组织已有文献。",
            "",
            "## 3. 方法与系统分析",
            "- 比较代表论文的方法思路、实验设置和核心结论。",
            "",
            "## 4. 挑战与展望",
            "- 总结现有不足，提出未来研究方向。",
        ]
    )


def ensure_writing_files(project_id: str) -> dict[str, Any]:
    papers = load_project_papers(project_id)
    state = load_writing_state(project_id)
    if not state:
        selected_ids = [paper["paper_id"] for paper in papers if paper.get("has_structured_reading")]
        state = {
            "current_stage": "topic",
            "selected_paper_ids": selected_ids,
            "active_matrix_paper_id": selected_ids[0] if selected_ids else (papers[0]["paper_id"] if papers else ""),
            "topic": "",
            "outline_hash": "",
            "csv_hash": "",
            "draft_hash": "",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        save_writing_state(project_id, state)

    outline_path = resolve_project_file(project_id, writing_outline_relative_path())
    outline_path.parent.mkdir(parents=True, exist_ok=True)
    if not outline_path.exists():
        outline_path.write_text(default_writing_outline(), encoding="utf-8")

    survey_path = resolve_project_file(project_id, writing_survey_relative_path())
    survey_path.parent.mkdir(parents=True, exist_ok=True)
    if not survey_path.exists():
        survey_path.write_text("# 综述草稿\n\n请在右侧对话中让光明生成或修改综述正文。\n", encoding="utf-8")

    mappings_path = resolve_project_file(project_id, writing_section_mappings_relative_path())
    mappings_path.parent.mkdir(parents=True, exist_ok=True)
    if not mappings_path.exists():
        write_json(mappings_path, [])

    refresh_writing_csv(project_id)
    return load_writing_state(project_id)


def load_writing_outline(project_id: str) -> str:
    path = resolve_project_file(project_id, writing_outline_relative_path())
    return path.read_text(encoding="utf-8-sig") if path.exists() else default_writing_outline()


def save_writing_outline(project_id: str, outline_text: str) -> str:
    path = resolve_project_file(project_id, writing_outline_relative_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(outline_text, encoding="utf-8")
    state = load_writing_state(project_id)
    state["outline_hash"] = text_hash(outline_text)
    state["updated_at"] = now_iso()
    save_writing_state(project_id, state)
    return outline_text


def load_writing_survey(project_id: str) -> str:
    path = resolve_project_file(project_id, writing_survey_relative_path())
    return path.read_text(encoding="utf-8-sig") if path.exists() else ""


def save_writing_survey(project_id: str, markdown: str) -> str:
    path = resolve_project_file(project_id, writing_survey_relative_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    state = load_writing_state(project_id)
    state["draft_hash"] = text_hash(markdown)
    state["updated_at"] = now_iso()
    save_writing_state(project_id, state)
    return markdown


def outline_number_prefix(title: str) -> str:
    match = re.match(r"^(\d+(?:\.\d+)*)(?:[\.、]\s+|\s+)", title.strip())
    return match.group(1) if match else ""


def parse_outline_sections(outline_text: str) -> list[dict[str, str]]:
    raw_sections: list[dict[str, Any]] = []
    for line in outline_text.splitlines():
        stripped = line.strip()
        title = ""
        heading_level = 0
        if stripped.startswith("##"):
            heading_level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped.lstrip("#").strip()
        elif re.match(r"^\d+(?:\.\d+)*[\.、\s]+", stripped):
            title = stripped
        if title:
            raw_sections.append(
                {
                    "section_id": safe_project_slug(title).lower() or f"section-{len(raw_sections) + 1}",
                    "title": title,
                    "order": len(raw_sections) + 1,
                    "heading_level": heading_level,
                    "number_prefix": outline_number_prefix(title),
                }
            )

    leaf_sections: list[dict[str, Any]] = []
    for index, section in enumerate(raw_sections):
        number_prefix = section.get("number_prefix") or ""
        heading_level = int(section.get("heading_level") or 0)
        has_child = False
        for later in raw_sections[index + 1 :]:
            later_number = later.get("number_prefix") or ""
            if number_prefix and later_number.startswith(f"{number_prefix}."):
                has_child = True
                break
            if number_prefix:
                continue
            later_level = int(later.get("heading_level") or 0)
            if heading_level and later_level and later_level <= heading_level:
                break
            if heading_level and later_level > heading_level:
                has_child = True
                break
        if not has_child:
            leaf_sections.append(section)

    return [
        {"section_id": section["section_id"], "title": section["title"], "order": order}
        for order, section in enumerate(leaf_sections, start=1)
    ]


def paper_reading_values(project_id: str, paper_id: str) -> dict[str, str]:
    reading = load_paper_reading(project_id, paper_id)
    fields = reading.get("fields") if isinstance(reading.get("fields"), dict) else {}
    values: dict[str, str] = {}
    for field in fields.values():
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if name:
            values[name] = str(field.get("value") or "").strip()
    return values


def refresh_writing_csv(project_id: str) -> str:
    state = load_writing_state(project_id)
    selected = set(state.get("selected_paper_ids") or [])
    matrix_fields = [field for field in load_reading_matrix_fields(project_id) if field.get("enabled", True)]
    base_fields = ["paper_id", "title", "authors", "year", "venue", "abstract_zh"]
    matrix_columns = [f"matrix_{field['name']}" for field in matrix_fields]
    output_fields = [*base_fields, *matrix_columns, "paper_dir"]
    rows: list[dict[str, str]] = []
    for paper in load_project_papers(project_id):
        paper_id = paper.get("paper_id")
        if paper_id not in selected:
            continue
        reading_values = paper_reading_values(project_id, paper_id)
        row: dict[str, str] = {
            "paper_id": str(paper_id),
            "title": str(paper.get("title") or ""),
            "authors": " / ".join(paper.get("authors") or []),
            "year": str(paper.get("year") or ""),
            "venue": str(paper.get("venue") or ""),
            "abstract_zh": str(paper.get("abstract_zh") or ""),
            "paper_dir": f"papers/{safe_project_slug(str(paper_id))}",
        }
        for field in matrix_fields:
            row[f"matrix_{field['name']}"] = reading_values.get(field["name"], "")
        rows.append(row)

    path = resolve_project_file(project_id, writing_sources_relative_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(rows)
    csv_text = path.read_text(encoding="utf-8-sig")
    state["csv_hash"] = text_hash(csv_text)
    state["updated_at"] = now_iso()
    save_writing_state(project_id, state)
    return writing_sources_relative_path()


def load_writing_section_mappings(project_id: str | None) -> list[dict[str, Any]]:
    if not project_id:
        return []
    path = resolve_project_file(project_id, writing_section_mappings_relative_path())
    return read_json(path, [])


def save_writing_section_mappings(project_id: str, mappings: list[dict[str, Any]]) -> None:
    path = resolve_project_file(project_id, writing_section_mappings_relative_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, mappings)


def prune_writing_section_mappings(project_id: str) -> list[dict[str, Any]]:
    valid_section_ids = {section["section_id"] for section in parse_outline_sections(load_writing_outline(project_id))}
    valid_paper_ids = {str(paper.get("paper_id") or "") for paper in selected_writing_papers(project_id)}
    rows = load_writing_section_mappings(project_id)
    pruned = [
        row
        for row in rows
        if str(row.get("section_id") or "") in valid_section_ids
        and str(row.get("paper_id") or "") in valid_paper_ids
    ]
    if pruned != rows:
        save_writing_section_mappings(project_id, pruned)
    return pruned


def selected_writing_papers(project_id: str) -> list[dict[str, Any]]:
    state = load_writing_state(project_id)
    selected_ids = set(state.get("selected_paper_ids") or [])
    return [paper for paper in load_project_papers(project_id) if paper.get("paper_id") in selected_ids]


def selected_writing_paper_context(project_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for paper in selected_writing_papers(project_id):
        paper_id = str(paper.get("paper_id") or "")
        rows.append(
            {
                "paper_id": paper_id,
                "title": str(paper.get("title") or ""),
                "authors": paper.get("authors") or [],
                "year": paper.get("year") or "",
                "venue": paper.get("venue") or "",
                "abstract_zh": str(paper.get("abstract_zh") or ""),
                "matrix": paper_reading_values(project_id, paper_id),
                "paper_dir": f"papers/{safe_project_slug(paper_id)}",
            }
        )
    return rows


def normalize_section_mapping(
    project_id: str,
    section: dict[str, Any],
    value: dict[str, Any],
    *,
    paper_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    paper_id = str(value.get("paper_id") or "").strip()
    if paper_id not in paper_lookup:
        return None
    section_id = str(section.get("section_id") or "").strip()
    paper = paper_lookup[paper_id]
    return {
        "mapping_id": f"{section_id}__{paper_id}",
        "section_id": section_id,
        "section_title": str(section.get("title") or ""),
        "section_order": int(section.get("order") or 0),
        "paper_id": paper_id,
        "paper_title": str(paper.get("title") or value.get("paper_title") or ""),
        "citation_role": str(value.get("citation_role") or "辅助证据").strip(),
        "writing_note": str(value.get("writing_note") or "").strip(),
        "evidence_detail": str(value.get("evidence_detail") or "").strip(),
        "missing_detail": str(value.get("missing_detail") or "").strip(),
        "source_basis": value.get("source_basis") if isinstance(value.get("source_basis"), list) else ["writing_sources.csv", "文献矩阵", "paper_dir"],
        "updated_at": now_iso(),
    }


def replace_section_mappings(project_id: str, section: dict[str, Any], raw_mappings: Any) -> list[dict[str, Any]]:
    sections = parse_outline_sections(load_writing_outline(project_id))
    valid_section_ids = {item["section_id"] for item in sections}
    section_id = str(section.get("section_id") or "")
    if section_id not in valid_section_ids:
        return load_writing_section_mappings(project_id)
    paper_lookup = {str(paper.get("paper_id")): paper for paper in selected_writing_papers(project_id)}
    new_rows: list[dict[str, Any]] = []
    if isinstance(raw_mappings, list):
        for value in raw_mappings:
            if not isinstance(value, dict):
                continue
            normalized = normalize_section_mapping(project_id, section, value, paper_lookup=paper_lookup)
            if normalized:
                new_rows.append(normalized)
    existing = [row for row in prune_writing_section_mappings(project_id) if row.get("section_id") != section_id]
    merged = [*existing, *new_rows]
    merged.sort(key=lambda row: (int(row.get("section_order") or 0), str(row.get("paper_title") or "")))
    save_writing_section_mappings(project_id, merged)
    return merged


def writing_mapping_payload(project_id: str) -> dict[str, Any]:
    state = load_writing_state(project_id)
    papers = [{"paper_id": str(paper.get("paper_id") or ""), "title": str(paper.get("title") or "")} for paper in selected_writing_papers(project_id)]
    return {
        "sections": parse_outline_sections(load_writing_outline(project_id)),
        "papers": papers,
        "mappings": prune_writing_section_mappings(project_id),
        "state": state,
    }


def bibtex_identifier(paper: dict[str, Any]) -> str:
    doi = str(paper.get("doi") or "").strip()
    if doi:
        return doi

    for key in ("arxiv_id", "paper_url", "pdf_url"):
        value = str(paper.get(key) or "").strip()
        arxiv_id = extract_arxiv_id(value)
        if arxiv_id:
            return f"10.48550/arXiv.{arxiv_id}"

    for key in ("paper_url", "title"):
        value = str(paper.get(key) or "").strip()
        if value:
            return value
    return ""


def extract_arxiv_id(value: str) -> str:
    if not value:
        return ""
    value = value.strip()
    if value.lower().startswith("arxiv:"):
        value = value.split(":", 1)[1].strip()
    match = re.search(r"arxiv\.org/(?:abs|pdf|html)/([^?#\s]+)", value, flags=re.I)
    if match:
        value = match.group(1)
    value = re.sub(r"\.pdf$", "", value, flags=re.I)
    if re.match(r"^\d{4}\.\d+(?:v\d+)?$", value):
        return re.sub(r"v\d+$", "", value)
    return ""


def write_paper_bibtex(project_id: str, paper_id: str, bibtex: str) -> str:
    relative_path = paper_bibtex_relative_path(paper_id)
    target = resolve_project_file(project_id, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(bibtex.strip() + "\n", encoding="utf-8")
    return relative_path


def update_paper_bibtex_status(project_id: str, paper_id: str, status: str, bibtex_path: str = "") -> None:
    papers = load_project_papers(project_id)
    for paper in papers:
        if paper.get("paper_id") != paper_id:
            continue
        paper["bibtex_status"] = status
        paper["bibtex_path"] = bibtex_path
        paper["updated_at"] = now_iso()
        ensure_paper_asset_files(project_id, paper)
        break
    save_project_papers(project_id, papers)


def fetch_bibtex_for_paper(paper: dict[str, Any]) -> str:
    from doi2bib3 import fetch_bibtex
    from doi2bib3.backend import _fetch_bibtex_for_doi
    from doi2bib3.normalize import normalize_bibtex

    identifier = bibtex_identifier(paper)
    if not identifier:
        raise ValueError("缺少 DOI、arXiv、论文链接或标题")
    arxiv_match = re.match(r"^10\.48550/arxiv\.(?P<id>\d{4}\.\d+)$", identifier, flags=re.I)
    if arxiv_match:
        arxiv_id = arxiv_match.group("id")
        raw_bibtex = _fetch_bibtex_for_doi(identifier, timeout=20)
        bibtex = normalize_bibtex(raw_bibtex, arxiv_id=arxiv_id, include_arxiv_fields=True)
    else:
        bibtex = fetch_bibtex(identifier, timeout=20)
    if "@" not in bibtex:
        raise ValueError("返回内容不是 BibTeX")
    return bibtex


def dedupe_bibtex_keys(entries: list[str]) -> str:
    seen: dict[str, int] = {}
    output: list[str] = []
    pattern = re.compile(r"(@\w+\s*\{\s*)([^,\s]+)", re.IGNORECASE)
    for entry in entries:
        match = pattern.search(entry)
        if not match:
            output.append(entry.strip())
            continue
        key = match.group(2)
        count = seen.get(key, 0) + 1
        seen[key] = count
        if count == 1:
            output.append(entry.strip())
            continue
        new_key = f"{key}_{count}"
        output.append(pattern.sub(rf"\1{new_key}", entry.strip(), count=1))
    return "\n\n".join(item for item in output if item.strip()) + "\n"


def export_selected_bibtex(project_id: str, run_id: str, selected_paper_ids: list[str]) -> tuple[str, int]:
    selected = set(selected_paper_ids)
    entries: list[str] = []
    for paper in load_project_papers(project_id):
        if paper.get("paper_id") not in selected:
            continue
        path_value = str(paper.get("bibtex_path") or "").strip()
        if paper.get("bibtex_status") != "已生成" or not path_value:
            continue
        path = resolve_project_file(project_id, path_value)
        if path.exists():
            entries.append(path.read_text(encoding="utf-8-sig"))
    if not entries:
        raise RuntimeError("勾选文献中没有可导出的 BibTeX")
    export_path = f"exports/references-{run_id}.bib"
    target = resolve_project_file(project_id, export_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedupe_bibtex_keys(entries), encoding="utf-8")
    return export_path, len(entries)


def save_project_papers(project_id: str, papers: list[dict[str, Any]]) -> None:
    write_json(project_papers_path(project_id), papers)
    update_project_timestamp(project_id)


def ensure_paper_asset_files(project_id: str, paper: dict[str, Any]) -> None:
    paper_id = paper.get("paper_id") or paper.get("id")
    if not paper_id:
        return
    root = paper_asset_dir(project_id, paper_id)
    root.mkdir(parents=True, exist_ok=True)

    metadata = {key: value for key, value in paper.items() if key != "reading"}
    write_json(root / "metadata.json", metadata)


def compute_stats(papers: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "papers": len(papers),
        "read": sum(1 for paper in papers if paper.get("has_structured_reading") is True),
        "selected": sum(1 for paper in papers if "重点" in (paper.get("tags") or [])),
        "outline": 0,
    }


def sidebar_project() -> dict[str, Any]:
    current = get_current_project()
    if current:
        return current
    return {
        "name": "未选择项目",
        "subtitle": "创建项目后在这里查看当前工作区状态",
        "stats": {"papers": 0, "read": 0, "selected": 0, "outline": 0},
    }


def get_current_project() -> dict[str, Any] | None:
    current_id = session.get("current_project_id")
    current = load_project(current_id)
    if current:
        return current
    projects = list_projects()
    if not projects:
        return None
    session["current_project_id"] = projects[0]["id"]
    return load_project(projects[0]["id"])


def catalog_search(query: str) -> list[dict[str, Any]]:
    normalized = query.strip().lower()
    if not normalized:
        return SEARCH_CATALOG
    terms = [term for term in re.split(r"\s+", normalized) if term]
    results: list[dict[str, Any]] = []
    for item in SEARCH_CATALOG:
        haystack = " ".join(
            [
                item["title"],
                item["authors"],
                item["source"],
                item["abstract"],
                " ".join(item["keywords"]),
            ]
        ).lower()
        if all(term in haystack for term in terms):
            results.append(item)
    return results


def filter_candidate_papers(candidates: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    normalized = query.strip().lower()
    if not normalized:
        return candidates
    terms = [term for term in re.split(r"\s+", normalized) if term]
    filtered = []
    for item in candidates:
        haystack = " ".join(
            [
                str(item.get("title") or ""),
                " ".join(item.get("authors") or []),
                str(item.get("venue") or ""),
                str(item.get("abstract") or ""),
                " ".join(item.get("keywords") or []),
            ]
        ).lower()
        if all(term in haystack for term in terms):
            filtered.append(item)
    return filtered


def import_paper_to_project(project_id: str, paper_id: str) -> bool:
    catalog_item = next((item for item in SEARCH_CATALOG if item["id"] == paper_id), None)
    if not catalog_item:
        return False

    papers = load_project_papers(project_id)
    if any((item.get("paper_id") or item.get("id")) == paper_id for item in papers):
        return False

    record = {
        "paper_id": catalog_item["id"],
        "candidate_id": "",
        "title": catalog_item["title"],
        "authors": catalog_item["authors"],
        "year": catalog_item["year"],
        "venue": catalog_item["source"],
        "paper_url": "",
        "doi": None,
        "abstract": catalog_item["abstract"],
        "abstract_zh": catalog_item["abstract"],
        "keywords": catalog_item["keywords"],
        "tags": [],
        "notes": "",
        "pdf_url": "",
        "pdf_status": "无来源",
        "pdf_path": paper_pdf_relative_path(catalog_item["id"]),
        "bibtex_status": "未生成",
        "bibtex_path": "",
        "has_structured_reading": False,
        "structured_reading_json_path": paper_reading_relative_path(catalog_item["id"]),
        "imported_at": now_iso(),
        "updated_at": now_iso(),
    }
    papers.append(record)
    save_project_papers(project_id, papers)
    ensure_paper_asset_files(project_id, record)
    return True


def import_candidate_to_project(project_id: str, candidate_id: str) -> bool:
    candidate = next(
        (item for item in load_candidate_papers(project_id) if item.get("candidate_id") == candidate_id),
        None,
    )
    if not candidate:
        return False

    papers = load_project_papers(project_id)
    candidate_identity = set(candidate_keys(candidate))
    if any(
        item.get("candidate_id") == candidate_id or candidate_identity.intersection(candidate_keys(item))
        for item in papers
    ):
        return False

    paper_id = f"paper-{uuid4().hex[:10]}"
    record = {
        "paper_id": paper_id,
        "candidate_id": candidate_id,
        "title": candidate["title"],
        "authors": candidate.get("authors") or [],
        "year": candidate.get("year") or "",
        "venue": candidate.get("venue") or "",
        "abstract": candidate.get("abstract") or "",
        "abstract_zh": candidate.get("abstract_zh") or "",
        "keywords": candidate.get("keywords") or [],
        "tags": [],
        "notes": "",
        "doi": candidate.get("doi"),
        "paper_url": candidate.get("paper_url"),
        "pdf_url": candidate.get("pdf_url"),
        "pdf_status": "可下载" if candidate.get("pdf_url") else "无来源",
        "pdf_path": paper_pdf_relative_path(paper_id),
        "bibtex_status": "未生成",
        "bibtex_path": "",
        "has_structured_reading": False,
        "structured_reading_json_path": paper_reading_relative_path(paper_id),
        "imported_at": now_iso(),
        "updated_at": now_iso(),
    }
    papers.append(record)
    save_project_papers(project_id, papers)
    ensure_paper_asset_files(project_id, record)

    candidates = load_candidate_papers(project_id)
    for item in candidates:
        if item.get("candidate_id") == candidate_id:
            item["import_status"] = "已导入"
    write_json(candidate_papers_path(project_id), candidates)
    return True


def remove_paper_from_library(project_id: str, paper_id: str = "", candidate_id: str = "") -> bool:
    papers = load_project_papers(project_id)
    removed: dict[str, Any] | None = None
    kept: list[dict[str, Any]] = []

    for paper in papers:
        matches_paper = paper_id and (paper.get("paper_id") == paper_id or paper.get("id") == paper_id)
        matches_candidate = candidate_id and paper.get("candidate_id") == candidate_id
        if removed is None and (matches_paper or matches_candidate):
            removed = paper
            continue
        kept.append(paper)

    if removed is None:
        return False

    save_project_papers(project_id, kept)

    removed_paper_id = removed.get("paper_id") or removed.get("id")
    if removed_paper_id:
        asset_dir = paper_asset_dir(project_id, removed_paper_id)
        if asset_dir.exists():
            shutil.rmtree(asset_dir)
        notes = [
            note
            for note in read_json(reading_notes_path(project_id), [])
            if note.get("paper_id") != removed_paper_id
        ]
        write_json(reading_notes_path(project_id), notes)

    removed_candidate_id = candidate_id or removed.get("candidate_id")
    if removed_candidate_id:
        candidates = load_candidate_papers(project_id)
        for item in candidates:
            if item.get("candidate_id") == removed_candidate_id:
                item["import_status"] = "未导入"
        write_json(candidate_papers_path(project_id), candidates)

    return True


def update_library_paper(project_id: str, paper_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    papers = load_project_papers(project_id)
    updated: dict[str, Any] | None = None
    allowed = {"tags", "notes"}
    for paper in papers:
        if paper.get("paper_id") != paper_id:
            continue
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "tags":
                paper[key] = clean_tags(value)
                register_project_tags(project_id, paper[key])
            if key == "notes":
                paper[key] = str(value or "").strip()
        paper["updated_at"] = now_iso()
        updated = paper
        break
    if updated is None:
        return None
    save_project_papers(project_id, papers)
    ensure_paper_asset_files(project_id, updated)
    return updated


def update_paper_pdf(project_id: str, paper_id: str, status: str, pdf_path: str | None = None) -> dict[str, Any] | None:
    papers = load_project_papers(project_id)
    updated: dict[str, Any] | None = None
    for paper in papers:
        if paper.get("paper_id") != paper_id:
            continue
        paper["pdf_path"] = pdf_path or paper.get("pdf_path") or paper_pdf_relative_path(paper_id)
        paper["pdf_status"] = status
        paper["updated_at"] = now_iso()
        updated = paper
        break
    if updated is None:
        return None
    save_project_papers(project_id, papers)
    ensure_paper_asset_files(project_id, updated)
    return updated


def update_paper_pdf_source(project_id: str, paper_id: str, pdf_url: str, status: str = "可下载") -> dict[str, Any] | None:
    papers = load_project_papers(project_id)
    updated: dict[str, Any] | None = None
    for paper in papers:
        if paper.get("paper_id") != paper_id:
            continue
        paper["pdf_url"] = pdf_url
        paper["pdf_status"] = status
        paper["pdf_path"] = paper.get("pdf_path") or paper_pdf_relative_path(paper_id)
        paper["updated_at"] = now_iso()
        updated = paper
        break
    if updated is None:
        return None
    save_project_papers(project_id, papers)
    ensure_paper_asset_files(project_id, updated)
    return updated


def normalize_import_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    title = str(raw.get("title") or "").strip()
    if not title:
        raise ValueError("缺少论文标题")
    authors = raw.get("authors") or []
    if isinstance(authors, str):
        authors = [item.strip() for item in re.split(r"\s*[,;/]\s*", authors) if item.strip()]
    if not isinstance(authors, list):
        authors = []
    keywords = raw.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [item.strip() for item in re.split(r"\s*[,;/]\s*", keywords) if item.strip()]
    if not isinstance(keywords, list):
        keywords = []
    year = raw.get("year") or ""
    try:
        year = int(year) if year not in ("", None) else ""
    except (TypeError, ValueError):
        year = ""
    return {
        "title": title,
        "authors": [str(item).strip() for item in authors if str(item).strip()],
        "year": year,
        "venue": str(raw.get("venue") or "").strip(),
        "doi": str(raw.get("doi") or "").strip(),
        "paper_url": str(raw.get("paper_url") or "").strip(),
        "abstract": str(raw.get("abstract") or "").strip(),
        "abstract_zh": str(raw.get("abstract_zh") or "").strip(),
        "keywords": [str(item).strip() for item in keywords if str(item).strip()][:4],
        "pdf_url": str(raw.get("pdf_url") or "").strip(),
    }


def update_import_draft(project_id: str, draft_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    drafts = load_import_drafts(project_id)
    updated = None
    for draft in drafts:
        if draft.get("draft_id") != draft_id:
            continue
        draft.update(updates)
        draft["updated_at"] = now_iso()
        updated = draft
        break
    save_import_drafts(project_id, drafts)
    return updated


def duplicate_library_paper(project_id: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
    identity = set(candidate_keys(metadata))
    if not identity:
        return None
    for paper in load_project_papers(project_id):
        if identity.intersection(candidate_keys(paper)):
            return paper
    return None


def draft_to_library_record(draft: dict[str, Any], paper_id: str) -> dict[str, Any]:
    metadata = draft.get("metadata") or {}
    has_temp_pdf = bool(draft.get("temp_pdf_path"))
    pdf_url = metadata.get("pdf_url") or ""
    return {
        "paper_id": paper_id,
        "candidate_id": "",
        "title": metadata["title"],
        "authors": metadata.get("authors") or [],
        "year": metadata.get("year") or "",
        "venue": metadata.get("venue") or "",
        "abstract": metadata.get("abstract") or "",
        "abstract_zh": metadata.get("abstract_zh") or "",
        "keywords": metadata.get("keywords") or [],
        "tags": [],
        "notes": "",
        "doi": metadata.get("doi") or "",
        "paper_url": metadata.get("paper_url") or "",
        "pdf_url": pdf_url,
        "pdf_status": "手动导入" if has_temp_pdf else ("可下载" if pdf_url else "无来源"),
        "pdf_path": paper_pdf_relative_path(paper_id),
        "bibtex_status": "未生成",
        "bibtex_path": "",
        "has_structured_reading": False,
        "structured_reading_json_path": paper_reading_relative_path(paper_id),
        "imported_at": now_iso(),
        "updated_at": now_iso(),
    }


def download_pdf_file(url: str, target: Path) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("PDF URL 必须是 http 或 https")

    request_obj = urllib.request.Request(
        url,
        headers={"User-Agent": "GuangmingAIWorkbench/0.1"},
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request_obj, timeout=45) as response:
        content_type = (response.headers.get("Content-Type") or "").lower()
        first_chunk = response.read(1024)
        if b"%PDF" not in first_chunk[:20] and "pdf" not in content_type:
            raise ValueError("下载结果不是 PDF 文件")
        with target.open("wb") as file:
            file.write(first_chunk)
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                file.write(chunk)


def download_selected_pdfs(project_id: str, paper_ids: list[str]) -> dict[str, Any]:
    selected = set(paper_ids)
    papers = load_project_papers(project_id)
    results: list[dict[str, str]] = []
    downloaded = 0
    failed = 0
    skipped = 0

    for paper in papers:
        paper_id = paper.get("paper_id")
        if paper_id not in selected:
            continue
        pdf_url = str(paper.get("pdf_url") or "").strip()
        pdf_path = paper.get("pdf_path") or paper_pdf_relative_path(paper_id)
        paper["pdf_path"] = pdf_path
        if not pdf_url:
            paper["pdf_status"] = "无来源"
            skipped += 1
            results.append({"paper_id": paper_id, "status": "skipped", "message": "没有 PDF URL"})
            continue
        try:
            download_pdf_file(pdf_url, resolve_project_file(project_id, pdf_path))
            paper["pdf_status"] = "已下载"
            paper["updated_at"] = now_iso()
            downloaded += 1
            results.append({"paper_id": paper_id, "status": "downloaded", "message": "下载成功"})
        except Exception as exc:
            paper["pdf_status"] = "下载失败"
            paper["updated_at"] = now_iso()
            failed += 1
            results.append({"paper_id": paper_id, "status": "failed", "message": str(exc)})

    save_project_papers(project_id, papers)
    for paper in papers:
        if paper.get("paper_id") in selected:
            ensure_paper_asset_files(project_id, paper)
    return {"downloaded": downloaded, "failed": failed, "skipped": skipped, "results": results}


def project_paper(project_id: str | None, paper_id: str | None) -> dict[str, Any] | None:
    if not project_id or not paper_id:
        return None
    papers = load_project_papers(project_id)
    return next((paper for paper in papers if (paper.get("paper_id") or paper.get("id")) == paper_id), None)


def common_context(active: str) -> dict[str, Any]:
    current = sidebar_project()
    return {
        "active": active,
        "project": current,
        "current_project": get_current_project(),
        "projects": list_projects(),
    }


def resolve_next_page(next_page: str | None) -> str:
    page = (next_page or "").strip()
    if page == "home":
        return "index"
    valid_pages = {"index", "search", "library", "reading", "writing", "history"}
    return page if page in valid_pages else "index"


def search_run_record_path(project_id: str, run_id: str) -> Path:
    return search_runs_dir(project_id) / f"{run_id}.json"


def finalize_search_run(project_id: str, run_id: str, search_mode: str) -> dict[str, Any]:
    run_record_path = search_run_record_path(project_id, run_id)
    run_record = read_json(run_record_path, {})
    report = merge_search_run_into_candidates(
        run_record_path=run_record_path,
        candidate_papers_path=candidate_papers_path(project_id),
    )
    run_record.update(
        {
            "search_mode": search_mode,
            "finished_at": now_iso(),
            "status": "success",
            "result_count": report.inserted_count,
            "deduplicated_count": report.updated_count,
            "total_raw": report.total_raw,
            "candidate_count": report.candidate_count,
        }
    )
    write_json(run_record_path, run_record)
    return run_record


def append_search_chat_message(project_id: str, message: dict[str, Any]) -> None:
    messages = load_search_chat(project_id)
    messages.append(message)
    save_search_chat(project_id, messages)


def task_thread_alive(project_id: str, run_id: str) -> bool:
    with SEARCH_TASK_LOCK:
        task = SEARCH_TASKS.get(project_id)
    thread = task.get("thread") if task and task.get("run_id") == run_id else None
    return bool(thread and thread.is_alive())


def parse_task_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def search_task_is_terminal(project_id: str, run_id: str) -> bool:
    return any(
        task.get("run_id") == run_id and task.get("status") in {"success", "failed"}
        for task in load_search_tasks(project_id)
    )



def finalize_search_success(
    *,
    project_id: str,
    run_id: str,
    user_request: str,
    search_mode: str = SEARCH_MODE_DEEP,
    assistant_message: str = "",
    recovered: bool = False,
) -> None:
    if search_task_is_terminal(project_id, run_id):
        return

    run_record = read_json(search_run_record_path(project_id, run_id), {})
    inserted_count = int(run_record.get("result_count") or 0)
    updated_count = int(run_record.get("deduplicated_count") or 0)
    candidate_count = int(run_record.get("candidate_count") or len(load_candidate_papers(project_id)))
    message = assistant_message or (
        f"本次检索已写入 {inserted_count} 篇候选文献，"
        f"更新去重记录 {updated_count} 条，当前候选池共 {candidate_count} 篇。"
    )
    if recovered:
        message = f"检测到后台检索已完成，已同步候选池。本次共写入 {inserted_count} 篇候选文献，当前候选池共 {candidate_count} 篇。"

    if run_record:
        run_record["assistant_message"] = message
        write_json(search_run_record_path(project_id, run_id), run_record)
    append_search_chat_message(
        project_id,
        {
            "role": "assistant",
            "content": message,
            "created_at": now_iso(),
            "run_id": run_id,
            "search_mode": search_mode,
            "search_mode_label": search_mode_title(search_mode),
            "inserted_count": inserted_count,
            "candidate_count": candidate_count,
        },
    )
    upsert_search_task(
        project_id,
        {
            "run_id": run_id,
            "user_request": user_request,
            "search_mode": search_mode,
            "search_mode_label": search_mode_title(search_mode),
            "status": "success",
            "finished_at": now_iso(),
            "inserted_count": inserted_count,
            "candidate_count": candidate_count,
            "recovered": recovered,
            "events": [
                *next(
                    (
                        task.get("events", [])
                        for task in load_search_tasks(project_id)
                        if task.get("run_id") == run_id
                    ),
                    [],
                ),
                {
                    "kind": "success",
                    "message": f"检索完成，新增 {inserted_count} 篇，当前候选池 {candidate_count} 篇。",
                    "created_at": now_iso(),
                },
            ][-30:],
        },
    )


def finalize_search_failure(
    project_id: str,
    run_id: str,
    user_request: str,
    error: str,
    search_mode: str = SEARCH_MODE_DEEP,
) -> None:
    if run_id and search_task_is_terminal(project_id, run_id):
        return

    append_search_chat_message(
        project_id,
        {
            "role": "assistant",
            "content": f"检索任务失败：{error}",
            "created_at": now_iso(),
            "run_id": run_id,
            "search_mode": search_mode,
            "search_mode_label": search_mode_title(search_mode),
            "error": True,
        },
    )
    upsert_search_task(
        project_id,
        {
            "run_id": run_id,
            "user_request": user_request,
            "search_mode": search_mode,
            "search_mode_label": search_mode_title(search_mode),
            "status": "failed",
            "finished_at": now_iso(),
            "error": error,
        },
    )


def recover_search_tasks(project_id: str) -> None:
    for task in load_search_tasks(project_id):
        if task.get("status") != "running":
            continue

        run_id = task.get("run_id")
        user_request = task.get("user_request") or ""
        search_mode = normalize_search_mode(task.get("search_mode"))
        if not run_id:
            continue

        record_path = search_run_record_path(project_id, run_id)
        if record_path.exists():
            if task_thread_alive(project_id, run_id):
                continue
            finalize_search_run(project_id, run_id, search_mode)
            finalize_search_success(
                project_id=project_id,
                run_id=run_id,
                user_request=user_request,
                search_mode=search_mode,
                recovered=True,
            )
            continue

        started_at = parse_task_time(task.get("started_at"))
        expired = bool(started_at and datetime.now() - started_at > timedelta(minutes=SEARCH_TASK_TIMEOUT_MINUTES))
        if expired and not task_thread_alive(project_id, run_id):
            finalize_search_failure(
                project_id,
                run_id,
                user_request,
                "后台检索任务超时，已自动结束，请重新发起检索。",
                search_mode=search_mode,
            )


def execute_search_task(project_id: str, run_id: str, user_request: str, search_mode: str) -> None:
    search_mode = normalize_search_mode(search_mode)
    try:
        append_search_task_event(
            project_id,
            run_id,
            f"已创建{search_mode_title(search_mode)}任务，正在调用 Codex。",
        )
        task_result = run_literature_search(
            repo_dir=BASE_DIR,
            project_dir=project_dir(project_id),
            run_id=run_id,
            user_request=user_request,
            search_mode=search_mode,
            max_results=8,
            progress=lambda message: append_search_task_event(project_id, run_id, message),
        )
        if not task_result["run_record_exists"]:
            raise RuntimeError("Codex 未生成 search_runs 结果文件。")

        if search_task_is_terminal(project_id, run_id):
            append_search_task_event(project_id, run_id, "用户已停止本次检索，丢弃迟到结果。")
            return
        finalize_search_run(project_id, run_id, search_mode)
        append_search_task_event(project_id, run_id, "检索结果已合并进 candidate_papers.json。")
        finalize_search_success(
            project_id=project_id,
            run_id=run_id,
            user_request=user_request,
            search_mode=search_mode,
            assistant_message=task_result["assistant_message"],
        )
    except Exception as exc:
        finalize_search_failure(project_id, run_id, user_request, str(exc), search_mode=search_mode)
    finally:
        with SEARCH_TASK_LOCK:
            if SEARCH_TASKS.get(project_id, {}).get("run_id") == run_id:
                SEARCH_TASKS.pop(project_id, None)


def selected_library_papers(project_id: str, paper_ids: list[str]) -> list[dict[str, Any]]:
    selected = set(paper_ids)
    return [paper for paper in load_project_papers(project_id) if paper.get("paper_id") in selected]


def append_library_chat_message(project_id: str, message: dict[str, Any]) -> None:
    messages = load_library_chat(project_id)
    messages.append(message)
    save_library_chat(project_id, messages)


def same_paper_selection(left: list[str], right: list[str]) -> bool:
    return sorted(str(item) for item in left) == sorted(str(item) for item in right)


def execute_library_chat_task(project_id: str, run_id: str, user_question: str, paper_ids: list[str]) -> None:
    try:
        papers = selected_library_papers(project_id, paper_ids)
        if not papers:
            raise RuntimeError("未选择可用于问答的文献。")

        state = load_library_chat_state(project_id)
        thread_id = state.get("thread_id")
        last_context_paper_ids = state.get("last_context_paper_ids") or []
        include_paper_context = not thread_id or not same_paper_selection(paper_ids, last_context_paper_ids)
        if include_paper_context:
            append_library_chat_task_event(project_id, run_id, "正在整理并注入勾选文献上下文。")
        else:
            append_library_chat_task_event(project_id, run_id, "勾选文献未变化，沿用当前对话线程中的文献上下文。")
        result = run_library_qa_turn(
            repo_dir=BASE_DIR,
            project_dir=project_dir(project_id),
            thread_id=thread_id,
            user_question=user_question,
            selected_papers=papers,
            include_paper_context=include_paper_context,
            progress=lambda message: append_library_chat_task_event(project_id, run_id, message),
        )
        if not library_chat_task_is_running(project_id, run_id):
            append_library_chat_task_event(project_id, run_id, "用户已停止本次问答，丢弃迟到回复。")
            return
        thread_id = result.get("thread_id") or thread_id
        save_library_chat_state(
            project_id,
            {
                "thread_id": thread_id,
                "created_at": state.get("created_at") or now_iso(),
                "updated_at": now_iso(),
                "last_context_paper_ids": paper_ids if include_paper_context else last_context_paper_ids,
            },
        )
        append_library_chat_message(
            project_id,
            {
                "role": "assistant",
                "content": result.get("assistant_message") or "知识库问答已完成，但没有返回内容。",
                "created_at": now_iso(),
                "run_id": run_id,
                "thread_id": thread_id,
                "selected_paper_ids": paper_ids,
            },
        )
        upsert_library_chat_task(
            project_id,
            {
                "run_id": run_id,
                "status": "success",
                "finished_at": now_iso(),
                "thread_id": thread_id,
                "selected_paper_ids": paper_ids,
            },
        )
    except Exception as exc:
        append_library_chat_message(
            project_id,
            {
                "role": "assistant",
                "content": f"知识库问答失败：{exc}",
                "created_at": now_iso(),
                "run_id": run_id,
                "error": True,
                "selected_paper_ids": paper_ids,
            },
        )
        upsert_library_chat_task(
            project_id,
            {
                "run_id": run_id,
                "status": "failed",
                "finished_at": now_iso(),
                "error": str(exc),
                "selected_paper_ids": paper_ids,
            },
        )
    finally:
        with LIBRARY_QA_LOCK:
            if LIBRARY_QA_TASKS.get(project_id, {}).get("run_id") == run_id:
                LIBRARY_QA_TASKS.pop(project_id, None)


def execute_reading_chat_task(
    project_id: str,
    paper_id: str,
    run_id: str,
    user_question: str,
    attachments: list[dict[str, Any]] | None = None,
) -> None:
    try:
        paper = project_paper(project_id, paper_id)
        if not paper:
            raise RuntimeError("当前论文不存在。")

        state = load_paper_reading_chat_state(project_id, paper_id)
        thread_id = state.get("thread_id")
        include_paper_context = not thread_id
        if include_paper_context:
            append_reading_chat_task_event(project_id, run_id, "正在注入当前论文上下文。")
        else:
            append_reading_chat_task_event(project_id, run_id, "沿用当前论文研读线程中的上下文。")
        result = run_reading_chat_turn(
            repo_dir=BASE_DIR,
            project_dir=project_dir(project_id),
            thread_id=thread_id,
            paper=paper,
            user_question=user_question,
            include_paper_context=include_paper_context,
            image_paths=[
                str(resolve_project_file(project_id, attachment["path"]))
                for attachment in (attachments or [])
                if attachment.get("type") == "image" and attachment.get("path")
            ],
            progress=lambda message: append_reading_chat_task_event(project_id, run_id, message),
        )
        if not reading_chat_task_is_running(project_id, run_id):
            append_reading_chat_task_event(project_id, run_id, "用户已停止本次研读问答，丢弃迟到回复。")
            return
        thread_id = result.get("thread_id") or thread_id
        save_paper_reading_chat_state(
            project_id,
            paper_id,
            {
                "thread_id": thread_id,
                "created_at": state.get("created_at") or now_iso(),
                "updated_at": now_iso(),
                "paper_id": paper_id,
            },
        )
        append_paper_reading_chat_message(
            project_id,
            paper_id,
            {
                "role": "assistant",
                "content": result.get("assistant_message") or "论文研读问答已完成，但没有返回内容。",
                "created_at": now_iso(),
                "run_id": run_id,
                "thread_id": thread_id,
                "paper_id": paper_id,
            },
        )
        upsert_reading_chat_task(
            project_id,
            {
                "run_id": run_id,
                "paper_id": paper_id,
                "status": "success",
                "finished_at": now_iso(),
                "thread_id": thread_id,
            },
        )
    except Exception as exc:
        append_paper_reading_chat_message(
            project_id,
            paper_id,
            {
                "role": "assistant",
                "content": f"论文研读问答失败：{exc}",
                "created_at": now_iso(),
                "run_id": run_id,
                "paper_id": paper_id,
                "error": True,
                "attachments": attachments or [],
            },
        )
        upsert_reading_chat_task(
            project_id,
            {
                "run_id": run_id,
                "paper_id": paper_id,
                "status": "failed",
                "finished_at": now_iso(),
                "error": str(exc),
            },
        )
    finally:
        with READING_CHAT_LOCK:
            if READING_CHAT_TASKS.get(f"{project_id}:{paper_id}", {}).get("run_id") == run_id:
                READING_CHAT_TASKS.pop(f"{project_id}:{paper_id}", None)


def execute_writing_chat_task(project_id: str, run_id: str, user_question: str, stage: str) -> None:
    try:
        ensure_writing_files(project_id)
        chat_state = load_writing_chat_state(project_id)
        thread_id = chat_state.get("thread_id")
        csv_path = writing_sources_relative_path()
        outline_path = writing_outline_relative_path()
        survey_path = writing_survey_relative_path()
        csv_hash = text_hash(resolve_project_file(project_id, csv_path).read_text(encoding="utf-8-sig"))
        outline_hash = text_hash(load_writing_outline(project_id))
        draft_hash = text_hash(load_writing_survey(project_id))
        include_context = not thread_id or chat_state.get("last_stage") != stage or chat_state.get("last_csv_hash") != csv_hash
        outline_changed = chat_state.get("last_outline_hash") != outline_hash
        draft_changed = chat_state.get("last_draft_hash") != draft_hash
        append_writing_chat_task_event(project_id, run_id, f"正在进入“{WRITING_STAGE_LABELS.get(stage, stage)}”阶段对话。")
        if stage == "mapping":
            sections = parse_outline_sections(load_writing_outline(project_id))
            paper_context = selected_writing_paper_context(project_id)
            upsert_writing_chat_task(project_id, {"run_id": run_id, "total_sections": len(sections), "completed_sections": 0, "current_section": ""})
            if not sections:
                raise RuntimeError("当前大纲没有可识别章节，请先生成并保存大纲。")
            if not paper_context:
                raise RuntimeError("当前没有已选写作文献，请先在第一阶段选择论文。")
            append_writing_chat_task_event(project_id, run_id, f"将按 {len(sections)} 个小节逐段生成小节-文献映射。")
            completed_sections = 0
            collected_count = 0
            for section in sections:
                if not writing_chat_task_is_running(project_id, run_id):
                    append_writing_chat_task_event(project_id, run_id, "用户已停止本次内容核对任务。", kind="warning")
                    return
                upsert_writing_chat_task(project_id, {"run_id": run_id, "current_section": section.get("title")})
                append_writing_chat_task_event(project_id, run_id, f"正在处理小节：{section.get('title')}")
                section_question = "\n".join(
                    [
                        "请只处理下面这个大纲小节，生成该小节需要引用的文献及小节级写作内容备注。",
                        "必须从候选论文中选择真正适合本小节的论文，不要把所有论文都塞进来。",
                        "写作备注必须针对当前小节，说明这篇论文在本小节可以写成什么具体内容；如果需要实验指标、方法细节或数据但资料不足，写入 missing_detail。",
                        "paper_dir 是论文本地资料目录，目录中可能有 PDF 和相关资料；可以读取它们补充真实细节，不能虚构。",
                        "必须在 <guangming_actions> 的 writing_mappings 数组里返回结果，section_id 必须使用给定值。",
                        "",
                        f"当前小节：{json.dumps(section, ensure_ascii=False)}",
                        f"已选论文上下文：{json.dumps(paper_context, ensure_ascii=False)}",
                        f"用户原始任务：{user_question}",
                    ]
                )
                result = run_writing_turn(
                    repo_dir=BASE_DIR,
                    project_dir=project_dir(project_id),
                    thread_id=thread_id,
                    stage=stage,
                    user_question=section_question,
                    csv_path=csv_path,
                    outline_path=outline_path,
                    survey_path=survey_path,
                    selected_topic=str(load_writing_state(project_id).get("topic") or ""),
                    include_context=include_context or completed_sections == 0,
                    outline_changed=outline_changed and completed_sections == 0,
                    draft_changed=draft_changed and completed_sections == 0,
                    progress=lambda message: append_writing_chat_task_event(project_id, run_id, message),
                )
                thread_id = result.get("thread_id") or thread_id
                result_actions = result.get("actions") or {}
                section_mappings = result_actions.get("writing_mappings") if isinstance(result_actions, dict) else []
                latest = replace_section_mappings(project_id, section, section_mappings)
                completed_sections += 1
                collected_count = len(latest)
                upsert_writing_chat_task(project_id, {"run_id": run_id, "completed_sections": completed_sections})
                append_writing_chat_task_event(project_id, run_id, f"已写入小节映射：{section.get('title')}（累计 {collected_count} 条）。")
            save_writing_chat_state(
                project_id,
                {
                    "thread_id": thread_id,
                    "created_at": chat_state.get("created_at") or now_iso(),
                    "updated_at": now_iso(),
                    "last_stage": stage,
                    "last_csv_hash": text_hash(resolve_project_file(project_id, csv_path).read_text(encoding="utf-8-sig")),
                    "last_outline_hash": text_hash(load_writing_outline(project_id)),
                    "last_draft_hash": text_hash(load_writing_survey(project_id)),
                },
            )
            append_writing_chat_message(
                project_id,
                {
                    "role": "assistant",
                    "content": f"已按当前大纲逐小节完成内容核对，并写入 `{writing_section_mappings_relative_path()}`。本次共处理 {completed_sections} 个小节，生成 {collected_count} 条小节-文献映射。左侧卡片已按小节显示每篇文献的引用角色、写作内容备注、证据细节和缺失细节。",
                    "actions": {},
                    "created_at": now_iso(),
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "stage": stage,
                },
            )
            upsert_writing_chat_task(
                project_id,
                {
                    "run_id": run_id,
                    "stage": stage,
                    "status": "success",
                    "finished_at": now_iso(),
                    "thread_id": thread_id,
                    "completed_sections": completed_sections,
                    "current_section": "",
                },
            )
            return
        result = run_writing_turn(
            repo_dir=BASE_DIR,
            project_dir=project_dir(project_id),
            thread_id=thread_id,
            stage=stage,
            user_question=user_question,
            csv_path=csv_path,
            outline_path=outline_path,
            survey_path=survey_path,
            selected_topic=str(load_writing_state(project_id).get("topic") or ""),
            include_context=include_context,
            outline_changed=outline_changed,
            draft_changed=draft_changed,
            progress=lambda message: append_writing_chat_task_event(project_id, run_id, message),
        )
        if not writing_chat_task_is_running(project_id, run_id):
            append_writing_chat_task_event(project_id, run_id, "用户已停止本次综述写作任务，丢弃迟到回复。", kind="warning")
            return
        thread_id = result.get("thread_id") or thread_id
        result_actions = result.get("actions") or {}
        latest_csv_hash = text_hash(resolve_project_file(project_id, csv_path).read_text(encoding="utf-8-sig"))
        display_actions = dict(result_actions) if isinstance(result_actions, dict) else {}
        display_actions.pop("writing_mappings", None)
        save_writing_chat_state(
            project_id,
            {
                "thread_id": thread_id,
                "created_at": chat_state.get("created_at") or now_iso(),
                "updated_at": now_iso(),
                "last_stage": stage,
                "last_csv_hash": latest_csv_hash,
                "last_outline_hash": text_hash(load_writing_outline(project_id)),
                "last_draft_hash": text_hash(load_writing_survey(project_id)),
            },
        )
        append_writing_chat_message(
            project_id,
            {
                "role": "assistant",
                "content": result.get("assistant_message") or "综述写作任务已完成，但没有返回内容。",
                "actions": display_actions,
                "created_at": now_iso(),
                "run_id": run_id,
                "thread_id": thread_id,
                "stage": stage,
            },
        )
        upsert_writing_chat_task(
            project_id,
            {
                "run_id": run_id,
                "stage": stage,
                "status": "success",
                "finished_at": now_iso(),
                "thread_id": thread_id,
            },
        )
    except Exception as exc:
        append_writing_chat_message(
            project_id,
            {
                "role": "assistant",
                "content": f"综述写作任务失败：{exc}",
                "created_at": now_iso(),
                "run_id": run_id,
                "stage": stage,
                "error": True,
            },
        )
        upsert_writing_chat_task(
            project_id,
            {
                "run_id": run_id,
                "stage": stage,
                "status": "failed",
                "finished_at": now_iso(),
                "error": str(exc),
            },
        )
    finally:
        with WRITING_LOCK:
            if WRITING_TASKS.get(project_id, {}).get("run_id") == run_id:
                WRITING_TASKS.pop(project_id, None)


def field_needs_matrix_generation(reading: dict[str, Any], field: dict[str, Any]) -> bool:
    fields = reading.get("fields") if isinstance(reading.get("fields"), dict) else {}
    current = fields.get(field["field_id"]) if isinstance(fields, dict) else None
    if not isinstance(current, dict):
        return True
    if not str(current.get("value") or "").strip():
        return True
    expected_hash = field.get("rule_hash") or field_rule_hash(field.get("rule", ""))
    current_hash = current.get("rule_hash") or field_rule_hash(str(current.get("rule") or ""))
    return current_hash != expected_hash


def target_matrix_fields_for_paper(project_id: str, paper: dict[str, Any], fields: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == "overwrite_existing":
        return fields
    paper_id = paper.get("paper_id")
    reading_path = resolve_project_file(project_id, paper.get("structured_reading_json_path") or paper_reading_relative_path(paper_id))
    reading = read_json(reading_path, {}) if reading_path.exists() else {}
    return [field for field in fields if field_needs_matrix_generation(reading, field)]


def selected_matrix_papers(project_id: str, paper_ids: list[str], fields: list[dict[str, Any]], mode: str) -> tuple[list[dict[str, Any]], int, int]:
    selected = set(paper_ids)
    eligible: list[dict[str, Any]] = []
    skipped_no_pdf = 0
    skipped_no_target = 0
    for paper in load_project_papers(project_id):
        paper_id = paper.get("paper_id")
        if paper_id not in selected:
            continue
        pdf_path = str(paper.get("pdf_path") or paper_pdf_relative_path(paper_id))
        if not pdf_path or not resolve_project_file(project_id, pdf_path).exists():
            skipped_no_pdf += 1
            continue
        target_fields = target_matrix_fields_for_paper(project_id, paper, fields, mode)
        if not target_fields:
            skipped_no_target += 1
            continue
        item = {**paper, "_target_matrix_fields": target_fields}
        eligible.append(item)
    return eligible, skipped_no_pdf, skipped_no_target


def execute_reading_matrix_task(project_id: str, run_id: str, paper_ids: list[str], mode: str) -> None:
    completed = 0
    failed = 0
    try:
        fields = [field for field in load_reading_matrix_fields(project_id) if field.get("enabled", True)]
        papers, skipped_no_pdf, skipped_no_target = selected_matrix_papers(project_id, paper_ids, fields, mode)
        upsert_reading_matrix_task(
            project_id,
            {
                "run_id": run_id,
                "status": "running",
                "total": len(papers),
                "completed": 0,
                "failed": 0,
                "skipped_no_pdf": skipped_no_pdf,
                "skipped_no_target": skipped_no_target,
            },
        )
        if skipped_no_pdf:
            append_reading_matrix_task_event(project_id, run_id, f"已跳过 {skipped_no_pdf} 篇未下载 PDF 的论文。", kind="warning")
        if skipped_no_target:
            append_reading_matrix_task_event(project_id, run_id, f"已跳过 {skipped_no_target} 篇没有待补字段的论文。")
        if not fields:
            raise RuntimeError("当前没有可用的文献矩阵字段。")
        if not papers:
            append_reading_matrix_task_event(project_id, run_id, "没有可处理论文：请确认已勾选论文且本地 PDF 已下载。", kind="warning")
            upsert_reading_matrix_task(
                project_id,
                {
                    "run_id": run_id,
                    "status": "success",
                    "finished_at": now_iso(),
                    "completed": 0,
                    "failed": 0,
                    "current_paper_id": "",
                },
            )
            return

        for index, paper in enumerate(papers, start=1):
            if not reading_matrix_task_is_running(project_id, run_id):
                append_reading_matrix_task_event(project_id, run_id, "用户已停止文献矩阵任务。", kind="warning")
                return
            paper_id = paper["paper_id"]
            pdf_path = str(paper.get("pdf_path") or paper_pdf_relative_path(paper_id))
            upsert_reading_matrix_task(
                project_id,
                {
                    "run_id": run_id,
                    "current_paper_id": paper_id,
                    "current_title": paper.get("title") or "",
                    "completed": completed,
                    "failed": failed,
                },
            )
            append_reading_matrix_task_event(project_id, run_id, f"正在生成第 {index}/{len(papers)} 篇：{paper.get('title') or paper_id}")
            try:
                target_fields = paper.get("_target_matrix_fields") or fields
                append_reading_matrix_task_event(project_id, run_id, f"本篇仅处理 {len(target_fields)} 个待补字段。")
                result = run_reading_matrix_for_paper(
                    repo_dir=BASE_DIR,
                    project_dir=project_dir(project_id),
                    paper=paper,
                    fields=target_fields,
                    pdf_path=pdf_path,
                    progress=lambda message: append_reading_matrix_task_event(project_id, run_id, message),
                )
                if not reading_matrix_task_is_running(project_id, run_id):
                    append_reading_matrix_task_event(project_id, run_id, "用户已停止文献矩阵任务，丢弃迟到结果。", kind="warning")
                    return
                save_generated_reading(project_id, paper, target_fields, result)
                completed += 1
                append_reading_matrix_task_event(project_id, run_id, f"已完成：{paper.get('title') or paper_id}")
            except Exception as exc:
                failed += 1
                append_reading_matrix_task_event(project_id, run_id, f"生成失败：{paper.get('title') or paper_id}，原因：{exc}", kind="error")
            upsert_reading_matrix_task(
                project_id,
                {
                    "run_id": run_id,
                    "completed": completed,
                    "failed": failed,
                },
            )

        upsert_reading_matrix_task(
            project_id,
            {
                "run_id": run_id,
                "status": "success" if failed == 0 else "failed",
                "finished_at": now_iso(),
                "completed": completed,
                "failed": failed,
                "current_paper_id": "",
            },
        )
    except Exception as exc:
        append_reading_matrix_task_event(project_id, run_id, f"文献矩阵任务失败：{exc}", kind="error")
        upsert_reading_matrix_task(
            project_id,
            {
                "run_id": run_id,
                "status": "failed",
                "finished_at": now_iso(),
                "completed": completed,
                "failed": failed,
                "error": str(exc),
            },
        )
    finally:
        with READING_MATRIX_LOCK:
            if READING_MATRIX_TASKS.get(project_id, {}).get("run_id") == run_id:
                READING_MATRIX_TASKS.pop(project_id, None)


def execute_import_task(project_id: str, run_id: str) -> None:
    completed = 0
    failed = 0
    try:
        drafts = [draft for draft in load_import_drafts(project_id) if draft.get("run_id") == run_id]
        upsert_import_task(
            project_id,
            {
                "run_id": run_id,
                "status": "running",
                "total": len(drafts),
                "completed": 0,
                "failed": 0,
            },
        )
        if not drafts:
            append_import_task_event(project_id, run_id, "没有需要解析的导入项。", kind="warning")

        for index, draft in enumerate(drafts, start=1):
            if not import_task_is_running(project_id, run_id):
                append_import_task_event(project_id, run_id, "用户已停止导入补全任务。", kind="warning")
                return
            draft_id = str(draft.get("draft_id") or "")
            label = draft.get("raw_input") or draft.get("filename") or draft_id
            update_import_draft(project_id, draft_id, {"status": "running", "error": ""})
            upsert_import_task(
                project_id,
                {
                    "run_id": run_id,
                    "current_draft_id": draft_id,
                    "current_label": label,
                    "completed": completed,
                    "failed": failed,
                },
            )
            append_import_task_event(project_id, run_id, f"正在解析第 {index}/{len(drafts)} 项：{label}")
            try:
                temp_pdf_path = str(draft.get("temp_pdf_path") or "")
                result = run_import_resolution(
                    repo_dir=BASE_DIR,
                    project_dir=project_dir(project_id),
                    draft=draft,
                    pdf_path=temp_pdf_path,
                    progress=lambda message: append_import_task_event(project_id, run_id, message),
                )
                if result.get("status") == "failed":
                    raise ValueError(str(result.get("error") or "Codex 未能解析该文献"))
                metadata = normalize_import_metadata(result)
                duplicate = duplicate_library_paper(project_id, metadata)
                updates: dict[str, Any] = {
                    "metadata": metadata,
                    "status": "ready",
                    "error": "",
                    "duplicate_of_paper_id": "",
                    "duplicate_action": "",
                }
                if duplicate:
                    updates["duplicate_of_paper_id"] = duplicate.get("paper_id") or ""
                    existing_pdf = duplicate.get("pdf_status") in {"已下载", "手动导入"} and duplicate.get("pdf_path")
                    if temp_pdf_path and not existing_pdf:
                        updates["duplicate_action"] = "attach_pdf"
                        updates["status"] = "ready"
                    else:
                        updates["status"] = "duplicate"
                        updates["error"] = "知识库中已存在该文献"
                update_import_draft(project_id, draft_id, updates)
                completed += 1
                append_import_task_event(project_id, run_id, f"已解析：{metadata['title']}")
            except Exception as exc:
                failed += 1
                update_import_draft(project_id, draft_id, {"status": "failed", "error": str(exc)})
                append_import_task_event(project_id, run_id, f"解析失败：{label}，原因：{exc}", kind="error")
            upsert_import_task(
                project_id,
                {
                    "run_id": run_id,
                    "completed": completed,
                    "failed": failed,
                },
            )

        status = "stopped" if not import_task_is_running(project_id, run_id) else "success"
        upsert_import_task(
            project_id,
            {
                "run_id": run_id,
                "status": status,
                "finished_at": now_iso(),
                "completed": completed,
                "failed": failed,
                "current_draft_id": "",
            },
        )
    except Exception as exc:
        append_import_task_event(project_id, run_id, f"导入补全任务失败：{exc}", kind="error")
        upsert_import_task(
            project_id,
            {
                "run_id": run_id,
                "status": "failed",
                "finished_at": now_iso(),
                "completed": completed,
                "failed": failed,
                "error": str(exc),
            },
        )
    finally:
        with IMPORT_LOCK:
            if IMPORT_TASKS.get(project_id, {}).get("run_id") == run_id:
                IMPORT_TASKS.pop(project_id, None)


def execute_pdf_lookup_task(project_id: str, run_id: str, selected_paper_ids: list[str]) -> None:
    completed = 0
    failed = 0
    skipped = 0
    try:
        selected = set(selected_paper_ids)
        papers = [
            paper
            for paper in load_project_papers(project_id)
            if paper.get("paper_id") in selected
        ]
        pending: list[dict[str, Any]] = []
        for paper in papers:
            status = str(paper.get("pdf_status") or "").strip()
            if status != "无来源":
                skipped += 1
                continue
            pending.append(paper)

        upsert_pdf_lookup_task(
            project_id,
            {
                "run_id": run_id,
                "status": "running",
                "total": len(pending),
                "completed": 0,
                "failed": 0,
                "skipped": skipped,
            },
        )
        if skipped:
            append_pdf_lookup_task_event(project_id, run_id, f"已跳过 {skipped} 篇非“无来源”状态的论文。")
        if not pending:
            append_pdf_lookup_task_event(project_id, run_id, "没有需要查找 PDF 来源的论文。", kind="warning")

        for index, paper in enumerate(pending, start=1):
            if not pdf_lookup_task_is_running(project_id, run_id):
                append_pdf_lookup_task_event(project_id, run_id, "用户已停止 PDF 查找任务。", kind="warning")
                return
            paper_id = str(paper.get("paper_id") or "")
            title = paper.get("title") or paper_id
            upsert_pdf_lookup_task(
                project_id,
                {
                    "run_id": run_id,
                    "current_paper_id": paper_id,
                    "current_title": title,
                    "completed": completed,
                    "failed": failed,
                    "skipped": skipped,
                },
            )
            append_pdf_lookup_task_event(project_id, run_id, f"正在查找第 {index}/{len(pending)} 篇：{title}")
            try:
                result = resolve_open_pdf_url(paper)
                update_paper_pdf_source(project_id, paper_id, result.pdf_url)
                completed += 1
                append_pdf_lookup_task_event(project_id, run_id, f"已找到开放 PDF：{title}（{result.source}）")
            except OpenPdfNotFoundError as exc:
                failed += 1
                append_pdf_lookup_task_event(project_id, run_id, f"未找到开放 PDF：{title}，原因：{exc}", kind="warning")
            except Exception as exc:
                failed += 1
                append_pdf_lookup_task_event(project_id, run_id, f"查找失败：{title}，原因：{exc}", kind="error")
            upsert_pdf_lookup_task(
                project_id,
                {
                    "run_id": run_id,
                    "completed": completed,
                    "failed": failed,
                    "skipped": skipped,
                },
            )

        status = "stopped" if not pdf_lookup_task_is_running(project_id, run_id) else "success"
        upsert_pdf_lookup_task(
            project_id,
            {
                "run_id": run_id,
                "status": status,
                "finished_at": now_iso(),
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "current_paper_id": "",
            },
        )
    except Exception as exc:
        append_pdf_lookup_task_event(project_id, run_id, f"PDF 查找任务失败：{exc}", kind="error")
        upsert_pdf_lookup_task(
            project_id,
            {
                "run_id": run_id,
                "status": "failed",
                "finished_at": now_iso(),
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "error": str(exc),
            },
        )
    finally:
        with PDF_LOOKUP_LOCK:
            if PDF_LOOKUP_TASKS.get(project_id, {}).get("run_id") == run_id:
                PDF_LOOKUP_TASKS.pop(project_id, None)


def execute_pdf_download_task(project_id: str, run_id: str, selected_paper_ids: list[str]) -> None:
    completed = 0
    failed = 0
    skipped = 0
    try:
        selected = set(selected_paper_ids)
        all_papers = load_project_papers(project_id)
        papers = [paper for paper in all_papers if paper.get("paper_id") in selected]
        pending: list[dict[str, Any]] = []
        for paper in papers:
            status = str(paper.get("pdf_status") or "").strip()
            if status != "可下载":
                skipped += 1
                continue
            if not str(paper.get("pdf_url") or "").strip():
                skipped += 1
                continue
            pending.append(paper)

        upsert_pdf_download_task(
            project_id,
            {
                "run_id": run_id,
                "status": "running",
                "total": len(pending),
                "completed": 0,
                "failed": 0,
                "skipped": skipped,
            },
        )
        if skipped:
            append_pdf_download_task_event(project_id, run_id, f"已跳过 {skipped} 篇非“可下载”状态或缺少 PDF URL 的论文。")
        if not pending:
            append_pdf_download_task_event(project_id, run_id, "没有可下载的勾选论文。", kind="warning")

        papers_by_id = {paper.get("paper_id"): paper for paper in all_papers}
        for index, paper in enumerate(pending, start=1):
            if not pdf_download_task_is_running(project_id, run_id):
                append_pdf_download_task_event(project_id, run_id, "用户已停止 PDF 下载任务。", kind="warning")
                return
            paper_id = str(paper.get("paper_id") or "")
            title = paper.get("title") or paper_id
            pdf_url = str(paper.get("pdf_url") or "").strip()
            pdf_path = str(paper.get("pdf_path") or paper_pdf_relative_path(paper_id))
            upsert_pdf_download_task(
                project_id,
                {
                    "run_id": run_id,
                    "current_paper_id": paper_id,
                    "current_title": title,
                    "completed": completed,
                    "failed": failed,
                    "skipped": skipped,
                },
            )
            append_pdf_download_task_event(project_id, run_id, f"正在下载第 {index}/{len(pending)} 篇：{title}")
            try:
                download_pdf_file(pdf_url, resolve_project_file(project_id, pdf_path))
                current = papers_by_id.get(paper_id)
                if current:
                    current["pdf_path"] = pdf_path
                    current["pdf_status"] = "已下载"
                    current["updated_at"] = now_iso()
                completed += 1
                append_pdf_download_task_event(project_id, run_id, f"已下载：{title}")
            except Exception as exc:
                current = papers_by_id.get(paper_id)
                if current:
                    current["pdf_status"] = "下载失败"
                    current["updated_at"] = now_iso()
                failed += 1
                append_pdf_download_task_event(project_id, run_id, f"下载失败：{title}，原因：{exc}", kind="error")
            save_project_papers(project_id, all_papers)
            if paper_id:
                ensure_paper_asset_files(project_id, papers_by_id.get(paper_id, paper))
            upsert_pdf_download_task(
                project_id,
                {
                    "run_id": run_id,
                    "completed": completed,
                    "failed": failed,
                    "skipped": skipped,
                },
            )

        status = "stopped" if not pdf_download_task_is_running(project_id, run_id) else "success"
        upsert_pdf_download_task(
            project_id,
            {
                "run_id": run_id,
                "status": status,
                "finished_at": now_iso(),
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "current_paper_id": "",
            },
        )
    except Exception as exc:
        append_pdf_download_task_event(project_id, run_id, f"PDF 下载任务失败：{exc}", kind="error")
        upsert_pdf_download_task(
            project_id,
            {
                "run_id": run_id,
                "status": "failed",
                "finished_at": now_iso(),
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "error": str(exc),
            },
        )
    finally:
        with PDF_DOWNLOAD_LOCK:
            if PDF_DOWNLOAD_TASKS.get(project_id, {}).get("run_id") == run_id:
                PDF_DOWNLOAD_TASKS.pop(project_id, None)


def execute_bibtex_task(project_id: str, run_id: str, selected_paper_ids: list[str]) -> None:
    completed = 0
    failed = 0
    skipped = 0
    export_path = ""
    try:
        papers = load_project_papers(project_id)
        pending = [
            paper
            for paper in papers
            if paper.get("bibtex_status") not in {"已生成", "手动导入"}
        ]
        upsert_bibtex_task(
            project_id,
            {
                "run_id": run_id,
                "status": "running",
                "total": len(pending),
                "completed": 0,
                "failed": 0,
                "skipped": 0,
            },
        )
        if not pending:
            append_bibtex_task_event(project_id, run_id, "所有文献 BibTeX 均已生成，直接执行导出。")

        for index, paper in enumerate(pending, start=1):
            if not bibtex_task_is_running(project_id, run_id):
                append_bibtex_task_event(project_id, run_id, "用户已停止 BibTeX 任务。", kind="warning")
                return
            paper_id = paper.get("paper_id")
            title = paper.get("title") or paper_id
            upsert_bibtex_task(
                project_id,
                {
                    "run_id": run_id,
                    "current_paper_id": paper_id,
                    "current_title": title,
                    "completed": completed,
                    "failed": failed,
                    "skipped": skipped,
                },
            )
            append_bibtex_task_event(project_id, run_id, f"正在补全第 {index}/{len(pending)} 篇：{title}")
            if not bibtex_identifier(paper):
                skipped += 1
                update_paper_bibtex_status(project_id, paper_id, "无来源", "")
                append_bibtex_task_event(project_id, run_id, f"跳过：{title} 缺少可用 DOI、arXiv、链接或标题。", kind="warning")
                continue
            try:
                bibtex = fetch_bibtex_for_paper(paper)
                path = write_paper_bibtex(project_id, paper_id, bibtex)
                update_paper_bibtex_status(project_id, paper_id, "已生成", path)
                completed += 1
                append_bibtex_task_event(project_id, run_id, f"已生成 BibTeX：{title}")
            except Exception as exc:
                failed += 1
                update_paper_bibtex_status(project_id, paper_id, "生成失败", "")
                append_bibtex_task_event(project_id, run_id, f"生成失败：{title}，原因：{exc}", kind="error")
            upsert_bibtex_task(
                project_id,
                {
                    "run_id": run_id,
                    "completed": completed,
                    "failed": failed,
                    "skipped": skipped,
                },
            )

        if not bibtex_task_is_running(project_id, run_id):
            append_bibtex_task_event(project_id, run_id, "用户已停止 BibTeX 任务，取消导出。", kind="warning")
            return
        export_path, exported_count = export_selected_bibtex(project_id, run_id, selected_paper_ids)
        append_bibtex_task_event(project_id, run_id, f"已打包导出 {exported_count} 条 BibTeX。")
        upsert_bibtex_task(
            project_id,
            {
                "run_id": run_id,
                "status": "success",
                "finished_at": now_iso(),
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "export_path": export_path,
                "exported_count": exported_count,
                "current_paper_id": "",
            },
        )
    except Exception as exc:
        append_bibtex_task_event(project_id, run_id, f"BibTeX 任务失败：{exc}", kind="error")
        upsert_bibtex_task(
            project_id,
            {
                "run_id": run_id,
                "status": "failed",
                "finished_at": now_iso(),
                "completed": completed,
                "failed": failed,
                "skipped": skipped,
                "export_path": export_path,
                "error": str(exc),
            },
        )
    finally:
        with BIBTEX_LOCK:
            if BIBTEX_TASKS.get(project_id, {}).get("run_id") == run_id:
                BIBTEX_TASKS.pop(project_id, None)


def delete_project_workspace(project_id: str) -> None:
    root = project_dir(project_id)
    if not root.exists():
        return
    shutil.rmtree(root)


@app.context_processor
def inject_asset_helpers() -> dict[str, Any]:
    return {"asset_url": asset_url, "tag_style": tag_style}


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/")
def index():
    recent_projects = list_projects()[:4]
    return render_template(
        "index.html",
        recent_projects=recent_projects,
        **common_context("home"),
    )


@app.route("/projects/create", methods=["POST"])
def create_project():
    name = request.form.get("name", "").strip()
    topic_detail = request.form.get("topic_detail", "").strip()
    if not name:
        name = "untitled-project"
    if not topic_detail:
        topic_detail = name

    project = create_project_workspace(name, topic_detail)
    session["current_project_id"] = project["id"]
    return redirect(url_for("search"))


@app.route("/projects/<project_id>/activate", methods=["POST"])
def activate_project(project_id: str):
    if load_project(project_id):
        session["current_project_id"] = project_id
    next_page = resolve_next_page(request.form.get("next_page"))
    return redirect(url_for(next_page))


@app.route("/projects/<project_id>/delete", methods=["POST"])
def delete_project(project_id: str):
    delete_project_workspace(project_id)
    if session.get("current_project_id") == project_id:
        session.pop("current_project_id", None)
    next_page = resolve_next_page(request.form.get("next_page"))
    return redirect(url_for(next_page))


@app.route("/search")
def search():
    current = get_current_project()
    if current:
        recover_search_tasks(current["id"])
    query = request.args.get("q", "").strip()
    candidates = load_candidate_papers(current["id"]) if current else []
    results = filter_candidate_papers(candidates, query)
    demo_results = catalog_search(query) if not candidates else []
    imported_ids = {paper.get("candidate_id") for paper in load_project_papers(current["id"])} if current else set()
    search_tasks = load_search_tasks(current["id"]) if current else []
    active_search_task = next((task for task in reversed(search_tasks) if task.get("status") == "running"), None)
    task_input_value = (
        active_search_task.get("user_request")
        if active_search_task
        else query or (current.get("description") if current else "")
    )
    return render_template(
        "search.html",
        query=query,
        results=results,
        demo_results=demo_results,
        chat_messages=load_search_chat(current["id"]) if current else [],
        active_search_task=active_search_task,
        task_input_value=task_input_value,
        imported_ids=imported_ids,
        **common_context("search"),
    )


@app.route("/projects/<project_id>/search/run", methods=["POST"])
def run_search_task(project_id: str):
    current = load_project(project_id)
    if not current:
        return redirect(url_for("index"))

    if project_has_running_search(project_id):
        session["current_project_id"] = project_id
        return redirect(url_for("search"))

    search_mode = normalize_search_mode(request.form.get("search_mode"))
    user_request = request.form.get("user_request", "").strip() or current.get("topic") or current.get("name")
    run_id = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    messages = load_search_chat(project_id)
    messages.append(
        {
            "role": "user",
            "content": user_request,
            "created_at": now_iso(),
            "search_mode": search_mode,
            "search_mode_label": search_mode_title(search_mode),
        }
    )
    save_search_chat(project_id, messages)
    upsert_search_task(
        project_id,
        {
            "run_id": run_id,
            "user_request": user_request,
            "search_mode": search_mode,
            "search_mode_label": search_mode_title(search_mode),
            "status": "running",
            "started_at": now_iso(),
        },
    )

    thread = threading.Thread(
        target=execute_search_task,
        args=(project_id, run_id, user_request, search_mode),
        daemon=True,
    )
    with SEARCH_TASK_LOCK:
        SEARCH_TASKS[project_id] = {"run_id": run_id, "thread": thread}
    thread.start()

    session["current_project_id"] = project_id
    return redirect(url_for("search"))


@app.route("/projects/<project_id>/search/status")
def search_task_status(project_id: str):
    recover_search_tasks(project_id)
    tasks = load_search_tasks(project_id)
    latest = tasks[-1] if tasks else None
    return jsonify(
        {
            "running": bool(latest and latest.get("status") == "running"),
            "latest": latest,
            "candidate_count": len(load_candidate_papers(project_id)),
            "message_count": len(load_search_chat(project_id)),
        }
    )


@app.route("/projects/<project_id>/search/reset", methods=["POST"])
def reset_search_task(project_id: str):
    tasks = load_search_tasks(project_id)
    latest = tasks[-1] if tasks else None
    if latest and latest.get("status") == "running":
        finalize_search_failure(
            project_id,
            latest.get("run_id", ""),
            latest.get("user_request", ""),
            "用户已手动停止本次检索任务。",
            search_mode=normalize_search_mode(latest.get("search_mode")),
        )
    session["current_project_id"] = project_id
    return redirect(url_for("search"))


@app.route("/projects/<project_id>/papers/import", methods=["POST"])
def import_paper(project_id: str):
    paper_id = request.form.get("paper_id", "")
    query = request.form.get("query", "")
    if paper_id.startswith("cand-"):
        imported = import_candidate_to_project(project_id, paper_id)
    else:
        imported = import_paper_to_project(project_id, paper_id)
    session["current_project_id"] = project_id

    if request.headers.get("Accept") == "application/json" or request.headers.get("X-Requested-With") == "fetch":
        project = load_project(project_id)
        return jsonify(
            {
                "ok": imported or True,
                "imported": imported,
                "paper_id": paper_id,
                "status": "已导入",
                "stats": project.get("stats", {}) if project else {},
            }
        )

    return redirect(url_for("search", q=query))


@app.route("/projects/<project_id>/papers/remove", methods=["POST"])
def remove_paper(project_id: str):
    paper_id = request.form.get("paper_id", "")
    candidate_id = request.form.get("candidate_id", "")
    query = request.form.get("query", "")
    removed = remove_paper_from_library(project_id, paper_id=paper_id, candidate_id=candidate_id)
    session["current_project_id"] = project_id

    if request.headers.get("Accept") == "application/json" or request.headers.get("X-Requested-With") == "fetch":
        project = load_project(project_id)
        return jsonify(
            {
                "ok": removed or True,
                "removed": removed,
                "paper_id": paper_id,
                "candidate_id": candidate_id,
                "status": "未导入",
                "stats": project.get("stats", {}) if project else {},
            }
        )

    return redirect(url_for("search", q=query))


@app.route("/projects/<project_id>/papers/<paper_id>/update", methods=["POST"])
def update_paper(project_id: str, paper_id: str):
    payload = request.get_json(silent=True) or request.form.to_dict(flat=False)
    updates: dict[str, Any] = {}
    if "tags" in payload:
        tags = payload.get("tags")
        if isinstance(tags, str):
            updates["tags"] = [item.strip() for item in tags.split(",") if item.strip()]
        elif isinstance(tags, list):
            updates["tags"] = tags
    if "notes" in payload:
        notes = payload.get("notes")
        updates["notes"] = notes[0] if isinstance(notes, list) else notes

    updated = update_library_paper(project_id, paper_id, updates)
    if updated is None:
        return jsonify({"ok": False, "error": "paper not found"}), 404
    return jsonify({"ok": True, "paper": updated, "project_tags": load_project_tags(project_id)})


@app.route("/projects/<project_id>/tags/delete", methods=["POST"])
def delete_tag(project_id: str):
    payload = request.get_json(silent=True) or request.form
    tag = str(payload.get("tag") or "").strip()
    deleted = delete_project_tag(project_id, tag)
    return jsonify(
        {
            "ok": deleted,
            "tag": tag,
            "project_tags": load_project_tags(project_id),
            "papers": load_project_papers(project_id),
        }
    )


@app.route("/projects/<project_id>/papers/download-pdfs", methods=["POST"])
def download_pdfs(project_id: str):
    payload = request.get_json(silent=True) or {}
    paper_ids = payload.get("paper_ids") if isinstance(payload, dict) else []
    if not isinstance(paper_ids, list):
        paper_ids = []
    report = download_selected_pdfs(project_id, [str(item) for item in paper_ids])
    project = load_project(project_id)
    return jsonify({"ok": True, **report, "stats": project.get("stats", {}) if project else {}})


def clear_import_workspace(project_id: str) -> None:
    save_import_drafts(project_id, [])
    imports_root = project_dir(project_id) / "imports"
    if imports_root.exists():
        shutil.rmtree(imports_root)
    imports_root.mkdir(parents=True, exist_ok=True)


@app.route("/projects/<project_id>/imports/run", methods=["POST"])
def run_imports(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    if project_has_running_import(project_id):
        return jsonify({"ok": False, "error": "当前已有导入补全任务正在运行"}), 409

    lines_text = request.form.get("lines_text", "")
    lines = [line.strip() for line in lines_text.splitlines() if line.strip()]
    uploads = request.files.getlist("pdf_files")
    uploads = [item for item in uploads if item and item.filename]
    if not lines and not uploads:
        return jsonify({"ok": False, "error": "请至少输入一行题名/DOI，或上传一个 PDF"}), 400

    clear_import_workspace(project_id)
    run_id = f"import-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    run_dir = import_run_dir(project_id, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    drafts: list[dict[str, Any]] = []

    for line in lines:
        draft_id = f"draft-{uuid4().hex[:10]}"
        drafts.append(
            {
                "draft_id": draft_id,
                "run_id": run_id,
                "input_type": "text",
                "raw_input": line,
                "filename": "",
                "temp_pdf_path": "",
                "status": "pending",
                "metadata": {},
                "error": "",
                "duplicate_of_paper_id": "",
                "duplicate_action": "",
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )

    for upload in uploads:
        if not upload.filename.lower().endswith(".pdf"):
            continue
        draft_id = f"draft-{uuid4().hex[:10]}"
        filename = Path(upload.filename).name
        temp_relative = f"imports/{safe_project_slug(run_id)}/{draft_id}.pdf"
        target = resolve_project_file(project_id, temp_relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        upload.save(target)
        drafts.append(
            {
                "draft_id": draft_id,
                "run_id": run_id,
                "input_type": "pdf",
                "raw_input": filename,
                "filename": filename,
                "temp_pdf_path": temp_relative,
                "status": "pending",
                "metadata": {},
                "error": "",
                "duplicate_of_paper_id": "",
                "duplicate_action": "",
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )

    if not drafts:
        return jsonify({"ok": False, "error": "没有可处理的 PDF 文件"}), 400

    save_import_drafts(project_id, drafts)
    upsert_import_task(
        project_id,
        {
            "run_id": run_id,
            "status": "running",
            "total": len(drafts),
            "completed": 0,
            "failed": 0,
            "events": [],
            "started_at": now_iso(),
        },
    )
    thread = threading.Thread(target=execute_import_task, args=(project_id, run_id), daemon=True)
    with IMPORT_LOCK:
        IMPORT_TASKS[project_id] = {"run_id": run_id, "thread": thread}
    thread.start()
    return jsonify({"ok": True, "run_id": run_id, "task": load_import_tasks(project_id)[-1], "drafts": drafts})


@app.route("/projects/<project_id>/imports/status")
def import_status(project_id: str):
    tasks = load_import_tasks(project_id)
    latest = tasks[-1] if tasks else None
    return jsonify(
        {
            "ok": True,
            "running": bool(latest and latest.get("status") == "running"),
            "latest": latest,
            "drafts": load_import_drafts(project_id),
        }
    )


@app.route("/projects/<project_id>/imports/confirm", methods=["POST"])
def confirm_imports(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    payload = request.get_json(silent=True) or {}
    selected_ids = {str(item) for item in payload.get("draft_ids") or []}
    drafts = load_import_drafts(project_id)
    papers = load_project_papers(project_id)
    imported = 0
    attached = 0
    skipped = 0

    for draft in drafts:
        if draft.get("draft_id") not in selected_ids:
            continue
        if draft.get("status") != "ready":
            skipped += 1
            continue
        temp_pdf_path = str(draft.get("temp_pdf_path") or "")
        duplicate_id = str(draft.get("duplicate_of_paper_id") or "")
        if duplicate_id and draft.get("duplicate_action") == "attach_pdf" and temp_pdf_path:
            target_pdf = paper_pdf_relative_path(duplicate_id)
            source = resolve_project_file(project_id, temp_pdf_path)
            target = resolve_project_file(project_id, target_pdf)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.exists():
                shutil.move(str(source), str(target))
                for paper in papers:
                    if paper.get("paper_id") == duplicate_id:
                        paper["pdf_path"] = target_pdf
                        paper["pdf_status"] = "手动导入"
                        paper["updated_at"] = now_iso()
                        ensure_paper_asset_files(project_id, paper)
                        break
                attached += 1
            else:
                skipped += 1
            continue

        metadata = draft.get("metadata") or {}
        if duplicate_library_paper(project_id, metadata):
            skipped += 1
            continue
        paper_id = f"paper-{uuid4().hex[:10]}"
        record = draft_to_library_record(draft, paper_id)
        if temp_pdf_path:
            source = resolve_project_file(project_id, temp_pdf_path)
            target = resolve_project_file(project_id, record["pdf_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.exists():
                shutil.move(str(source), str(target))
        papers.append(record)
        ensure_paper_asset_files(project_id, record)
        imported += 1

    save_project_papers(project_id, papers)
    clear_import_workspace(project_id)
    project = load_project(project_id)
    return jsonify(
        {
            "ok": True,
            "imported": imported,
            "attached": attached,
            "skipped": skipped,
            "papers": load_project_papers(project_id),
            "stats": project.get("stats", {}) if project else {},
        }
    )


@app.route("/projects/<project_id>/imports/cancel", methods=["POST"])
def cancel_imports(project_id: str):
    tasks = load_import_tasks(project_id)
    latest = tasks[-1] if tasks else None
    if latest and latest.get("status") == "running":
        run_id = latest.get("run_id", "")
        append_import_task_event(project_id, run_id, "用户已取消导入任务。", kind="warning")
        upsert_import_task(project_id, {"run_id": run_id, "status": "stopped", "finished_at": now_iso()})
    clear_import_workspace(project_id)
    return jsonify({"ok": True, "drafts": []})


@app.route("/projects/<project_id>/pdf-lookup/run", methods=["POST"])
def run_pdf_lookup(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    if project_has_running_pdf_lookup(project_id):
        return jsonify({"ok": False, "error": "当前已有 PDF 查找任务正在运行"}), 409
    payload = request.get_json(silent=True) or {}
    paper_ids = [str(item) for item in payload.get("paper_ids") or [] if str(item).strip()]
    if not paper_ids:
        return jsonify({"ok": False, "error": "请先勾选文献"}), 400

    run_id = f"pdf-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    upsert_pdf_lookup_task(
        project_id,
        {
            "run_id": run_id,
            "status": "running",
            "selected_paper_ids": paper_ids,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "events": [],
            "started_at": now_iso(),
        },
    )
    thread = threading.Thread(
        target=execute_pdf_lookup_task,
        args=(project_id, run_id, paper_ids),
        daemon=True,
    )
    with PDF_LOOKUP_LOCK:
        PDF_LOOKUP_TASKS[project_id] = {"run_id": run_id, "thread": thread}
    thread.start()
    return jsonify({"ok": True, "run_id": run_id, "task": load_pdf_lookup_tasks(project_id)[-1]})


@app.route("/projects/<project_id>/pdf-lookup/status")
def pdf_lookup_status(project_id: str):
    tasks = load_pdf_lookup_tasks(project_id)
    latest = tasks[-1] if tasks else None
    return jsonify(
        {
            "ok": True,
            "running": bool(latest and latest.get("status") == "running"),
            "latest": latest,
            "papers": load_project_papers(project_id),
        }
    )


@app.route("/projects/<project_id>/pdf-lookup/stop", methods=["POST"])
def stop_pdf_lookup(project_id: str):
    tasks = load_pdf_lookup_tasks(project_id)
    latest = tasks[-1] if tasks else None
    if latest and latest.get("status") == "running":
        run_id = latest.get("run_id", "")
        append_pdf_lookup_task_event(project_id, run_id, "用户已停止 PDF 查找任务。", kind="warning")
        upsert_pdf_lookup_task(
            project_id,
            {
                "run_id": run_id,
                "status": "stopped",
                "finished_at": now_iso(),
            },
        )
    tasks = load_pdf_lookup_tasks(project_id)
    return jsonify({"ok": True, "latest": tasks[-1] if tasks else None, "papers": load_project_papers(project_id)})


@app.route("/projects/<project_id>/pdf-download/run", methods=["POST"])
def run_pdf_download(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    if project_has_running_pdf_download(project_id):
        return jsonify({"ok": False, "error": "当前已有 PDF 下载任务正在运行"}), 409
    payload = request.get_json(silent=True) or {}
    paper_ids = [str(item) for item in payload.get("paper_ids") or [] if str(item).strip()]
    if not paper_ids:
        return jsonify({"ok": False, "error": "请先勾选文献"}), 400

    run_id = f"pdfdl-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    upsert_pdf_download_task(
        project_id,
        {
            "run_id": run_id,
            "status": "running",
            "selected_paper_ids": paper_ids,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "events": [],
            "started_at": now_iso(),
        },
    )
    thread = threading.Thread(
        target=execute_pdf_download_task,
        args=(project_id, run_id, paper_ids),
        daemon=True,
    )
    with PDF_DOWNLOAD_LOCK:
        PDF_DOWNLOAD_TASKS[project_id] = {"run_id": run_id, "thread": thread}
    thread.start()
    return jsonify({"ok": True, "run_id": run_id, "task": load_pdf_download_tasks(project_id)[-1]})


@app.route("/projects/<project_id>/pdf-download/status")
def pdf_download_status(project_id: str):
    tasks = load_pdf_download_tasks(project_id)
    latest = tasks[-1] if tasks else None
    return jsonify(
        {
            "ok": True,
            "running": bool(latest and latest.get("status") == "running"),
            "latest": latest,
            "papers": load_project_papers(project_id),
        }
    )


@app.route("/projects/<project_id>/pdf-download/stop", methods=["POST"])
def stop_pdf_download(project_id: str):
    tasks = load_pdf_download_tasks(project_id)
    latest = tasks[-1] if tasks else None
    if latest and latest.get("status") == "running":
        run_id = latest.get("run_id", "")
        append_pdf_download_task_event(project_id, run_id, "用户已停止 PDF 下载任务。", kind="warning")
        upsert_pdf_download_task(
            project_id,
            {
                "run_id": run_id,
                "status": "stopped",
                "finished_at": now_iso(),
            },
        )
    tasks = load_pdf_download_tasks(project_id)
    return jsonify({"ok": True, "latest": tasks[-1] if tasks else None, "papers": load_project_papers(project_id)})


@app.route("/projects/<project_id>/bibtex/run", methods=["POST"])
def run_bibtex_export(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    if project_has_running_bibtex(project_id):
        return jsonify({"ok": False, "error": "已有 BibTeX 任务正在运行"}), 409
    payload = request.get_json(silent=True) or {}
    paper_ids = payload.get("paper_ids") if isinstance(payload, dict) else []
    if not isinstance(paper_ids, list):
        paper_ids = []
    paper_ids = [str(item).strip() for item in paper_ids if str(item).strip()]
    if not paper_ids:
        return jsonify({"ok": False, "error": "请先勾选要导出的文献"}), 400
    run_id = f"bibtex-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    upsert_bibtex_task(
        project_id,
        {
            "run_id": run_id,
            "status": "running",
            "selected_paper_ids": paper_ids,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "started_at": now_iso(),
        },
    )
    thread = threading.Thread(
        target=execute_bibtex_task,
        args=(project_id, run_id, paper_ids),
        daemon=True,
    )
    with BIBTEX_LOCK:
        BIBTEX_TASKS[project_id] = {"run_id": run_id, "thread": thread}
    thread.start()
    return jsonify({"ok": True, "run_id": run_id, "task": load_bibtex_tasks(project_id)[-1]})


@app.route("/projects/<project_id>/bibtex/status")
def bibtex_status(project_id: str):
    tasks = load_bibtex_tasks(project_id)
    latest = tasks[-1] if tasks else None
    download_url = ""
    if latest and latest.get("export_path"):
        download_url = url_for("download_bibtex_export", project_id=project_id, run_id=latest["run_id"])
    return jsonify(
        {
            "running": bool(latest and latest.get("status") == "running"),
            "latest": latest,
            "download_url": download_url,
            "papers": load_project_papers(project_id),
        }
    )


@app.route("/projects/<project_id>/bibtex/stop", methods=["POST"])
def stop_bibtex_export(project_id: str):
    tasks = load_bibtex_tasks(project_id)
    latest = tasks[-1] if tasks else None
    if latest and latest.get("status") == "running":
        run_id = latest.get("run_id", "")
        append_bibtex_task_event(project_id, run_id, "用户已停止 BibTeX 任务。", kind="warning")
        upsert_bibtex_task(
            project_id,
            {
                "run_id": run_id,
                "status": "stopped",
                "finished_at": now_iso(),
                "current_paper_id": "",
            },
        )
    tasks = load_bibtex_tasks(project_id)
    return jsonify({"ok": True, "latest": tasks[-1] if tasks else None})


@app.route("/projects/<project_id>/bibtex/download/<run_id>")
def download_bibtex_export(project_id: str, run_id: str):
    task = next((item for item in load_bibtex_tasks(project_id) if item.get("run_id") == run_id), None)
    if not task or not task.get("export_path"):
        abort(404)
    try:
        export_path = resolve_project_file(project_id, str(task["export_path"]))
    except ValueError:
        abort(404)
    if not export_path.exists():
        abort(404)
    return send_file(export_path, as_attachment=True, download_name=f"{run_id}.bib", mimetype="application/x-bibtex")


@app.route("/projects/<project_id>/papers/<paper_id>/pdf/upload", methods=["POST"])
def upload_pdf(project_id: str, paper_id: str):
    upload = request.files.get("pdf_file")
    if not upload or not upload.filename:
        return jsonify({"ok": False, "error": "未选择 PDF 文件"}), 400
    if not upload.filename.lower().endswith(".pdf"):
        return jsonify({"ok": False, "error": "只允许上传 PDF 文件"}), 400

    pdf_path = paper_pdf_relative_path(paper_id)
    target = resolve_project_file(project_id, pdf_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    upload.save(target)
    updated = update_paper_pdf(project_id, paper_id, "手动导入", pdf_path)
    if updated is None:
        target.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": "paper not found"}), 404
    return jsonify({"ok": True, "paper": updated})


@app.route("/projects/<project_id>/papers/<paper_id>/pdf")
def open_pdf(project_id: str, paper_id: str):
    paper = project_paper(project_id, paper_id)
    if not paper or not paper.get("pdf_path"):
        abort(404)
    try:
        pdf_path = resolve_project_file(project_id, str(paper["pdf_path"]))
    except ValueError:
        abort(404)
    if not pdf_path.is_file():
        abort(404)
    return send_file(pdf_path)


@app.route("/projects/<project_id>/papers/<paper_id>/reading-assets/<filename>")
def reading_chat_asset(project_id: str, paper_id: str, filename: str):
    if not load_project(project_id) or not project_paper(project_id, paper_id):
        abort(404)
    safe_name = Path(filename).name
    if safe_name != filename:
        abort(404)
    try:
        image_path = resolve_project_file(project_id, f"{paper_reading_assets_relative_dir(paper_id)}/{safe_name}")
    except ValueError:
        abort(404)
    if not image_path.is_file():
        abort(404)
    return send_file(image_path)


@app.route("/projects/<project_id>/library-chat/run", methods=["POST"])
def run_library_chat(project_id: str):
    current = load_project(project_id)
    if not current:
        return redirect(url_for("index"))
    wants_json = request.is_json or request.headers.get("Accept") == "application/json" or request.headers.get("X-Requested-With") == "fetch"
    if project_has_running_library_chat(project_id):
        session["current_project_id"] = project_id
        if wants_json:
            return jsonify({"ok": False, "error": "已有知识库问答正在运行"}), 409
        return redirect(url_for("library"))

    payload = request.get_json(silent=True) if request.is_json else None
    if isinstance(payload, dict):
        user_question = str(payload.get("user_question") or "").strip()
        raw_paper_ids = payload.get("paper_ids") or []
        paper_ids = [str(item).strip() for item in raw_paper_ids if str(item).strip()] if isinstance(raw_paper_ids, list) else []
    else:
        user_question = request.form.get("user_question", "").strip()
        paper_ids = [item.strip() for item in request.form.getlist("paper_ids") if item.strip()]
    if not user_question or not paper_ids:
        session["current_project_id"] = project_id
        if wants_json:
            return jsonify({"ok": False, "error": "请先勾选文献并输入问题"}), 400
        return redirect(url_for("library"))

    run_id = f"libqa-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    user_message = {
        "role": "user",
        "content": user_question,
        "created_at": now_iso(),
        "run_id": run_id,
        "selected_paper_ids": paper_ids,
    }
    append_library_chat_message(
        project_id,
        user_message,
    )
    upsert_library_chat_task(
        project_id,
        {
            "run_id": run_id,
            "user_question": user_question,
            "selected_paper_ids": paper_ids,
            "status": "running",
            "started_at": now_iso(),
        },
    )

    thread = threading.Thread(
        target=execute_library_chat_task,
        args=(project_id, run_id, user_question, paper_ids),
        daemon=True,
    )
    with LIBRARY_QA_LOCK:
        LIBRARY_QA_TASKS[project_id] = {"run_id": run_id, "thread": thread}
    thread.start()

    session["current_project_id"] = project_id
    if wants_json:
        return jsonify({"ok": True, "run_id": run_id, "user_message": user_message, "messages": load_library_chat(project_id)})
    return redirect(url_for("library"))


@app.route("/projects/<project_id>/library-chat/status")
def library_chat_status(project_id: str):
    tasks = load_library_chat_tasks(project_id)
    latest = tasks[-1] if tasks else None
    messages = load_library_chat(project_id)
    return jsonify(
        {
            "running": bool(latest and latest.get("status") == "running"),
            "latest": latest,
            "messages": messages,
            "message_count": len(messages),
        }
    )


@app.route("/projects/<project_id>/library-chat/stop", methods=["POST"])
def stop_library_chat(project_id: str):
    current = load_project(project_id)
    if not current:
        return redirect(url_for("index"))
    wants_json = request.is_json or request.headers.get("Accept") == "application/json" or request.headers.get("X-Requested-With") == "fetch"
    tasks = load_library_chat_tasks(project_id)
    latest = tasks[-1] if tasks else None
    if latest and latest.get("status") == "running":
        run_id = latest.get("run_id", "")
        paper_ids = latest.get("selected_paper_ids") or []
        append_library_chat_task_event(project_id, run_id, "用户已停止本次知识库问答。", kind="warning")
        upsert_library_chat_task(
            project_id,
            {
                "run_id": run_id,
                "status": "stopped",
                "finished_at": now_iso(),
                "selected_paper_ids": paper_ids,
            },
        )
        append_library_chat_message(
            project_id,
            {
                "role": "assistant",
                "content": "已停止本次知识库问答。当前对话线程会保留，下一次可以继续提问；如果想清空记忆，请点击“重置”。",
                "created_at": now_iso(),
                "run_id": run_id,
                "selected_paper_ids": paper_ids,
                "stopped": True,
            },
        )
    session["current_project_id"] = project_id
    if wants_json:
        return jsonify({"ok": True, "messages": load_library_chat(project_id), "latest": latest})
    return redirect(url_for("library"))


@app.route("/projects/<project_id>/library-chat/reset", methods=["POST"])
def reset_library_chat(project_id: str):
    current = load_project(project_id)
    if not current:
        return redirect(url_for("index"))
    wants_json = request.is_json or request.headers.get("Accept") == "application/json" or request.headers.get("X-Requested-With") == "fetch"
    if project_has_running_library_chat(project_id):
        if wants_json:
            return jsonify({"ok": False, "error": "当前有知识库问答正在运行，请完成后再重置"}), 409
        return redirect(url_for("library"))

    divider = {
        "role": "divider",
        "content": "新的对话",
        "created_at": now_iso(),
    }
    save_library_chat_state(project_id, {})
    append_library_chat_message(project_id, divider)
    session["current_project_id"] = project_id
    if wants_json:
        return jsonify({"ok": True, "divider": divider, "messages": load_library_chat(project_id)})
    return redirect(url_for("library"))


@app.route("/projects/<project_id>/papers/<paper_id>/reading-chat/run", methods=["POST"])
def run_reading_chat(project_id: str, paper_id: str):
    current = load_project(project_id)
    paper = project_paper(project_id, paper_id)
    if not current or not paper:
        return jsonify({"ok": False, "error": "paper not found"}), 404
    if project_has_running_reading_chat(project_id, paper_id):
        return jsonify({"ok": False, "error": "当前论文已有研读问答正在运行"}), 409

    payload = request.get_json(silent=True) if request.is_json else None
    user_question = (
        str(payload.get("user_question") or "").strip()
        if isinstance(payload, dict)
        else request.form.get("user_question", "").strip()
    )
    run_id = f"readqa-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    attachments = save_reading_chat_uploads(project_id, paper_id, run_id, request.files.getlist("images"))
    if not user_question and not attachments:
        return jsonify({"ok": False, "error": "请输入研读问题或附加截图"}), 400
    user_message = {
        "role": "user",
        "content": user_question or "请根据我附加的图片回答。",
        "created_at": now_iso(),
        "run_id": run_id,
        "paper_id": paper_id,
        "attachments": attachments,
    }
    append_paper_reading_chat_message(project_id, paper_id, user_message)
    upsert_reading_chat_task(
        project_id,
        {
            "run_id": run_id,
            "paper_id": paper_id,
            "user_question": user_message["content"],
            "status": "running",
            "started_at": now_iso(),
            "attachment_count": len(attachments),
        },
    )

    thread = threading.Thread(
        target=execute_reading_chat_task,
        args=(project_id, paper_id, run_id, user_message["content"], attachments),
        daemon=True,
    )
    with READING_CHAT_LOCK:
        READING_CHAT_TASKS[f"{project_id}:{paper_id}"] = {"run_id": run_id, "thread": thread}
    thread.start()
    session["current_project_id"] = project_id
    return jsonify(
        {
            "ok": True,
            "run_id": run_id,
            "user_message": serialize_reading_chat_messages(project_id, paper_id, [user_message])[0],
            "messages": serialize_reading_chat_messages(project_id, paper_id, load_paper_reading_chat(project_id, paper_id)),
        }
    )


@app.route("/projects/<project_id>/papers/<paper_id>/reading-chat/status")
def reading_chat_status(project_id: str, paper_id: str):
    tasks = [task for task in load_reading_chat_tasks(project_id) if task.get("paper_id") == paper_id]
    latest = tasks[-1] if tasks else None
    messages = load_paper_reading_chat(project_id, paper_id)
    return jsonify(
        {
            "running": bool(latest and latest.get("status") == "running"),
            "latest": latest,
            "messages": serialize_reading_chat_messages(project_id, paper_id, messages),
            "message_count": len(messages),
        }
    )


@app.route("/projects/<project_id>/papers/<paper_id>/reading-chat/stop", methods=["POST"])
def stop_reading_chat(project_id: str, paper_id: str):
    if not load_project(project_id) or not project_paper(project_id, paper_id):
        return jsonify({"ok": False, "error": "paper not found"}), 404
    tasks = [task for task in load_reading_chat_tasks(project_id) if task.get("paper_id") == paper_id]
    latest = tasks[-1] if tasks else None
    if latest and latest.get("status") == "running":
        run_id = latest.get("run_id", "")
        append_reading_chat_task_event(project_id, run_id, "用户已停止本次论文研读问答。", kind="warning")
        upsert_reading_chat_task(
            project_id,
            {
                "run_id": run_id,
                "paper_id": paper_id,
                "status": "stopped",
                "finished_at": now_iso(),
            },
        )
        append_paper_reading_chat_message(
            project_id,
            paper_id,
            {
                "role": "assistant",
                "content": "已停止本次论文研读问答。当前论文对话记忆会保留，下一次可以继续提问；如果想清空记忆，请点击“重置”。",
                "created_at": now_iso(),
                "run_id": run_id,
                "paper_id": paper_id,
                "stopped": True,
            },
        )
    return jsonify(
        {
            "ok": True,
            "messages": serialize_reading_chat_messages(project_id, paper_id, load_paper_reading_chat(project_id, paper_id)),
            "latest": latest,
        }
    )


@app.route("/projects/<project_id>/papers/<paper_id>/reading-chat/reset", methods=["POST"])
def reset_reading_chat(project_id: str, paper_id: str):
    if not load_project(project_id) or not project_paper(project_id, paper_id):
        return jsonify({"ok": False, "error": "paper not found"}), 404
    if project_has_running_reading_chat(project_id, paper_id):
        return jsonify({"ok": False, "error": "当前有论文研读问答正在运行，请完成后再重置"}), 409

    divider = {
        "role": "divider",
        "content": "新的对话",
        "created_at": now_iso(),
    }
    save_paper_reading_chat_state(project_id, paper_id, {})
    append_paper_reading_chat_message(project_id, paper_id, divider)
    return jsonify(
        {
            "ok": True,
            "divider": divider,
            "messages": serialize_reading_chat_messages(project_id, paper_id, load_paper_reading_chat(project_id, paper_id)),
        }
    )


@app.route("/projects/<project_id>/writing/stage", methods=["POST"])
def update_writing_stage(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    payload = request.get_json(silent=True) or {}
    stage = normalize_writing_stage(str(payload.get("stage") or ""))
    state = ensure_writing_files(project_id)
    state["current_stage"] = stage
    state["updated_at"] = now_iso()
    save_writing_state(project_id, state)
    return jsonify({"ok": True, "state": state})


@app.route("/projects/<project_id>/writing/selection", methods=["POST"])
def update_writing_selection(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    payload = request.get_json(silent=True) or {}
    paper_ids = payload.get("paper_ids") if isinstance(payload.get("paper_ids"), list) else []
    valid = {paper.get("paper_id") for paper in load_project_papers(project_id)}
    selected = [str(item) for item in paper_ids if str(item) in valid]
    active_paper_id = str(payload.get("active_paper_id") or "")
    state = ensure_writing_files(project_id)
    state["selected_paper_ids"] = selected
    state["active_matrix_paper_id"] = active_paper_id if active_paper_id in valid else (selected[0] if selected else "")
    state["updated_at"] = now_iso()
    save_writing_state(project_id, state)
    refresh_writing_csv(project_id)
    return jsonify({"ok": True, "state": load_writing_state(project_id), "csv_path": writing_sources_relative_path()})


@app.route("/projects/<project_id>/writing/outline", methods=["POST"])
def update_writing_outline(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    payload = request.get_json(silent=True) or {}
    outline = str(payload.get("outline") or "")
    save_writing_outline(project_id, outline)
    return jsonify({"ok": True, "outline": outline, "sections": parse_outline_sections(outline), "mapping": writing_mapping_payload(project_id)})


@app.route("/projects/<project_id>/writing/mappings", methods=["POST"])
def update_writing_mappings(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    payload = request.get_json(silent=True) or {}
    mappings = payload.get("mappings") if isinstance(payload.get("mappings"), list) else []
    sections_by_id = {section["section_id"]: section for section in parse_outline_sections(load_writing_outline(project_id))}
    paper_lookup = {str(paper.get("paper_id")): paper for paper in selected_writing_papers(project_id)}
    normalized: list[dict[str, Any]] = []
    for item in mappings:
        if not isinstance(item, dict):
            continue
        section = sections_by_id.get(str(item.get("section_id") or ""))
        if not section:
            continue
        row = normalize_section_mapping(project_id, section, item, paper_lookup=paper_lookup)
        if row:
            normalized.append(row)
    save_writing_section_mappings(project_id, normalized)
    return jsonify({"ok": True, "state": load_writing_state(project_id), "csv_path": writing_sources_relative_path(), "mapping": writing_mapping_payload(project_id)})


@app.route("/projects/<project_id>/writing/draft", methods=["POST"])
def update_writing_draft(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    payload = request.get_json(silent=True) or {}
    markdown = str(payload.get("markdown") or "")
    save_writing_survey(project_id, markdown)
    return jsonify({"ok": True, "markdown": markdown})


@app.route("/projects/<project_id>/writing/topic", methods=["POST"])
def update_writing_topic(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    payload = request.get_json(silent=True) or {}
    topic = str(payload.get("topic") or "").strip()
    if not topic:
        return jsonify({"ok": False, "error": "请选择或输入综述主题"}), 400
    state = ensure_writing_files(project_id)
    state["topic"] = topic
    state["updated_at"] = now_iso()
    save_writing_state(project_id, state)
    append_writing_chat_message(
        project_id,
        {
            "role": "divider",
            "content": f"已选择主题：{topic}",
            "created_at": now_iso(),
        },
    )
    return jsonify({"ok": True, "topic": topic, "state": load_writing_state(project_id), "messages": load_writing_chat(project_id)})


@app.route("/projects/<project_id>/writing/download/markdown")
def download_writing_markdown(project_id: str):
    if not load_project(project_id):
        abort(404)
    ensure_writing_files(project_id)
    path = resolve_project_file(project_id, writing_survey_relative_path())
    return send_file(path, as_attachment=True, download_name="survey.md", mimetype="text/markdown")


@app.route("/projects/<project_id>/writing/download/csv")
def download_writing_csv(project_id: str):
    if not load_project(project_id):
        abort(404)
    ensure_writing_files(project_id)
    path = resolve_project_file(project_id, writing_sources_relative_path())
    return send_file(path, as_attachment=True, download_name="writing_sources.csv", mimetype="text/csv")


@app.route("/projects/<project_id>/writing-chat/run", methods=["POST"])
def run_writing_chat(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    if project_has_running_writing_chat(project_id):
        return jsonify({"ok": False, "error": "已有综述写作任务正在运行"}), 409
    payload = request.get_json(silent=True) or {}
    stage = normalize_writing_stage(str(payload.get("stage") or ""))
    user_question = str(payload.get("user_question") or "").strip()
    if not user_question:
        return jsonify({"ok": False, "error": "请输入综述写作问题"}), 400
    state = ensure_writing_files(project_id)
    state["current_stage"] = stage
    save_writing_state(project_id, state)
    run_id = f"write-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    user_message = {
        "role": "user",
        "content": user_question,
        "created_at": now_iso(),
        "run_id": run_id,
        "stage": stage,
    }
    append_writing_chat_message(project_id, user_message)
    upsert_writing_chat_task(
        project_id,
        {
            "run_id": run_id,
            "stage": stage,
            "user_question": user_question,
            "status": "running",
            "started_at": now_iso(),
        },
    )
    thread = threading.Thread(
        target=execute_writing_chat_task,
        args=(project_id, run_id, user_question, stage),
        daemon=True,
    )
    with WRITING_LOCK:
        WRITING_TASKS[project_id] = {"run_id": run_id, "thread": thread}
    thread.start()
    return jsonify({"ok": True, "run_id": run_id, "user_message": user_message, "messages": load_writing_chat(project_id)})


@app.route("/projects/<project_id>/writing-chat/status")
def writing_chat_status(project_id: str):
    tasks = load_writing_chat_tasks(project_id)
    latest = tasks[-1] if tasks else None
    project_exists = bool(load_project(project_id))
    mapping = writing_mapping_payload(project_id) if project_exists else {"sections": [], "papers": [], "state": {}}
    return jsonify(
        {
            "running": bool(latest and latest.get("status") == "running"),
            "latest": latest,
            "messages": load_writing_chat(project_id),
            "outline": load_writing_outline(project_id) if project_exists else "",
            "draft": load_writing_survey(project_id) if project_exists else "",
            "mapping": mapping,
        }
    )


@app.route("/projects/<project_id>/writing-chat/stop", methods=["POST"])
def stop_writing_chat(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    tasks = load_writing_chat_tasks(project_id)
    latest = tasks[-1] if tasks else None
    if latest and latest.get("status") == "running":
        run_id = latest.get("run_id", "")
        stage = latest.get("stage") or "topic"
        append_writing_chat_task_event(project_id, run_id, "用户已停止本次综述写作任务。", kind="warning")
        upsert_writing_chat_task(project_id, {"run_id": run_id, "stage": stage, "status": "stopped", "finished_at": now_iso()})
        append_writing_chat_message(
            project_id,
            {
                "role": "assistant",
                "content": "已停止本次综述写作任务。当前对话记忆会保留，下一次可继续；如需清空记忆，请点击“重置”。",
                "created_at": now_iso(),
                "run_id": run_id,
                "stage": stage,
                "stopped": True,
            },
        )
    return jsonify({"ok": True, "messages": load_writing_chat(project_id), "latest": latest})


@app.route("/projects/<project_id>/writing-chat/reset", methods=["POST"])
def reset_writing_chat(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    if project_has_running_writing_chat(project_id):
        return jsonify({"ok": False, "error": "当前有综述写作任务正在运行，请完成后再重置"}), 409
    divider = {"role": "divider", "content": "新的对话", "created_at": now_iso()}
    save_writing_chat_state(project_id, {})
    append_writing_chat_message(project_id, divider)
    return jsonify({"ok": True, "divider": divider, "messages": load_writing_chat(project_id)})


@app.route("/projects/<project_id>/reading-matrix/fields", methods=["POST"])
def update_reading_matrix_fields(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    payload = request.get_json(silent=True) or {}
    fields = payload.get("fields")
    if not isinstance(fields, list):
        return jsonify({"ok": False, "error": "fields must be a list"}), 400
    cleaned: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        field_id = str(field.get("field_id") or safe_project_slug(name).lower() or f"field_{index + 1}").strip()
        base_id = field_id
        suffix = 2
        while field_id in used_ids:
            field_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(field_id)
        cleaned.append(
            {
                "field_id": field_id,
                "name": name,
                "rule": str(field.get("rule") or "").strip(),
                "enabled": bool(field.get("enabled", True)),
                "created_at": field.get("created_at") or now_iso(),
                "updated_at": now_iso(),
            }
        )
    if not cleaned:
        return jsonify({"ok": False, "error": "至少保留一个文献矩阵字段"}), 400
    fields = save_reading_matrix_fields(project_id, cleaned, migrate=True)
    return jsonify({"ok": True, "fields": fields})


@app.route("/projects/<project_id>/reading-matrix/recommend-fields", methods=["POST"])
def recommend_reading_matrix_fields(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    payload = request.get_json(silent=True) or {}
    paper_ids = payload.get("paper_ids") if isinstance(payload, dict) else []
    if not isinstance(paper_ids, list):
        paper_ids = []
    selected = {str(item).strip() for item in paper_ids if str(item).strip()}
    papers = load_project_papers(project_id)
    context_papers = [paper for paper in papers if paper.get("paper_id") in selected] if selected else papers
    if not context_papers:
        return jsonify({"ok": False, "error": "当前知识库没有可用于推荐字段的论文"}), 400
    try:
        fields = recommend_matrix_fields(
            repo_dir=BASE_DIR,
            project_dir=project_dir(project_id),
            papers=context_papers,
            existing_fields=load_reading_matrix_fields(project_id),
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": f"AI 推荐字段失败：{exc}"}), 500
    return jsonify({"ok": True, "fields": fields, "source_count": len(context_papers)})


@app.route("/projects/<project_id>/reading-matrix/run", methods=["POST"])
def run_reading_matrix(project_id: str):
    if not load_project(project_id):
        return jsonify({"ok": False, "error": "project not found"}), 404
    if project_has_running_reading_matrix(project_id):
        return jsonify({"ok": False, "error": "已有文献矩阵任务正在运行"}), 409
    payload = request.get_json(silent=True) or {}
    paper_ids = payload.get("paper_ids") if isinstance(payload, dict) else []
    if not isinstance(paper_ids, list):
        paper_ids = []
    paper_ids = [str(item).strip() for item in paper_ids if str(item).strip()]
    mode = str(payload.get("mode") or "skip_existing")
    if mode not in {"skip_existing", "overwrite_existing"}:
        mode = "skip_existing"
    if not paper_ids:
        return jsonify({"ok": False, "error": "请先勾选文献"}), 400

    run_id = f"matrix-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    upsert_reading_matrix_task(
        project_id,
        {
            "run_id": run_id,
            "status": "running",
            "selected_paper_ids": paper_ids,
            "mode": mode,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "current_paper_id": "",
            "started_at": now_iso(),
        },
    )
    thread = threading.Thread(
        target=execute_reading_matrix_task,
        args=(project_id, run_id, paper_ids, mode),
        daemon=True,
    )
    with READING_MATRIX_LOCK:
        READING_MATRIX_TASKS[project_id] = {"run_id": run_id, "thread": thread}
    thread.start()
    return jsonify({"ok": True, "run_id": run_id, "task": load_reading_matrix_tasks(project_id)[-1]})


@app.route("/projects/<project_id>/reading-matrix/status")
def reading_matrix_status(project_id: str):
    tasks = load_reading_matrix_tasks(project_id)
    latest = tasks[-1] if tasks else None
    return jsonify(
        {
            "running": bool(latest and latest.get("status") == "running"),
            "latest": latest,
            "fields": load_reading_matrix_fields(project_id),
            "papers": load_project_papers(project_id),
        }
    )


@app.route("/projects/<project_id>/reading-matrix/stop", methods=["POST"])
def stop_reading_matrix(project_id: str):
    tasks = load_reading_matrix_tasks(project_id)
    latest = tasks[-1] if tasks else None
    if latest and latest.get("status") == "running":
        run_id = latest.get("run_id", "")
        append_reading_matrix_task_event(project_id, run_id, "用户已停止文献矩阵任务。", kind="warning")
        upsert_reading_matrix_task(
            project_id,
            {
                "run_id": run_id,
                "status": "stopped",
                "finished_at": now_iso(),
                "current_paper_id": "",
            },
        )
    tasks = load_reading_matrix_tasks(project_id)
    return jsonify({"ok": True, "latest": tasks[-1] if tasks else None, "tasks": tasks})


@app.route("/library")
def library():
    current = get_current_project()
    rows = load_project_papers(current["id"]) if current else []
    project_tags = (
        clean_tags([*load_project_tags(current["id"]), *collect_custom_tags_from_papers(rows)])
        if current
        else []
    )
    return render_template(
        "library.html",
        rows=rows,
        builtin_tags=BUILTIN_TAGS,
        project_tags=project_tags,
        reading_matrix_fields=load_reading_matrix_fields(current["id"]) if current else [],
        active_bibtex_task=next(
            (
                task
                for task in reversed(load_bibtex_tasks(current["id"]))
                if task.get("status") == "running"
            ),
            None,
        )
        if current
        else None,
        active_pdf_lookup_task=next(
            (
                task
                for task in reversed(load_pdf_lookup_tasks(current["id"]))
                if task.get("status") == "running"
            ),
            None,
        )
        if current
        else None,
        active_pdf_download_task=next(
            (
                task
                for task in reversed(load_pdf_download_tasks(current["id"]))
                if task.get("status") == "running"
            ),
            None,
        )
        if current
        else None,
        import_drafts=load_import_drafts(current["id"]) if current else [],
        active_import_task=next(
            (
                task
                for task in reversed(load_import_tasks(current["id"]))
                if task.get("status") == "running"
            ),
            None,
        )
        if current
        else None,
        active_reading_matrix_task=next(
            (
                task
                for task in reversed(load_reading_matrix_tasks(current["id"]))
                if task.get("status") == "running"
            ),
            None,
        )
        if current
        else None,
        library_chat_messages=load_library_chat(current["id"]) if current else [],
        active_library_chat_task=next(
            (
                task
                for task in reversed(load_library_chat_tasks(current["id"]))
                if task.get("status") == "running"
            ),
            None,
        )
        if current
        else None,
        **common_context("library"),
    )


@app.route("/reading")
def reading():
    current = get_current_project()
    selected_id = request.args.get("paper_id")
    papers = load_project_papers(current["id"]) if current else []
    paper = project_paper(current["id"], selected_id) if current and selected_id else (papers[0] if papers else None)
    matrix_fields = load_reading_matrix_fields(current["id"]) if current else []
    reading_record = load_paper_reading(current["id"], paper.get("paper_id")) if current and paper else {}
    reading_values = reading_record.get("fields") if isinstance(reading_record.get("fields"), dict) else {}
    paper_id = paper.get("paper_id") if paper else ""
    pdf_exists = False
    if current and paper:
        try:
            pdf_exists = resolve_project_file(current["id"], str(paper.get("pdf_path") or paper_pdf_relative_path(paper_id))).exists()
        except ValueError:
            pdf_exists = False
    active_reading_chat_task = None
    if current and paper:
        active_reading_chat_task = next(
            (
                task
                for task in reversed(load_reading_chat_tasks(current["id"]))
                if task.get("paper_id") == paper_id and task.get("status") == "running"
            ),
            None,
        )
    return render_template(
        "reading.html",
        paper=paper,
        papers=papers,
        matrix_fields=matrix_fields,
        reading_values=reading_values,
        pdf_exists=pdf_exists,
        reading_chat_messages=serialize_reading_chat_messages(
            current["id"],
            paper_id,
            load_paper_reading_chat(current["id"], paper_id),
        )
        if current and paper
        else [],
        active_reading_chat_task=active_reading_chat_task,
        **common_context("reading"),
    )


@app.route("/writing")
def writing():
    current = get_current_project()
    papers = load_project_papers(current["id"]) if current else []
    state = ensure_writing_files(current["id"]) if current else {}
    requested_stage = request.args.get("stage")
    if current and requested_stage:
        state["current_stage"] = normalize_writing_stage(requested_stage)
        save_writing_state(current["id"], state)
    selected_ids = set(state.get("selected_paper_ids") or [])
    matrix_fields = load_reading_matrix_fields(current["id"]) if current else []
    matrix_by_paper = {
        paper.get("paper_id"): paper_reading_values(current["id"], paper.get("paper_id"))
        for paper in papers
        if current and paper.get("paper_id")
    }
    outline_text = load_writing_outline(current["id"]) if current else ""
    survey_text = load_writing_survey(current["id"]) if current else ""
    active_task = (
        next((task for task in reversed(load_writing_chat_tasks(current["id"])) if task.get("status") == "running"), None)
        if current
        else None
    )
    return render_template(
        "writing.html",
        papers=papers,
        selected_paper_ids=selected_ids,
        writing_state=state,
        writing_stages=WRITING_STAGES,
        writing_stage_labels=WRITING_STAGE_LABELS,
        matrix_fields=matrix_fields,
        matrix_by_paper=matrix_by_paper,
        writing_mapping=writing_mapping_payload(current["id"]) if current else {"sections": [], "papers": [], "mappings": []},
        outline_text=outline_text,
        outline_sections=parse_outline_sections(outline_text),
        survey_text=survey_text,
        writing_chat_messages=load_writing_chat(current["id"]) if current else [],
        active_writing_task=active_task,
        csv_path=writing_sources_relative_path(),
        outline_path=writing_outline_relative_path(),
        survey_path=writing_survey_relative_path(),
        **common_context("writing"),
    )


@app.route("/history")
def history():
    current = get_current_project()
    papers = load_project_papers(current["id"]) if current else []
    events = []
    if current:
        events.append({"name": "创建项目", "detail": current["created_at"]})
    for paper in papers[:5]:
        events.append({"name": "导入文献", "detail": f"{paper['title']} · {paper['imported_at']}"})
    return render_template(
        "history.html",
        events=events,
        **common_context("history"),
    )


if __name__ == "__main__":
    ensure_workspace()
    app.run(debug=True, use_reloader=False, host="127.0.0.1", port=5000)
