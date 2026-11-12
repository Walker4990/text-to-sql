"""CSV 폴더 출처와 내보내기 왕복 (설계 07, 13장 5단계).

왕복: JSON 프로필 → CSV(탭별 파일) → 시트 행 변환 → 프로필 이 원래 프로필과 같아야 한다.
지금 손으로 관리하던 demo.json을 시트로 옮겨도 동작이 바뀌지 않는다는 근거다.
"""

from pathlib import Path

import pytest

from text_to_sql.errors import ProfileSourceError
from text_to_sql.repositories.metadata_repository import load_site_profile
from text_to_sql.repositories.profile_source import CsvFolderSource
from text_to_sql.sync.sheet_rows import rows_to_profile
from text_to_sql.sync.snapshot import export_csv

SITES = Path("src/text_to_sql/metadata/sites")


# 문제 1: demo·sample 왕복
@pytest.mark.parametrize("site", ["demo", "sample"])
def test_round_trip(tmp_path: Path, site: str) -> None:
    original = load_site_profile(str(SITES / f"{site}.json"))
    export_csv(original, tmp_path)
    result = rows_to_profile(CsvFolderSource(tmp_path).read_tabs(), site)
    assert result.errors == []
    assert result.profile == original


# 문제 2: 탭 6개를 파일로 만든다. 엑셀에서 한글이 깨지지 않게 BOM을 붙인다
def test_export_files(tmp_path: Path) -> None:
    paths = export_csv(load_site_profile(str(SITES / "demo.json")), tmp_path, owner="홍길동")
    names = sorted(p.name for p in paths)
    assert names == sorted(
        f"{tab}.csv" for tab in ("설정", "유형", "별칭", "표준항목", "항목", "테이블별칭")
    )
    settings = (tmp_path / "설정.csv").read_bytes()
    assert settings.startswith(b"\xef\xbb\xbf")
    assert "담당자,홍길동" in settings.decode("utf-8-sig")


# 문제 3: 별칭 파일에는 정식 이름 자기 연결 줄을 쓰지 않는다
def test_export_aliases_without_self(tmp_path: Path) -> None:
    export_csv(load_site_profile(str(SITES / "demo.json")), tmp_path)
    lines = (tmp_path / "별칭.csv").read_text(encoding="utf-8-sig").splitlines()
    assert "라이더,특약,Y," in lines
    assert not any(line.startswith("특약,특약,") for line in lines)


# 문제 4: 폴더가 없으면 ProfileSourceError
def test_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(ProfileSourceError):
        CsvFolderSource(tmp_path / "none").read_tabs()


# 문제 5: BOM 없는 UTF-8 파일도 읽는다, 파일 이름(확장자 뺀 것)이 탭 이름
def test_reads_plain_utf8(tmp_path: Path) -> None:
    (tmp_path / "유형.csv").write_text("유형명,TYPEID\n보험상품,1100\n", encoding="utf-8")
    assert CsvFolderSource(tmp_path).read_tabs() == {
        "유형": [["유형명", "TYPEID"], ["보험상품", "1100"]]
    }
