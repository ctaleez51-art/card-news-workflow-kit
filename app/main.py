from __future__ import annotations

import socket
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import jobs, store
from .models import empty_project
from .paths import STATIC_DIR, cards_dir, export_dir, project_dir


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.mark_interrupted()
    yield


app = FastAPI(title="카드뉴스 워크플로", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class CreateProject(BaseModel):
    topic: str
    periodDays: int | None = 7


class RequestIdBody(BaseModel):
    requestId: str


class SelectionBody(BaseModel):
    candidateIds: list[str]
    version: int


class AnswerBody(BaseModel):
    questionId: str
    version: int
    answer: str | list[str]
    requestId: str


class ApproveBody(BaseModel):
    stage: str
    version: int
    requestId: str


class EditorialBody(BaseModel):
    audience: str | None = None
    format: str | None = None
    hook: str | None = None
    version: int
    requestId: str


class StoryboardChangeBody(BaseModel):
    cardIds: list[str]
    cardNotes: dict[str, str] = {}
    version: int
    requestId: str


def _change_instruction(body: StoryboardChangeBody) -> str:
    lines = []
    for cid in body.cardIds:
        note = (body.cardNotes.get(cid) or "").strip()
        lines.append(f"카드 {cid}: " + (note if note else "더 정확하고 읽기 쉽게 다시 쓰세요."))
    return "\n".join(lines)


class RevisionBody(BaseModel):
    cardIds: list[str]
    instruction: str
    version: int
    requestId: str


def _idempotent(project: dict[str, Any], request_id: str, payload: Any) -> dict[str, Any] | None:
    log = project.get("requestLog") or {}
    prev = log.get(request_id)
    if not prev:
        return None
    if prev.get("payload") != payload:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "request_conflict",
                "message": "같은 requestId에 다른 내용이 있습니다.",
                "nextActions": ["새 requestId로 다시 보내세요."],
            },
        )
    return prev.get("result")


def _remember(project: dict[str, Any], request_id: str, payload: Any, result: dict[str, Any]) -> None:
    project.setdefault("requestLog", {})[request_id] = {"payload": payload, "result": result}
    store.save(project)


def _public(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": project["id"],
        "topic": project["topic"],
        "periodDays": project["periodDays"],
        "researchAsOf": project.get("researchAsOf"),
        "engine": project.get("engine"),
        "status": project["status"],
        "stage": project.get("stage"),
        "waitKind": project.get("waitKind"),
        "progress": project.get("progress") or [],
        "currentQuestion": project.get("currentQuestion"),
        "candidates": project.get("candidates") or [],
        "candidateVersion": project.get("candidateVersion"),
        "selection": project.get("selection") or [],
        "selectionVersion": project.get("selectionVersion"),
        "investigation": project.get("investigation"),
        "editorial": project.get("editorial"),
        "editorialVersion": project.get("editorialVersion"),
        "editorialChoice": project.get("editorialChoice"),
        "storyboard": project.get("storyboard"),
        "storyboardVersion": project.get("storyboardVersion"),
        "cards": project.get("cards") or [],
        "resultVersion": project.get("resultVersion"),
        "finalApproved": project.get("finalApproved"),
        "activeRunId": project.get("activeRunId"),
        "errors": project.get("errors") or [],
        "createdAt": project.get("createdAt"),
        "updatedAt": project.get("updatedAt"),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/projects")
def create_project(body: CreateProject) -> dict[str, str]:
    topic = (body.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail={"code": "invalid_answer", "message": "주제를 입력하세요."})
    project = empty_project(topic, body.periodDays or 7)
    store.save(project)
    return {"projectId": project["id"]}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    project = store.load(project_id)
    if not project:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "프로젝트가 없습니다."})
    return _public(project)


