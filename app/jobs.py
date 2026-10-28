from __future__ import annotations

import asyncio
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import store
from .engine import run_engine
from .images import ImageGenError, generate_background, generate_backgrounds, test_mode
from .models import new_id, now_iso
from .paths import cards_dir, export_dir, project_dir, work_dir
from .prompts import (
    investigate_prompt,
    research_prompt,
    revision_prompt,
    storyboard_change_prompt,
    storyboard_prompt,
)
from .renderer import render_card

_tasks: dict[str, asyncio.Task] = {}
_cancel_flags: dict[str, asyncio.Event] = {}


def _append_progress(project: dict[str, Any], text: str) -> None:
    project["progress"] = (project.get("progress") or [])[-12:] + [
        {"at": now_iso(), "text": text}
    ]
    store.save(project)


def _error(project: dict[str, Any], code: str, message: str, actions: list[str]) -> None:
    project["status"] = "error"
    project["errors"] = [
        {"code": code, "message": message, "nextActions": actions}
    ]
    store.save(project)


def _actions_for(code: str, default: list[str]) -> list[str]:
    if code == "usage_limit":
        return ["Claude 사용 한도가 풀린 뒤 재시도하세요."]
    if code == "auth_required":
        return ["Claude CLI에 로그인한 뒤 재시도하세요."]
    return default


def _question_from(raw: dict[str, Any] | None, version: int) -> dict[str, Any] | None:
    if not raw:
        return None
    options = raw.get("options") or []
    normalized = []
    for i, opt in enumerate(options):
        if isinstance(opt, dict):
            normalized.append(
                {
                    "id": opt.get("id") or f"opt-{i}",
                    "label": opt.get("label") or str(opt),
                    "hint": opt.get("hint") or "",
                }
            )
        else:
            normalized.append({"id": f"opt-{i}", "label": str(opt), "hint": ""})
    return {
        "id": raw.get("id") or "q",
        "version": version,
        "question": raw.get("question") or "",
        "options": normalized,
        "allowFreeText": bool(raw.get("allowFreeText")),
        "recommended": raw.get("recommended"),
        "reason": raw.get("reason") or "",
    }


async def _engine_call(project: dict[str, Any], prompt: str) -> dict[str, Any]:
    def on_progress(text: str) -> None:
        latest = store.load(project["id"])
        if latest:
            _append_progress(latest, text)

    result = await run_engine(
        project["id"],
        prompt,
        project.get("engineSessionId"),
        on_progress,
    )
    latest = store.load(project["id"]) or project
    if result.get("engineSessionId"):
        latest["engineSessionId"] = result["engineSessionId"]
        store.save(latest)
    return result


_RESTORE_ON_CANCEL = {
    "research": None,
    "investigate": "selection",
    "storyboard": "editorial",
    "storyboard_change": "storyboard",
    "produce": "storyboard",
    "revise": "review",
}


def _restore_after_cancel(project: dict[str, Any], kind: str) -> None:
    """취소하면 작업 전에 기다리던 화면으로 되돌린다."""
    target = _RESTORE_ON_CANCEL.get(kind)
    if kind == "revise":
        _restore_revision(project)
    if kind == "research" and project.get("candidates"):
        target = "selection"
    if target == "editorial" and not project.get("editorial"):
        target = "selection" if project.get("candidates") else None
    if target == "review" and not project.get("cards"):
        target = "storyboard"
    if target:
        project["status"] = "waiting_for_user"
        project["waitKind"] = target
        project["stage"] = target
        project["errors"] = []
    else:
        project["status"] = "cancelled"
        project["waitKind"] = None
        project["stage"] = "research"
        project["errors"] = [
            {
                "code": "cancelled",
                "message": "조사를 취소했습니다.",
                "nextActions": ["조사를 다시 시작하거나 이전 단계로 돌아가세요."],
            }
        ]


def start_run(project_id: str, kind: str, extra: dict[str, Any] | None = None) -> str:
    project = store.load(project_id)
    if not project:
        raise KeyError(project_id)
    run_id = new_id()
    project["activeRunId"] = run_id
    project["status"] = "running"
    project["errors"] = []
    project["runs"][run_id] = {
        "id": run_id,
        "kind": kind,
        "status": "running",
        "startedAt": now_iso(),
    }
    store.save(project)
    flag = asyncio.Event()
    _cancel_flags[run_id] = flag
    task = asyncio.create_task(_execute(project_id, run_id, kind, extra or {}))
    _tasks[run_id] = task
    return run_id


