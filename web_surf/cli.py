from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from web_surf.classify import classify_task
from web_surf.config import default_config
from web_surf.runner import run_web_research
from web_surf.spec import structure_research_spec
from web_surf.store import facts_summary_for_agent, index_summary_for_agent, load_facts, load_index
from web_surf.search import web_search


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local web research agent (phase 1)")
    sub = parser.add_subparsers(dest="command", required=True)

    research = sub.add_parser("research", help="Search, fetch, extract, and store web data")
    research.add_argument("--query", required=True, help="What to research")
    research.add_argument("--project", help="Project folder for .agent/web store")
    research.add_argument("--max-pages", type=int)
    research.add_argument("--max-search-results", type=int)
    research.add_argument("--no-ollama", action="store_true", help="Skip LLM structuring/extraction")
    research.add_argument("--emit-events", action="store_true", help="Emit NDJSON events for test-runner UI")
    research.add_argument("--json", action="store_true", help="Print machine-readable summary")
    research.add_argument("-v", "--verbose", action="store_true")

    spec_cmd = sub.add_parser("spec", help="Only structure a research spec with Ollama")
    spec_cmd.add_argument("--query", required=True)
    spec_cmd.add_argument("-v", "--verbose", action="store_true")

    classify_cmd = sub.add_parser("classify", help="Classify task as ui_test or web_research")
    classify_cmd.add_argument("--task", required=True)
    classify_cmd.add_argument("--no-ollama", action="store_true")

    search_cmd = sub.add_parser("search", help="Only run web search")
    search_cmd.add_argument("--query", required=True)
    search_cmd.add_argument("--max-results", type=int, default=5)
    search_cmd.add_argument("-v", "--verbose", action="store_true")

    show_cmd = sub.add_parser("show", help="Show stored web index and facts")
    show_cmd.add_argument("--project", help="Project folder")
    show_cmd.add_argument("--query", default="", help="Filter facts by query tokens")
    show_cmd.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "verbose", False))
    cfg = default_config()

    if args.command == "classify":
        print(classify_task(args.task, use_ollama=not args.no_ollama))
        return 0

    if args.command == "spec":
        spec = structure_research_spec(
            query=args.query,
            ollama_url=cfg["ollama_url"],
            model=cfg["ollama_model"],
            timeout_sec=float(cfg["ollama_timeout_sec"]),
        )
        print(json.dumps(spec, indent=2, ensure_ascii=False))
        return 0

    if args.command == "search":
        rows = web_search(args.query, max_results=args.max_results)
        for row in rows:
            print(f"{row.title}\n  {row.url}\n  {row.snippet}\n")
        print(f"{len(rows)} result(s)")
        return 0

    if args.command == "show":
        project = Path(args.project or cfg["project"]).resolve()
        index = load_index(project)
        facts = load_facts(project)
        print(index_summary_for_agent(index))
        print()
        print(facts_summary_for_agent(facts, query=args.query))
        return 0

    if args.command == "research":
        result = run_web_research(
            args.query,
            project=args.project,
            max_pages=args.max_pages,
            max_search_results=args.max_search_results,
            use_ollama=not args.no_ollama,
            emit_events=args.emit_events,
        )
        if args.json and not args.emit_events:
            payload = {
                "query": result.query,
                "spec": result.spec,
                "pages_fetched": result.pages_fetched,
                "facts_added": result.facts_added,
                "errors": result.errors,
                "answer": result.answer,
                "store_dir": result.store_dir,
                "search_results": [
                    {"title": row.title, "url": row.url, "snippet": row.snippet}
                    for row in result.search_results
                ],
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        elif not args.emit_events:
            print(result.to_tool_text())
        return 0 if result.pages_fetched > 0 or result.facts_added > 0 else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
