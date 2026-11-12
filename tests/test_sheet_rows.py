"""시트 탭(행 목록) → 사이트 프로필 변환과 검증 (설계 07, 3·4장, 13장 2·3단계).

탭은 {탭 이름: [헤더 행, 데이터 행, ...]} 모양이다. 행 번호는 시트 기준(헤더가 1행)이다.
"""

import copy

import pytest

from text_to_sql.errors import MetadataError
from text_to_sql.repositories.metadata_repository import DEFAULT_TABLE_ALIASES, ItemDef
from text_to_sql.sync import sheet_rows
from text_to_sql.sync.sheet_rows import RowIssue, rows_to_profile

Tabs = dict[str, list[list[str]]]

BASE: Tabs = {
    "설정": [["키", "값"], ["site", "demo"], ["담당자", "홍길동"]],
    "유형": [
        ["유형명", "TYPEID", "사용", "설명"],
        ["보험상품", "1100", "Y", "최상위 상품"],
        ["특약", "1120", "", ""],
    ],
    "별칭": [["별칭", "유형명", "사용", "설명"], ["라이더", "특약", "Y", ""]],
    "항목": [
        ["유형명", "항목명", "물리컬럼", "다중값", "사용", "설명"],
        ["보험상품", "보험기간", "char01", "N", "Y", ""],
        ["보험상품", "판매채널", "CHAR05", "Y", "Y", ""],
    ],
    "등록항목": [["항목명칭", "사용", "설명"], ["상품코드", "Y", ""], ["특약코드", "", ""]],
    "테이블별칭": [["역할", "별칭", "설명"], ["main", "OMA", ""]],
}


def tabs(**changes: list[list[str]] | None) -> Tabs:
    """BASE를 복사하고 탭을 바꾼다. None을 넘기면 그 탭을 지운다."""
    result = copy.deepcopy(BASE)
    for name, rows in changes.items():
        if rows is None:
            result.pop(name, None)
        else:
            result[name] = rows
    return result


def codes(issues: list[RowIssue]) -> list[str]:
    return [issue.code for issue in issues]


# 문제 1: 정상 시트 → 프로필
def test_valid_sheet_builds_profile() -> None:
    result = rows_to_profile(BASE, "demo")
    assert result.errors == []
    profile = result.profile
    assert profile is not None
    assert profile.site == "demo"
    assert profile.types == {"보험상품": 1100, "특약": 1120}
    # 정식 이름 자기 연결은 자동으로 채운다
    assert profile.aliases == {"보험상품": "보험상품", "특약": "특약", "라이더": "특약"}
    # 물리컬럼은 대문자로 바꿔 읽는다, 다중값 Y
    assert profile.items == {
        "보험상품": {"보험기간": ItemDef("CHAR01"), "판매채널": ItemDef("CHAR05", multi=True)}
    }
    assert profile.registered_items == ["상품코드", "특약코드"]  # 시트 순서 유지
    assert profile.table_aliases == {**DEFAULT_TABLE_ALIASES, "main": "OMA"}
    assert result.owner == "홍길동"


# 문제 2: 선택 탭이 없으면 빈 값과 기본값으로 만든다
def test_optional_tabs_absent() -> None:
    result = rows_to_profile(tabs(별칭=None, 항목=None, 등록항목=None, 테이블별칭=None), "demo")
    assert result.errors == []
    assert result.profile is not None
    assert result.profile.items == {}
    assert result.profile.registered_items == []
    assert result.profile.table_aliases == DEFAULT_TABLE_ALIASES


# 문제 3: 필수 탭이 없거나 헤더에 필수 열이 없으면 MV-01
def test_required_tab_and_header() -> None:
    assert "MV-01" in codes(rows_to_profile(tabs(설정=None), "demo").errors)
    no_typeid = tabs(유형=[["유형명", "사용"], ["보험상품", "Y"]])
    result = rows_to_profile(no_typeid, "demo")
    assert result.profile is None
    assert any(e.code == "MV-01" and e.tab == "유형" and e.row == 1 for e in result.errors)


# 문제 4: site가 없거나, 형식이 틀리거나, --site와 다르면 MV-02
@pytest.mark.parametrize(
    "rows",
    [
        [["키", "값"], ["담당자", "홍길동"]],
        [["키", "값"], ["site", "../demo"]],
        [["키", "값"], ["site", "other"]],
    ],
)
def test_site_setting(rows: list[list[str]]) -> None:
    result = rows_to_profile(tabs(설정=rows), "demo")
    assert result.profile is None
    assert "MV-02" in codes(result.errors)


