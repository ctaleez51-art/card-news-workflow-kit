from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .paths import BOLD_FONTS, FONTS

WIDTH = 1080
HEIGHT = 1350

MARGIN = 72
TITLE_TOP, TITLE_MAX_H = 56, 170
# 그림은 카드 가로 전체에 크게 넣는다. 정사각형 그림은 위아래를 조금 잘라 맞춘다.
ART_TOP, ART_BOTTOM = 250, 880
BODY_TOP, BODY_BOTTOM = 914, 1222
FOOTER_TOP = 1240

INK = "#18243A"
PAPER = "#F8F5EC"


def korean_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [*BOLD_FONTS, *FONTS] if bold else FONTS
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """한국어는 글자 단위로, 가능하면 띄어쓰기에서 줄을 바꾼다."""
    lines: list[str] = []
    for paragraph in (text or "").split("\n"):
        line = ""
        for ch in paragraph:
            test = line + ch
            if draw.textlength(test, font=font) <= max_width:
                line = test
                continue
            cut = line.rfind(" ")
            if ch != " " and cut > len(line) * 0.6:
                lines.append(line[:cut])
                line = line[cut + 1 :] + ch
            else:
                if line:
                    lines.append(line)
                line = ch.lstrip()
        if line:
            lines.append(line)
        if not paragraph:
            lines.append("")
    return lines or [""]


def _fit(draw, text: str, sizes: range, max_w: int, max_h: int, spacing: float, bold: bool = False):
    for size in sizes:
        font = korean_font(size, bold=bold)
        lines = wrap(draw, text, font, max_w)
        if len(lines) * round(size * spacing) <= max_h:
            return font, lines
    return None, []


def render_card(
    background: Path | None,
    title: str,
    body: str,
    source: str,
    dest: Path,
    failed: str | None = None,
) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)
    max_w = WIDTH - MARGIN * 2

    title_font, title_lines = _fit(draw, title, range(60, 37, -2), max_w, TITLE_MAX_H, 1.23, bold=True)
    if title_font is None:
        raise ValueError("카드 제목이 너무 깁니다.")
    block_h = len(title_lines) * round(title_font.size * 1.23)
    y = TITLE_TOP + (TITLE_MAX_H - block_h) // 2 + 6
    for line in title_lines:
        draw.text((MARGIN, y), line, font=title_font, fill=INK)
        y += round(title_font.size * 1.23)

    art_box = (WIDTH, ART_BOTTOM - ART_TOP)
    if background and background.exists():
        with Image.open(background) as raw:
            art = ImageOps.fit(raw.convert("RGB"), art_box, Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        img.paste(art, (0, ART_TOP))
    else:
        draw.rectangle((0, ART_TOP, WIDTH, ART_BOTTOM), fill="#E4EBEC")

    if failed:
        fail_font = korean_font(24)
        draw.rectangle((0, ART_BOTTOM - 52, WIDTH, ART_BOTTOM), fill="#8B1E1E")
        draw.text((MARGIN, ART_BOTTOM - 42), wrap(draw, failed, fail_font, max_w)[0], font=fail_font, fill="white")

    body_font, body_lines = _fit(draw, body, range(38, 23, -1), max_w, BODY_BOTTOM - BODY_TOP, 1.38)
    if body_font is None:
        raise ValueError("카드 본문이 너무 깁니다.")
    y = BODY_TOP
    for line in body_lines:
        draw.text((MARGIN, y), line, font=body_font, fill=INK)
        y += round(body_font.size * 1.38)

    draw.rectangle((0, FOOTER_TOP, WIDTH, HEIGHT), fill="#17263A")
    source_font = korean_font(26)
    source_label = re.sub(r"https?://([^/\s;]+)[^\s;]*", r"\1", source or "출처 미기재")
    sy = FOOTER_TOP + 14
    for line in wrap(draw, source_label, source_font, max_w)[:2]:
        draw.text((MARGIN, sy), line, font=source_font, fill="#F4EFE4")
        sy += 36

    img.save(dest, "PNG")
    with Image.open(dest) as check:
        w, h = check.size
        fmt = check.format
    if (w, h) != (WIDTH, HEIGHT) or fmt != "PNG":
        raise RuntimeError(f"렌더 검증 실패: {w}x{h} {fmt}")
    return {"path": str(dest), "width": w, "height": h, "format": fmt}
