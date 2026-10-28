from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .models import now_iso
from .paths import DATA_DIR

_lock = threading.Lock()


def _path(project_id: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{project_id}.json"


def load(project_id: str) -> dict[str, Any] | None:
    path = _path(project_id)
    if not path.exists():
        return None
    with _lock:
        return json.loads(path.read_text(encoding="utf-8"))


def save(project: dict[str, Any]) -> dict[str, Any]:
    project["updatedAt"] = now_iso()
    path = _path(project["id"])
    tmp = path.with_suffix(".tmp")
    text = json.dumps(project, ensure_ascii=False, indent=2)
    with _lock:
        tmp.write_text(text, encoding="utf-8")
        # Windows에서는 백신·OneDrive·검색 색인이 파일을 잠깐 열고 있으면
        # 덮어쓰기가 거부된다(WinError 5). 잠깐 기다렸다가 다시 시도한다.
        for attempt in range(20):
            try:
                tmp.replace(path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05 * (attempt + 1))
    return project


def mark_interrupted() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path in DATA_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("status") == "running":
            data["status"] = "interrupted"
            data["errors"] = data.get("errors") or []
            data["errors"].append(
                {
                    "code": "interrupted",
                    "message": "서버가 재시작되어 진행 중이던 작업을 멈췄습니다.",
                    "nextActions": ["research를 다시 요청하거나 마지막 단계에서 재시도하세요."],
                }
            )
            data["updatedAt"] = now_iso()
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
