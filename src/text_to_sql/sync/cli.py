"""사이트 프로필 동기화 도구.

  python -m text_to_sql.sync --site demo --sheet-id <ID>                 미리보기 (저장 안 함)
  python -m text_to_sql.sync --site demo --sheet-id <ID> --apply [--yes]  저장
  python -m text_to_sql.sync --site demo --csv-dir <폴더>                 시트 대신 CSV 폴더
  python -m text_to_sql.sync --site demo --export-csv <폴더>              지금 스냅샷 → CSV

흐름: 출처 읽기 → 검증(MV-01~09) → 차이 보고 → (--apply일 때만) 스냅샷 저장.
종료 코드: 0 성공, 1 검증 실패, 2 출처 접근 실패, 3 위험 변경인데 --yes 없음.
"""

import argparse
import os
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path

from text_to_sql.config import DEFAULT_METADATA_DIR
from text_to_sql.errors import MetadataError, ProfileSourceError
from text_to_sql.repositories.metadata_repository import SiteProfile, load_site_profile
from text_to_sql.repositories.profile_source import CsvFolderSource, ProfileSource
from text_to_sql.sync.diff import diff_profiles
from text_to_sql.sync.sheet_rows import rows_to_profile
from text_to_sql.sync.snapshot import export_csv, snapshot_text, write_snapshot

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_SOURCE = 2
EXIT_RISKY = 3

# (서비스 계정 키 경로, 스프레드시트 ID) → 출처. 테스트에서는 가짜를 넘긴다
SheetFactory = Callable[[str, str], ProfileSource]

_SITE = re.compile(r"[A-Za-z0-9_-]+")
SYNCED_BY = "text_to_sql.sync"


def _open_sheet(credentials: str, sheet_id: str) -> ProfileSource:
    # gspread는 시트를 읽을 때만 필요하다 (선택 설치 [sheets]). 그래서 여기서 import한다
    from text_to_sql.repositories.sheet_source import open_google_sheet

    return open_google_sheet(credentials, sheet_id)


def _now() -> datetime:
    return datetime.now().astimezone()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m text_to_sql.sync",
        description="구글 시트의 사이트 프로필을 검증해 스냅샷 JSON으로 저장한다",
    )
    parser.add_argument("--site", required=True, help="사이트 이름 (설정 탭의 site와 같아야 함)")
    parser.add_argument("--sheet-id", help="스프레드시트 ID (기본: 환경변수 TTS_SHEET_ID)")
    parser.add_argument(
        "--credentials", help="서비스 계정 키 JSON 경로 (기본: 환경변수 TTS_GOOGLE_CREDENTIALS)"
    )
    parser.add_argument("--csv-dir", help="시트 대신 탭별 CSV 폴더에서 읽는다")
    parser.add_argument(
        "--out-dir", help="스냅샷 폴더 (기본: TTS_METADATA_DIR, 없으면 패키지 metadata/)"
    )
    parser.add_argument("--apply", action="store_true", help="검증을 통과하면 저장한다")
    parser.add_argument("--yes", action="store_true", help="위험 변경이 있어도 저장한다")
    parser.add_argument("--export-csv", help="지금 스냅샷을 탭별 CSV로 내보낼 폴더")
    return parser


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    sheet_factory: SheetFactory | None = None,
    now: Callable[[], datetime] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    source_env = os.environ if env is None else env
    site: str = args.site
    if not _SITE.fullmatch(site):
        print(f"[오류] --site는 영문·숫자·_·- 만 쓸 수 있습니다: {site!r}")
        return EXIT_INVALID
    out_dir = Path(args.out_dir or source_env.get("TTS_METADATA_DIR") or DEFAULT_METADATA_DIR)
    snapshot_path = out_dir / "sites" / f"{site}.json"

    if args.export_csv:
        return _export(snapshot_path, Path(args.export_csv))

    # ── 1. 출처 정하기 ──
    meta: dict[str, str]
    try:
        if args.csv_dir:
            source: ProfileSource = CsvFolderSource(Path(args.csv_dir))
            meta = {"source": "csv", "csv_dir": str(args.csv_dir)}
        else:
            sheet_id = args.sheet_id or source_env.get("TTS_SHEET_ID", "")
            credentials = args.credentials or source_env.get("TTS_GOOGLE_CREDENTIALS", "")
            if not sheet_id or not credentials:
                print(
                    "[시트 접근 실패] --sheet-id(또는 TTS_SHEET_ID)와 "
                    "--credentials(또는 TTS_GOOGLE_CREDENTIALS)가 필요합니다"
                )
                return EXIT_SOURCE
            source = (sheet_factory or _open_sheet)(credentials, sheet_id)
            meta = {"source": "google_sheet", "sheet_id": sheet_id}
        # ── 2. 읽기 ──
        tabs = source.read_tabs()
    except ProfileSourceError as err:
        print(f"[시트 접근 실패] {err}")
        if err.__cause__ is not None:
            print(f"  원인: {err.__cause__!r}")
        return EXIT_SOURCE

    # ── 3. 검증 ──
    result = rows_to_profile(tabs, site)
    for warning in result.warnings:
        print(f"[경고] {warning}")
    if result.profile is None:
        print(f"[검증 실패] {len(result.errors)}건 - 스냅샷을 저장하지 않았습니다")
        for error in result.errors:
            print(f"  {error}")
        return EXIT_INVALID

    # ── 4. 차이 보고 ──
    old = _load_old(snapshot_path)
    changes = diff_profiles(old, result.profile)
    if old is None:
        print(f"[새로 만듦] {snapshot_path} (기존 스냅샷 없음)")
    elif not changes:
        print("[차이 없음] 시트와 스냅샷이 같습니다. 저장하지 않습니다")
        return EXIT_OK
    for change in changes:
        print(f"  {change}")
    risky = sum(change.risky for change in changes)
    counts = {kind: sum(c.kind == kind for c in changes) for kind in ("add", "remove", "change")}
    print(
        f"추가 {counts['add']} · 삭제 {counts['remove']} · 변경 {counts['change']} (위험 {risky})"
    )

    # ── 5. 저장 ──
    if not args.apply:
        print("미리보기입니다. 저장하려면 --apply를 붙입니다")
        return EXIT_OK
    if risky and not args.yes:
        print("[저장 안 함] 위험 변경이 있습니다. 내용을 확인했으면 --yes를 함께 붙입니다")
        return EXIT_RISKY
    version = (now or _now)().isoformat(timespec="seconds")
    full_meta = {"version": version, **meta}
    if result.owner:
        full_meta["owner"] = result.owner
    full_meta["synced_by"] = SYNCED_BY
    write_snapshot(snapshot_path, snapshot_text(result.profile, full_meta))
    print(f"[저장 완료] {snapshot_path} version={version}")
    print("서비스를 재시작해야 반영됩니다")
    return EXIT_OK


def _load_old(path: Path) -> SiteProfile | None:
    """지금 스냅샷. 없거나 깨졌으면 None (깨졌으면 경고를 남기고 새로 만드는 것으로 본다)."""
    if not path.exists():
        return None
    try:
        return load_site_profile(str(path))
    except MetadataError as err:
        print(f"[경고] 기존 스냅샷을 읽지 못해 새로 만드는 것으로 봅니다: {err}")
        return None


def _export(snapshot_path: Path, folder: Path) -> int:
    try:
        profile = load_site_profile(str(snapshot_path))
    except MetadataError as err:
        print(f"[오류] 내보낼 스냅샷을 읽지 못했습니다: {err}")
        return EXIT_INVALID
    for path in export_csv(profile, folder):
        print(f"[내보냄] {path}")
    return EXIT_OK
