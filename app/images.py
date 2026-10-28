from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .paths import TEST_BACKGROUND, work_dir

TOOL_NAME = "Antigravity CLI agy, generate_image"

# 모든 카드가 같은 그림으로 보이도록 그림체를 고정한다.
# 렌더러가 그림을 카드 가로 전체에 맞춰 위아래를 잘라 쓰므로 여백은 요청하지 않는다.
STYLE = (
    "그림체: 플랫 벡터 일러스트, 단순한 도형, 일정한 굵기의 외곽선, "
    "밝은 아이보리 배경, 코발트 블루를 주색으로 하고 주황을 포인트로 사용, "
    "그림자·질감·사진 효과·3D 렌더링 없음. "
    "주요 대상은 화면 가운데에 크게 두고, 위아래 가장자리는 잘려도 되는 구도. "
    "이미지 안에 글자·숫자·로고·실제 인물 얼굴 없음. 실제 제품 화면이나 보도 사진처럼 보이지 않게."
)


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


def test_mode() -> bool:
    """CARDNEWS_IMAGE_MODE=test 이면 agy를 부르지 않는다 (비용 없이 흐름 시험)."""
    return os.environ.get("CARDNEWS_IMAGE_MODE", "").strip().lower() == "test"


def _classify_failure(text: str) -> tuple[str, bool]:
    """agy 출력으로 실패 종류를 나눈다. (코드, 남은 카드도 멈출지)"""
    low = text.lower()
    if any(k in low for k in ("429", "resource_exhausted", "quota", "rate limit")) or any(
        k in text for k in ("할당량", "사용 한도")
    ):
        return "quota_exceeded", True
    if "auto-denied" in low or "permission denied" in low or ("required the" in low and "permission" in low) or (
        "권한" in text and "거부" in text
    ):
        return "permission_denied", True
    if any(k in low for k in ("not logged in", "please log in", "login required", "unauthenticated", "api key not valid")) or (
        "로그인" in text and "필요" in text
    ):
        return "auth_required", True
    return "image_failed", False


def _agy_bin() -> str:
    found = shutil.which("agy")
    if found:
        return found
    fallback = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
    if fallback.exists():
        return str(fallback)
    raise ImageGenError(
        "agy CLI를 찾을 수 없습니다. Antigravity CLI 설치와 PATH를 확인하세요.",
        code="auth_required",
        stop_all=True,
    )


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


async def _run_agy(cwd: Path, prompt: str, timeout: int) -> tuple[str, int | None]:
    cmd = [_agy_bin(), "-p", prompt]
    if os.environ.get("CARDNEWS_AGY_SKIP_PERMISSIONS") == "1":
        cmd.append("--dangerously-skip-permissions")
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
        raise ImageGenError("agy를 실행할 수 없습니다. 설치와 로그인을 확인하세요.", code="auth_required", stop_all=True) from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        _kill_tree(proc)
        raise ImageGenError("이미지 생성이 시간 초과되었습니다.") from exc
    except asyncio.CancelledError:
        _kill_tree(proc)
        raise
    text = out.decode("utf-8", errors="replace") + "\n" + err.decode("utf-8", errors="replace")
    return text.strip(), proc.returncode


def _find_saved(cwd: Path, name: str, since: float) -> Path | None:
    """agy가 요청한 이름으로 저장한 파일을 찾는다. 하위 폴더에 저장한 경우도 본다."""
    direct = cwd / name
    if direct.exists() and direct.stat().st_mtime >= since:
        return direct
    for found in cwd.rglob(name):
        if found.is_file() and found.stat().st_mtime >= since:
            return found
    return None


def _to_png(src: Path, dest: Path) -> dict:
    """실제 이미지인지 열어 보고, PNG가 아니면 PNG로 다시 저장한다."""
    from PIL import Image

    try:
        with Image.open(src) as img:
            img.load()
            original_format = img.format
            size = img.size
            converted = img.convert("RGB") if original_format != "PNG" else None
    except OSError as exc:
        raise ImageGenError(f"생성 결과를 이미지로 열 수 없습니다: {exc}") from exc
    if converted is not None:
        converted.save(dest, "PNG")
    elif src.resolve() != dest.resolve():
        dest.write_bytes(src.read_bytes())
    with Image.open(dest) as img:
        if img.format != "PNG":
            raise ImageGenError("생성 결과를 PNG로 바꾸지 못했습니다.")
    return {"width": size[0], "height": size[1], "originalFormat": original_format}


def _meta(dest: Path, info: dict, prompt: str) -> dict:
    return {
        "path": str(dest),
        "file": dest.name,
        "width": info["width"],
        "height": info["height"],
        "format": "PNG",
        "originalFormat": info["originalFormat"],
        "tool": TOOL_NAME,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt,
        "generated": True,
    }


