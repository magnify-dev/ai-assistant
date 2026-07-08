from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ui_test.project_paths import agent_specs_dir, agent_tasks_dir
from ui_test.config_loader import load_yaml
from ui_test.step_log import collect_test_ids


@dataclass(frozen=True)
class SpecBundle:
    path: Path
    data: dict[str, Any]
    required_test_ids: set[str] = field(default_factory=set)


def load_spec(path: Path) -> SpecBundle:
    data = load_yaml(path)
    return SpecBundle(path=path, data=data, required_test_ids=collect_test_ids(data))


def resolve_spec_file(project: Path, config: dict[str, Any]) -> Path:
    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
    spec_name = str(defaults.get("spec") or "mini-admin.yaml")
    spec_dir = agent_specs_dir(project)
    candidate = spec_dir / spec_name
    if candidate.is_file():
        return candidate
    fallback = spec_dir / "mini-admin.yaml"
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(f"No spec found in {spec_dir}")


def base_url_for_spec(
    spec: SpecBundle,
    config: dict[str, Any],
    railway_services: dict[str, Any],
    *,
    project: Path | None = None,
    skip_deploy: bool = False,
) -> str:
    project_meta = spec.data.get("project") if isinstance(spec.data.get("project"), dict) else {}
    if project_meta.get("root"):
        return str(project_meta["root"]).rstrip("/")
    if skip_deploy and project is not None:
        from ui_test.project_profile import local_base_url

        local_url = local_base_url(project)
        if local_url:
            return local_url
    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
    service_name = str(project_meta.get("base_service") or defaults.get("base_url_service") or "admin")
    svc = railway_services.get(service_name)
    if svc and hasattr(svc, "url"):
        return str(svc.url).rstrip("/")
    if isinstance(svc, dict):
        return str(svc.get("url") or "").rstrip("/")
    return ""


def save_structured_task(project: Path, payload: dict[str, Any]) -> Path:
    tasks_dir = agent_tasks_dir(project)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    path = tasks_dir / "current.structured.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path
