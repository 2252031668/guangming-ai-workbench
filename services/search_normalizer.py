from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CANDIDATE_IMPORT_STATUS = "未导入"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_doi(value: Any) -> str:
    doi = normalize_text(value).lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi


def extract_arxiv_id(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    if text.lower().startswith("arxiv:"):
        text = text.split(":", 1)[1].strip()
    doi_match = re.search(r"10\.48550/arxiv\.([^?#\s]+)", text, flags=re.I)
    if doi_match:
        text = doi_match.group(1)
    url_match = re.search(r"arxiv\.org/(?:abs|pdf|html)/([^?#\s]+)", text, flags=re.I)
    if url_match:
        text = url_match.group(1)
    text = re.sub(r"\.pdf$", "", text, flags=re.I)
    if re.match(r"^\d{4}\.\d+(?:v\d+)?$", text):
        return re.sub(r"v\d+$", "", text)
    return ""


def normalize_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", normalize_text(value).lower()).strip()


def normalize_url(value: Any) -> str:
    url = normalize_text(value).lower()
    if not url:
        return ""
    arxiv_id = extract_arxiv_id(url)
    if arxiv_id:
        return f"https://arxiv.org/abs/{arxiv_id}"
    return url.rstrip("/")


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            text = normalize_text(item)
            if text:
                items.append(text)
        return items
    if isinstance(value, str):
        parts = re.split(r"\s*;\s*|\s*,\s*", value)
        return [part for part in (normalize_text(item) for item in parts) if part]
    text = normalize_text(value)
    return [text] if text else []


def as_int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def candidate_key(item: dict[str, Any]) -> str:
    keys = candidate_keys(item)
    return keys[0] if keys else "title:|year:0"


def candidate_keys(item: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    doi = normalize_doi(item.get("doi"))
    if doi:
        keys.append(f"doi:{doi}")
        arxiv_id = extract_arxiv_id(doi)
        if arxiv_id:
            keys.append(f"arxiv:{arxiv_id}")
    for field in ("arxiv_id", "paper_url", "pdf_url"):
        arxiv_id = extract_arxiv_id(item.get(field))
        if arxiv_id:
            keys.append(f"arxiv:{arxiv_id}")
    url = normalize_url(item.get("paper_url"))
    if url:
        keys.append(f"url:{url}")
    title = normalize_title(item.get("title"))
    year = as_int_or_none(item.get("year")) or 0
    if title:
        keys.append(f"title:{title}|year:{year}")
    return list(dict.fromkeys(keys))


def make_candidate_id(item: dict[str, Any]) -> str:
    digest = hashlib.sha1(candidate_key(item).encode("utf-8")).hexdigest()[:12]
    return f"cand-{digest}"


def extract_run_results(raw_payload: Any) -> list[dict[str, Any]]:
    if isinstance(raw_payload, dict):
        results = raw_payload.get("results")
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]
    if isinstance(raw_payload, list):
        return [item for item in raw_payload if isinstance(item, dict)]
    return []


def normalize_candidate(raw: dict[str, Any], *, source_mode: str = "") -> dict[str, Any] | None:
    title = normalize_text(raw.get("title"))
    year = as_int_or_none(raw.get("year"))
    if not title or year is None:
        return None

    doi = normalize_doi(raw.get("doi"))
    authors = as_list(raw.get("authors"))
    keywords = as_list(raw.get("keywords"))[:4]

    candidate = {
        "candidate_id": "",
        "title": title,
        "authors": authors,
        "year": year,
        "venue": normalize_text(raw.get("venue")),
        "paper_url": normalize_text(raw.get("paper_url")),
        "doi": doi,
        "abstract": normalize_text(raw.get("abstract")),
        "abstract_zh": normalize_text(raw.get("abstract_zh")),
        "keywords": keywords,
        "pdf_url": normalize_text(raw.get("pdf_url")),
        "source_mode": source_mode,
        "source_modes": [source_mode] if source_mode else [],
        "import_status": CANDIDATE_IMPORT_STATUS,
    }
    candidate["candidate_id"] = make_candidate_id(candidate)
    return candidate


def merge_source_modes(current: dict[str, Any], candidate: dict[str, Any]) -> None:
    modes: list[str] = []
    for value in (
        current.get("source_mode"),
        *(current.get("source_modes") or []),
        candidate.get("source_mode"),
        *(candidate.get("source_modes") or []),
    ):
        mode = normalize_text(value)
        if mode in {"quick", "deep"} and mode not in modes:
            modes.append(mode)
    current["source_modes"] = modes
    if "deep" in modes and "quick" in modes:
        current["source_mode"] = "mixed"
    elif modes:
        current["source_mode"] = modes[0]


def merge_candidate_record(current: dict[str, Any], candidate: dict[str, Any]) -> None:
    merge_source_modes(current, candidate)
    incoming_is_deep = normalize_text(candidate.get("source_mode")) == "deep"
    current_has_deep = "deep" in (current.get("source_modes") or [])
    for field, value in candidate.items():
        if field in {"candidate_id", "source_mode", "source_modes", "import_status"}:
            continue
        if value in (None, "", []):
            continue
        existing_value = current.get(field)
        if field == "authors" and isinstance(value, list):
            if incoming_is_deep or not isinstance(existing_value, list) or len(value) > len(existing_value):
                current[field] = value
            continue
        if field == "keywords" and isinstance(value, list):
            if incoming_is_deep:
                merged = list(dict.fromkeys(value + ((existing_value or []) if isinstance(existing_value, list) else [])))
            else:
                merged = list(dict.fromkeys((existing_value or []) + value)) if isinstance(existing_value, list) else value
            current[field] = merged[:4]
            continue
        if incoming_is_deep:
            current[field] = value
            continue
        if current_has_deep:
            continue
        if not existing_value:
            current[field] = value
    if current.get("import_status") != "已导入" and candidate.get("import_status"):
        current["import_status"] = candidate["import_status"]


@dataclass(slots=True)
class NormalizeReport:
    total_raw: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    candidate_count: int


def merge_search_run_into_candidates(*, run_record_path: Path, candidate_papers_path: Path) -> NormalizeReport:
    run_record = read_json(run_record_path, {})
    raw_items = extract_run_results(run_record)
    source_mode = normalize_text(run_record.get("search_mode"))
    return merge_candidate_items(
        raw_items=raw_items,
        candidate_papers_path=candidate_papers_path,
        source_mode=source_mode,
    )


def merge_candidate_items(
    *,
    raw_items: list[dict[str, Any]],
    candidate_papers_path: Path,
    source_mode: str = "",
) -> NormalizeReport:
    existing = read_json(candidate_papers_path, [])
    if not isinstance(existing, list):
        existing = []

    by_key: dict[str, dict[str, Any]] = {}
    for item in existing:
        if not isinstance(item, dict):
            continue
        if not item.get("source_modes"):
            mode = normalize_text(item.get("source_mode"))
            item["source_modes"] = [mode] if mode else []
        for key in candidate_keys(item):
            by_key[key] = item
    inserted = 0
    updated = 0
    skipped = 0

    for raw in raw_items:
        candidate = normalize_candidate(raw, source_mode=source_mode or normalize_text(raw.get("source_mode")))
        if candidate is None:
            skipped += 1
            continue

        keys = candidate_keys(candidate)
        current = next((by_key[key] for key in keys if key in by_key), None)
        if current is None:
            for key in keys:
                by_key[key] = candidate
            inserted += 1
            continue

        merge_candidate_record(current, candidate)
        for key in candidate_keys(current):
            by_key[key] = current
        updated += 1

    candidates = list({id(item): item for item in by_key.values()}.values())
    candidates.sort(key=lambda item: (item.get("year") or 0, normalize_title(item.get("title"))), reverse=True)
    write_json(candidate_papers_path, candidates)

    return NormalizeReport(
        total_raw=len(raw_items),
        inserted_count=inserted,
        updated_count=updated,
        skipped_count=skipped,
        candidate_count=len(candidates),
    )