# 문제 5: TYPEID 형식 (숫자 서식이 섞이면 오류), 행 번호
@pytest.mark.parametrize("typeid", ["1,100", "1100.0", "-1", "0", "", "abc"])
def test_bad_typeid(typeid: str) -> None:
    rows = [["유형명", "TYPEID"], ["보험상품", typeid], ["특약", "1120"]]
    result = rows_to_profile(tabs(유형=rows, 별칭=None, 항목=None), "demo")
    assert any(e.code == "MV-03" and e.row == 2 for e in result.errors)


# 문제 6: 유형명 형식·중복, TYPEID 중복, 사용 유형 0개
def test_type_rules() -> None:
    bad_name = [["유형명", "TYPEID"], ["보험 상품", "1100"]]
    dup_name = [["유형명", "TYPEID"], ["보험상품", "1100"], ["보험상품", "1101"]]
    dup_id = [["유형명", "TYPEID"], ["보험상품", "1100"], ["특약", "1100"]]
    none_used = [["유형명", "TYPEID", "사용"], ["보험상품", "1100", "N"]]
    for rows in (bad_name, dup_name, dup_id, none_used):
        result = rows_to_profile(tabs(유형=rows, 별칭=None, 항목=None), "demo")
        assert result.profile is None
        assert "MV-03" in codes(result.errors), rows
    dup = rows_to_profile(tabs(유형=dup_name, 별칭=None, 항목=None), "demo").errors
    assert any(e.row == 3 and "2행" in e.message for e in dup)


# 문제 7: 별칭 규칙
def test_alias_rules() -> None:
    unknown = [["별칭", "유형명"], ["라이더", "없는유형"]]
    dup = [["별칭", "유형명"], ["라이더", "특약"], ["라이더", "보험상품"]]
    other_canonical = [["별칭", "유형명"], ["특약", "보험상품"]]
    for rows in (unknown, dup, other_canonical):
        result = rows_to_profile(tabs(별칭=rows), "demo")
        assert result.profile is None
        assert "MV-04" in codes(result.errors), rows


# 문제 8: 자기 자신을 가리키는 별칭 줄은 무시, 사용=N 줄은 제외
def test_alias_self_and_disabled() -> None:
    rows = [["별칭", "유형명", "사용"], ["특약", "특약", "Y"], ["선택특약", "특약", "N"]]
    result = rows_to_profile(tabs(별칭=rows), "demo")
    assert result.errors == []
    assert result.profile is not None
    assert "선택특약" not in result.profile.aliases


# 문제 9: 사용=N 유형을 가리키는 별칭·항목은 경고와 함께 제외
def test_disabled_type_references_warn() -> None:
    types = [["유형명", "TYPEID", "사용"], ["보험상품", "1100", "Y"], ["특약", "1120", "N"]]
    items = [["유형명", "항목명", "물리컬럼"], ["특약", "보장기간", "CHAR01"]]
    result = rows_to_profile(tabs(유형=types, 항목=items), "demo")
    assert result.errors == []
    assert result.profile is not None
    assert "특약" not in result.profile.types
    assert "라이더" not in result.profile.aliases
    assert result.profile.items == {}
    assert codes(result.warnings) == ["MV-04", "MV-05"]


# 문제 10: 항목 규칙
@pytest.mark.parametrize(
    "rows",
    [
        [["유형명", "항목명", "물리컬럼"], ["보험상품", "보험기간", "CHAR1"]],
        [["유형명", "항목명", "물리컬럼"], ["보험상품", "보험 기간", "CHAR01"]],
        [["유형명", "항목명", "물리컬럼"], ["없는유형", "보험기간", "CHAR01"]],
        [["유형명", "항목명", "물리컬럼", "다중값"], ["보험상품", "보험기간", "CHAR01", "X"]],
        [
            ["유형명", "항목명", "물리컬럼"],
            ["보험상품", "보험기간", "CHAR01"],
            ["보험상품", "보험기간", "CHAR02"],
        ],
        [
            ["유형명", "항목명", "물리컬럼"],
            ["보험상품", "보험기간", "CHAR01"],
            ["보험상품", "납입기간", "char01"],
        ],
    ],
)
def test_item_rules(rows: list[list[str]]) -> None:
    result = rows_to_profile(tabs(항목=rows), "demo")
    assert result.profile is None
    assert "MV-05" in codes(result.errors)