async def _execute(project_id: str, run_id: str, kind: str, extra: dict[str, Any]) -> None:
    try:
        if kind == "research":
            await do_research(project_id)
        elif kind == "investigate":
            await do_investigate(project_id)
        elif kind == "storyboard":
            await do_storyboard(project_id, extra.get("audience"), extra.get("note"))
        elif kind == "storyboard_change":
            await do_storyboard_change(project_id, extra.get("instruction") or "")
        elif kind == "produce":
            await do_produce(project_id, extra.get("cardIds"))
        elif kind == "revise":
            await do_revise(
                project_id,
                extra.get("cardIds") or [],
                extra.get("instruction") or "",
                extra.get("regenerateImage") or [],
            )
        else:
            project = store.load(project_id)
            if project:
                _error(project, "invalid_answer", f"알 수 없는 작업: {kind}", ["다시 시도하세요."])
    except asyncio.CancelledError:
        project = store.load(project_id)
        if project:
            _restore_after_cancel(project, kind)
            project["runs"][run_id]["status"] = "cancelled"
            project["runs"][run_id]["finishedAt"] = now_iso()
            store.save(project)
        raise
    except Exception as exc:  # noqa: BLE001
        project = store.load(project_id)
        if project and kind == "revise":
            _revise_failed(project, "search_failed", f"수정 실패: {exc}", ["다시 수정 요청을 보내세요."])
        elif project:
            _error(
                project,
                "search_failed",
                f"작업 실패: {exc}",
                ["같은 단계에서 재시도하세요."],
            )
    finally:
        project = store.load(project_id)
        if project and project.get("activeRunId") == run_id and project.get("status") == "running":
            # worker forgot to set a wait/done state
            project["status"] = "error"
            store.save(project)
        _tasks.pop(run_id, None)
        _cancel_flags.pop(run_id, None)
        if project:
            run = project.get("runs", {}).get(run_id)
            if run and run.get("status") == "running":
                run["status"] = project.get("status")
                run["finishedAt"] = now_iso()
                store.save(project)


async def cancel_run(run_id: str) -> dict[str, Any]:
    flag = _cancel_flags.get(run_id)
    if flag:
        flag.set()
    task = _tasks.get(run_id)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return {"runId": run_id, "status": "cancelled"}


async def do_research(project_id: str) -> None:
    project = store.load(project_id)
    if not project:
        return
    project["stage"] = "research"
    project["waitKind"] = None
    project["researchAsOf"] = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
    store.save(project)
    prompt = research_prompt(project["topic"], project["periodDays"], project["researchAsOf"])
    result = await _engine_call(project, prompt)
    project = store.load(project_id) or project
    if result.get("status") == "error":
        code = result.get("code") or "search_failed"
        actions = _actions_for(code, ["검색 권한과 로그인을 확인한 뒤 조사를 다시 시작하세요."])
        _error(project, code, result.get("message") or "조사 실패", actions)
        return
    candidates = result.get("candidates") or []
    project["candidates"] = candidates
    project["candidateVersion"] = int(project.get("candidateVersion") or 1) + 1
    if result.get("periodDays"):
        project["periodDays"] = result["periodDays"]
    if result.get("status") == "need_input" and result.get("question"):
        qv = int((project.get("currentQuestion") or {}).get("version") or 0) + 1
        project["currentQuestion"] = _question_from(result["question"], qv)
        project["status"] = "waiting_for_user"
        project["waitKind"] = "question"
        project["stage"] = "research"
        store.save(project)
        return
    project["currentQuestion"] = None
    project["status"] = "waiting_for_user"
    project["waitKind"] = "selection"
    project["stage"] = "selection"
    store.save(project)


async def do_investigate(project_id: str) -> None:
    project = store.load(project_id)
    if not project:
        return
    selected_ids = set(project.get("selection") or [])
    selected = [c for c in project.get("candidates") or [] if c.get("id") in selected_ids]
    project["stage"] = "investigate"
    project["waitKind"] = None
    store.save(project)
    prompt = investigate_prompt(
        project["topic"], selected, project["periodDays"], project.get("researchAsOf") or now_iso()
    )
    result = await _engine_call(project, prompt)
    project = store.load(project_id) or project
    if result.get("status") == "error":
        _error(
            project,
            result.get("code") or "search_failed",
            result.get("message") or "심층 조사 실패",
            _actions_for(result.get("code") or "", ["원문 접근과 검색 권한을 확인한 뒤 다시 시도하세요."]),
        )
        return
    project["investigation"] = result.get("investigation")
    project["editorial"] = result.get("editorial")
    project["editorialVersion"] = int(project.get("editorialVersion") or 1) + 1
    project["currentQuestion"] = None
    project["status"] = "waiting_for_user"
    project["waitKind"] = "editorial"
    project["stage"] = "editorial"
    store.save(project)