@app.post("/api/projects/{project_id}/research")
async def start_research(project_id: str, body: RequestIdBody) -> dict[str, Any]:
    project = store.load(project_id)
    if not project:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "프로젝트가 없습니다."})
    payload = body.model_dump()
    cached = _idempotent(project, body.requestId, payload)
    if cached:
        return cached
    if project.get("status") == "running":
        result = {"runId": project.get("activeRunId"), "status": "running"}
        _remember(project, body.requestId, payload, result)
        return result
    run_id = jobs.start_run(project_id, "research")
    result = {"runId": run_id, "status": "running"}
    project = store.load(project_id) or project
    _remember(project, body.requestId, payload, result)
    return result


@app.post("/api/projects/{project_id}/selection")
async def save_selection(project_id: str, body: SelectionBody) -> dict[str, Any]:
    project = store.load(project_id)
    if not project:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "프로젝트가 없습니다."})
    if body.version != project.get("candidateVersion"):
        raise HTTPException(
            status_code=409,
            detail={"code": "stale_question", "message": "후보 목록이 바뀌었습니다. 새로고침 후 다시 고르세요."},
        )
    if not 1 <= len(body.candidateIds) <= 3:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_answer", "message": "후보는 1~3개 고르세요.", "nextActions": ["체크박스를 조정하세요."]},
        )
    known = {c["id"] for c in project.get("candidates") or []}
    if any(cid not in known for cid in body.candidateIds):
        raise HTTPException(status_code=400, detail={"code": "invalid_answer", "message": "알 수 없는 후보입니다."})
    project["selection"] = body.candidateIds
    project["selectionVersion"] = int(project.get("selectionVersion") or 1) + 1
    project["finalApproved"] = False
    store.save(project)
    run_id = jobs.start_run(project_id, "investigate")
    return {"saved": True, "runId": run_id, "selection": body.candidateIds}


@app.post("/api/projects/{project_id}/answers")
async def answer(project_id: str, body: AnswerBody) -> dict[str, Any]:
    project = store.load(project_id)
    if not project:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "프로젝트가 없습니다."})
    payload = {"questionId": body.questionId, "version": body.version, "answer": body.answer}
    cached = _idempotent(project, body.requestId, payload)
    if cached:
        return cached
    q = project.get("currentQuestion")
    if not q or q.get("id") != body.questionId or q.get("version") != body.version:
        raise HTTPException(
            status_code=409,
            detail={"code": "stale_question", "message": "오래된 질문 답변입니다.", "nextActions": ["화면을 새로고침하세요."]},
        )
    if project.get("status") == "running":
        result = {"accepted": False, "status": "running"}
        _remember(project, body.requestId, payload, result)
        return result

    answer_text = body.answer if isinstance(body.answer, str) else ", ".join(body.answer)
    project.setdefault("answers", []).append(
        {"questionId": body.questionId, "version": body.version, "answer": answer_text, "at": store.load(project_id) and project.get("updatedAt")}
    )
    project["currentQuestion"] = None

    if body.questionId == "expand_period":
        if "30" in answer_text:
            project["periodDays"] = 30
        store.save(project)
        run_id = jobs.start_run(project_id, "research")
        result = {"accepted": True, "status": "running", "runId": run_id}
        project = store.load(project_id) or project
        _remember(project, body.requestId, payload, result)
        return result

    store.save(project)
    run_id = jobs.start_run(project_id, "storyboard", {"audience": answer_text, "note": answer_text})
    result = {"accepted": True, "status": "running", "runId": run_id}
    project = store.load(project_id) or project
    _remember(project, body.requestId, payload, result)
    return result


