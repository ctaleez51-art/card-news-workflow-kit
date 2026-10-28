from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .paths import work_dir


class ImageGenError(Exception):
    def __init__(
        self,
        message: str,
        retryable: bool = True,
        code: str = "image_failed",
        stop_all: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.code = code
        # True면 다음 카드도 같은 이유로 실패하므로 남은 카드를 요청하지 않는다.
        self.stop_all = stop_all


def _classify_failure(text: str) -> tuple[str, bool]:
    """agy 출력으로 실패 종류를 나눈다. (코드, 남은 카드도 멈출지)"""
    low = text.lower()
    if any(k in low for k in ("429", "resource_exhausted", "quota")) or any(
        k in text for k in ("할당량", "쿼터", "사용량")
    ):
        return "quota_exceeded", True
    if "permission" in low or "auto-denied" in low or "권한" in text:
        return "permission_denied", True
    if "login" in low or "auth" in low or "로그인" in text:
        return "auth_required", True
    return "image_failed", False


def _agy_bin() -> str:
    found = shutil.which("agy")
    if found:
        return found
    fallback = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
    if fallback.exists():
        return str(fallback)
    raise ImageGenError("agy CLI를 찾을 수 없습니다. Antigravity CLI 설치와 PATH를 확인하세요.", code="auth_required")


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """agy와 agy가 띄운 하위 프로세스까지 끈다."""
    if proc.returncode is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def generate_background(project_id: str, card_id: str, plan: str, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cwd = work_dir(project_id)
    cwd.mkdir(parents=True, exist_ok=True)
    filename = dest.name
    prompt = (
        f"내장 generate_image 도구로 카드뉴스용 설명 일러스트를 만들어 줘. "
        f"장면: {plan}. 밝은 아이보리와 코발트, 주황 포인트, 단순한 구도, "
        f"위쪽 제목 여백. 이미지 안에 글자·로고·인물 없음. 실제 제품 화면이나 보도 사진이 아님. "
        f"현재 작업 폴더의 {filename}으로 저장하고 실제 저장 경로를 알려 줘. "
        f"하위 에이전트에 맡기고 먼저 끝내지 말고, 파일 저장이 끝날 때까지 기다렸다가 끝내. "
        f"generate_image가 실패하면 코드나 다른 도구로 그림을 그리지 말고, 실패 이유만 답해."
    )
    cmd = [_agy_bin(), "-p", prompt]
    if os.environ.get("CARDNEWS_AGY_SKIP_PERMISSIONS") == "1":
        cmd.append("--dangerously-skip-permissions")

    # 이전 실행의 같은 파일이 남아 있으면 실패를 성공으로 착각하므로 먼저 치운다.
    if dest.exists():
        dest.unlink()
    def _png_snapshot() -> dict[Path, float]:
        return {q: q.stat().st_mtime for q in set(cwd.glob("*.png")) | set(cwd.glob("**/*.png"))}

    before = _png_snapshot()

    extra = {} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **extra,
        )
    except FileNotFoundError as exc:
        raise ImageGenError("agy를 실행할 수 없습니다. 설치와 로그인을 확인하세요.", code="auth_required") from exc

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=240)
    except asyncio.TimeoutError as exc:
        _kill_tree(proc)
        raise ImageGenError("이미지 생성이 시간 초과되었습니다.") from exc
    except asyncio.CancelledError:
        _kill_tree(proc)
        raise
    stdout = out.decode("utf-8", errors="replace")
    stderr = err.decode("utf-8", errors="replace")

    # 이번 실행에서 새로 생긴 PNG만 본다. 다른 카드의 bg-*.png는 제외한다.
    fresh = [
        q
        for q, mtime in _png_snapshot().items()
        if (q not in before or mtime > before[q])
        and (q.resolve() == dest.resolve() or not q.name.startswith("bg-"))
    ]
    if fresh:
        newest = max(fresh, key=lambda p: p.stat().st_mtime)
        if newest.resolve() != dest.resolve():
            dest.write_bytes(newest.read_bytes())
    if not dest.exists():
        combined = (stdout + "\n" + stderr).strip()
        hint = combined[-800:] if combined else f"종료 코드 {proc.returncode}"
        code, stop_all = _classify_failure(combined or hint)
        raise ImageGenError(f"이미지 파일이 생성되지 않았습니다. {hint}", code=code, stop_all=stop_all)

    from PIL import Image

    # agy가 JPEG 등을 .png 이름으로 저장하는 경우가 있어 실제 PNG로 바꿔 저장한다.
    try:
        with Image.open(dest) as img:
            original_format = img.format
            if original_format != "PNG":
                img.load()
                converted = img.convert("RGB")
            else:
                converted = None
            size = img.size
    except OSError as exc:
        raise ImageGenError(f"생성 결과를 이미지로 열 수 없습니다: {exc}") from exc
    if converted is not None:
        converted.save(dest, "PNG")
    with Image.open(dest) as img:
        if img.format != "PNG":
            raise ImageGenError("생성 결과를 PNG로 바꾸지 못했습니다.")
    return {
        "path": str(dest),
        "width": size[0],
        "height": size[1],
        "format": "PNG",
        "originalFormat": original_format,
        "tool": "Antigravity CLI agy, generate_image",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt,
        "generated": True,
    }