def _test_image(dest: Path) -> dict:
    if not TEST_BACKGROUND.exists():
        raise ImageGenError("시험 모드용 배경 파일(assets/ai-news-background.png)이 없습니다.", stop_all=True)
    info = _to_png(TEST_BACKGROUND, dest)
    return {
        "path": str(dest),
        "file": dest.name,
        "width": info["width"],
        "height": info["height"],
        "format": "PNG",
        "originalFormat": info["originalFormat"],
        "tool": "시험 모드: agy 호출 없음. assets/ai-news-background.png(2026-09-08 agy 생성)를 재사용",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "prompt": None,
        "generated": True,
        "testMode": True,
    }


def _batch_prompt(items: list[tuple[str, str, Path]]) -> str:
    lines = [
        f"내장 generate_image 도구로 카드뉴스용 설명 일러스트 {len(items)}장을 만들어 줘.",
        "하위 에이전트를 쓰지 말고 직접 generate_image를 한 장씩 호출해.",
        STYLE,
        "",
        "각 이미지는 아래 파일 이름으로 현재 작업 폴더에 저장해.",
    ]
    for i, (_sid, plan, dest) in enumerate(items, start=1):
        lines.append(f"{i}. 파일 {dest.name}: {plan}")
    lines += [
        "",
        "모든 파일 저장이 끝난 뒤에 끝내. 대답은 '완료' 한 단어만 써.",
        "generate_image가 실패하면 코드나 다른 도구로 그림을 그리지 말고, 실패한 파일 이름과 이유만 한 줄씩 답해.",
    ]
    return "\n".join(lines)


async def generate_backgrounds(
    project_id: str,
    items: list[tuple[str, str, Path]],
    on_saved: Callable[[int, int], None] | None = None,
) -> dict[str, dict | ImageGenError]:
    """여러 카드의 배경을 agy 한 번 실행으로 만든다.

    items: (카드 ID, 장면 설명, 저장할 파일) 목록.
    결과: 카드 ID → 이미지 기록 또는 ImageGenError.
    """
    results: dict[str, dict | ImageGenError] = {}
    if not items:
        return results
    for _sid, _plan, dest in items:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()

    if test_mode():
        for sid, _plan, dest in items:
            try:
                results[sid] = _test_image(dest)
            except ImageGenError as exc:
                results[sid] = exc
        return results

    cwd = work_dir(project_id)
    cwd.mkdir(parents=True, exist_ok=True)
    prompt = _batch_prompt(items)
    started = time.time() - 1
    timeout = min(240 + 120 * len(items), 1500)

    async def _watch() -> None:
        # agy가 끝나기 전에도 저장된 장수를 화면에 알린다.
        last = 0
        while True:
            await asyncio.sleep(5)
            done = sum(1 for _s, _p, d in items if _find_saved(cwd, d.name, started))
            if done != last and on_saved:
                last = done
                on_saved(done, len(items))

    watcher = asyncio.create_task(_watch()) if on_saved and len(items) > 1 else None
    try:
        output, returncode = await _run_agy(cwd, prompt, timeout)
    except ImageGenError as exc:
        for sid, _plan, _dest in items:
            results[sid] = exc
        return results
    finally:
        if watcher:
            watcher.cancel()

    missing = []
    for sid, plan, dest in items:
        saved = _find_saved(cwd, dest.name, started)
        if not saved and len(items) == 1:
            # 한 장만 요청했으면 이름이 달라도 이번 실행에서 새로 생긴 이미지를 결과로 본다.
            fresh = [
                p
                for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp")
                for p in cwd.rglob(ext)
                if p.is_file() and p.stat().st_mtime >= started and not p.name.startswith("bg-")
            ]
            if fresh:
                saved = max(fresh, key=lambda p: p.stat().st_mtime)
        if not saved:
            missing.append(sid)
            continue
        try:
            info = _to_png(saved, dest)
            results[sid] = _meta(dest, info, f"{STYLE}\n장면: {plan}")
        except ImageGenError as exc:
            results[sid] = exc
    if missing:
        hint = output[-800:] if output else f"종료 코드 {returncode}"
        code, stop_all = _classify_failure(hint)
        for sid in missing:
            results[sid] = ImageGenError(f"이미지 파일이 생성되지 않았습니다. {hint}", code=code, stop_all=stop_all)
    return results


async def generate_background(project_id: str, card_id: str, plan: str, dest: Path) -> dict:
    """카드 한 장의 배경을 만든다. 묶음 생성에서 빠진 카드를 다시 만들 때 쓴다."""
    result = (await generate_backgrounds(project_id, [(card_id, plan, dest)]))[card_id]
    if isinstance(result, ImageGenError):
        raise result
    return result
