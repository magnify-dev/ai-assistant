from __future__ import annotations

import re
import time

from audit import audit_event
from tools import execute_tool, extract_playlist_query
from voice_context import _last_action
def _handle_browser_command(text: str, cfg: dict, command_id: str | None = None) -> str | None:
    lowered = text.lower()
    previous_text = str(_last_action.get("text", "")).lower()
    previous_reply = str(_last_action.get("reply", "")).lower()
    youtube_context = "youtube" in lowered or "youtube" in previous_text or "youtube" in previous_reply
    playlist_intent = bool(re.search(r"\bplaylists?\b", lowered))
    read_intent = bool(re.search(r"\b(see|read|which|what|list|contents)\b", lowered))
    open_intent = bool(re.search(r"\b(open|go|navigate|show|take)\b", lowered))
    click_intent = bool(re.search(r"\b(click|press|select|choose)\b", lowered))
    browser_context_intent = bool(
        re.search(r"\b(browser|firefox|page|tab|website|site|link|button|video|playing|song|music)\b", lowered)
        or "what do you see" in lowered
    )
    browser_control_intent = bool(
        re.search(r"\b(control|use|start|launch)\b.*\b(browser|firefox|web)\b", lowered)
        or "firefox bridge" in lowered
    )
    browser_question_intent = bool(
        browser_context_intent
        and re.search(r"\b(is|are|am|do|does|did|has|have|can|could|would|will|should)\b", lowered)
    )

    def browser_error(result: str) -> str:
        lowered_result = result.lower()
        if "no visible video found" in lowered_result:
            return "I couldn't find a visible video to play."
        if "no visible play button or video found" in lowered_result:
            return "I couldn't find a play button or video."
        if "play blocked" in lowered_result or "play failed" in lowered_result:
            return "I couldn't start playback. Make sure the YouTube tab is visible in Firefox and try again."
        if "no visible element matched" in lowered_result:
            return "I couldn't find that on the page."
        if "no extension connection" in lowered_result:
            return "Firefox isn't connected to Jarvis yet. Wait a moment after startup, or open Firefox with the FoxMCP extension enabled."
        if "script result from tab" in lowered_result:
            return "The browser action did not complete."
        return result[:140]

    def browser_target(verbs: tuple[str, ...], source_text: str | None = None) -> str:
        source = source_text or text
        command = re.sub(r"\bplease\b", " ", source, flags=re.IGNORECASE).strip(" .!?")
        match = re.search(r"\b(" + "|".join(re.escape(verb) for verb in verbs) + r")\b", command, re.IGNORECASE)
        if not match:
            return ""
        target = command[match.end() :].strip(" .!?")
        target = re.split(
            r"\b(?:and|then)\s+(?:press|click|hit)?\s*play\b",
            target,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" .!?")
        target = re.sub(
            r"^(on|in|inside|the|current|this|firefox|browser|page|tab|link|button|to)\b\s*",
            "",
            target,
            flags=re.IGNORECASE,
        ).strip(" .!?")
        name_match = re.search(r"(.+?)\s+(?:is|as)\s+the\s+name\b", target, flags=re.IGNORECASE)
        if name_match:
            target = name_match.group(1)
        target = re.split(r"\s*,\s*|\s+\b(?:playlist|link|button)\b", target, maxsplit=1, flags=re.IGNORECASE)[0]
        target = re.sub(r"^(the|a|an)\b\s*", "", target, flags=re.IGNORECASE).strip(" .!?")
        return target

    def browser_steps(command_text: str) -> list[dict[str, str]]:
        parts = [
            part.strip(" .!?")
            for part in re.split(r"\b(?:and then|then|and)\b", command_text, flags=re.IGNORECASE)
            if part.strip(" .!?")
        ]
        if not parts:
            parts = [command_text.strip(" .!?")]

        steps: list[dict[str, str]] = []
        for part in parts:
            part_lower = part.lower()
            part_click = bool(re.search(r"\b(click|press|select|choose)\b", part_lower))
            part_open = bool(re.search(r"\b(open|go|navigate|show|take)\b", part_lower))
            if re.search(r"\b(?:press|click|hit)?\s*play\b", part_lower):
                if re.search(r"\b(first|1st)\b", part_lower) and re.search(r"\b(video|song|track)\b", part_lower):
                    steps.append({"action": "click", "query": "play first video"})
                else:
                    steps.append({"action": "click", "query": "play"})
                continue
            if part_click:
                if re.search(r"\bplaylists?\b", part_lower):
                    query = extract_playlist_query(part)
                else:
                    query = browser_target(("click", "press", "select", "choose"), part)
                if query:
                    steps.append({"action": "click", "query": query})
                continue
            if "youtube" in part_lower and part_open:
                steps.append({"action": "navigate", "url": "https://www.youtube.com"})
                continue
            if "playlist" in part_lower and (part_open or youtube_context):
                steps.append({"action": "navigate", "url": "https://www.youtube.com/feed/playlists"})
                continue
            url_match = re.search(r"https?://\S+", part)
            if part_open and url_match:
                steps.append({"action": "navigate", "url": url_match.group(0).rstrip(" .!?")})
                continue
        return steps

    planned_steps = browser_steps(text)
    if planned_steps:
        audit_event(cfg, "browser_plan", command_id=command_id, text=text, steps=planned_steps)
        completed: list[str] = []
        for idx, step in enumerate(planned_steps, start=1):
            if idx > 1:
                time.sleep(0.4)
            if step["action"] == "navigate":
                audit_event(cfg, "tool_call", command_id=command_id, name="navigate_browser_context", arguments={"url": step["url"]})
                result = execute_tool("navigate_browser_context", {"url": step["url"]})
                audit_event(cfg, "tool_result", command_id=command_id, name="navigate_browser_context", result=result)
                if result != "OK":
                    return f"I completed {len(completed)} step{'s' if len(completed) != 1 else ''}, then got stuck. {browser_error(result)}"
                completed.append("navigated")
            elif step["action"] == "click":
                click_args = {"query": step["query"], "utterance": text}
                audit_event(cfg, "tool_call", command_id=command_id, name="click_browser_context", arguments=click_args)
                result = execute_tool("click_browser_context", click_args)
                audit_event(cfg, "tool_result", command_id=command_id, name="click_browser_context", result=result)
                if result != "OK":
                    return f"I completed {len(completed)} step{'s' if len(completed) != 1 else ''}, then got stuck. {browser_error(result)}"
                completed.append(f"clicked {step['query']}")
        return "Done."

    if click_intent:
        query = browser_target(("click", "press", "select", "choose"))
        if query:
            audit_event(cfg, "local_command", command_id=command_id, kind="browser_context_click", text=text, query=query)
            click_args = {"query": query, "utterance": text}
            audit_event(cfg, "tool_call", command_id=command_id, name="click_browser_context", arguments=click_args)
            result = execute_tool("click_browser_context", click_args)
            audit_event(cfg, "tool_result", command_id=command_id, name="click_browser_context", result=result)
            if result == "OK":
                return f"Clicked {query}."
            return browser_error(result)

    if playlist_intent and open_intent and not read_intent:
        context_result = None
        # If the user names a specific playlist, try the real Firefox DOM links first.
        generic_playlist_page = bool(re.search(r"\b(my playlists?|my playlist|playlists? page)\b", lowered))
        if not generic_playlist_page:
            query = extract_playlist_query(text)
            if query:
                link_args = {"query": query, "utterance": text}
                audit_event(cfg, "tool_call", command_id=command_id, name="open_browser_context_link", arguments=link_args)
                context_result = execute_tool("open_browser_context_link", link_args)
                audit_event(cfg, "tool_result", command_id=command_id, name="open_browser_context_link", result=context_result)
                if context_result.startswith("Opened "):
                    return "Done."

        url = "https://www.youtube.com/feed/playlists"
        audit_event(cfg, "local_command", command_id=command_id, kind="browser", url=url, text=text)
        audit_event(cfg, "tool_call", command_id=command_id, name="navigate_browser_context", arguments={"url": url})
        result = execute_tool("navigate_browser_context", {"url": url})
        audit_event(cfg, "tool_result", command_id=command_id, name="navigate_browser_context", result=result)
        if result == "OK":
            return "Done."
        return f"I couldn't open your YouTube playlists. {browser_error(result)}"

    if playlist_intent and read_intent:
        question = "List the visible YouTube playlist names from Firefox."
        audit_event(cfg, "local_command", command_id=command_id, kind="browser_context_read", text=text, question=question)
        audit_event(cfg, "tool_call", command_id=command_id, name="read_browser_context", arguments={"question": question})
        result = execute_tool("read_browser_context", {"question": question})
        audit_event(cfg, "tool_result", command_id=command_id, name="read_browser_context", result=result)
        if not result or "no firefox page context" in result.lower():
            return "I can't read Firefox yet. Load the Jarvis extension into your normal Firefox first."
        return result

    if playlist_intent and youtube_context:
        url = "https://www.youtube.com/feed/playlists"
        audit_event(cfg, "local_command", command_id=command_id, kind="browser", url=url, text=text)
        audit_event(cfg, "tool_call", command_id=command_id, name="navigate_browser_context", arguments={"url": url})
        result = execute_tool("navigate_browser_context", {"url": url})
        audit_event(cfg, "tool_result", command_id=command_id, name="navigate_browser_context", result=result)
        if result == "OK":
            return "Done."
        return f"I couldn't open your YouTube playlists. {browser_error(result)}"

    if "youtube" in lowered and open_intent:
        url = "https://www.youtube.com"
        audit_event(cfg, "local_command", command_id=command_id, kind="browser", url=url, text=text)
        audit_event(cfg, "tool_call", command_id=command_id, name="navigate_browser_context", arguments={"url": url})
        result = execute_tool("navigate_browser_context", {"url": url})
        audit_event(cfg, "tool_result", command_id=command_id, name="navigate_browser_context", result=result)
        if result == "OK":
            return "Done."
        return f"I couldn't open YouTube. {browser_error(result)}"

    if browser_control_intent:
        audit_event(cfg, "local_command", command_id=command_id, kind="browser_context_start", text=text)
        audit_event(cfg, "tool_call", command_id=command_id, name="setup_firefox_bridge", arguments={})
        result = execute_tool("setup_firefox_bridge", {})
        audit_event(cfg, "tool_result", command_id=command_id, name="setup_firefox_bridge", result=result)
        if result.startswith("Opened "):
            return "I opened the Firefox extension setup. Load the Jarvis extension into your normal Firefox."
        return result[:220]

    if read_intent and browser_context_intent:
        question = text.strip()
        audit_event(cfg, "local_command", command_id=command_id, kind="browser_context_read", text=text, question=question)
        audit_event(cfg, "tool_call", command_id=command_id, name="read_browser_context", arguments={"question": question})
        result = execute_tool("read_browser_context", {"question": question})
        audit_event(cfg, "tool_result", command_id=command_id, name="read_browser_context", result=result)
        return result

    if browser_question_intent:
        question = text.strip()
        audit_event(cfg, "local_command", command_id=command_id, kind="browser_context_question", text=text, question=question)
        audit_event(cfg, "tool_call", command_id=command_id, name="read_browser_context", arguments={"question": question})
        result = execute_tool("read_browser_context", {"question": question})
        audit_event(cfg, "tool_result", command_id=command_id, name="read_browser_context", result=result)
        return result

    url_match = re.search(r"https?://\S+", text)
    if open_intent and url_match:
        url = url_match.group(0).rstrip(" .!?")
        audit_event(cfg, "local_command", command_id=command_id, kind="browser_context_navigate", text=text, url=url)
        audit_event(cfg, "tool_call", command_id=command_id, name="navigate_browser_context", arguments={"url": url})
        result = execute_tool("navigate_browser_context", {"url": url})
        audit_event(cfg, "tool_result", command_id=command_id, name="navigate_browser_context", result=result)
        if result == "OK":
            return "Navigated Firefox."
        return browser_error(result)

    if open_intent and browser_context_intent:
        query = browser_target(("open", "go", "navigate", "show", "take"))
        if query:
            audit_event(cfg, "local_command", command_id=command_id, kind="browser_context_open_link", text=text, query=query)
            link_args = {"query": query, "utterance": text}
            audit_event(cfg, "tool_call", command_id=command_id, name="open_browser_context_link", arguments=link_args)
            result = execute_tool("open_browser_context_link", link_args)
            audit_event(cfg, "tool_result", command_id=command_id, name="open_browser_context_link", result=result)
            if result.startswith(("Clicked ", "Opened ")):
                return f"Opened {query}."
            return browser_error(result)

    return None


