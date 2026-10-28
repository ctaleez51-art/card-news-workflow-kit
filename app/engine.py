from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from .paths import work_dir
from .prompts import RESULT_FILE

ProgressFn = Callable[[str], None]


def classify_engine_error(text: str, is_error: bool = True) -> str:
    """엔진 오류 문장을 앱의 오류 코드로 바꾼다."""
    low = (text or "").lower()
    if any(k in low for k in ("session limit", "usage limit", "hit your limit", "rate limit", "limit reached")):
        return "usage_limit"
    if is_error and any(k in low for k in ("login", "log in", "authenticat", "api key")):
        return "auth_required"
    return "search_failed"


USAGE_LIMIT_MESSAGE = "Claude 사용 한도에 도달했습니다. 한도가 풀린 뒤 다시 시도하세요."


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


async def run_engine(
    project_id: str,
    prompt: str,
    session_id: str | None,
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    cwd = work_dir(project_id)
    cwd.mkdir(parents=True, exist_ok=True)
    result_path = cwd / RESULT_FILE
    if result_path.exists():
        result_path.unlink()

    options = ClaudeAgentOptions(
        cwd=str(cwd),
        resume=session_id,
        tools=["WebSearch", "WebFetch", "Read", "Write", "Glob"],
        allowed_tools=["WebSearch", "WebFetch", "Read", "Write", "Glob"],
        permission_mode="acceptEdits",
        max_turns=28,
        system_prompt=(
            "카드뉴스 조사·기획 작업자입니다. 웹 검색과 파일 저장만 사용하세요. "
            "Bash로 사용자 주제를 실행하지 마세요. 추측으로 최신 소식을 만들지 마세요."
        ),
        env={"CLAUDE_AGENT_SDK_CLIENT_APP": "card-news-workflow-kit/1.0"},
    )

    texts: list[str] = []
    new_session = session_id
    is_error = False
    error_text = ""

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text:
                        texts.append(block.text)
                        if on_progress:
                            snippet = block.text.strip().replace("\n", " ")
                            if snippet:
                                on_progress(snippet[:240])
                    elif isinstance(block, ToolUseBlock) and on_progress:
                        on_progress(f"도구: {block.name}")
            elif isinstance(message, ResultMessage):
                new_session = message.session_id or new_session
                is_error = bool(message.is_error)
                error_text = message.result or ""
                if message.structured_output and isinstance(message.structured_output, dict):
                    parsed = message.structured_output
                    parsed["engineSessionId"] = new_session
                    return parsed
    except Exception as exc:  # noqa: BLE001 — surface engine failures to the UI
        detail = f"{exc} {' '.join(texts[-3:])}"
        code = classify_engine_error(detail)
        prefix = USAGE_LIMIT_MESSAGE + " " if code == "usage_limit" else ""
        return {
            "status": "error",
            "code": code,
            "message": f"{prefix}엔진 호출 실패: {type(exc).__name__}: {exc}",
            "engineSessionId": new_session,
        }

    parsed: dict[str, Any] | None = None
    if result_path.exists():
        try:
            parsed = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            parsed = None
    if parsed is None:
        parsed = _extract_json("\n".join(texts))
    if parsed is None:
        detail = f"{error_text} {' '.join(texts[-3:])}"
        code = classify_engine_error(detail, is_error)
        message = error_text or "엔진이 구조화된 결과를 반환하지 않았습니다. 로그인·검색 권한을 확인하세요."
        if code == "usage_limit":
            raw = error_text or (texts[-1] if texts else "")
            message = f"{USAGE_LIMIT_MESSAGE} ({raw.strip()[:200]})"
        return {
            "status": "error",
            "code": code,
            "message": message,
            "engineSessionId": new_session,
        }
    parsed["engineSessionId"] = new_session
    if is_error and parsed.get("status") != "error":
        parsed.setdefault("message", error_text)
    return parsed