@app.post("/api/projects/{project_id}/approve")
async def approve(project_id: str, body: ApproveBody) -> dict[str, Any]:
    project = store.load(project_id)
    if not project:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "프로젝트가 없습니다."})
    payload = body.model_dump()
    cached = _idempotent(project, body.requestId, payload)
    if cached:
        return cached
    if project.get("status") == "running":
        result = {"runId": project.get("activeRunId"), "status": "running"}
        _remember(project, body.requestId, payload, result)
        return result

    if body.stage == "storyboard":
        if body.version != project.get("storyboardVersion"):
            raise HTTPException(status_code=409, detail={"code": "stale_question", "message": "스토리보드 버전이 바뀌었습니다."})
        run_id = jobs.start_run(project_id, "produce")
        result = {"runId": run_id, "status": "running"}
        project = store.load(project_id) or project
        _remember(project, body.requestId, payload, result)
        return result

    if body.stage == "final":
        if body.version != project.get("resultVersion"):
            raise HTTPException(status_code=409, detail={"code": "stale_question", "message": "결과 버전이 바뀌었습니다."})
        failed = [c for c in project.get("cards") or [] if c.get("failed")]
        if failed:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "image_failed",
                    "message": "실패한 카드가 있어 최종 승인할 수 없습니다.",
                    "nextActions": ["해당 카드만 다시 생성하세요."],
                },
            )
        project["finalApproved"] = True
        project["finalApprovedVersion"] = body.version
        project["status"] = "done"
        jobs.write_export(project)
        store.save(project)
        result = {"status": "done", "finalApproved": True}
        _remember(project, body.requestId, payload, result)
        return result

    raise HTTPException(status_code=400, detail={"code": "invalid_answer", "message": "stage는 storyboard 또는 final입니다."})


_BACK = {
    "investigate": "selection",
    "editorial": "selection",
    "storyboard": "editorial",
    "produce": "storyboard",
    "review": "storyboard",
}


@app.post("/api/projects/{project_id}/back")
async def go_back(project_id: str, body: RequestIdBody) -> dict[str, Any]:
    project = store.load(project_id)
    if not project:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "프로젝트가 없습니다."})
    payload = body.model_dump()
    cached = _idempotent(project, body.requestId, payload)
    if cached:
        return cached
    if project.get("status") == "running":
        raise HTTPException(
            status_code=409,
            detail={"code": "running", "message": "작업 중에는 돌아갈 수 없습니다.", "nextActions": ["먼저 취소하세요."]},
        )
    target = _BACK.get(project.get("stage") or "")
    if not target:
        raise HTTPException(status_code=400, detail={"code": "invalid_answer", "message": "더 돌아갈 단계가 없습니다."})
    if target == "storyboard" and not project.get("storyboard"):
        target = "editorial"
    if target == "editorial" and not project.get("editorial"):
        target = "selection"
    project["status"] = "waiting_for_user"
    project["waitKind"] = target
    project["stage"] = target
    project["errors"] = []
    project["currentQuestion"] = None
    project["finalApproved"] = False
    store.save(project)
    result = {"stage": target, "status": "waiting_for_user"}
    _remember(project, body.requestId, payload, result)
    return result


@app.post("/api/projects/{project_id}/editorial")
async def choose_editorial(project_id: str, body: EditorialBody) -> dict[str, Any]:
    project = store.load(project_id)
    if not project:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "프로젝트가 없습니다."})
    payload = body.model_dump()
    cached = _idempotent(project, body.requestId, payload)
    if cached:
        return cached
    if project.get("status") == "running":
        result = {"runId": project.get("activeRunId"), "status": "running"}
        _remember(project, body.requestId, payload, result)
        return result
    if project.get("waitKind") != "editorial":
        raise HTTPException(
            status_code=409,
            detail={"code": "stale_question", "message": "편집 방향을 고르는 단계가 아닙니다.", "nextActions": ["화면을 새로고침하세요."]},
        )
    if body.version != project.get("editorialVersion"):
        raise HTTPException(status_code=409, detail={"code": "stale_question", "message": "심층 조사 결과가 바뀌었습니다."})
    ed = project.get("editorial") or {}
    hooks = ed.get("hooks") or []
    choice = {
        "audience": (body.audience or "").strip() or ed.get("audience"),
        "format": (body.format or "").strip() or ed.get("format"),
        "hook": (body.hook or "").strip() or (hooks[0] if hooks else None),
    }
    project["editorialChoice"] = choice
    store.save(project)
    note = f"형식: {choice['format']}\n표지 후킹 문구: {choice['hook']}"
    run_id = jobs.start_run(project_id, "storyboard", {"audience": choice["audience"], "note": note})
    result = {"runId": run_id, "status": "running", "choice": choice}
    project = store.load(project_id) or project
    _remember(project, body.requestId, payload, result)
    return result


