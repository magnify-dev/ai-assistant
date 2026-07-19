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
from web_surf.fetch import PageResult, fetch_page_tier1, is_js_shell_text
from web_surf.page_match import (
    focus_query,
    parse_user_preferred_domains,
    score_result_url,
    score_search_result,
    url_on_preferred_source,
)
from web_surf.spec import classify_search_sources
from web_surf.search import SearchResult, web_search
from web_surf.spec import fallback_research_spec, structure_research_spec, wants_verbatim_copy
from web_surf.store import (
    empty_facts,
    empty_index,
    facts_for_query,
    facts_summary_for_agent,
    index_summary_for_agent,
    merge_facts,
    merge_page_index,
    normalize_url,
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


def _rank_by_relevance(results: list[Any], query: str) -> list[Any]:
    goal = focus_query(query)
    return sorted(
        results,
        key=lambda row: (
            -(
                score_search_result(row, goal)
                + score_result_url(str(getattr(row, "url", "") or ""), goal)
            ),
            str(getattr(row, "url", "") or ""),
        ),
    )


def _candidate_tiers(
    results: list[Any],
    query: str,
    *,
    limit: int,
    spec: dict[str, Any] | None = None,
    use_llm: bool = False,
    cfg: dict[str, Any] | None = None,
) -> tuple[list[Any], list[Any], set[str]]:
    """Rank results and split into publisher-primary vs secondary tiers."""
    ranked = _rank_by_relevance(results, query)
    publisher_domains: set[str] = set()
    if use_llm and cfg:
        official, secondary, publisher_domains = classify_search_sources(
            query=query,
            spec=spec or {},
            results=ranked,
            ollama_url=str(cfg["ollama_url"]),
            model=str(cfg["ollama_model"]),
            timeout_sec=float(cfg["ollama_timeout_sec"]),
        )
    else:
        from web_surf.spec import _fallback_source_tiers
        from web_surf.page_match import official_registrable_domains

        official, secondary = _fallback_source_tiers(ranked, query)
        official_urls = [str(getattr(row, "url", "") or "") for row in official]
        publisher_domains = official_registrable_domains(official_urls)
    official = _unique_urls(official, limit=limit)
    secondary = _unique_urls(secondary, limit=limit)
    preferred = parse_user_preferred_domains(query)
    if preferred:
        def _is_preferred(row: Any) -> bool:
            return url_on_preferred_source(str(getattr(row, "url", "") or ""), preferred)

        preferred_rows = [row for row in ranked if _is_preferred(row)]
        official = [row for row in official if not _is_preferred(row)]
        secondary = [row for row in secondary if not _is_preferred(row)]
        official = _unique_urls(preferred_rows + official, limit=limit)
    return official, secondary, publisher_domains


def _explore_candidate_tier(
    *,
    tier_name: str,
    candidates: list[Any],
    query: str,
    project_path: Path,
    page_budget: int,
    pages_fetched: int,
    max_steps_total: int,
    steps_used: int,
    spec: dict[str, Any],
    cfg: dict[str, Any],
    emit_events: bool,
    publisher_domains: set[str] | None = None,
    publishers: list[str] | None = None,
) -> tuple[list[PageResult], str, bool, dict[str, Any], int]:
    """Run browser exploration for one source tier; returns pages, content, goal_met, metadata, steps_used."""
    empty_meta: dict[str, Any] = {}
    if not candidates or pages_fetched >= page_budget:
        return [], "", False, empty_meta, steps_used

    from web_surf.browser_explore import explore_candidates_in_browser, stdin_help_provider

    remaining_visits = page_budget - pages_fetched
    remaining_steps = max(1, max_steps_total - steps_used)
    if emit_events:
        web_events.web_progress(
            step="browse",
            message=f"Exploring {tier_name} sources ({len(candidates[:remaining_visits])} site(s))",
        )
    exploration = explore_candidates_in_browser(
        query=query.strip(),
        candidates=candidates[:remaining_visits],
        project_path=project_path,
        max_visits=remaining_visits,
        max_steps=remaining_steps,
        max_steps_per_branch=int(cfg.get("max_steps_per_branch") or 20),
        ollama_url=cfg["ollama_url"],
        model=cfg["ollama_model"],
        timeout_sec=float(cfg["ollama_timeout_sec"]),
        help_provider=stdin_help_provider if emit_events else None,
        success_criteria=[
            str(item)
            for item in (spec.get("success_criteria") or [])
            if str(item).strip()
        ],
        data_needed=[
            str(item)
            for item in (spec.get("data_needed") or [])
            if str(item).strip()
        ],
        accomplishment_steps=list(spec.get("accomplishment_steps") or []),
        form_values={
            str(key): str(value)
            for key, value in (cfg.get("form_values") or {}).items()
            if str(value)
        },
        publisher_domains=set(publisher_domains or set()),
        publishers=list(publishers or []),
    )
    browser_pages, found_content, goal_met = exploration[:3]
    metadata = exploration[3] if len(exploration) > 3 and isinstance(exploration[3], dict) else empty_meta
    tier_steps = len(metadata.get("steps") or [])
    return browser_pages, found_content, goal_met, metadata, steps_used + tier_steps


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

    from web_surf.plan import compact_plan_for_prompt, normalize_accomplishment_steps

    question = focus_query(query)
    scoped_index = index
    scoped_facts = {"facts": facts_for_query(facts_doc, question, max_facts=30)}
    copy_mode = wants_verbatim_copy(query)
    prompt_key = "web_research.answer_copy" if copy_mode else "web_research.answer"
    plan_steps = normalize_accomplishment_steps(
        spec.get("accomplishment_steps"),
        query=question,
    )
    plan = compact_plan_for_prompt(plan_steps) if plan_steps else {}
    needed = [
        str(item).strip()
        for item in (spec.get("data_needed") or [])
        if str(item).strip()
    ]
    criteria = [
        str(item).strip()
        for item in (spec.get("success_criteria") or [])
        if str(item).strip()
    ]
    user = (
        f"question: {question}\n"
        f"summary: {str(spec.get('summary') or question)}\n"
        f"need: {', '.join(needed) if needed else 'answer the question'}\n"
        f"success_criteria: {'; '.join(criteria) if criteria else 'complete answer'}\n"
        f"plan: {' → '.join(str(s.get('description') or '') for s in plan.get('user_goal_steps') or [])}\n"
        f"pages:\n{index_summary_for_agent(scoped_index, query=question, max_pages=12)}\n"
        f"facts:\n{facts_summary_for_agent(scoped_facts, query=question, max_facts=20)}"
    )
    try:
        return ollama_chat(
            prompt_key=prompt_key,
            ollama_url=ollama_url,
            model=model,
            timeout_sec=timeout_sec,
            system=_get_prompt(prompt_key),
            user=user,
        ).strip()
    except Exception as exc:
        logger.warning("Answer synthesis failed: %s", exc)
        return facts_summary_for_agent(scoped_facts, query=question)


def _ingest_counts_as_fetch(page: PageResult, *, added: int) -> bool:
    if not page.ok:
        return False
    if added > 0:
        return True
    return len(page.text.strip()) >= 200 and not is_js_shell_text(page.text)


def _browser_retry_js_shell(
    *,
    row: Any,
    query: str,
    project_path: Path,
    spec: dict[str, Any],
    cfg: dict[str, Any],
    emit_events: bool,
    publisher_domains: set[str],
    publishers: list[str],
    steps_used: int,
    max_steps_total: int,
) -> tuple[PageResult | None, int]:
    """Try Playwright when HTTP only returned a JS-required shell."""
    tier_pages, _, _, metadata, steps_used = _explore_candidate_tier(
        tier_name="browser_retry",
        candidates=[row],
        query=query.strip(),
        project_path=project_path,
        page_budget=1,
        pages_fetched=0,
        max_steps_total=max_steps_total,
        steps_used=steps_used,
        spec=spec,
        cfg=cfg,
        emit_events=emit_events,
        publisher_domains=publisher_domains,
        publishers=publishers,
    )
    page = tier_pages[0] if tier_pages else None
    if page and page.ok:
        from web_surf import events as web_events

        if emit_events:
            web_events.log(f"Browser fetch succeeded for {row.url}", level="info")
    return page, steps_used


def _infer_goal_met(
    *,
    query: str,
    facts_doc: dict[str, Any],
    pages_fetched: int,
    facts_added: int,
    answer: str,
    browser_goal_met: bool,
) -> bool:
    if browser_goal_met:
        return True
    if pages_fetched <= 0 or facts_added <= 0 or not answer.strip():
        return False
    matching = facts_for_query(facts_doc, query, max_facts=50)
    return len(matching) >= 2


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

    # Page markdown / facts are run-local only — maps are the sole cross-run reuse.
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
    try:
        from web_capture.context import set_active_project

        set_active_project(project_path)
    except ImportError:
        pass
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
        web_events.web_progress(step="spec", message="Structuring research plan from your prompt")
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
    if emit_events:
        steps = list(spec.get("accomplishment_steps") or [])
        step_preview = " → ".join(
            str(item.get("description") or "")[:80]
            for item in steps[:6]
            if isinstance(item, dict)
        )
        web_events.web_progress(
            step="plan",
            message=f"Accomplishment plan: {step_preview}" if step_preview else "Research plan ready",
        )
        web_events.criteria(
            {
                "goal": focus_query(query),
                "criteria": [
                    {"criterion": str(item), "met": False}
                    for item in (spec.get("success_criteria") or [])
                    if str(item).strip()
                ],
                "unmet_criteria": [
                    str(item)
                    for item in (spec.get("success_criteria") or [])
                    if str(item).strip()
                ],
                "accomplishment_steps": steps,
                "data_needed": list(spec.get("data_needed") or []),
                "summary": spec.get("summary"),
            }
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

    official_candidates, secondary_candidates, publisher_domains = _candidate_tiers(
        all_search, query, limit=page_budget, spec=spec, use_llm=use_llm, cfg=cfg
    )
    publishers = [
        str(item).strip()
        for item in (spec.get("official_sources") or [])
        if str(item).strip()
    ]
    candidates = official_candidates + secondary_candidates
    result.search_results = candidates
    if emit_events:
        official_urls = {row.url.lower() for row in official_candidates}
        web_events.candidates(
            {
                "candidates": [
                    {
                        "title": row.title,
                        "url": row.url,
                        "snippet": row.snippet,
                        "tier": "official"
                        if row.url.lower() in official_urls
                        else "secondary",
                    }
                    for row in candidates
                ]
            }
        )
    if not candidates and search_errors:
        result.errors.extend(search_errors)
        result.answer = "Web search failed. Check network connectivity and ddgs installation."
        if emit_events:
            web_events.phase_done("web_research", ok=False, message=result.answer)
            web_events.web_result_event(_result_payload(result, empty_index(), empty_facts()))
            web_events.finish(overall_ok=False, error=result.answer)
            web_events.set_running(False)
        save_run_state(
            project_path,
            run_id,
            {"query": query.strip(), "status": "failed", "errors": result.errors},
        )
        return result

    # Fresh facts/index each run — prior research must not bias the answer.
    # The only cross-run reuse is URL-keyed page maps under .agent/web-capture/by-url/.
    index = empty_index()
    facts_doc = empty_facts()
    pages_fetched = 0
    facts_added = 0

    browser_pages: list[PageResult] = []
    found_content = ""
    browser_completed = False
    max_steps_total = max(20, page_budget * max(4, int(cfg.get("max_steps_per_branch") or 20) // 5))
    steps_used = 0
    browser_visits = 0
    if candidates and use_llm:
        try:
            for tier_name, tier_candidates in (
                ("official", official_candidates),
                ("secondary", secondary_candidates),
            ):
                if result.goal_met or browser_visits >= page_budget:
                    break
                if not tier_candidates:
                    continue
                tier_pages, tier_content, goal_met, metadata, steps_used = _explore_candidate_tier(
                    tier_name=tier_name,
                    candidates=tier_candidates,
                    query=query.strip(),
                    project_path=project_path,
                    page_budget=page_budget,
                    pages_fetched=browser_visits,
                    max_steps_total=max_steps_total,
                    steps_used=steps_used,
                    spec=spec,
                    cfg=cfg,
                    emit_events=emit_events,
                    publisher_domains=publisher_domains,
                    publishers=publishers,
                )
                browser_pages.extend(tier_pages)
                browser_visits += len(tier_candidates[: max(0, page_budget - browser_visits)])
                if tier_content.strip():
                    found_content = tier_content
                if metadata:
                    result.steps.extend(list(metadata.get("steps") or []))
                    result.visited_pages.extend(list(metadata.get("visited_pages") or []))
                    if metadata.get("unmet_criteria"):
                        result.unmet_criteria = [
                            str(item) for item in (metadata.get("unmet_criteria") or [])
                        ]
                    result.helper_history.extend(list(metadata.get("helper_history") or []))
                result.goal_met = goal_met
                if goal_met:
                    break
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
        if _ingest_counts_as_fetch(browser_page, added=added):
            pages_fetched += 1
            facts_added += added
            visited_urls.add(browser_page.url)

    fetch_tiers = [
        ("official", official_candidates),
        ("secondary", secondary_candidates),
    ]
    for tier_name, tier_rows in fetch_tiers:
        if result.goal_met:
            break
        if not tier_rows:
            continue
        total_candidates = len(tier_rows)
        for idx, row in enumerate(tier_rows, start=1):
            if pages_fetched >= page_budget:
                break
            if normalize_url(row.url) in visited_urls or row.url in visited_urls:
                continue
            if emit_events:
                web_events.web_progress(
                    step="fetch",
                    url=row.url,
                    index=idx,
                    total=total_candidates,
                    message=f"Fetching {tier_name} source: {row.title or row.url}",
                )
            page = fetch_page_tier1(
                row.url,
                timeout_sec=float(cfg["fetch_timeout_sec"]),
                max_chars=int(cfg["content_max_chars"]),
            )
            if (
                not page.ok
                and use_llm
                and "browser fetch required" in str(page.error or "").lower()
            ):
                if emit_events:
                    web_events.log(
                        f"HTTP fetch got JS shell for {row.url} — retrying in browser",
                        level="warn",
                    )
                page, steps_used = _browser_retry_js_shell(
                    row=row,
                    query=query.strip(),
                    project_path=project_path,
                    spec=spec,
                    cfg=cfg,
                    emit_events=emit_events,
                    publisher_domains=publisher_domains,
                    publishers=publishers,
                    steps_used=steps_used,
                    max_steps_total=max_steps_total,
                )
                if not page:
                    page = PageResult(
                        url=row.url,
                        title="",
                        text="",
                        markdown="",
                        content_hash="",
                        fetch_tier=2,
                        error="JavaScript-rendered page — browser fetch required",
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
            if _ingest_counts_as_fetch(page, added=added):
                pages_fetched += 1
                facts_added += added
                visited_urls.add(page.url)

    # Do not persist facts/index across runs (stale answers). Maps persist via url_cache.

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
        result.answer = facts_summary_for_agent(
            {"facts": facts_for_query(facts_doc, query, max_facts=20)},
            query=query,
        )
    else:
        result.answer = "Fetched pages but extracted no verified facts. Try a more specific query."

    result.goal_met = _infer_goal_met(
        query=query.strip(),
        facts_doc=facts_doc,
        pages_fetched=pages_fetched,
        facts_added=facts_added,
        answer=result.answer,
        browser_goal_met=result.goal_met,
    )

    if emit_events:
        partial = (
            not result.goal_met
            and (pages_fetched > 0 or facts_added > 0)
            and bool(result.answer.strip())
        )
        ok = result.goal_met
        web_events.web_result_event(_result_payload(result, index, facts_doc))
        web_events.phase_done(
            "web_research",
            ok=ok or partial,
            message=f"{pages_fetched} page(s), {facts_added} fact(s)"
            + ("" if result.goal_met else " · goal not fully met"),
        )
        web_events.finish(
            overall_ok=ok or partial,
            goal_met=result.goal_met,
            partial=partial,
            error="" if (ok or partial) else result.answer,
        )
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
