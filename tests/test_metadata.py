"""확인할 것:
- 정상 로드 / 파일 없음 → MetadataError
- 잘못된 식별자·물리 컬럼 형식 거부
- 유형명·별칭(대소문자 무시)으로 TYPEID 조회, 프로필에 없으면 오류
- SITE를 바꾸면 TYPEID가 바뀜"""

from pathlib import Path

import pytest

from text_to_sql.errors import MetadataError, UnsupportedQuestionError
from text_to_sql.repositories.metadata_repository import load_site_profile


# [스프링] JUnit5의 @Test 메서드. 함수 이름이 test_로 시작하면 pytest가 자동 실행한다
# assert A == B 는 assertEquals(B, A). 틀리면 그 테스트가 실패한다
def test_find_typeid() -> None:
    profile = load_site_profile("src/text_to_sql/metadata/sites/sample.json")
    assert profile.find_typeid("기본상품") == 901
    assert profile.find_alias("옵션") == "부가옵션"

def test_find_typeid_by_alias() -> None:
    profile = load_site_profile("src/text_to_sql/metadata/sites/sample.json")
    assert profile.find_typeid("옵션") == 903

def test_find_typeid_unknown_type() -> None:
    profile = load_site_profile("src/text_to_sql/metadata/sites/sample.json")
    # [스프링] assertThrows(UnsupportedQuestionError.class, () -> profile.findTypeid(...))
    with pytest.raises(UnsupportedQuestionError):
        profile.find_typeid("unknown_type")

def test_find_alias_unknown_alias() -> None:
    profile = load_site_profile("src/text_to_sql/metadata/sites/sample.json")
    with pytest.raises(UnsupportedQuestionError):
        profile.find_alias("unknown_alias")

def test_load_site_profile_file_not_found() -> None:
    with pytest.raises(MetadataError):
        load_site_profile("src/text_to_sql/metadata/sites/nonexistent.json")


# [스프링] @TempDir. pytest가 임시 폴더를 만들어 넘겨 주고 테스트가 끝나면 지운다
def test_load_site_profile_invalid_json(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{ 깨진 json", encoding="utf-8")
    with pytest.raises(MetadataError):
        load_site_profile(str(broken))


def test_load_site_profile_missing_required_key(tmp_path: Path) -> None:
    no_types = tmp_path / "no_types.json"
    no_types.write_text('{ "site": "x", "aliases": {} }', encoding="utf-8")
    with pytest.raises(MetadataError):
        load_site_profile(str(no_types))


def test_load_site_profile_alias_points_to_unknown_type(tmp_path: Path) -> None:
    inconsistent = tmp_path / "inconsistent.json"
    inconsistent.write_text(
        '{ "site": "x", "types": { "기본상품": 901 }, "aliases": { "옵션": "부가옵션" } }',
        encoding="utf-8",
    )
    with pytest.raises(MetadataError):
        load_site_profile(str(inconsistent))

# ---------------------------------------------------------------------------
# 테이블 별칭 (table_aliases): SQL 안에서 OBJ_MAIN 등에 붙이는 별칭. 현장 개발 가이드마다 다르다
#   키: main(T1·T2의 OBJ_MAIN), parent(상위 OBJ_MAIN), inv(OBJ_INV), child(하위 OBJ_MAIN)
#   기본값: main=M, parent=P, inv=I, child=C
# 별칭은 SQL 글자에 그대로 들어가므로, 프로필을 읽을 때 형식을 검사해야 한다.
# ---------------------------------------------------------------------------


def write_profile(tmp_path: Path, table_aliases: str | None) -> str:
    """table_aliases 부분만 바꿔 가며 프로필 파일을 만든다 (None이면 키를 아예 쓰지 않는다)."""
    extra = "" if table_aliases is None else f', "table_aliases": {table_aliases}'
    path = tmp_path / "profile.json"
    path.write_text(
        '{ "site": "x", "types": { "기본상품": 901 }, "aliases": { "기본상품": "기본상품" }'
        + extra
        + " }",
        encoding="utf-8",
    )
    return str(path)


# 별칭 문제 1: 파일에 table_aliases가 없으면 기본값(M, P, I, C)을 쓴다
def test_table_aliases_default() -> None:
    profile = load_site_profile("src/text_to_sql/metadata/sites/sample.json")
    assert profile.table_aliases == {"main": "M", "parent": "P", "inv": "I", "child": "C"}


# 별칭 문제 2: 일부만 적으면 나머지는 기본값이 유지된다
def test_table_aliases_partial_override(tmp_path: Path) -> None:
    profile = load_site_profile(write_profile(tmp_path, '{ "main": "OMA" }'))
    assert profile.table_aliases == {"main": "OMA", "parent": "P", "inv": "I", "child": "C"}


# 별칭 문제 3: 전부 바꿀 수 있다
def test_table_aliases_full_override(tmp_path: Path) -> None:
    text = '{ "main": "OMA", "parent": "OMC", "inv": "OI", "child": "OMB" }'
    profile = load_site_profile(write_profile(tmp_path, text))
    assert profile.table_aliases == {"main": "OMA", "parent": "OMC", "inv": "OI", "child": "OMB"}


# 별칭 문제 4 (보안): 테이블 이름과 같은 규칙(영문 대문자로 시작, 대문자·숫자·밑줄)만 허용한다
# [스프링] @ParameterizedTest. 같은 검사를 값만 바꿔 여러 번 돈다
@pytest.mark.parametrize(
    "bad_alias",
    ["m", "1A", "A B", "A;DROP", "A--", "", "A.B", "A'"],
)
def test_table_aliases_reject_invalid_format(tmp_path: Path, bad_alias: str) -> None:
    path = write_profile(tmp_path, f'{{ "main": "{bad_alias}" }}')
    with pytest.raises(MetadataError):
        load_site_profile(path)


# 별칭 문제 5: 서로 다른 역할에 같은 별칭을 쓰면 SQL이 깨지므로 거부한다
def test_table_aliases_reject_duplicates(tmp_path: Path) -> None:
    path = write_profile(tmp_path, '{ "parent": "A", "child": "A" }')
    with pytest.raises(MetadataError):
        load_site_profile(path)


# 별칭 문제 6: 기본값과 겹치는 경우도 거부한다 (main만 P로 바꾸면 기본 parent=P와 충돌)
def test_table_aliases_reject_conflict_with_default(tmp_path: Path) -> None:
    path = write_profile(tmp_path, '{ "main": "P" }')
    with pytest.raises(MetadataError):
        load_site_profile(path)


# 별칭 문제 7: 모르는 키(오타 등)는 조용히 무시하지 않고 거부한다
def test_table_aliases_reject_unknown_key(tmp_path: Path) -> None:
    path = write_profile(tmp_path, '{ "mian": "OMA" }')
    with pytest.raises(MetadataError):
        load_site_profile(path)
