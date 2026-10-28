from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .engine import ask

ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT.parents[1] / "daily"
OUTPUTS = ROOT / "outputs"
app = FastAPI(title="학습 기록 블로그 초안")


class CandidateRequest(BaseModel):
    filename: str


class DraftRequest(BaseModel):
    projectId: str
    candidateId: str
    length: str


def sections(text: str) -> dict[str, str]:
    result = {}
    current = "서두"
    for line in text.splitlines():
        if line.startswith("#"):
            current = re.sub(r"^#+\s*", "", line).strip()
            result[current] = ""
        else:
            result[current] = result.get(current, "") + line + "\n"
    return {key: value.strip() for key, value in result.items() if value.strip()}


def project_path(pid: str) -> Path:
    try:
        uuid.UUID(pid)
    except ValueError:
        raise HTTPException(400, "잘못된 프로젝트 ID")
    return OUTPUTS / pid


def read_project(pid: str) -> dict:
    path = project_path(pid) / "project.json"
    if not path.exists():
        raise HTTPException(404, "프로젝트 없음")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse((ROOT / "app" / "index.html").read_text(encoding="utf-8"))


@app.get("/api/files")
def files() -> dict:
    return {"files": sorted(p.name for p in DAILY.glob("????-??-??.md"))}


@app.post("/api/candidates")
async def candidates(body: CandidateRequest) -> dict:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", body.filename):
        raise HTTPException(400, "학습 기록 파일명만 선택하세요")
    path = DAILY / body.filename
    if not path.exists():
        raise HTTPException(404, "학습 기록 없음")
    source = path.read_text(encoding="utf-8")
    parts = sections(source)
    pid = str(uuid.uuid4())
    prompt = (
        "다음 학습 기록에서 블로그 글 주제 후보 3~5개를 뽑으세요. 각 후보에 title, "
        "reason, evidenceSections(입력의 절 제목 배열)를 넣으세요. 기록이 글을 쓰기에 "
        "부족하면 status=insufficient, missing(부족한 점 배열), candidates=[]로 답하세요. "
        "충분하면 status=ok, candidates 배열로 답하세요. JSON만 답하세요.\n"
        + json.dumps(parts, ensure_ascii=False)
    )
    try:
        result = await ask(prompt)
    except Exception as exc:
        raise HTTPException(502, f"Claude 엔진 오류: {type(exc).__name__}: {exc}")
    raw = result.get("candidates") or []
    valid = []
    for item in raw[:5]:
        evidence = [e for e in item.get("evidenceSections", []) if e in parts]
        if item.get("title") and evidence:
            valid.append({
                "id": f"c{len(valid)+1}",
                "title": item["title"],
                "reason": item.get("reason", ""),
                "evidenceSections": evidence,
            })
    # A short note cannot support the requested 800-3000 character article.
    insufficient = len(source.strip()) < 600 or result.get("status") == "insufficient" or not valid
    listing = (DAILY / "발행목록.md").read_text(encoding="utf-8")
    for item in valid:
        candidate_text = item["title"] + item["reason"] + " ".join(item["evidenceSections"])
        clean = lambda value: re.sub(r"[^가-힣a-z0-9]", "", value.lower())
        candidate_clean = clean(candidate_text)
        candidate_grams = {candidate_clean[i:i+3] for i in range(len(candidate_clean)-2)}
        matches = []
        for line in listing.splitlines():
            if not line.startswith("|") or "`" not in line:
                continue
            publication = re.search(r"\d{4}-\d{2}-\d{2}", line)
            if not publication or publication.group() < body.filename[:10]:
                continue
            topic = clean(line.split("|")[-2])
            grams = {topic[i:i+3] for i in range(len(topic)-2)}
            if grams and len(candidate_grams & grams) / len(grams) >= 0.3:
                matches.append(line.split("|")[2].strip())
        item["publishedMatches"] = matches[:3]
    state = {
        "projectId": pid, "filename": body.filename, "sections": parts,
        "status": "insufficient" if insufficient else "select",
        "candidates": [] if insufficient else valid,
        "missing": result.get("missing") or (["기록 분량이 짧아 800자 글의 근거가 부족합니다."] if insufficient else []),
        "engine": "Claude Agent SDK",
        "createdAt": datetime.now().isoformat(),
    }
    folder = project_path(pid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: value for key, value in state.items() if key != "sections"}


@app.post("/api/drafts")
async def draft(body: DraftRequest) -> dict:
    state = read_project(body.projectId)
    if state["status"] == "insufficient":
        raise HTTPException(409, "자료가 부족해 초안을 만들 수 없습니다")
    candidate = next((c for c in state["candidates"] if c["id"] == body.candidateId), None)
    if not candidate or body.length not in {"short", "medium", "long"}:
        raise HTTPException(400, "후보와 길이를 선택하세요")
    target = {"short": 800, "medium": 1500, "long": 3000}[body.length]
    parts = {e: state["sections"][e] for e in candidate["evidenceSections"]}
    prompt = (
        f"주제 {candidate['title']}의 한국어 블로그 초안을 약 {target}자로 쓰세요. "
        "JSON 객체에 title과 paragraphs 배열을 넣고, 각 문단은 text와 evidenceSections "
        "배열을 가지게 하세요. 근거 절 제목은 입력에 있는 것만 쓰세요. 기록에 없는 "
        "정보가 필요하면 그 문장에 '기록에 없음'이라고 명시하세요. JSON만 답하세요.\n"
        + json.dumps(parts, ensure_ascii=False)
    )
    try:
        result = await ask(prompt)
    except Exception as exc:
        raise HTTPException(502, f"Claude 엔진 오류: {type(exc).__name__}: {exc}")
    paragraphs = result.get("paragraphs") or []
    if not paragraphs:
        raise HTTPException(502, "Claude가 문단을 반환하지 않았습니다")
    output = [f"# {result.get('title') or candidate['title']}", ""]
    checks = []
    for paragraph in paragraphs:
        text = str(paragraph.get("text") or "").strip()
        refs = [e for e in paragraph.get("evidenceSections", []) if e in parts]
        missing = len(refs) == 0
        if not text:
            continue
        output += [text + (" **[기록에 없음]**" if missing else ""), "", f"> 근거: {', '.join(refs) if refs else '기록에 없음'}", ""]
        checks.append({"evidenceSections": refs, "missing": missing})
    if not checks:
        raise HTTPException(502, "빈 초안")
    versions = list(project_path(body.projectId).glob("draft-v*.md"))
    filename = f"draft-v{len(versions)+1}.md"
    (project_path(body.projectId) / filename).write_text("\n".join(output), encoding="utf-8")
    state.update(status="drafted", choice={"candidateId": body.candidateId, "length": body.length}, draft=filename, checks=checks)
    (project_path(body.projectId) / "project.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"projectId": body.projectId, "file": filename, "checks": checks, "markdown": "\n".join(output)}


@app.get("/api/projects/{pid}")
def get_project(pid: str) -> dict:
    state = read_project(pid)
    state.pop("sections", None)
    return state


@app.get("/api/projects/{pid}/files/{filename}")
def get_file(pid: str, filename: str) -> FileResponse:
    if not re.fullmatch(r"draft-v\d+\.md", filename):
        raise HTTPException(400, "잘못된 파일명")
    path = project_path(pid) / filename
    if not path.exists():
        raise HTTPException(404, "파일 없음")
    return FileResponse(path)


def run() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8766)
