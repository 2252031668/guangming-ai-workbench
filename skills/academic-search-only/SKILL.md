---
name: academic-search-only
description: |
  Standalone deep academic retrieval skill for producing structured, JSON-ready paper records. Use when the task asks for deep literature search, fixed-schema paper results, strict fields such as title/authors/year/abstract/pdf_url, multi-discipline academic search, or JSON-compatible academic-search output.
---

# Academic Search Only

## Purpose

Use this skill to perform deep academic retrieval and produce structured paper records that can be returned, written, or embedded according to the active prompt. The skill should not assume a specific wrapper program or storage convention. If the active prompt provides an output schema, destination, result limit, or final-reply rule, follow that prompt exactly.

This skill is standalone. It contains the platform rules, discipline routing, ranking guidance, metadata schema, and open-access policies needed for JSON-ready deep retrieval. Do not depend on another skill being present.

## Core Behavior

- Treat the user request as a complete retrieval instruction, not just a keyword string.
- First extract constraints from the request: topic, year/date range, top-k/count, venue preference, domain/scenario emphasis, source preference, and OA/PDF preference.
- Prefer structured academic APIs and authoritative metadata sources before general web extraction.
- Use a two-pass strategy: broad candidate recall first, then deep enrichment for selected papers.
- Follow the active prompt for output shape, output destination, and final user-facing response.
- Do not expose local paths, script paths, internal reference choices, tool choices, API details, or implementation steps unless the user explicitly asks for implementation details.

## Paper Field Semantics

When the active schema includes these fields, fill them with the following meanings:

- `title`: paper title; required when requested.
- `authors`: author array; preserve source order when available.
- `year`: publication or preprint year.
- `venue`: conference, journal, repository, or publisher venue when available.
- `paper_url`: authoritative paper detail page, arXiv page, DOI landing page, or publisher page when available.
- `doi`: fill from Crossref, publisher, Semantic Scholar, OpenAlex, or source metadata when present; do not reject strong papers only because DOI is missing.
- `abstract`: original English abstract; do not replace it with a summary or translation.
- `keywords`: source keywords when available; otherwise derive concise technical keywords from title plus abstract. Respect any max-count from the active schema.
- `abstract_zh`: translate and simplify from the English abstract.
- `pdf_url`: legal open PDF or directly available open full-text PDF only; otherwise use the empty/null value required by the active schema.

## Bundled References

Load only the local references needed for the current request:

- `references/api-cookbook.md`: API examples and platform response fields.
- `references/metadata-schema.md`: schema normalization, deduplication, merge, and BibTeX-related field mapping.
- `references/disciplines/*.md`: discipline routing, query expansion, and discipline-specific ranking.
- `references/venue-rankings.md`: CS venue and CCF-style ranking support.
- `references/rankings/biomed-evidence-ranking.md`: biomedical evidence ordering.
- `references/site-patterns/{domain}.md`: platform-specific extraction patterns after selecting a source.
- `references/cdp-api.md`: optional browser-control reference for Google Scholar, CNKI, or other browser-only sources.
- `scripts/cdp-proxy.mjs`: optional local CDP proxy used only when the request explicitly requires browser-only sources and the environment has Chrome remote debugging available.

Use these behavior policies:

- API-first retrieval.
- Discipline-based platform selection.
- Structured deduplication and source merging.
- Open-access legality rules for PDF URLs.
- CDP/browser automation is optional, never default. Prefer API and open web sources. Use bundled CDP support only when the request explicitly requires browser-only sources such as Google Scholar or CNKI and the environment already supports Chrome remote debugging.
- Bulk PDF download helpers are intentionally not bundled. This skill fills legal `pdf_url` values but does not download PDFs.

## Workflow

1. Parse the request into retrieval constraints.
2. Detect the discipline and load only the relevant local discipline profile.
3. Select platforms from the discipline profile and request shape.
4. Run first-pass retrieval to collect a candidate pool larger than the requested result count.
5. Deduplicate candidates by DOI, arXiv ID, PMID/PMCID, then normalized title/year/author signals.
6. Rank candidates with discipline-aware heuristics and select top K according to the explicit request count or active prompt default.
7. Run second-pass enrichment on selected papers to fill requested fields such as abstract, DOI, venue, paper URL, keywords, and legal PDF URL.
8. Merge fields from multiple sources using the metadata schema rules; preserve authoritative values and keep requested required fields populated.
9. Format the final records according to the active schema and output/write them exactly as the active prompt requires.
10. If the active prompt defines a final user-facing reply rule, follow it exactly; otherwise provide a concise summary without exposing internals.

## Ranking Policy

Primary ranking signals:

- Topical relevance to the full request.
- Satisfaction of explicit constraints such as year range, result count, venue preference, method focus, scenario, language, or OA preference.
- Year/date filter satisfaction.

Secondary ranking signals:

- Venue quality according to the relevant discipline.
- Citation count or influence signal when available.
- Code, dataset, benchmark, or PDF usefulness when relevant to the task.
- For biomedical tasks, evidence type and study quality outrank CS venue-style heuristics.

## Field Fill Policy

- `title`, `authors`, `year`, `venue`, `paper_url`, and `abstract`: prefer authoritative source pages, official APIs, Crossref/OpenAlex/Semantic Scholar, then repository metadata.
- `doi`: prefer publisher/Crossref DOI, then Semantic Scholar/OpenAlex external IDs.
- `pdf_url`: use arXiv PDF, PubMed Central PDF, publisher open PDF, or other legal open repository PDF. Never use Sci-Hub, LibGen, WebVPN bypasses, Tor, credential sharing, or paywall circumvention.
- `keywords`: prefer source keywords; otherwise derive concise technical keywords from the English title and abstract.
- `abstract_zh`: always derive from `abstract`; keep it shorter and clearer than the source abstract.