async def do_storyboard(project_id: str, audience: str | None, note: str | None) -> None:
    project = store.load(project_id)
    if not project:
        return
    project["stage"] = "storyboard"
    project["waitKind"] = None
    store.save(project)
    result = await _engine_call(project, storyboard_prompt(audience, note))
    project = store.load(project_id) or project
    if result.get("status") == "error":
        code = result.get("code") or "search_failed"
        _error(project, code, result.get("message") or "스토리보드 실패", _actions_for(code, ["다시 시도하세요."]))
        return
    board = result.get("storyboard") or {"title": "", "cards": []}
    project["storyboard"] = board
    project["storyboardVersion"] = int(project.get("storyboardVersion") or 1) + 1
    project["currentQuestion"] = None
    project["status"] = "waiting_for_user"
    project["waitKind"] = "storyboard"
    project["finalApproved"] = False
    store.save(project)


async def do_storyboard_change(project_id: str, instruction: str) -> None:
    project = store.load(project_id)
    if not project:
        return
    project["stage"] = "storyboard"
    project["waitKind"] = None
    project.setdefault("storyboardRequests", []).append({"at": now_iso(), "instruction": instruction})
    store.save(project)
    result = await _engine_call(project, storyboard_change_prompt(instruction))
    project = store.load(project_id) or project
    if result.get("status") == "error" or not result.get("storyboard"):
        _error(
            project,
            result.get("code") or "search_failed",
            result.get("message") or "스토리보드 변경 실패",
            _actions_for(result.get("code") or "", ["변경 요청을 다시 보내세요."]),
        )
        project["waitKind"] = "storyboard"
        store.save(project)
        return
    if result.get("editorial"):
        project["editorial"] = result["editorial"]
    project["storyboard"] = result["storyboard"]
    project["storyboardVersion"] = int(project.get("storyboardVersion") or 1) + 1
    project["status"] = "waiting_for_user"
    project["waitKind"] = "storyboard"
    project["finalApproved"] = False
    store.save(project)


def _revise_failed(project: dict[str, Any], code: str, message: str, actions: list[str]) -> None:
    """수정이 실패하면 수정 전 기획과 카드로 되돌리고 검수 화면에서 오류를 보여 준다."""
    _restore_revision(project)
    project["status"] = "waiting_for_user"
    project["waitKind"] = "review" if project.get("cards") else "storyboard"
    project["stage"] = project["waitKind"]
    project["errors"] = [{"code": code, "message": message, "nextActions": actions}]
    store.save(project)


def _restore_revision(project: dict[str, Any]) -> None:
    backup = project.pop("revisionBackup", None)
    if not backup:
        return
    project["storyboard"] = backup.get("storyboard")
    project["storyboardVersion"] = backup.get("storyboardVersion")
    project["finalApproved"] = bool(backup.get("finalApproved"))


async def do_revise(project_id: str, card_ids: list[str], instruction: str, regen_ids: list[str] | None = None) -> None:
    project = store.load(project_id)
    if not project:
        return
    old_board = project.get("storyboard") or {"title": "", "cards": []}
    # 수정 중에도 검수 화면과 기존 카드를 그대로 두고, 실패·취소 때 되돌릴 기획을 보관한다.
    project["revisionBackup"] = {
        "storyboard": old_board,
        "storyboardVersion": project.get("storyboardVersion"),
        "finalApproved": project.get("finalApproved"),
    }
    project["stage"] = "review"
    project["waitKind"] = None
    project["finalApproved"] = False
    store.save(project)

    if instruction.strip():
        result = await _engine_call(project, revision_prompt(card_ids, instruction))
        project = store.load(project_id) or project
        board = result.get("storyboard") or {}
        if result.get("status") == "error" or not board.get("cards"):
            code = result.get("code") or "search_failed"
            actions = ["고칠 내용을 확인하고 다시 수정 요청을 보내세요."]
            if code == "usage_limit":
                actions = ["Claude 사용 한도가 풀린 뒤 다시 수정 요청을 보내세요."]
            _revise_failed(project, code, result.get("message") or "글자 수정 결과를 받지 못했습니다.", actions)
            return
        # 지정한 카드만 새 내용으로 바꾸고, 나머지 카드는 이전 기획을 그대로 쓴다.
        new_by_id = {c.get("id"): c for c in board["cards"]}
        merged = [
            new_by_id.get(c.get("id"), c) if c.get("id") in card_ids else c
            for c in old_board.get("cards") or []
        ]
        project["storyboard"] = {**old_board, "cards": merged}
        project["storyboardVersion"] = int(project.get("storyboardVersion") or 1) + 1
        store.save(project)

    await do_produce(project_id, card_ids, regen_ids or [])
    project = store.load(project_id)
    if project and project.pop("revisionBackup", None) is not None:
        store.save(project)


