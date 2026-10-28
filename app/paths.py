from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "projects"
OUTPUTS_DIR = ROOT / "outputs"
STATIC_DIR = Path(__file__).resolve().parent / "static"
ASSETS_DIR = ROOT / "assets"
# 시험 모드에서 agy 대신 쓰는 기존 생성 이미지 (assets/PROVENANCE.md 참고)
TEST_BACKGROUND = ASSETS_DIR / "ai-news-background.png"

FONTS = [
    Path(r"C:\Windows\Fonts\malgun.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
]
BOLD_FONTS = [
    Path(r"C:\Windows\Fonts\malgunbd.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
]


def project_dir(project_id: str) -> Path:
    return OUTPUTS_DIR / project_id


def work_dir(project_id: str) -> Path:
    return project_dir(project_id) / "work"


def cards_dir(project_id: str) -> Path:
    return project_dir(project_id) / "cards"


def export_dir(project_id: str) -> Path:
    return project_dir(project_id) / "export"
