"""T5 속성 조회 문제지 (문제 1~36). 명세: docs/05_구현명세서.md 8장.

"BP001 기본상품의 약정기간 알려줘" 같은 질문을 OBJ_DETAIL 조인 SQL로 만든다.
OBJ_DETAIL의 값은 CHAR01, CHAR02 … 같은 물리 컬럼에 들어 있고, 어느 컬럼이 "약정기간"인지는
사이트 프로필의 항목 사전(items)으로만 알 수 있다.

단계별 구현할 곳 (명세 8장의 단계 순서와 같다)
- 1단계 (문제 1~6):   repositories/metadata_repository.py  ItemDef, SiteProfile.items, find_item
- 2단계 (문제 7~14):  repositories/metadata_repository.py  load_site_profile가 items를 읽고 검사
- 3단계 (문제 15~20): domain/intent.py                      Intent.attributes 필드와 검사
- 4단계 (문제 21~29): services/sql_builder.py               build_attribute (T5 템플릿)
- 5단계 (문제 30~34): services/intent_extractor.py          프롬프트에 항목 목록·예시
- 6단계 (문제 35~36): 전체 흐름 (demo 프로필 + 서비스)

함께 고칠 기존 문제 (8장 각 단계에 적혀 있다)
- tests/test_metadata.py 별칭 문제 1~3: 기본 테이블 별칭에 "detail": "D"가 추가된다
- tests/test_sql_builder.py 문제 31: attribute는 이제 구현되므로 pattern·pattern_catalog만 거절
"""

import json
from pathlib import Path

import httpx
import pytest

from text_to_sql.domain.intent import Intent
from text_to_sql.errors import MetadataError, UnsupportedQuestionError
from text_to_sql.repositories.metadata_repository import (
    DEFAULT_TABLE_ALIASES,
    ItemDef,
    Metadata,
    SiteProfile,
    TableProfile,
    load_common_metadata,
    load_site_profile,
)
from text_to_sql.services.intent_extractor import (
    FakeIntentExtractor,
    OpenAiIntentExtractor,
    build_system_prompt,
)
from text_to_sql.services.sql_builder import SqlBuilder, render_sql
from text_to_sql.services.sql_validator import SqlValidator
from text_to_sql.services.text_to_sql_service import TextToSqlService

COMMON_PATH = "src/text_to_sql/metadata/common.json"
SAMPLE_PATH = "src/text_to_sql/metadata/sites/sample.json"
DEMO_PATH = "src/text_to_sql/metadata/sites/demo.json"


def flat(sql: str) -> str:
    """줄 바꿈과 여러 칸 공백을 한 칸으로 정리한다 (비교하기 쉽게)."""
    return " ".join(sql.split())


def small_profile(**overrides: object) -> SiteProfile:
    """1단계용 작은 프로필. items를 직접 넣어 만든다 (파일을 읽지 않는다)."""
    values: dict[str, object] = {
        "site": "t",
        "types": {"기본상품": 901, "부가옵션": 903},
        "aliases": {"기본상품": "기본상품", "부가옵션": "부가옵션", "상품": "기본상품"},
        "items": {
            "기본상품": {
                "약정기간": ItemDef(column="CHAR01"),
                "부가혜택": ItemDef(column="CHAR03", multi=True),
            }
        },
    }
    values.update(overrides)
    return SiteProfile(**values)  # type: ignore[arg-type]


def make_builder() -> SqlBuilder:
    return SqlBuilder(load_common_metadata(COMMON_PATH), load_site_profile(SAMPLE_PATH))


def attribute(**fields: object) -> Intent:
    """attribute 의도를 만든다. 기본값: 기본상품 BP001의 약정기간."""
    values: dict[str, object] = {
        "kind": "attribute",
        "target_type": "기본상품",
        "code": "BP001",
        "attributes": ["약정기간"],
    }
    values.update(fields)
    return Intent.model_validate(values)


def write_profile(tmp_path: Path, items: object) -> str:
    """items 부분만 바꿔 가며 프로필 파일을 만든다."""
    data = {
        "site": "x",
        "types": {"기본상품": 901},
        "aliases": {"기본상품": "기본상품"},
        "items": items,
    }
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


