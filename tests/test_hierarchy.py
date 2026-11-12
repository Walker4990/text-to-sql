"""여러 단계 계층 + 단계별 속성 (kind="hierarchy").

예: "보험상품코드를 여러 개로 받고 주계약 → 특약 → 보장 구조의 속성값을 보여줘"
- L0: 질문에서 지목한 기준 오브젝트 (target_type + code 또는 code_item)
- L1~: path 순서대로 OBJ_INV를 따라 내려가며 각 단계를 유형(TYPEID)으로 거른다
- 속성이 있는 단계만 OBJ_DETAIL을 LEFT JOIN (값이 없어도 구조 행은 나온다)
"""

import pytest
from pydantic import ValidationError

from text_to_sql.domain.intent import Intent
from text_to_sql.errors import UnsupportedQuestionError
from text_to_sql.repositories.metadata_repository import load_common_metadata, load_site_profile
from text_to_sql.services.intent_extractor import FakeIntentExtractor, build_system_prompt
from text_to_sql.services.sql_builder import SqlBuilder, render_sql
from text_to_sql.services.sql_validator import SqlValidator
from text_to_sql.services.text_to_sql_service import TextToSqlService

COMMON_PATH = "src/text_to_sql/metadata/common.json"
DEMO_PATH = "src/text_to_sql/metadata/sites/demo.json"
SAMPLE_PATH = "src/text_to_sql/metadata/sites/sample.json"


def flat(sql: str) -> str:
    return " ".join(sql.split())


def demo_builder() -> SqlBuilder:
    return SqlBuilder(load_common_metadata(COMMON_PATH), load_site_profile(DEMO_PATH))


def hierarchy(**fields: object) -> Intent:
    values: dict[str, object] = {
        "kind": "hierarchy",
        "target_type": "보험상품",
        "code_item": "상품코드",
        "path": ["주계약", "특약", "보장"],
    }
    values.update(fields)
    return Intent.model_validate(values)