def _existing_background(project_id: str, card: dict[str, Any] | None) -> Path | None:
    """이미 만든 카드의 배경 그림 파일. 글자만 고칠 때 다시 쓴다."""
    if not card or card.get("failed"):
        return None
    wdir = work_dir(project_id)
    if card.get("background"):
        path = wdir / card["background"]
        return path if path.exists() else None
    legacy = wdir / f"bg-{int(card.get('index') or 0):02d}.png"
    return legacy if legacy.exists() else None


async def do_produce(project_id: str, only_ids: list[str] | None = None, regen_ids: list[str] | None = None) -> None:
    """카드를 만든다. only_ids가 있으면 그 카드만 다시 만들고 나머지는 그대로 둔다.

    결과는 끝까지 만든 뒤에 한 번에 저장한다. 중간에 취소·실패해도 기존 카드는 지워지지 않는다.
    새 파일은 버전 번호를 붙여 저장해 기존 카드 파일을 덮어쓰지 않는다.
    """
    project = store.load(project_id)
    if not project or not project.get("storyboard"):
        return
    revising = only_ids is not None
    if not revising:
        project["stage"] = "produce"
    project["waitKind"] = None
    store.save(project)

    cards_spec = list((project["storyboard"] or {}).get("cards") or [])
    existing = {c.get("storyId"): c for c in project.get("cards") or []}
    version = int(project.get("resultVersion") or 1) + 1
    regen = set(regen_ids or [])
    cdir = cards_dir(project_id)
    wdir = work_dir(project_id)

    plan: list[dict[str, Any]] = []
    gen_items: list[tuple[str, str, Path]] = []
    for index, spec in enumerate(cards_spec, start=1):
        sid = spec.get("id") or f"s{index}"
        old = existing.get(sid)
        if revising and sid not in only_ids and old:
            plan.append({"keep": old})
            continue
        reuse = _existing_background(project_id, old) if (revising and sid not in regen) else None
        plan.append({"sid": sid, "index": index, "spec": spec, "reuse": reuse, "old": old})
        if reuse is None:
            scene = spec.get("imagePlan") or spec.get("title") or ""
            gen_items.append((sid, scene, wdir / f"bg-{index:02d}-v{version}.png"))

    results: dict[str, Any] = {}
    stop: ImageGenError | None = None
    if gen_items:
        mode = "시험 모드: 기존 배경 사용" if test_mode() else "agy 1회 실행"
        _append_progress(store.load(project_id) or project, f"이미지 {len(gen_items)}장 생성 시작 ({mode})")

        def on_saved(done: int, total: int) -> None:
            _append_progress(store.load(project_id) or project, f"이미지 {done}/{total}장 저장됨")

        results = await generate_backgrounds(project_id, gen_items, on_saved)
        for value in results.values():
            if isinstance(value, ImageGenError) and value.stop_all:
                stop = value
                break
        if not stop:
            # 묶음 실행에서 빠진 카드만 한 장씩 다시 요청한다.
            for sid, scene, dest in gen_items:
                value = results.get(sid)
                if not isinstance(value, ImageGenError):
                    continue
                _append_progress(store.load(project_id) or project, f"빠진 이미지 다시 생성: {dest.name}")
                try:
                    results[sid] = await generate_background(project_id, sid, scene, dest)
                except ImageGenError as exc:
                    results[sid] = exc
                    if exc.stop_all:
                        stop = exc
                        break

    produced: list[dict[str, Any]] = []
    failed_names: list[str] = []
    for entry in plan:
        if "keep" in entry:
            produced.append(entry["keep"])
            continue
        sid, index, spec = entry["sid"], entry["index"], entry["spec"]
        fail_msg = None
        if entry["reuse"] is not None:
            background: Path | None = entry["reuse"]
            image_meta = (entry["old"] or {}).get("image")
        else:
            value = results.get(sid)
            if isinstance(value, ImageGenError):
                fail_msg = value.message if not (stop and value is not stop) else "앞 카드의 오류 때문에 이 카드는 이미지를 만들지 못했습니다."
                image_meta = {"generated": False, "error": value.message, "code": value.code}
                background = None
                failed_names.append(f"{index}번")
            elif value is None:
                fail_msg = "앞 카드의 오류 때문에 이 카드는 이미지를 요청하지 않았습니다."
                image_meta = {"generated": False, "error": fail_msg, "code": "skipped"}
                background = None
                failed_names.append(f"{index}번")
            else:
                image_meta = value
                background = Path(value["path"])
        source = spec.get("source") or ""
        footer = f"출처: {source}" if source else "출처 미기재"
        if project.get("researchAsOf"):
            footer += f" · 조사 {project['researchAsOf'][:10]}"
        dest = cdir / f"card-{index:02d}-v{version}.png"
        render_meta = render_card(
            background,
            spec.get("title") or spec.get("headline") or "",
            spec.get("body") or spec.get("headline") or "",
            footer,
            dest,
            failed=fail_msg,
        )
        produced.append(
            {
                "id": f"card-{index:02d}",
                "storyId": sid,
                "index": index,
                "title": spec.get("title"),
                "headline": spec.get("headline"),
                "body": spec.get("body"),
                "source": source,
                "file": dest.name,
                "background": background.name if background else None,
                "render": render_meta,
                "image": image_meta,
                "failed": bool(fail_msg),
            }
        )

    project = store.load(project_id) or project
    project["cards"] = produced
    project["resultVersion"] = version
    project["finalApproved"] = False
    project["stage"] = "review"
    project["status"] = "waiting_for_user"
    project["waitKind"] = "review"
    project["errors"] = []
    if stop:
        reason = {
            "quota_exceeded": "이미지 생성 한도를 초과했습니다.",
            "permission_denied": "agy가 필요한 권한을 받지 못했습니다.",
            "auth_required": "agy 로그인 또는 API 키 설정이 필요합니다.",
        }.get(stop.code, "이미지 생성에 실패했습니다.")
        project["errors"] = [
            {
                "code": stop.code,
                "message": f"{reason} {', '.join(failed_names)} 카드는 이미지가 없습니다.",
                "nextActions": ["원인을 해결한 뒤 재시도를 누르면 실패한 카드만 다시 만듭니다."],
            }
        ]
    elif failed_names:
        project["errors"] = [
            {
                "code": "image_failed",
                "message": f"{', '.join(failed_names)} 카드 이미지를 만들지 못했습니다.",
                "nextActions": ["재시도를 누르면 실패한 카드만 다시 만듭니다."],
            }
        ]
    store.save(project)