# ───────────── 1단계: 항목 사전 모델 (ItemDef, SiteProfile.items, find_item) ─────────────


# 문제 1: 항목 이름으로 항목 정의(물리 컬럼)를 찾는다
def test_find_item_returns_definition() -> None:
    assert small_profile().find_item("기본상품", "약정기간") == ItemDef(column="CHAR01")


# 문제 2: 유형은 별칭으로 말해도 된다 ("상품" → 기본상품)
def test_find_item_accepts_type_alias() -> None:
    assert small_profile().find_item("상품", "약정기간").column == "CHAR01"


# 문제 3: 사전에 없는 항목은 거절한다
def test_find_item_unknown_item() -> None:
    with pytest.raises(UnsupportedQuestionError):
        small_profile().find_item("기본상품", "없는항목")


# 문제 4: 항목 사전이 아예 없는 유형(부가옵션)도 거절한다
# (KeyError가 아니라 UnsupportedQuestionError)
def test_find_item_type_without_items() -> None:
    with pytest.raises(UnsupportedQuestionError):
        small_profile().find_item("부가옵션", "약정기간")


# 문제 5: 다중값 항목은 이번 PoC에서 지원하지 않는다. 사유에 "다중값"이 들어간다
def test_find_item_multi_value_is_unsupported() -> None:
    with pytest.raises(UnsupportedQuestionError, match="다중값"):
        small_profile().find_item("기본상품", "부가혜택")


# 문제 6: items 없이도 SiteProfile을 만들 수 있다 (기본값: 빈 딕셔너리)
# 기존 코드·테스트가 깨지지 않게
def test_site_profile_items_default_empty() -> None:
    profile = SiteProfile(site="t", types={"기본상품": 901}, aliases={"기본상품": "기본상품"})
    assert profile.items == {}


# ───────────── 2단계: 프로필 파일에서 items 읽기 ─────────────


# 문제 7: sample.json의 항목 사전을 읽는다
def test_load_items_from_sample() -> None:
    profile = load_site_profile(SAMPLE_PATH)
    assert profile.items["기본상품"]["약정기간"] == ItemDef(column="CHAR01", multi=False)
    assert profile.items["기본상품"]["부가혜택"] == ItemDef(column="CHAR03", multi=True)
    assert profile.items["부가옵션"]["옵션요금"] == ItemDef(column="CHAR01")


# 문제 8: 파일에 items가 없으면 빈 딕셔너리
def test_load_without_items(tmp_path: Path) -> None:
    path = tmp_path / "no_items.json"
    path.write_text(
        '{ "site": "x", "types": { "기본상품": 901 }, "aliases": { "기본상품": "기본상품" } }',
        encoding="utf-8",
    )
    assert load_site_profile(str(path)).items == {}


# 문제 9: TEXT, DESC 컬럼과 세 자리 번호(CHAR300)도 허용한다
def test_load_accepts_text_desc_and_three_digits(tmp_path: Path) -> None:
    items = {
        "기본상품": {
            "설명": {"column": "TEXT01"},
            "상세설명": {"column": "DESC02"},
            "비고": {"column": "CHAR300"},
        }
    }
    profile = load_site_profile(write_profile(tmp_path, items))
    assert [item.column for item in profile.items["기본상품"].values()] == [
        "TEXT01",
        "DESC02",
        "CHAR300",
    ]


# 문제 10: types에 없는 유형의 항목 사전은 거부한다
def test_load_items_for_unknown_type(tmp_path: Path) -> None:
    with pytest.raises(MetadataError):
        load_site_profile(write_profile(tmp_path, {"없는유형": {"약정기간": {"column": "CHAR01"}}}))


# 문제 11: 물리 컬럼은 CHAR/TEXT/DESC + 숫자 2~3자리만 허용한다 (SELECT 절에 그대로 들어가기 때문)
@pytest.mark.parametrize(
    "column",
    ["CHAR1", "CHAR1234", "NAME", "char01", "CHAR01; DROP TABLE X", "OBJ_NAME", "", 1],
)
def test_load_rejects_invalid_column(tmp_path: Path, column: object) -> None:
    with pytest.raises(MetadataError):
        load_site_profile(write_profile(tmp_path, {"기본상품": {"약정기간": {"column": column}}}))


