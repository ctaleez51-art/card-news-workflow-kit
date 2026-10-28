from __future__ import annotations

from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .paths import FONTS

WIDTH = 1080
HEIGHT = 1350


def korean_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = FONTS
    if bold:
        candidates = [FONTS[1], FONTS[0], *FONTS[2:]]
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in (text or "").split("\n"):
        line = ""
        for ch in paragraph:
            test = line + ch
            if draw.textlength(test, font=font) <= max_width:
                line = test
            else:
                if line:
                    lines.append(line)
                line = ch
        if line:
            lines.append(line)
        if not paragraph:
            lines.append("")
    return lines or [""]


def render_card(
    background: Path | None,
    title: str,
    body: str,
    source: str,
    dest: Path,
    failed: str | None = None,
) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (WIDTH, HEIGHT), "#F8F5EC")
    draw = ImageDraw.Draw(img)
    margin = 72
    max_w = WIDTH - margin * 2
    title_font = None
    title_lines = []
    for size in range(58, 37, -2):
        font = korean_font(size, bold=True)
        lines = wrap(draw, title, font, max_w)
        if len(lines) * round(size * 1.23) <= 170:
            title_font, title_lines = font, lines
            break
    if title_font is None:
        raise ValueError("카드 제목이 너무 깁니다.")
    y = 64
    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill="#18243A")
        y += round(title_font.size * 1.23)
    draw.rectangle((margin, 238, WIDTH - margin, 245), fill="#1B638D")

    # 그림과 글자를 분리하고, 그림은 비율을 유지해 전부 보이게 한다.
    art_top, art_bottom = 267, 790
    if background and background.exists():
        with Image.open(background) as raw:
            art = ImageOps.contain(raw.convert("RGB"), (max_w, art_bottom - art_top), Image.Resampling.LANCZOS)
        img.paste(art, ((WIDTH - art.width) // 2, art_top + (art_bottom - art_top - art.height) // 2))
    else:
        draw.rounded_rectangle((margin, art_top, WIDTH - margin, art_bottom), 22, fill="#E4EBEC")

    body_font = None
    body_lines = []
    for size in range(36, 24, -1):
        font = korean_font(size)
        lines = wrap(draw, body, font, max_w)
        if len(lines) * round(size * 1.35) <= 387:
            body_font, body_lines = font, lines
            break
    if body_font is None:
        raise ValueError("카드 본문이 너무 깁니다.")
    y = 830
    for line in body_lines:
        draw.text((margin, y), line, font=body_font, fill="#18243A")
        y += round(body_font.size * 1.35)

    if failed:
        draw.rectangle((margin, 1175, WIDTH - margin, 1222), fill="#8B1E1E")
        fail_font = korean_font(23)
        draw.text((margin + 12, 1185), wrap(draw, failed, fail_font, max_w - 24)[0], font=fail_font, fill="white")

    draw.rectangle((0, 1240, WIDTH, HEIGHT), fill="#17263A")
    source_font = korean_font(26)
    source_label = re.sub(r"https?://([^/\s;]+)[^\s;]*", r"\1", source or "출처 미기재")
    src_lines = wrap(draw, source_label, source_font, max_w)[:3]
    sy = 1250
    for line in src_lines:
        draw.text((margin, sy), line, font=source_font, fill="#F4EFE4")
        sy += 33

    img.save(dest, "PNG")
    with Image.open(dest) as check:
        w, h = check.size
        fmt = check.format
    if (w, h) != (WIDTH, HEIGHT) or fmt != "PNG":
        raise RuntimeError(f"렌더 검증 실패: {w}x{h} {fmt}")
    return {"path": str(dest), "width": w, "height": h, "format": fmt}
