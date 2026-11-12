"""제공용 소스 내보내기: 원본 두 저장소에서 허용 목록에 있는 파일만 골라 제공 폴더에 복사한다.

    python scripts/export_release.py <제공 폴더>

- 허용 목록 방식이다. 새로 만든 명세·메모 파일이 실수로 섞이지 않도록, 여기에 적은 것만 나간다.
- 제공 폴더는 비어 있거나 Git 저장소(.git만 남기고 다시 채움)여야 한다.
- 명세(docs/*.md), 테스트, 학습용 파일, Render 설정은 넣지 않는다.
"""

import argparse
import shutil
import sys
from pathlib import Path

TTS_ROOT = Path(__file__).resolve().parent.parent
CHATBOT_ROOT = Path(r"C:\side_chatbot")
RELEASE_DIR = TTS_ROOT / "release"

# (원본 루트, 제공 폴더 안의 위치, 복사할 경로 패턴)
SOURCES: list[tuple[Path, str, list[str]]] = [
    (
        TTS_ROOT,
        "text-to-sql",
        ["pyproject.toml", "src/text_to_sql/**/*", "templates/**/*"],
    ),
    (
        CHATBOT_ROOT,
        "chatbot",
        [
            ".env.example",
            ".python-version",
            "requirements.txt",
            "app/__init__.py",
            "app/auth.py",
            "app/create_admin.py",
            "app/db.py",
            "app/main.py",
            "app/rag.py",
            "app/sql_mode.py",
            "static/*",
            "docs/*.txt",
        ],
    ),
]

# 제공 폴더 맨 위에 둘 파일 (release/ 폴더에서 관리)
TOP_FILES = {"README.md": "README.md", ".gitignore": "gitignore"}

SKIP_PARTS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def collect(root: Path, patterns: list[str]) -> list[Path]:
    files: set[Path] = set()
    for pattern in patterns:
        matched = [p for p in root.glob(pattern) if p.is_file()]
        if not matched:
            raise SystemExit(f"허용 목록의 경로가 없습니다: {root / pattern}")
        files.update(matched)
    return sorted(
        p
        for p in files
        if not SKIP_PARTS.intersection(p.relative_to(root).parts) and p.suffix not in SKIP_SUFFIXES
    )


def chatbot_requirements(text: str) -> str:
    """챗봇 requirements.txt에서 원본 저장소(git 주소)로 text-to-sql을 받는 줄을 뺀다.

    제공본에서는 같은 저장소의 text-to-sql 폴더를 설치한다 (README 참고).
    """
    lines = [
        line
        for line in text.splitlines()
        if "text-to-sql" not in line and "Text-to-SQL" not in line and "pip install -e" not in line
    ]
    while lines and not lines[-1].strip():
        lines.pop()
    lines += ["", "# SQL 모드: 같은 저장소의 text-to-sql 폴더를 먼저 설치한다 (README 참고)"]
    return "\n".join(lines) + "\n"


def prepare_output(out: Path) -> None:
    """제공 폴더를 비운다. 비어 있거나 Git 저장소일 때만 허용하고 .git은 남긴다."""
    for root in (TTS_ROOT, CHATBOT_ROOT):
        if out == root or root in out.parents or out in root.parents:
            raise SystemExit(f"제공 폴더가 원본 저장소와 겹칩니다: {out}")
    if not out.exists():
        out.mkdir(parents=True)
        return
    entries = [p for p in out.iterdir() if p.name != ".git"]
    if entries and not (out / ".git").is_dir():
        raise SystemExit(f"제공 폴더가 비어 있지 않고 Git 저장소도 아닙니다: {out}")
    for entry in entries:
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="제공 폴더 (비어 있거나 제공용 Git 저장소)")
    out = parser.parse_args().out.resolve()

    prepare_output(out)
    count = 0
    for root, dest_name, patterns in SOURCES:
        for src in collect(root, patterns):
            dest = out / dest_name / src.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if root == CHATBOT_ROOT and src.name == "requirements.txt":
                dest.write_text(chatbot_requirements(src.read_text(encoding="utf-8")), "utf-8")
            else:
                shutil.copy2(src, dest)
            count += 1
    for dest_name, release_name in TOP_FILES.items():
        shutil.copy2(RELEASE_DIR / release_name, out / dest_name)
        count += 1
    print(f"{count}개 파일을 {out}에 내보냈습니다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