# 문제 11: 다른 유형이면 같은 항목명·컬럼을 써도 된다
def test_item_same_name_other_type() -> None:
    rows = [
        ["유형명", "항목명", "물리컬럼"],
        ["보험상품", "보장기간", "CHAR01"],
        ["특약", "보장기간", "CHAR01"],
    ]
    result = rows_to_profile(tabs(항목=rows), "demo")
    assert result.errors == []


# 문제 12: 등록항목 형식·중복
@pytest.mark.parametrize(
    "rows",
    [[["항목명칭"], ["상품 코드"]], [["항목명칭"], ["상품코드"], ["상품코드"]]],
)
def test_registered_rules(rows: list[list[str]]) -> None:
    assert "MV-06" in codes(rows_to_profile(tabs(등록항목=rows), "demo").errors)


# 문제 13: 테이블별칭 역할·형식·중복 (기본값과 겹치는 것도 중복)
@pytest.mark.parametrize(
    "rows",
    [
        [["역할", "별칭"], ["master", "M"]],
        [["역할", "별칭"], ["main", "oma"]],
        [["역할", "별칭"], ["main", "OMA"], ["main", "OMB"]],
        [["역할", "별칭"], ["main", "P"]],
    ],
)
def test_table_alias_rules(rows: list[list[str]]) -> None:
    assert "MV-07" in codes(rows_to_profile(tabs(테이블별칭=rows), "demo").errors)


# 문제 14: 사용 칸은 Y/N/빈 칸만 (소문자 y·n은 허용)
def test_use_column() -> None:
    bad = [["유형명", "TYPEID", "사용"], ["보험상품", "1100", "예"], ["특약", "1120", "y"]]
    result = rows_to_profile(tabs(유형=bad, 별칭=None, 항목=None), "demo")
    assert [(e.code, e.row) for e in result.errors] == [("MV-08", 2)]


# 문제 15: 오류는 첫 번째만이 아니라 전부 모은다
def test_collects_all_errors() -> None:
    result = rows_to_profile(
        tabs(
            유형=[["유형명", "TYPEID"], ["보험상품", "x"], ["특약", "1120"]],
            항목=[["유형명", "항목명", "물리컬럼"], ["특약", "보장기간", "C1"]],
            등록항목=[["항목명칭"], ["상품 코드"]],
        ),
        "demo",
    )
    assert result.profile is None
    assert {"MV-03", "MV-05", "MV-06"} <= set(codes(result.errors))


# 문제 15-1: 유형 행 자체가 잘못됐으면, 그 유형을 가리키는 별칭·항목은 오류를 겹쳐 내지 않는다
# (TYPEID 하나 틀렸는데 "없는 유형" 오류가 수십 줄 나오면 진짜 원인을 찾기 어렵다)
def test_no_cascade_from_broken_type() -> None:
    rows = [["유형명", "TYPEID"], ["보험상품", "11O0"], ["특약", "1120"]]
    result = rows_to_profile(tabs(유형=rows), "demo")
    assert [(e.code, e.tab, e.row) for e in result.errors] == [("MV-03", "유형", 2)]


# 문제 16: 빈 행은 건너뛰고, 짧은 행은 빈 칸으로 채우고, 행 번호는 시트 그대로
def test_blank_and_short_rows() -> None:
    rows = [["유형명", "TYPEID", "사용"], ["보험상품", "1100"], ["", "", ""], [], ["특약", "x"]]
    result = rows_to_profile(tabs(유형=rows, 별칭=None, 항목=None), "demo")
    assert [(e.code, e.row) for e in result.errors] == [("MV-03", 5)]


# 문제 17: 앞뒤 공백은 지우고 읽는다
def test_cells_are_stripped() -> None:
    rows = [[" 유형명 ", "TYPEID "], [" 보험상품 ", " 1100 "]]
    result = rows_to_profile(tabs(유형=rows, 별칭=None, 항목=None), "demo")
    assert result.profile is not None
    assert result.profile.types == {"보험상품": 1100}


# 문제 18: 오류 출력 형식
def test_issue_str() -> None:
    assert str(RowIssue("MV-03", "유형", 5, "잘못")) == "MV-03 유형 5행: 잘못"
    assert str(RowIssue("MV-01", "설정", None, "없음")) == "MV-01 설정: 없음"


# 문제 19: MV-09 — 최종 결과가 로더 검사에 걸리면 저장하지 않는다
def test_loader_check(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(data: object, origin: str) -> None:
        raise MetadataError(f"로더 거절 {origin}")

    monkeypatch.setattr(sheet_rows, "parse_site_profile", broken)
    result = rows_to_profile(BASE, "demo")
    assert result.profile is None
    assert codes(result.errors) == ["MV-09"]
