from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


USER_AGENT = "GuangmingAIWorkbench/0.1 (open-pdf-resolver)"


@dataclass(frozen=True)
class OpenPdfResult:
    pdf_url: str
    source: str
    message: str


class OpenPdfNotFoundError(RuntimeError):
    pass


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


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


def request_json(url: str, timeout: int = 20) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def choose_pdf_url(work: dict[str, Any]) -> str:
    locations = []
    primary = work.get("primary_location")
    if isinstance(primary, dict):
        locations.append(primary)
    for location in work.get("locations") or []:
        if isinstance(location, dict):
            locations.append(location)

    for location in locations:
        pdf_url = normalize_text(location.get("pdf_url"))
        if pdf_url.startswith(("http://", "https://")):
            return pdf_url

    open_access = work.get("open_access") if isinstance(work.get("open_access"), dict) else {}
    oa_url = normalize_text(open_access.get("oa_url"))
    if oa_url.startswith(("http://", "https://")) and ".pdf" in oa_url.lower():
        return oa_url
    return ""


def resolve_from_openalex_by_doi(doi: str) -> OpenPdfResult | None:
    if not doi:
        return None
    url = f"https://api.openalex.org/works/https://doi.org/{quote(doi, safe='')}"
    work = request_json(url)
    pdf_url = choose_pdf_url(work)
    if pdf_url:
        return OpenPdfResult(pdf_url=pdf_url, source="OpenAlex", message="通过 DOI 在 OpenAlex 找到开放 PDF。")
    return None


def title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_text(left).lower(), normalize_text(right).lower()).ratio()


def resolve_from_openalex_by_title(title: str) -> OpenPdfResult | None:
    if not title:
        return None
    url = f"https://api.openalex.org/works?search={quote(title)}&per-page=5"
    payload = request_json(url)
    for work in payload.get("results") or []:
        if not isinstance(work, dict):
            continue
        if title_similarity(title, work.get("title") or "") < 0.82:
            continue
        pdf_url = choose_pdf_url(work)
        if pdf_url:
            return OpenPdfResult(pdf_url=pdf_url, source="OpenAlex", message="通过标题在 OpenAlex 找到开放 PDF。")
    return None


def resolve_open_pdf_url(paper: dict[str, Any]) -> OpenPdfResult:
    for key in ("arxiv_id", "doi", "paper_url"):
        arxiv_id = extract_arxiv_id(paper.get(key))
        if arxiv_id:
            return OpenPdfResult(
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                source="arXiv",
                message="通过 arXiv ID 构造开放 PDF 链接。",
            )

    doi = normalize_doi(paper.get("doi"))
    if doi:
        result = resolve_from_openalex_by_doi(doi)
        if result:
            return result

    result = resolve_from_openalex_by_title(normalize_text(paper.get("title")))
    if result:
        return result

    raise OpenPdfNotFoundError("未在 arXiv 或 OpenAlex 找到合法开放 PDF。")
