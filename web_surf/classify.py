from __future__ import annotations

import argparse
import json
import logging

import httpx

from web_surf.config import default_config

logger = logging.getLogger(__name__)

RUN_KIND_UI_TEST = "ui_test"
RUN_KIND_WEB_RESEARCH = "web_research"

_UI_SIGNALS = (
    "button",
    "modal",
    "login",
    "navbar",
    "home screen",
    "home page",
    "playwright",
    "deploy",
    "click the",
    "remove a",
    "add a",
    "verify",
    "test the",
    "page load",
    "settings page",
    "ui test",
    "explore the app",
    "open the app",
)

_WEB_SIGNALS = (
    "research",
    "look up",
    "find out",
    "what is",
    "who is",
    "search the web",
    "search online",
    "compare prices",
    "competitors",
    "latest news",
    "tell me about",
    "how does",
    "explain what",
    "find information",
    "gather data",
    "scrape",
    "from the internet",
    "on the web",
)


def _score(signals: tuple[str, ...], text: str) -> int:
    return sum(1 for signal in signals if signal in text)


def classify_task_heuristic(task: str) -> str | None:
    text = task.strip().lower()
    if not text:
        return RUN_KIND_UI_TEST

    ui_score = _score(_UI_SIGNALS, text)
    web_score = _score(_WEB_SIGNALS, text)

    if web_score > 0 and ui_score == 0:
        return RUN_KIND_WEB_RESEARCH
    if ui_score > 0 and web_score == 0:
        return RUN_KIND_UI_TEST
    if web_score >= 2 and web_score > ui_score:
        return RUN_KIND_WEB_RESEARCH
    if ui_score >= 2 and ui_score > web_score:
        return RUN_KIND_UI_TEST
    return None


def _get_classify_prompt() -> str:
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[1] / "prompts.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return str(data.get("web_research", {}).get("classify", "")).strip()


def classify_task_with_ollama(
    task: str,
    *,
    ollama_url: str,
    model: str,
    timeout_sec: float = 60.0,
) -> str:
    prompt = _get_classify_prompt()
    if not prompt:
        return RUN_KIND_UI_TEST

    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"User request:\n{task.strip()}"},
        ],
    }
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            response = client.post(f"{ollama_url.rstrip('/')}/api/chat", json=payload)
            response.raise_for_status()
            content = (response.json().get("message") or {}).get("content") or ""
        parsed = json.loads(content)
        kind = str(parsed.get("run_kind") or "").strip()
        if kind in {RUN_KIND_UI_TEST, RUN_KIND_WEB_RESEARCH}:
            return kind
    except Exception as exc:
        logger.warning("Task classification failed: %s", exc)
    return RUN_KIND_UI_TEST


def classify_task(task: str, *, use_ollama: bool = True) -> str:
    text = task.strip()
    if not text:
        return RUN_KIND_UI_TEST

    heuristic = classify_task_heuristic(text)
    if heuristic:
        return heuristic

    if not use_ollama:
        return RUN_KIND_UI_TEST

    cfg = default_config()
    return classify_task_with_ollama(
        text,
        ollama_url=cfg["ollama_url"],
        model=cfg["ollama_model"],
        timeout_sec=min(float(cfg["ollama_timeout_sec"]), 60.0),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify a user task")
    parser.add_argument("--task", required=True)
    parser.add_argument("--no-ollama", action="store_true")
    args = parser.parse_args(argv)
    print(classify_task(args.task, use_ollama=not args.no_ollama))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
