from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid4())


def empty_project(topic: str, period_days: int = 7) -> dict[str, Any]:
    return {
        "id": new_id(),
        "topic": topic.strip(),
        "periodDays": period_days,
        "researchAsOf": None,
        "engine": "claude-agent-sdk",
        "engineSessionId": None,
        "status": "created",
        "stage": "research",
        "waitKind": None,
        "progress": [],
        "currentQuestion": None,
        "answers": [],
        "candidates": [],
        "candidateVersion": 1,
        "selection": [],
        "selectionVersion": 1,
        "investigation": None,
        "editorial": None,
        "editorialVersion": 1,
        "editorialChoice": None,
        "storyboard": None,
        "storyboardVersion": 1,
        "cards": [],
        "resultVersion": 1,
        "finalApproved": False,
        "finalApprovedVersion": None,
        "activeRunId": None,
        "runs": {},
        "errors": [],
        "requestLog": {},
        "provenance": [],
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
    }
