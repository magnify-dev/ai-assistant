from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from web_surf import events as web_events
from web_surf.config import default_config
from web_surf.llm import get_trace, ollama_chat, reset_trace
from web_surf.extract import extract_facts_from_page
from web_surf.fetch import PageResult, fetch_page_tier1
from web_surf.page_match import focus_query, rank_search_results
from web_surf.search import SearchResult, web_search
from web_surf.spec import fallback_research_spec, structure_research_spec
from web_surf.store import (
    cache_page_markdown,
    facts_summary_for_agent,
    index_summary_for_agent,
    load_facts,
    load_index,
    merge_facts,
    merge_page_index,
    normalize_url,
    save_facts,
    save_index,
    save_run_state,
)

logger = logging.getLogger(__name__)


@dataclass
class WebResearchResult:
    query: str
    spec: dict[str, Any]
    pages_fetched: int = 0
    facts_added: int = 0
    search_results: list[SearchResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    answer: str = ""
    store_dir: str = ""
    goal_met: bool = False
    steps: list[dict[str, Any]] = field(default_factory=list)
    visited_pages: list[dict[str, Any]] = field(default_factory=list)
    unmet_criteria: list[str] = field(default_factory=list)
    helper_history: list[dict[str, Any]] = field(default_factory=list)

    def to_tool_text(self, *, max_chars: int = 8000) -> str:
        lines = [
            f"Research query: {self.query}",
            f"Summary: {self.spec.get('summary') or self.query}",
            f"Pages fetched: {self.pages_fetched}",
            f"Facts added: {self.facts_added}",
            f"Store: {self.store_dir}",
            "",
            "Answer:",
            self.answer or "(no answer synthesized)",
        ]
        if self.errors:
            lines.extend(["", "Errors:", *[f"- {err}" for err in self.errors[:8]]])
        text = "\n".join(lines)
        return text[:max_chars]


def _unique_urls(results: list[SearchResult], *, limit: int) -> list[SearchResult]:
    seen: set[str] = set()
    out: list[SearchResult] = []
    for row in results:
        key = row.url.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _synthesize_answer(
    *,
    query: str,
    spec: dict[str, Any],
    facts_doc: dict[str, Any],
    index: dict[str, Any],
    ollama_url: str,
    model: str,
    timeout_sec: float,
) -> str:
    from web_surf.spec import _get_prompt

    question = focus_query(query)
    user = (
        f"question: {question}\n"
        f"pages:\n{index_summary_for_agent(index, max_pages=12)}\n"
        f"facts:\n{facts_summary_for_agent(facts_doc, query=question, max_facts=20)}"
    )
    try:
        return ollama_chat(
            prompt_key="web_research.answer",
            ollama_url=ollama_url,
            model=model,
            timeout_sec=timeout_sec,
            system=_get_prompt("web_research.answer"),
            user=user,
        ).strip()
    except Exception as exc:
        logger.warning("Answer synthesis failed: %s", exc)
        return facts_summary_for_agent(facts_doc, query=query)


def _result_payload(result: WebResearchResult, index: dict[str, Any], facts_doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": result.query,
        "spec": result.spec,
        "pages_fetched": result.pages_fetched,
        "facts_added": result.facts_added,
        "errors": result.errors,
        "answer": result.answer,
        "store_dir": result.store_dir,
        "goal_met": result.goal_met,
        "steps": result.steps,
        "visited_pages": result.visited_pages,
        "unmet_criteria": result.unmet_criteria,
        "helper_history": result.helper_history,
        "search_results": [
            {"title": row.title, "url": row.url, "snippet": row.snippet}
            for row in result.search_results
        ],
        "index": index,
        "facts": facts_doc.get("facts") or [],
        "llm_exchanges": get_trace(),
    }


def _ingest_page(
    page: PageResult,
    row: Any,
    spec: dict[str, Any],
    query: str,
    index: dict[str, Any],
    facts_doc: dict[str, Any],
    project_path: Path,
    use_llm: bool,
    cfg: dict[str, Any],
    emit_events: bool,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    from web_surf import events as web_events

    cache_page_markdown(project_path, page.content_hash, page.markdown)
    page_summary = ""
    new_facts: list[dict[str, Any]] = []
    if emit_events:
        web_events.web_progress(
            step="extract",
            url=page.url,
            message="Extracting facts",
        )
    if use_llm:
        evidence_context = page.evidence_context or {}
        new_facts, page_summary = extract_facts_from_page(
            page_text=page.text,
            page_url=page.url,
            page_title=page.title or getattr(row, "title", ""),
            research_spec=spec,
            ollama_url=cfg["ollama_url"],
            model=cfg["ollama_model"],
            timeout_sec=float(cfg["ollama_timeout_sec"]),
            source_session_id=str(evidence_context.get("source_session_id") or ""),
            source_step_id=str(evidence_context.get("source_step_id") or ""),
            source_snapshot_id=str(evidence_context.get("source_snapshot_id") or ""),
        )
    else:
        page_summary = (page.title or getattr(row, "title", "") or page.url)[:200]

    index, _ = merge_page_index(
        index,
        url=page.url,
        title=page.title or getattr(row, "title", ""),
        summary=page_summary or getattr(row, "snippet", ""),
        fetch_tier=page.fetch_tier,
        page_hash=page.content_hash,
        search_query=getattr(row, "query", query),
    )
    facts_doc, added = merge_facts(
        facts_doc,
        new_facts,
        research_query=query.strip(),
    )
    for fact in new_facts:
        web_events.evidence(
            {
                "source": "fact_extraction",
                "source_url": fact.get("source_url", page.url),
                "session_id": fact.get("source_session_id", ""),
                "step_id": fact.get("source_step_id", ""),
                "snapshot_id": fact.get("source_snapshot_id", ""),
                "fact": fact,
            }
        )
    if emit_events:
        web_events.web_index_event(pages=index.get("pages") or {})
        web_events.web_facts_event(facts=facts_doc.get("facts") or [])
    return index, facts_doc, added


def run_web_research(
    query: str,
    *,
    project: str | Path | None = None,
    max_pages: int | None = None,
    max_search_results: int | None = None,
    use_ollama: bool | None = None,
    config: dict[str, Any] | None = None,
    emit_events: bool = False,
) -> WebResearchResult:
    web_events.configure(emit_json=emit_events)
    reset_trace()
    if emit_events:
        web_events.set_running(True)
        web_events.phase_start("web_research", "Starting web research")

    cfg = {**default_config(), **(config or {})}
    project_path = Path(project or cfg["project"]).resolve()
    project_path.mkdir(parents=True, exist_ok=True)
    run_id = f"research_{uuid.uuid4().hex}"
    save_run_state(
        project_path,
        run_id,
        {"query": query.strip(), "status": "starting", "emit_events": emit_events},
    )

    use_llm = cfg["use_ollama"] if use_ollama is None else use_ollama
    page_budget = max_pages if max_pages is not None else int(cfg["max_pages"])
    search_budget = max_search_results if max_search_results is not None else int(cfg["max_search_results"])

    if emit_events:
        web_events.web_progress(step="spec", message="Structuring research plan")
    if use_llm:
        spec = structure_research_spec(
            query=query,
            ollama_url=cfg["ollama_url"],
            model=cfg["ollama_model"],
            timeout_sec=float(cfg["ollama_timeout_sec"]),
        )
    else:
        spec = fallback_research_spec(query)

    page_budget = min(page_budget, int(spec.get("max_pages") or page_budget))
    queries = [str(q).strip() for q in (spec.get("search_queries") or [query]) if str(q).strip()]
    if not queries:
        queries = [query.strip()]

    result = WebResearchResult(
        query=query.strip(),
        spec=spec,
        store_dir=str(project_path / ".agent" / "web"),
    )

    all_search: list[SearchResult] = []
    search_errors: list[str] = []
    for search_query in queries[:3]:
        if emit_events:
            web_events.web_progress(step="search", message=f"Searching: {search_query}")
        try:
            all_search.extend(web_search(search_query, max_results=search_budget))
        except Exception as exc:
            search_errors.append(str(exc))
            if emit_events:
                web_events.log(f"Search failed for {search_query!r}: {exc}", level="error")

    candidates = _unique_urls(rank_search_results(all_search, query), limit=page_budget)
    result.search_results = candidates
    if emit_events:
        web_events.candidates(
            {
                "candidates": [
                    {"title": row.title, "url": row.url, "snippet": row.snippet}
                    for row in candidates
                ]
            }
        )
    if not candidates and search_errors:
        result.errors.extend(search_errors)
        result.answer = "Web search failed. Check network connectivity and ddgs installation."
        if emit_events:
            web_events.phase_done("web_research", ok=False, message=result.answer)
            web_events.web_result_event(_result_payload(result, load_index(project_path), load_facts(project_path)))
            web_events.finish(overall_ok=False, error=result.answer)
            web_events.set_running(False)
        save_run_state(
            project_path,
            run_id,
            {"query": query.strip(), "status": "failed", "errors": result.errors},
        )
        return result

    index = load_index(project_path)
    facts_doc = load_facts(project_path)
    pages_fetched = 0
    facts_added = 0

    browser_pages: list[PageResult] = []
    found_content = ""
    browser_completed = False
    if candidates and use_llm:
        try:
            from web_surf.browser_explore import explore_candidates_in_browser, stdin_help_provider

            if emit_events:
                web_events.web_progress(step="browse", message="Opening browser to explore search results")
            exploration = explore_candidates_in_browser(
                query=query.strip(),
                candidates=candidates,
                project_path=project_path,
                max_visits=page_budget,
                max_steps=max(8, page_budget * 4),
                ollama_url=cfg["ollama_url"],
                model=cfg["ollama_model"],
                timeout_sec=float(cfg["ollama_timeout_sec"]),
                help_provider=stdin_help_provider if emit_events else None,
                success_criteria=[
                    str(item)
                    for item in (spec.get("success_criteria") or [])
                    if str(item).strip()
                ],
                form_values={
                    str(key): str(value)
                    for key, value in (cfg.get("form_values") or {}).items()
                    if str(value)
                },
            )
            browser_pages, found_content, goal_met = exploration[:3]
            if len(exploration) > 3 and isinstance(exploration[3], dict):
                metadata = exploration[3]
                result.steps = list(metadata.get("steps") or [])
                result.visited_pages = list(metadata.get("visited_pages") or [])
                result.unmet_criteria = [
                    str(item) for item in (metadata.get("unmet_criteria") or [])
                ]
                result.helper_history = list(metadata.get("helper_history") or [])
            result.goal_met = goal_met
            browser_completed = True
        except Exception as exc:
            logger.warning("Browser exploration failed, falling back to HTTP fetch: %s", exc)
            if emit_events:
                web_events.log(f"Browser exploration unavailable ({exc}) — using HTTP fetch", level="warn")

    visited_urls: set[str] = set()
    for browser_page in browser_pages:
        if not browser_page.ok or pages_fetched >= page_budget:
            continue
        fake_row = type("Row", (), {"url": browser_page.url, "title": browser_page.title, "snippet": "", "query": query})()
        index, facts_doc, added = _ingest_page(
            browser_page,
            fake_row,
            spec,
            query,
            index,
            facts_doc,
            project_path,
            use_llm,
            cfg,
            emit_events,
        )
        pages_fetched += 1
        facts_added += added
        visited_urls.add(browser_page.url)

    total_candidates = len(candidates)
    for idx, row in enumerate(candidates, start=1):
        if browser_completed:
            break
        if normalize_url(row.url) in visited_urls or row.url in visited_urls:
            continue
        if pages_fetched >= page_budget:
            break
        if emit_events:
            web_events.web_progress(
                step="fetch",
                url=row.url,
                index=idx,
                total=total_candidates,
                message=f"Fetching {row.title or row.url}",
            )
        page = fetch_page_tier1(
            row.url,
            timeout_sec=float(cfg["fetch_timeout_sec"]),
            max_chars=int(cfg["content_max_chars"]),
        )
        if not page.ok:
            err = f"{row.url}: {page.error or 'fetch failed'}"
            result.errors.append(err)
            if emit_events:
                web_events.log(err, level="warning")
            continue

        index, facts_doc, added = _ingest_page(
            page,
            row,
            spec,
            query,
            index,
            facts_doc,
            project_path,
            use_llm,
            cfg,
            emit_events,
        )
        pages_fetched += 1
        facts_added += added

    save_index(project_path, index)
    save_facts(project_path, facts_doc)

    result.pages_fetched = pages_fetched
    result.facts_added = facts_added

    if emit_events:
        web_events.web_progress(step="answer", message="Synthesizing answer")
    if found_content.strip():
        excerpt = found_content.strip()[:6000]
        if use_llm:
            result.answer = _synthesize_answer(
                query=query,
                spec=spec,
                facts_doc=facts_doc,
                index=index,
                ollama_url=cfg["ollama_url"],
                model=cfg["ollama_model"],
                timeout_sec=float(cfg["ollama_timeout_sec"]),
            )
            if not result.answer.strip():
                result.answer = excerpt
        else:
            result.answer = excerpt
    elif use_llm and (facts_added > 0 or pages_fetched > 0):
        result.answer = _synthesize_answer(
            query=query,
            spec=spec,
            facts_doc=facts_doc,
            index=index,
            ollama_url=cfg["ollama_url"],
            model=cfg["ollama_model"],
            timeout_sec=float(cfg["ollama_timeout_sec"]),
        )
    elif facts_doc.get("facts"):
        result.answer = facts_summary_for_agent(facts_doc, query=query)
    else:
        result.answer = "Fetched pages but extracted no verified facts. Try a more specific query."

    if emit_events:
        ok = result.goal_met or pages_fetched > 0 or facts_added > 0
        web_events.web_result_event(_result_payload(result, index, facts_doc))
        web_events.phase_done(
            "web_research",
            ok=ok,
            message=f"{pages_fetched} page(s), {facts_added} fact(s)",
        )
        web_events.finish(overall_ok=ok, error="" if ok else result.answer)
        web_events.set_running(False)

    save_run_state(
        project_path,
        run_id,
        {
            "query": query.strip(),
            "status": "completed",
            "goal_met": result.goal_met,
            "pages_fetched": pages_fetched,
            "facts_added": facts_added,
            "errors": result.errors,
            "browser_session_ids": sorted(
                {
                    str((page.evidence_context or {}).get("source_session_id") or "")
                    for page in browser_pages
                    if (page.evidence_context or {}).get("source_session_id")
                }
            ),
        },
    )

    return result
