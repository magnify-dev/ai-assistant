import json
import re
from pathlib import Path

PROJECT = Path(r"C:\Users\marce\Documents\Programming\content-manager")
LOG = Path(r"C:\Users\marce\Documents\Programming\ai-assistant\logs\test-runner-last-run.log")
SID = "web_ec063a28db4140f185bfadce4accbae6"

cap = json.loads(
    (PROJECT / ".agent/web-capture/by-url/www-wowhead-com-mop-classic-news.json").read_text(
        encoding="utf-8"
    )
)
pu = cap.get("page_understanding") or {}
print("=== MOP FEED ===")
print("page_type", pu.get("page_type"))
print("summary", str(pu.get("summary") or "")[:240])
feeds = pu.get("feed_items") or []
print("feed_items", len(feeds))
for i, f in enumerate(feeds[:15]):
    print(i, f.get("date"), "|", (f.get("title") or "")[:90])

home = PROJECT / ".agent/web-capture/by-url/www-wowhead-com.json"
print("\nhome cache", home.exists())
if home.exists():
    h = json.loads(home.read_text(encoding="utf-8"))
    print("home els", len(h.get("elements") or []))
    print("home feeds", len((h.get("page_understanding") or {}).get("feed_items") or []))
    vp = h.get("viewport") or {}
    sm = h.get("scroll_map") or {}
    print("viewport", {k: vp.get(k) for k in ("width", "height", "document_height", "scroll_y")})
    print("scroll_map keys", list(sm.keys())[:12], "stitched", sm.get("stitched"))

text = LOG.read_text(encoding="utf-8", errors="replace")
# isolate session chunk
starts = [m.start() for m in re.finditer(re.escape(SID), text)]
print("\nsession mentions", len(starts))
chunk = text[starts[0] : starts[-1] + 5000] if starts else text[-500000:]

print("\n=== KEY EVENTS ===")
for line in chunk.splitlines():
    if any(
        k in line
        for k in (
            "web_research_result",
            "origin_loaded",
            "section_hub",
            "Direct navigate",
            "seed_url",
            "goto",
            "blocked",
            "status\":\"done",
            "status\":\"failed",
            "status\":\"running",
            "Pre-map overlay",
            "Action stalled",
            "web_step",
            "Reusing stored",
        )
    ):
        # keep short
        if '"type":"web_snapshot"' in line:
            continue
        if '"type":"web_llm_exchange"' in line:
            continue
        print(line[:320])

# parse NDJSON decisions
print("\n=== DECISIONS ===")
for line in chunk.splitlines():
    if '"type":"web_decision"' not in line and '"type":"web_step"' not in line:
        continue
    try:
        obj = json.loads(line)
    except Exception:
        continue
    if obj.get("session_id") != SID:
        continue
    if obj.get("type") == "web_decision":
        d = obj.get("decision") or {}
        print(
            "decision",
            obj.get("step_id"),
            d.get("action"),
            d.get("target_id"),
            str(d.get("reason") or "")[:160],
        )
    else:
        print(
            "step",
            obj.get("step_id"),
            obj.get("action"),
            "ok=",
            obj.get("ok"),
            "err=",
            str(obj.get("error") or "")[:120],
        )

# final result
print("\n=== RESULTS ===")
for line in chunk.splitlines():
    if '"type":"web_research_result"' in line and SID in line:
        try:
            obj = json.loads(line)
            print(json.dumps({k: obj.get(k) for k in ("status", "answer", "error", "url", "ok") if k in obj or True}, indent=2)[:800])
        except Exception:
            print(line[:400])
