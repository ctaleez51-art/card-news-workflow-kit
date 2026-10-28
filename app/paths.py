from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "projects"
OUTPUTS_DIR = ROOT / "outputs"
STATIC_DIR = Path(__file__).resolve().parent / "static"
FONTS = [
    Path(r"C:\Windows\Fonts\malgun.ttf"),
    Path(r"C:\Windows\Fonts\malgunbd.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
]


def project_dir(project_id: str) -> Path:
    return OUTPUTS_DIR / project_id


def work_dir(project_id: str) -> Path:
    return project_dir(project_id) / "work"


def cards_dir(project_id: str) -> Path:
    return project_dir(project_id) / "cards"


def export_dir(project_id: str) -> Path:
    return project_dir(project_id) / "export"
