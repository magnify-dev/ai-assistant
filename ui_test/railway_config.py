from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ui_test.project_paths import agent_railway_path
from ui_test.config_loader import load_yaml


@dataclass(frozen=True)
class RailwayService:
    name: str
    service_id: str
    url: str
    healthcheck: str = "/health"
    watch_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class RailwayConfig:
    project_id: str
    environment_id: str
    services: dict[str, RailwayService] = field(default_factory=dict)

    def service(self, name: str) -> RailwayService | None:
        return self.services.get(name)

    def services_for_paths(self, changed_paths: list[str]) -> list[RailwayService]:
        if not changed_paths:
            return list(self.services.values())
        matched: list[RailwayService] = []
        for svc in self.services.values():
            if not svc.watch_paths:
                continue
            for path in changed_paths:
                normalized = path.replace("\\", "/")
                for pattern in svc.watch_paths:
                    prefix = pattern.rstrip("*").rstrip("/")
                    if normalized.startswith(prefix) or pattern.rstrip("/") in normalized:
                        matched.append(svc)
                        break
        return matched or list(self.services.values())


def load_railway_config(project: Path) -> RailwayConfig:
    raw = load_yaml(agent_railway_path(project))
    services: dict[str, RailwayService] = {}
    for name, data in (raw.get("services") or {}).items():
        if not isinstance(data, dict):
            continue
        watch = data.get("watch_paths") or []
        if isinstance(watch, str):
            watch = [watch]
        services[name] = RailwayService(
            name=str(name),
            service_id=str(data.get("service_id") or ""),
            url=str(data.get("url") or "").rstrip("/"),
            healthcheck=str(data.get("healthcheck") or "/health"),
            watch_paths=tuple(str(p) for p in watch),
        )
    return RailwayConfig(
        project_id=str(raw.get("project_id") or ""),
        environment_id=str(raw.get("environment_id") or ""),
        services=services,
    )
