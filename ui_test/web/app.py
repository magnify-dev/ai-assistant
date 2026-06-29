from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ui_test.config_loader import engine_root, load_engine_config, merged_config
from ui_test.events import get_run_state
from ui_test.pipeline import run_ui_test_loop

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="UI Test Runner", version="0.1.0")


class RunRequest(BaseModel):
    project: str
    task: str = ""
    push: bool = False
    skip_deploy: bool = False
    skip_structure: bool = False
    skip_ui: bool = False
    no_ollama: bool = False


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    path = STATIC_DIR / "index.html"
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/api/status")
def status() -> dict[str, Any]:
    state = get_run_state()
    return {
        "running": state.get("running", False),
        "log": state.get("log") or [],
        "last_result": state.get("last_result"),
    }


@app.get("/api/config")
def config() -> dict[str, Any]:
    cfg = load_engine_config()
    web = cfg.get("web") if isinstance(cfg.get("web"), dict) else {}
    default_project = ""
    defaults = cfg.get("defaults") if isinstance(cfg.get("defaults"), dict) else {}
    if defaults.get("project"):
        default_project = str(defaults["project"])
    return {
        "default_project": default_project,
        "port": web.get("port", 8767),
    }


@app.post("/api/run")
def run_tests(body: RunRequest) -> dict[str, Any]:
    state = get_run_state()
    if state.get("running"):
        raise HTTPException(status_code=409, detail="A test run is already in progress")

    project = Path(body.project).expanduser().resolve()
    if not project.is_dir():
        raise HTTPException(status_code=400, detail=f"Project not found: {project}")

    def worker() -> None:
        try:
            run_ui_test_loop(
                project,
                task=body.task or None,
                skip_deploy=body.skip_deploy,
                skip_structure=body.skip_structure,
                skip_ui=body.skip_ui,
                do_push=body.push,
                no_ollama=body.no_ollama,
            )
        except Exception as exc:
            state = get_run_state()
            state["log"] = (state.get("log") or []) + [f"ERROR: {exc}"]
            state["running"] = False
            state["last_result"] = {"overall_ok": False, "error": str(exc)}

    threading.Thread(target=worker, daemon=True).start()
    return {"started": True}


def run_server() -> None:
    cfg = load_engine_config()
    web = cfg.get("web") if isinstance(cfg.get("web"), dict) else {}
    host = str(web.get("host") or "127.0.0.1")
    port = int(web.get("port") or 8767)
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