# 문제 12: column 키가 없으면 거부한다
def test_load_rejects_missing_column(tmp_path: Path) -> None:
    with pytest.raises(MetadataError):
        load_site_profile(write_profile(tmp_path, {"기본상품": {"약정기간": {"multi": False}}}))


# 문제 13: multi는 true/false만 허용한다
@pytest.mark.parametrize("multi", ["yes", 1, None])
def test_load_rejects_non_bool_multi(tmp_path: Path, multi: object) -> None:
    items = {"기본상품": {"약정기간": {"column": "CHAR01", "multi": multi}}}
    with pytest.raises(MetadataError):
        load_site_profile(write_profile(tmp_path, items))


# 문제 14: 항목 이름은 한글·영문·숫자·_ 1~30자만 허용한다 (SQL의 "별칭"에 그대로 들어가기 때문)
@pytest.mark.parametrize("name", ['약정"기간', "약정 기간", "", "가" * 31, "a;b"])
def test_load_rejects_invalid_item_name(tmp_path: Path, name: str) -> None:
    with pytest.raises(MetadataError):
        load_site_profile(write_profile(tmp_path, {"기본상품": {name: {"column": "CHAR01"}}}))


# ───────────── 3단계: Intent.attributes ─────────────


# 문제 15: 코드 + 항목 목록으로 attribute 의도를 만든다
def test_attribute_intent_with_code() -> None:
    intent = attribute(attributes=["약정기간", "데이터제공량"])
    assert intent.kind == "attribute"
    assert intent.attributes == ["약정기간", "데이터제공량"]


# 문제 16: 코드 대신 이름 검색어로도 만들 수 있다
def test_attribute_intent_with_name_keyword() -> None:
    intent = attribute(code=None, name_keyword="5G")
    assert intent.name_keyword == "5G"