@app.post("/api/projects/{project_id}/storyboard-changes")
async def storyboard_changes(project_id: str, body: StoryboardChangeBody) -> dict[str, Any]:
    project = store.load(project_id)
    if not project:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "프로젝트가 없습니다."})
    payload = body.model_dump()
    cached = _idempotent(project, body.requestId, payload)
    if cached:
        return cached
    if project.get("status") == "running":
        result = {"runId": project.get("activeRunId"), "status": "running"}
        _remember(project, body.requestId, payload, result)
        return result
    if project.get("waitKind") != "storyboard":
        raise HTTPException(
            status_code=409,
            detail={"code": "stale_question", "message": "스토리보드 승인 단계가 아닙니다.", "nextActions": ["화면을 새로고침하세요."]},
        )
    if body.version != project.get("storyboardVersion"):
        raise HTTPException(status_code=409, detail={"code": "stale_question", "message": "스토리보드 버전이 바뀌었습니다."})
    instruction = _change_instruction(body)
    if not instruction:
        raise HTTPException(status_code=400, detail={"code": "invalid_answer", "message": "수정할 카드를 하나 이상 체크하세요."})
    run_id = jobs.start_run(project_id, "storyboard_change", {"instruction": instruction})
    result = {"runId": run_id, "status": "running"}
    project = store.load(project_id) or project
    _remember(project, body.requestId, payload, result)
    return result


@app.post("/api/projects/{project_id}/revisions")
async def revisions(project_id: str, body: RevisionBody) -> dict[str, Any]:
    project = store.load(project_id)
    if not project:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "프로젝트가 없습니다."})
    payload = body.model_dump()
    cached = _idempotent(project, body.requestId, payload)
    if cached:
        return cached
    if body.version != project.get("resultVersion") and body.version != project.get("storyboardVersion"):
        raise HTTPException(status_code=409, detail={"code": "stale_question", "message": "버전이 바뀌었습니다."})
    if project.get("status") == "running":
        result = {"runId": project.get("activeRunId"), "status": "running"}
        _remember(project, body.requestId, payload, result)
        return result
    project["finalApproved"] = False
    store.save(project)
    run_id = jobs.start_run(
        project_id,
        "revise",
        {"cardIds": body.cardIds, "instruction": body.instruction},
    )
    result = {"runId": run_id, "status": "running"}
    project = store.load(project_id) or project
    _remember(project, body.requestId, payload, result)
    return result


@app.post("/api/runs/{run_id}/cancel")
async def cancel(run_id: str) -> dict[str, Any]:
    return await jobs.cancel_run(run_id)


@app.get("/api/projects/{project_id}/exports")
def exports(project_id: str) -> dict[str, Any]:
    project = store.load(project_id)
    if not project:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "프로젝트가 없습니다."})
    if not project.get("finalApproved"):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_answer", "message": "최종 승인된 버전만 내려받을 수 있습니다."},
        )
    listing = jobs.write_export(project)
    return listing


@app.get("/api/projects/{project_id}/files/{name}")
def files(project_id: str, name: str) -> FileResponse:
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail={"code": "invalid_answer", "message": "잘못된 파일 이름입니다."})
    candidates = [
        cards_dir(project_id) / name,
        export_dir(project_id) / name,
        project_dir(project_id) / name,
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            return FileResponse(path)
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "파일이 없습니다."})


def pick_port(start: int = 8765) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("8765부터 사용할 포트를 찾지 못했습니다.")


def run() -> None:
    import uvicorn

    port = pick_port()
    if port != 8765:
        print(f"포트 8765가 사용 중이라 {port}에서 시작합니다.")
    print(f"카드뉴스 앱: http://127.0.0.1:{port}")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    run()