# ── Intent ──


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "hierarchy", "target_type": "보험상품", "path": ["주계약"]},  # 코드 없음
        {"kind": "hierarchy", "target_type": "보험상품", "code": "INS001"},  # path 없음
        {"kind": "hierarchy", "code": "INS001", "path": ["주계약"]},  # 기준 유형 없음
    ],
)
def test_hierarchy_required_fields(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Intent.model_validate(fields)


def test_path_is_limited_to_four_levels() -> None:
    with pytest.raises(ValidationError):
        hierarchy(path=["주계약", "특약", "보장", "보험료율", "카드상품"])


@pytest.mark.parametrize("bad", [{"주 계약": ["보험료"]}, {"주계약": ["보험료'"]}])
def test_path_attributes_format(bad: dict[str, list[str]]) -> None:
    with pytest.raises(ValidationError):
        hierarchy(path_attributes=bad)


# ── 빌더 ──


def test_three_levels_with_all_attributes() -> None:
    result = demo_builder().build(hierarchy())
    sql = flat(result.sql)
    assert sql.startswith(
        'SELECT L0.BIZCODE "보험상품", L0.OBJ_NAME "보험상품명", L1.BIZCODE "주계약"'
    )
    # 단계마다 OBJ_INV로 내려가고 유형으로 거른다
    assert "JOIN OBJ_INV I1 ON I1.SID = L0.SID" in sql
    assert "JOIN OBJ_MAIN L1 ON L1.SID = I1.SUBSID AND L1.TYPEID = :type_id_1" in sql
    assert "JOIN OBJ_MAIN L3 ON L3.SID = I3.SUBSID AND L3.TYPEID = :type_id_3" in sql
    # 속성은 LEFT JOIN, 항목을 지정하지 않으면 각 단계의 단일값 항목 전부
    assert "LEFT JOIN OBJ_DETAIL D2 ON D2.SID = L2.SID AND D2.KIND = :detail_kind" in sql
    assert 'D1.CHAR01 "보험가입금액"' in sql
    assert 'D3.TEXT01 "지급사유"' in sql
    assert "D0." not in sql  # 기준 오브젝트 속성은 지정했을 때만
    # 기준 코드는 룰 변수(기본 IN), 버전 조건은 모든 MAIN·INV에
    assert "L0.TYPEID = :type_id AND L0.BIZCODE IN (<$@distinct([상품코드].array)@$>)" in sql
    for alias in ("L0", "I1", "L1", "I2", "L2", "I3", "L3"):
        assert f"{alias}.END_DT = '99991231'" in sql
    assert "INV_TYPE" not in sql
    assert sql.endswith("ORDER BY L0.BIZCODE, L1.BIZCODE, L2.BIZCODE, L3.BIZCODE")
    assert result.params == {
        "type_id": 1100,
        "type_id_1": 1110,
        "type_id_2": 1120,
        "type_id_3": 1130,
        "detail_kind": 1,
    }


def test_chosen_attributes_only() -> None:
    intent = hierarchy(
        code="INS001",
        code_item=None,
        path=["주계약"],
        path_attributes={"보험": ["보험기간"], "주계약": ["보험료"]},
    )
    result = demo_builder().build(intent)
    sql = flat(result.sql)
    assert 'D0.CHAR01 "보험기간"' in sql  # 기준 유형(별칭 "보험")도 지정하면 나온다
    assert 'D1.CHAR02 "보험료"' in sql
    assert "보험가입금액" not in sql
    assert "L0.BIZCODE = :code" in sql
    assert result.params["code"] == "INS001"


def test_one_level_path_filters_child_type() -> None:
    """'보험상품과 연결된 주계약 목록' — 하위 중 주계약만 나온다.

    structure는 하위 유형으로 거르지 않지만, hierarchy는 단계마다 유형으로 거른다."""
    result = demo_builder().build(hierarchy(path=["주계약"], path_attributes={}))
    sql = flat(result.sql)
    assert "L1.TYPEID = :type_id_1" in sql
    assert "OBJ_DETAIL" not in sql
    assert "detail_kind" not in result.params


def test_multi_value_item_is_skipped_or_rejected() -> None:
    # 전부 출력일 때 다중값 항목(판매채널)은 빠지고, 직접 지정하면 거절된다
    sql = flat(
        demo_builder()
        .build(hierarchy(target_type="계좌상품", code_item="계좌상품코드", path=["거래수수료"]))
        .sql
    )
    assert "가입채널" not in sql
    with pytest.raises(UnsupportedQuestionError, match="다중값"):
        demo_builder().build(hierarchy(path=["주계약"], path_attributes={"보험상품": ["판매채널"]}))


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"path": ["대출상품"]}, "대출상품"),
        ({"path": ["주계약", "주계약"]}, "같은 유형"),
        ({"path": ["주계약"], "path_attributes": {"보장": ["보장금액"]}}, "경로에 없는 유형"),
        ({"path": ["주계약"], "path_attributes": {"주계약": ["적립률"]}}, "항목이 없습니다"),
    ],
)
def test_invalid_paths_are_rejected(fields: dict[str, object], message: str) -> None:
    with pytest.raises(UnsupportedQuestionError, match=message):
        demo_builder().build(hierarchy(**fields))


def test_hierarchy_sql_passes_validator_and_renders() -> None:
    common = load_common_metadata(COMMON_PATH)
    result = demo_builder().build(hierarchy())
    SqlValidator(set(common.tables)).validate(result.sql)
    rendered = flat(render_sql(result.sql, result.params))
    assert "L1.TYPEID = 1110" in rendered
    assert "D1.KIND = 1" in rendered


def test_demo_service_end_to_end() -> None:
    common = load_common_metadata(COMMON_PATH)
    service = TextToSqlService(
        FakeIntentExtractor({"q": hierarchy()}),
        SqlBuilder(common, load_site_profile(DEMO_PATH)),
        SqlValidator(set(common.tables)),
    )
    result = service.generate("q", "req-h")
    assert result.status == "ok"


# ── 프롬프트 ──


def test_prompt_describes_hierarchy() -> None:
    prompt = build_system_prompt(load_site_profile(DEMO_PATH))
    assert '"hierarchy"(여러 단계 구조 + 단계별 속성)' in prompt
    # 값 조건(설계 08)이 생겨 코드 없이 conditions만으로도 계층 조회를 할 수 있다
    assert "- hierarchy: target_type + path + (code 또는 code_item 또는 conditions)" in prompt
    assert "질문: A001 보험상품와 연결된 주계약 → 특약 구조 알려줘" in prompt
    assert '"path": ["주계약", "특약"]' in prompt


def test_prompt_hierarchy_example_with_two_types() -> None:
    prompt = build_system_prompt(load_site_profile(SAMPLE_PATH))
    assert '"path": ["부가옵션"]' in prompt