def write_export(project: dict[str, Any]) -> dict[str, Any]:
    pid = project["id"]
    ed = export_dir(pid)
    ed.mkdir(parents=True, exist_ok=True)
    # 이전 승인 때 만든 내보내기 파일이 섞이지 않게 비운다.
    for old in ed.glob("*.png"):
        old.unlink()
    cd = cards_dir(pid)
    files = []
    pairs = []
    for card in project.get("cards") or []:
        src = cd / card["file"]
        if src.exists():
            name = f"card-{int(card.get('index') or len(files) + 1):02d}.png"
            (ed / name).write_bytes(src.read_bytes())
            files.append(name)
            pairs.append((name, card))
    prov = ed / "provenance.md"
    lines = [
        "# 출처와 이미지 기록",
        f"- 주제: {project.get('topic')}",
        f"- 조사 기준: {project.get('researchAsOf')}",
        f"- 기간: {project.get('periodDays')}일",
        "",
        "## 선택 후보",
    ]
    selected = set(project.get("selection") or [])
    for c in project.get("candidates") or []:
        if c.get("id") in selected:
            lines.append(f"- {c.get('title')} ({c.get('publishedAt')}) {c.get('url')}")
    lines += ["", "## 카드"]
    for name, card in pairs:
        img = card.get("image") or {}
        lines.append(
            f"- {name}: 생성={img.get('generated')} 도구={img.get('tool')} 날짜={img.get('createdAt')} 프롬프트={img.get('prompt')}"
        )
        lines.append(f"  근거: {card.get('source')}")
    prov.write_text("\n".join(lines), encoding="utf-8")
    zip_path = project_dir(pid) / "cards.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in files:
            zf.write(ed / name, arcname=name)
        zf.write(prov, arcname="provenance.md")
    listing = {
        "png": files,
        "zip": "cards.zip",
        "provenance": "provenance.md",
    }
    project["exportFiles"] = listing
    return listing
