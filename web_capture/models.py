from __future__ import annotations

from typing import Any, Literal, TypedDict


LocatorStatus = Literal["unique", "ambiguous", "unresolved", "synthetic"]


class Rect(TypedDict):
    x: float
    y: float
    width: float
    height: float


class Viewport(TypedDict):
    width: float
    height: float
    scroll_x: float
    scroll_y: float
    document_width: float
    document_height: float


class LocatorCandidate(TypedDict, total=False):
    kind: str
    value: str
    role: str
    name: str
    count: int
    frame_index: int
    frame_url: str


class CaptureElement(TypedDict, total=False):
    id: str
    index: int
    kind: str
    text: str | None
    aria: str | None
    rect: Rect
    locator_candidates: list[LocatorCandidate]
    locator_status: LocatorStatus
    locator: LocatorCandidate | None
    ai_interactive: bool | None
    ai_confidence: float | None
    ai_control_type: str | None
    ai_reason: str | None
    map_layer: str | None
    content_role: str | None
    likely_clickable: bool | None
    title: str | None
    dates: list[str] | None
    deterministic_issues: list[str]
    raw: dict[str, Any]


class WebCapture(TypedDict, total=False):
    version: int
    capture_id: str
    fingerprint: str
    created_at: str
    url: str
    title: str
    context: str
    viewport: Viewport
    elements: list[CaptureElement]
    summary: dict[str, int]
    ai: dict[str, Any]
