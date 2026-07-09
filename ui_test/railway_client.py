from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ui_test.railway_config import RailwayConfig, RailwayService

logger = logging.getLogger(__name__)

RAILWAY_GRAPHQL = "https://backboard.railway.com/graphql/v2"

TERMINAL_STATUSES = {"SUCCESS", "FAILED", "CRASHED", "REMOVED", "SKIPPED"}
ACTIVE_STATUSES = {"BUILDING", "DEPLOYING", "WAITING", "QUEUED", "INITIALIZING"}


@dataclass(frozen=True)
class DeployResult:
    service: str
    deployment_id: str
    status: str
    ok: bool
    message: str


@dataclass(frozen=True)
class HealthResult:
    service: str
    url: str
    ok: bool
    status_code: int
    message: str


def _graphql(token: str, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    headers = {
        "Content-Type": "application/json",
        "Project-Access-Token": token,
    }
    with httpx.Client(timeout=60.0) as client:
        response = client.post(RAILWAY_GRAPHQL, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
    if body.get("errors"):
        raise RuntimeError(f"Railway GraphQL error: {body['errors']}")
    return body.get("data") or {}


def trigger_deploy(token: str, railway: RailwayConfig, service: RailwayService) -> str:
    query = """
    mutation Deploy($serviceId: String!, $environmentId: String!) {
      serviceInstanceDeployV2(serviceId: $serviceId, environmentId: $environmentId)
    }
    """
    data = _graphql(
        token,
        query,
        {"serviceId": service.service_id, "environmentId": railway.environment_id},
    )
    deployment_id = data.get("serviceInstanceDeployV2")
    if not deployment_id:
        raise RuntimeError(f"Deploy trigger returned no deployment id for {service.name}")
    logger.info("Triggered deploy for %s → %s", service.name, deployment_id)
    return str(deployment_id)


def latest_deployment_status(
    token: str,
    railway: RailwayConfig,
    service: RailwayService,
) -> tuple[str, str]:
    query = """
    query Deployments($projectId: String!, $serviceId: String!, $environmentId: String!) {
      deployments(
        first: 1
        input: { projectId: $projectId, serviceId: $serviceId, environmentId: $environmentId }
      ) {
        edges {
          node {
            id
            status
          }
        }
      }
    }
    """
    data = _graphql(
        token,
        query,
        {
            "projectId": railway.project_id,
            "serviceId": service.service_id,
            "environmentId": railway.environment_id,
        },
    )
    edges = (data.get("deployments") or {}).get("edges") or []
    if not edges:
        return "", "UNKNOWN"
    node = edges[0].get("node") or {}
    return str(node.get("id") or ""), str(node.get("status") or "UNKNOWN")


def snapshot_deployment_baselines(
    token: str,
    railway: RailwayConfig,
    services: list[RailwayService],
) -> dict[str, str]:
    """Latest deployment id per service before git push."""
    baselines: dict[str, str] = {}
    for svc in services:
        deployment_id, _status = latest_deployment_status(token, railway, svc)
        baselines[svc.name] = deployment_id
    return baselines


def wait_for_deployments(
    token: str,
    railway: RailwayConfig,
    services: list[RailwayService],
    *,
    baseline_ids: dict[str, str] | None = None,
    timeout_sec: float = 600,
    poll_interval_sec: float = 10,
) -> list[DeployResult]:
    if not services:
        return []
    pending = {svc.name: svc for svc in services}
    results: dict[str, DeployResult] = {}
    deadline = time.time() + timeout_sec
    saw_active: dict[str, bool] = {svc.name: False for svc in services}

    while pending and time.time() < deadline:
        for name, svc in list(pending.items()):
            deployment_id, status = latest_deployment_status(token, railway, svc)
            baseline_id = (baseline_ids or {}).get(name, "")
            logger.info("Deploy %s: %s (%s)", name, status, deployment_id or "no-id")

            if status in ACTIVE_STATUSES:
                saw_active[name] = True

            if baseline_ids is not None:
                if baseline_id and deployment_id == baseline_id and status == "SUCCESS" and not saw_active[name]:
                    # Old container still serving — wait for Railway to start a new build.
                    continue
                if baseline_id and deployment_id != baseline_id:
                    if status in TERMINAL_STATUSES:
                        ok = status == "SUCCESS"
                        results[name] = DeployResult(
                            service=name,
                            deployment_id=deployment_id,
                            status=status,
                            ok=ok,
                            message=f"New deployment {status.lower()}",
                        )
                        del pending[name]
                    continue
                if baseline_id and deployment_id == baseline_id and saw_active[name] and status in TERMINAL_STATUSES:
                    ok = status == "SUCCESS"
                    results[name] = DeployResult(
                        service=name,
                        deployment_id=deployment_id,
                        status=status,
                        ok=ok,
                        message=f"Redeploy {status.lower()}",
                    )
                    del pending[name]
                    continue
                continue

            if status in TERMINAL_STATUSES:
                ok = status == "SUCCESS"
                results[name] = DeployResult(
                    service=name,
                    deployment_id=deployment_id,
                    status=status,
                    ok=ok,
                    message=f"Deployment {status.lower()}",
                )
                del pending[name]
        if pending:
            time.sleep(poll_interval_sec)

    for name, svc in pending.items():
        deployment_id, status = latest_deployment_status(token, railway, svc)
        results[name] = DeployResult(
            service=name,
            deployment_id=deployment_id,
            status=status or "TIMEOUT",
            ok=False,
            message=f"Timed out waiting for new deploy (last status: {status or 'unknown'})",
        )
    return [results[name] for name in sorted(results)]


def check_health(service: RailwayService, *, timeout_sec: float = 30.0) -> HealthResult:
    url = f"{service.url.rstrip('/')}{service.healthcheck}"
    try:
        with httpx.Client(timeout=timeout_sec, follow_redirects=True) as client:
            response = client.get(url)
        ok = 200 <= response.status_code < 400
        return HealthResult(
            service=service.name,
            url=url,
            ok=ok,
            status_code=response.status_code,
            message="OK" if ok else f"Unexpected status {response.status_code}",
        )
    except Exception as exc:
        return HealthResult(
            service=service.name,
            url=url,
            ok=False,
            status_code=0,
            message=str(exc),
        )
