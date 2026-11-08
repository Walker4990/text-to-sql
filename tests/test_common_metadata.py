"""공통 메타데이터(src/text_to_sql/metadata/common.json) 읽기 문제지.

모든 현장이 똑같은 값(테이블 구조, 최신 버전 조건, DB 종류)을 담는 파일이다.
이 파일을 읽는 load_common_metadata()를 repositories/metadata_repository.py에 만들면 된다.
"""

from pathlib import Path

import pytest

from text_to_sql.errors import MetadataError, UnsupportedQuestionError
from text_to_sql.repositories.metadata_repository import load_common_metadata

COMMON_PATH = "src/text_to_sql/metadata/common.json"


# 문제 1: 파일을 읽으면 DB 종류와 최대 행 수를 알 수 있어야 한다
def test_load_common_metadata() -> None:
    common = load_common_metadata(COMMON_PATH)
    assert common.dialect == "postgresql"
    assert common.max_rows == 100


# 문제 2: 테이블 이름으로 "최신 버전 조건"을 찾을 수 있어야 한다
# (END_DT 컬럼이 '99991231'이면 최신 버전이라는 뜻)
def test_find_table_version_condition() -> None:
    common = load_common_metadata(COMMON_PATH)
    table = common.find_table("OBJ_MAIN")
    assert table.version_column == "END_DT"
    assert table.version_value == "99991231"


# 문제 3: 대상 테이블 3개가 모두 들어 있어야 한다
def test_find_all_target_tables() -> None:
    common = load_common_metadata(COMMON_PATH)
    for name in ("OBJ_MAIN", "OBJ_INV", "OBJ_DETAIL"):
        assert common.find_table(name).version_column == "END_DT"


# 문제 4: 없는 테이블을 찾으면 UnsupportedQuestionError
# (이미 만든 SiteProfile.find_typeid와 같은 방식)
def test_find_table_unknown() -> None:
    common = load_common_metadata(COMMON_PATH)
    with pytest.raises(UnsupportedQuestionError):
        common.find_table("NO_SUCH_TABLE")


# 문제 5: 파일이 없으면 MetadataError
def test_load_common_metadata_file_not_found() -> None:
    with pytest.raises(MetadataError):
        load_common_metadata("src/text_to_sql/metadata/없는파일.json")


# 문제 6 (보안): 테이블 이름에 SQL이 섞여 있으면 읽을 때 거부해야 한다
# 테이블 이름은 나중에 SQL 문장에 그대로 들어가기 때문에, 영문 대문자·숫자·_ 만 허용한다
def test_load_common_metadata_rejects_unsafe_table_name(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text(
        '{ "version": 1, "dialect": "postgresql", "max_rows": 100, "tables": {'
        ' "OBJ_MAIN; DROP TABLE X": { "label": "x",'
        ' "version_column": "END_DT", "version_value": "99991231" } } }',
        encoding="utf-8",
    )
    with pytest.raises(MetadataError):
        load_common_metadata(str(unsafe))