# 문제 17: attribute에는 target_type, (code 또는 name_keyword), attributes(1개 이상)가 모두 필요하다
@pytest.mark.parametrize(
    "missing",
    [
        {"target_type": None},
        {"code": None},
        {"attributes": None},
        {"attributes": []},
    ],
)
def test_attribute_required_fields(missing: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        attribute(**missing)


# 문제 18: 항목 이름 형식은 2단계와 같다 (한글·영문·숫자·_ 1~30자)
@pytest.mark.parametrize("name", ['약정"기간', "약정 기간", "", "가" * 31])
def test_attribute_name_format(name: str) -> None:
    with pytest.raises(ValueError):
        attribute(attributes=[name])


# 문제 19: 항목은 한 번에 10개까지
def test_attribute_at_most_ten() -> None:
    attribute(attributes=[f"항목{i}" for i in range(10)])
    with pytest.raises(ValueError):
        attribute(attributes=[f"항목{i}" for i in range(11)])


# 문제 20: 다른 종류(lookup 등)는 attributes가 없어도 된다 (기존 동작 유지)
def test_other_kinds_do_not_need_attributes() -> None:
    intent = Intent(kind="lookup", target_type="기본상품", code="BP001")
    assert intent.attributes is None


# ───────────── 4단계: SQL 빌더 T5 ─────────────


# 문제 21: 기본 테이블 별칭에 OBJ_DETAIL용 "detail": "D"가 추가된다
def test_default_table_alias_for_detail() -> None:
    assert DEFAULT_TABLE_ALIASES["detail"] == "D"


# 문제 22: T5 SQL 전체 모양과 params (코드로 조회)
def test_attribute_sql_with_code() -> None:
    result = make_builder().build(attribute())
    assert flat(result.sql) == (
        'SELECT M.BIZCODE, M.OBJ_NAME, D.CHAR01 "약정기간" '
        "FROM OBJ_MAIN M JOIN OBJ_DETAIL D ON D.SID = M.SID "
        "WHERE M.TYPEID = :type_id AND M.BIZCODE = :code AND D.KIND = :detail_kind "
        "AND M.END_DT = '99991231' AND D.END_DT = '99991231' "
        "ORDER BY M.BIZCODE"
    )
    assert result.params == {"type_id": 901, "code": "BP001", "detail_kind": 1}


# 문제 23: 항목 여러 개는 질문 순서대로 SELECT에 붙인다
def test_attribute_sql_keeps_order() -> None:
    result = make_builder().build(attribute(attributes=["데이터제공량", "약정기간"]))
    assert 'SELECT M.BIZCODE, M.OBJ_NAME, D.CHAR02 "데이터제공량", D.CHAR01 "약정기간" FROM' in (
        flat(result.sql)
    )


# 문제 24: 같은 항목이 두 번 오면 한 번만 쓴다 (첫 등장 순서 유지)
def test_attribute_sql_removes_duplicates() -> None:
    result = make_builder().build(attribute(attributes=["약정기간", "데이터제공량", "약정기간"]))
    assert flat(result.sql).count('"약정기간"') == 1
    assert 'D.CHAR01 "약정기간", D.CHAR02 "데이터제공량" FROM' in flat(result.sql)


# 문제 25: 이름 검색어면 T1처럼 LIKE + ESCAPE (검색어 이스케이프 포함)
def test_attribute_sql_with_name_keyword() -> None:
    result = make_builder().build(attribute(code=None, name_keyword="100%"))
    assert r"M.OBJ_NAME LIKE :name_keyword ESCAPE '\'" in flat(result.sql)
    assert "M.BIZCODE = :code" not in flat(result.sql)
    assert result.params == {"type_id": 901, "name_keyword": r"%100\%%", "detail_kind": 1}


# 문제 26: 유형 별칭("옵션")이어도 정식 유형의 TYPEID와 항목 사전을 쓴다
def test_attribute_sql_with_type_alias() -> None:
    result = make_builder().build(attribute(target_type="옵션", attributes=["옵션요금"]))
    assert result.params["type_id"] == 903
    assert 'D.CHAR01 "옵션요금"' in flat(result.sql)


# 문제 27: 사전에 없는 항목, 다중값 항목은 거절한다
@pytest.mark.parametrize("name", ["없는항목", "부가혜택"])
def test_attribute_sql_rejects_bad_item(name: str) -> None:
    with pytest.raises(UnsupportedQuestionError):
        make_builder().build(attribute(attributes=["약정기간", name]))


# 문제 28: 테이블 별칭과 최신 버전 조건은 프로필·공통 메타데이터에서 가져온다 (하드코딩 금지)
def test_attribute_sql_uses_profile_aliases_and_metadata() -> None:
    common = Metadata(
        dialect="postgresql",
        max_rows=100,
        tables={
            "OBJ_MAIN": TableProfile("OBJ_MAIN", "메인", "END_DT", "99991231"),
            "OBJ_DETAIL": TableProfile("OBJ_DETAIL", "속성", "DEL_YN", "N"),
        },
    )
    profile = small_profile(table_aliases={**DEFAULT_TABLE_ALIASES, "main": "OM", "detail": "OD"})
    result = SqlBuilder(common, profile).build(attribute())
    sql = flat(result.sql)
    assert 'SELECT OM.BIZCODE, OM.OBJ_NAME, OD.CHAR01 "약정기간"' in sql
    assert "FROM OBJ_MAIN OM JOIN OBJ_DETAIL OD ON OD.SID = OM.SID" in sql
    assert "OM.END_DT = '99991231' AND OD.DEL_YN = 'N'" in sql


# 문제 29: 만든 SQL은 검증기를 통과하고, 복사용 SQL에 값이 채워진다
def test_attribute_sql_passes_validator_and_renders() -> None:
    result = make_builder().build(attribute())
    SqlValidator(set(load_common_metadata(COMMON_PATH).tables)).validate(result.sql)
    rendered = flat(render_sql(result.sql, result.params))
    assert "M.TYPEID = 901 AND M.BIZCODE = 'BP001' AND D.KIND = 1" in rendered
    assert 'D.CHAR01 "약정기간"' in rendered


# ───────────── 5단계: 시스템 프롬프트 ─────────────


# 문제 30: 프롬프트에 [항목] 목록이 "유형: 항목, 항목" 모양으로 들어간다 (다중값 항목도 포함)
def test_prompt_lists_items() -> None:
    prompt = build_system_prompt(load_site_profile(SAMPLE_PATH))
    assert "[항목]" in prompt
    assert "기본상품: 약정기간, 데이터제공량, 부가혜택" in prompt
    assert "부가옵션: 옵션요금" in prompt


# 문제 31: 출력 스키마·필수값에 attribute와 attributes 설명이 들어간다
def test_prompt_describes_attribute() -> None:
    prompt = build_system_prompt(load_site_profile(SAMPLE_PATH))
    assert '"attribute"(속성 값)' in prompt
    assert "- attributes: attribute 전용. 조회할 항목 이름 목록 ([항목]에 있는 이름만)" in prompt
    assert "- attribute: target_type + (code 또는 name_keyword) + attributes" in prompt


# 문제 32: 항목이 있는 첫 유형과 그 첫 항목으로 attribute 예시를 만든다
def test_prompt_attribute_example() -> None:
    prompt = build_system_prompt(load_site_profile(SAMPLE_PATH))
    assert "질문: A001 기본상품의 약정기간 알려줘" in prompt
    expected = (
        '출력: {"kind": "attribute", "target_type": "기본상품", "code": "A001", '
        '"attributes": ["약정기간"]}'
    )
    assert expected in prompt


# 문제 33: 항목 사전이 없는 프로필이면 [항목] 줄과 attribute 예시를 넣지 않는다
def test_prompt_without_items() -> None:
    profile = SiteProfile(site="t", types={"펀드": 1}, aliases={"펀드": "펀드"})
    prompt = build_system_prompt(profile)
    # 스키마 설명 문장에도 "[항목]"이라는 글자가 있으므로, 그 글자만 있는 머리줄이 없는지 본다
    assert "[항목]" not in prompt.splitlines()
    assert '{"kind": "attribute"' not in prompt


# 문제 34: OpenAI가 attribute JSON을 돌려주면 attributes가 담긴 Intent가 된다
def test_openai_parses_attribute_intent() -> None:
    content = (
        '{"kind": "attribute", "target_type": "기본상품", "code": "BP001", '
        '"attributes": ["약정기간"]}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.Client(base_url="http://openai.test/v1", transport=httpx.MockTransport(handler))
    extractor = OpenAiIntentExtractor(client, "gpt-test", load_site_profile(SAMPLE_PATH))
    assert extractor.extract("BP001 약정기간") == attribute()


# ───────────── 6단계: 전체 흐름 (demo 프로필) ─────────────


def demo_service(intent: Intent) -> TextToSqlService:
    common = load_common_metadata(COMMON_PATH)
    return TextToSqlService(
        FakeIntentExtractor({"q": intent}),
        SqlBuilder(common, load_site_profile(DEMO_PATH)),
        SqlValidator(set(common.tables)),
    )


# 문제 35: 시연 질문 "INS001 보험상품의 보험기간과 납입기간 알려줘"
def test_demo_attribute_question() -> None:
    intent = attribute(target_type="보험", code="INS001", attributes=["보험기간", "납입기간"])
    result = demo_service(intent).generate("q", "req-t5")
    assert result.status == "ok"
    assert result.params == {"type_id": 1100, "code": "INS001", "detail_kind": 1}
    assert result.rendered_sql is not None
    assert 'D.CHAR01 "보험기간", D.CHAR02 "납입기간"' in flat(result.rendered_sql)


# 문제 36: 다중값 항목(판매채널)을 물으면 오류가 아니라 unsupported 응답이 된다
def test_demo_multi_value_question_is_unsupported() -> None:
    intent = attribute(target_type="보험상품", code="INS001", attributes=["판매채널"])
    result = demo_service(intent).generate("q", "req-t5")
    assert result.status == "unsupported"
    assert result.message is not None and "다중값" in result.message
