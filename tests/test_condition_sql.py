"""값 조건·기준일자의 SQL 생성 (설계 08, 5장, 8장 3·4단계)."""

from dataclasses import replace

import pytest

from text_to_sql.domain.intent import Condition, Intent
from text_to_sql.errors import UnsupportedQuestionError
from text_to_sql.repositories.metadata_repository import (
    ItemDef,
    Metadata,
    SiteProfile,
    load_common_metadata,
)
from text_to_sql.services.intent_extractor import FakeIntentExtractor
from text_to_sql.services.sql_builder import SqlBuilder, render_sql
from text_to_sql.services.sql_validator import SqlValidator
from text_to_sql.services.text_to_sql_service import TextToSqlService

COMMON_PATH = "src/text_to_sql/metadata/common.json"


def num(**kwargs: str) -> ItemDef:
    return ItemDef(kwargs["column"], type="number")


PROFILE = SiteProfile(
    site="t",
    types={"카드상품": 2100, "연회비": 2120, "펀드": 3100, "ETF": 3110},
    aliases={
        "카드상품": "카드상품",
        "연회비": "연회비",
        "펀드": "펀드",
        "ETF": "ETF",
        "카드": "카드상품",
    },
    items={
        "카드상품": {
            "카드등급": ItemDef("CHAR01"),
            "전월실적기준": num(column="CHAR05"),
            "제휴브랜드": ItemDef("CHAR04", multi=True),
            "비고": ItemDef("TEXT01"),
        },
        "연회비": {
            "국내전용연회비": num(column="CHAR01"),
            "해외겸용연회비": num(column="CHAR02"),
            "비고": ItemDef("TEXT01"),
        },
        "펀드": {"운용사": ItemDef("CHAR02"), "위험등급": ItemDef("CHAR01")},
        "ETF": {"총보수": num(column="CHAR02"), "기초지수": ItemDef("CHAR01")},
    },
)


def common(dialect: str = "postgresql") -> Metadata:
    return replace(load_common_metadata(COMMON_PATH), dialect=dialect)


def builder(dialect: str = "postgresql") -> SqlBuilder:
    return SqlBuilder(common(dialect), PROFILE)


# 숫자 항목은 정규식 검사 없이 컬럼을 바로 숫자로 바꿔 비교한다 (현장 SQL은 정규식을 쓰지 않음)
def pg_num(column: str) -> str:
    return f"CAST({column} AS NUMERIC)"


def oracle_num(column: str) -> str:
    return f"TO_NUMBER({column})"


# 문제 0: 생성된 값 조건 SQL에 정규식(~, REGEXP_LIKE)이 들어가지 않는다
@pytest.mark.parametrize("dialect", ["postgresql", "oracle"])
def test_number_condition_has_no_regex(dialect: str) -> None:
    intent = Intent(
        kind="list", target_type="연회비", conditions=[c("국내전용연회비", "le", "30000")]
    )
    sql = builder(dialect).build(intent).sql
    assert "~" not in sql
    assert "REGEXP_LIKE" not in sql
    assert "CASE WHEN" not in sql


def c(item: str, op: str, value: str) -> Condition:
    return Condition.model_validate({"item": item, "op": op, "value": value})


def list_intent(target: str, *conditions: Condition) -> Intent:
    return Intent(kind="list", target_type=target, conditions=list(conditions))


# ───────── list + 조건 ─────────


# 문제 1: 숫자 조건 — OBJ_DETAIL 조인, 숫자 변환, 정수는 int로 바인딩, 조건 항목이 열에 나온다
def test_list_number_condition() -> None:
    result = builder().build(list_intent("연회비", c("국내전용연회비", "le", "30000")))
    assert result.sql.startswith(
        'SELECT M.SID, M.BIZCODE, M.OBJ_NAME, D.CHAR01 "국내전용연회비" '
        "FROM OBJ_MAIN M JOIN OBJ_DETAIL D ON D.SID = M.SID WHERE "
    )
    assert f"{pg_num('D.CHAR01')} <= :cond_0" in result.sql
    assert "D.KIND = :detail_kind" in result.sql
    assert "M.TYPEID = :type_id" in result.sql
    assert "M.END_DT = '99991231'" in result.sql
    assert "D.END_DT = '99991231'" in result.sql
    assert result.sql.endswith("ORDER BY M.BIZCODE")
    assert result.params == {"type_id": 2120, "detail_kind": 1, "cond_0": 30000}
    assert isinstance(result.params["cond_0"], int)


# 문제 2: 연산자마다 SQL 기호가 다르다
@pytest.mark.parametrize(
    ("op", "symbol"),
    [("eq", "="), ("ne", "<>"), ("lt", "<"), ("le", "<="), ("gt", ">"), ("ge", ">=")],
)
def test_number_operators(op: str, symbol: str) -> None:
    result = builder().build(list_intent("연회비", c("국내전용연회비", op, "100")))
    assert f"{pg_num('D.CHAR01')} {symbol} :cond_0" in result.sql


# 문제 3: 소수는 문자열로 바인딩하고 SQL에서 변환한다, 음수 정수는 int
def test_decimal_and_negative() -> None:
    result = builder().build(list_intent("ETF", c("총보수", "le", "0.5")))
    assert f"{pg_num('D.CHAR02')} <= CAST(:cond_0 AS NUMERIC)" in result.sql
    assert result.params["cond_0"] == "0.5"
    negative = builder().build(list_intent("ETF", c("총보수", "ge", "-5")))
    assert negative.params["cond_0"] == -5


# 문제 4: 숫자 값 형식이 아니면 거절 (쉼표, 지수, 문자, 너무 긴 수, 소수 자릿수 초과)
@pytest.mark.parametrize("value", ["abc", "30,000", "1e5", "1.", ".5", "+3", "9" * 16, "1.1234567"])
def test_bad_number_value(value: str) -> None:
    with pytest.raises(UnsupportedQuestionError):
        builder().build(list_intent("연회비", c("국내전용연회비", "le", value)))


# 문제 5: 오라클 방언
def test_oracle_dialect() -> None:
    result = builder("oracle").build(
        list_intent("ETF", c("총보수", "le", "0.5"), c("기초지수", "eq", "KOSPI"))
    )
    assert f"{oracle_num('D.CHAR02')} <= TO_NUMBER(:cond_0)" in result.sql
    integer = builder("oracle").build(list_intent("연회비", c("국내전용연회비", "le", "30000")))
    assert f"{oracle_num('D.CHAR01')} <= :cond_0" in integer.sql


# 문제 6: 모르는 방언은 숫자 조건에서만 거절한다 (문자 조건은 방언과 무관)
def test_unknown_dialect() -> None:
    with pytest.raises(UnsupportedQuestionError):
        builder("mysql").build(list_intent("연회비", c("국내전용연회비", "le", "1")))
    assert builder("mysql").build(list_intent("펀드", c("운용사", "eq", "한화")))


# 문제 7: 문자 조건 — 같음·다름·포함, 포함은 % _ \ 를 이스케이프
def test_text_conditions() -> None:
    eq = builder().build(list_intent("펀드", c("운용사", "eq", "한화자산운용")))
    assert "D.CHAR02 = :cond_0" in eq.sql
    assert eq.params["cond_0"] == "한화자산운용"
    ne = builder().build(list_intent("펀드", c("운용사", "ne", "한화자산운용")))
    assert "D.CHAR02 <> :cond_0" in ne.sql
    contains = builder().build(list_intent("펀드", c("운용사", "contains", "한화")))
    assert "D.CHAR02 LIKE :cond_0 ESCAPE '\\'" in contains.sql
    assert contains.params["cond_0"] == "%한화%"
    escaped = builder().build(list_intent("펀드", c("운용사", "contains", "5%_\\")))
    assert escaped.params["cond_0"] == "%5\\%\\_\\\\%"


# 문제 8: 형식에 맞지 않는 연산자는 거절 (text에 대소 비교, number에 포함)
def test_operator_type_mismatch() -> None:
    with pytest.raises(UnsupportedQuestionError):
        builder().build(list_intent("펀드", c("운용사", "le", "100")))
    with pytest.raises(UnsupportedQuestionError):
        builder().build(list_intent("연회비", c("국내전용연회비", "contains", "3")))


# 문제 9: 없는 항목, 다른 유형의 항목, 다중값 항목은 거절
def test_unknown_or_multi_item() -> None:
    for item in ("없는항목", "위험등급"):  # 위험등급은 펀드의 항목
        with pytest.raises(UnsupportedQuestionError):
            builder().build(list_intent("연회비", c(item, "eq", "1")))
    with pytest.raises(UnsupportedQuestionError, match="다중값"):
        builder().build(list_intent("카드상품", c("제휴브랜드", "eq", "A")))


# 문제 10: 조건 여러 개는 조인 하나를 공유하고 cond_0, cond_1로 바인딩, 같은 항목은 열을 한 번만
def test_multiple_conditions_share_join() -> None:
    result = builder().build(
        list_intent(
            "연회비",
            c("국내전용연회비", "ge", "10000"),
            c("국내전용연회비", "le", "30000"),
            c("해외겸용연회비", "le", "50000"),
        )
    )
    assert result.sql.count("JOIN OBJ_DETAIL") == 1
    assert result.sql.count('"국내전용연회비"') == 1
    assert 'D.CHAR02 "해외겸용연회비"' in result.sql
    assert result.params == {
        "type_id": 2120,
        "detail_kind": 1,
        "cond_0": 10000,
        "cond_1": 30000,
        "cond_2": 50000,
    }


# 문제 11: 별칭으로 말한 유형도 쓴다
def test_alias_target() -> None:
    result = builder().build(list_intent("카드", c("전월실적기준", "ge", "300000")))
    assert result.params["type_id"] == 2100
    assert f"{pg_num('D.CHAR05')} >= :cond_0" in result.sql


# ───────── attribute + 조건 ─────────


# 문제 12: 속성 조회에 조건을 붙이면 조건 항목도 열에 나온다 (이미 있으면 한 번만)
def test_attribute_with_conditions() -> None:
    intent = Intent(
        kind="attribute",
        target_type="ETF",
        code="ETF01",
        attributes=["기초지수"],
        conditions=[c("총보수", "le", "0.5")],
    )
    result = builder().build(intent)
    assert 'D.CHAR01 "기초지수", D.CHAR02 "총보수"' in result.sql
    assert f"{pg_num('D.CHAR02')} <= CAST(:cond_0 AS NUMERIC)" in result.sql
    assert "M.BIZCODE = :code" in result.sql
    assert result.params == {"type_id": 3110, "code": "ETF01", "detail_kind": 1, "cond_0": "0.5"}
    same = intent.model_copy(update={"attributes": ["총보수"]})
    assert builder().build(same).sql.count('"총보수"') == 1


# 문제 12-1: 코드 없이 조건만으로 거르며 속성을 꺼낸다 (code 조건이 없어야 한다)
def test_attribute_conditions_without_code() -> None:
    intent = Intent(
        kind="attribute",
        target_type="ETF",
        attributes=["기초지수"],
        conditions=[c("총보수", "le", "0.5")],
    )
    result = builder().build(intent)
    assert "BIZCODE =" not in result.sql and "BIZCODE IN" not in result.sql
    assert "code" not in result.params
    assert f"{pg_num('D.CHAR02')} <= CAST(:cond_0 AS NUMERIC)" in result.sql


# ───────── hierarchy + 조건 ─────────


def hierarchy(*conditions: Condition, **fields: object) -> Intent:
    values: dict[str, object] = {
        "kind": "hierarchy",
        "target_type": "카드상품",
        "path": ["연회비"],
        "conditions": list(conditions) or None,
        **fields,
    }
    return Intent.model_validate(values)


# 문제 13: 코드 없이 조건만으로 계층 조회 — 조건은 해당 단계 D1에 걸린다
def test_hierarchy_condition_without_code() -> None:
    result = builder().build(hierarchy(c("국내전용연회비", "le", "30000")))
    assert f"{pg_num('D1.CHAR01')} <= :cond_0" in result.sql
    assert "L0.TYPEID = :type_id" in result.sql
    assert "L0.BIZCODE" in result.sql  # 열로는 나오지만
    assert "L0.BIZCODE =" not in result.sql and "L0.BIZCODE IN" not in result.sql  # 조건은 없다
    assert result.params == {
        "type_id": 2100,
        "type_id_1": 2120,
        "detail_kind": 1,
        "cond_0": 30000,
    }
    assert 'D1.CHAR01 "국내전용연회비"' in result.sql


# 문제 14: 코드와 조건을 함께 쓰면 둘 다 걸린다
def test_hierarchy_code_and_condition() -> None:
    result = builder().build(hierarchy(c("국내전용연회비", "le", "30000"), code="CRD001"))
    assert "L0.BIZCODE = :code" in result.sql
    assert f"{pg_num('D1.CHAR01')} <= :cond_0" in result.sql
    assert result.params["code"] == "CRD001"


# 문제 15: 항목을 골라 출력해도 조건 항목은 열에 덧붙는다
def test_hierarchy_condition_item_added_to_columns() -> None:
    result = builder().build(
        hierarchy(c("국내전용연회비", "le", "30000"), path_attributes={"연회비": ["비고"]})
    )
    assert 'D1.TEXT01 "비고", D1.CHAR01 "국내전용연회비"' in result.sql
    assert "해외겸용연회비" not in result.sql


# 문제 16: 기준 유형(0단계)의 항목에도 조건을 걸 수 있다 (D0 조인이 생긴다)
def test_hierarchy_condition_on_level_zero() -> None:
    result = builder().build(hierarchy(c("전월실적기준", "ge", "300000")))
    assert "LEFT JOIN OBJ_DETAIL D0 ON D0.SID = L0.SID" in result.sql
    assert f"{pg_num('D0.CHAR05')} >= :cond_0" in result.sql
    assert 'D0.CHAR05 "전월실적기준"' in result.sql


# 문제 17: 서로 다른 유형의 조건을 함께 건다
def test_hierarchy_conditions_on_two_levels() -> None:
    result = builder().build(
        hierarchy(c("전월실적기준", "ge", "300000"), c("국내전용연회비", "le", "30000"))
    )
    assert f"{pg_num('D0.CHAR05')} >= :cond_0" in result.sql
    assert f"{pg_num('D1.CHAR01')} <= :cond_1" in result.sql


# 문제 18: 항목이 경로에 없거나, 여러 유형에 있으면 거절 (어느 단계인지 정할 수 없다)
def test_hierarchy_item_resolution_errors() -> None:
    with pytest.raises(UnsupportedQuestionError, match="경로"):
        builder().build(hierarchy(c("위험등급", "eq", "1")))  # 펀드의 항목
    with pytest.raises(UnsupportedQuestionError, match="여러 유형"):
        builder().build(hierarchy(c("비고", "contains", "신규")))  # 카드상품·연회비 둘 다 있음


# 문제 19: 조건 없는 계층 조회는 [현행]과 같다 (D0 조인도 없다)
def test_hierarchy_without_conditions_unchanged() -> None:
    result = builder().build(hierarchy(code="CRD001"))
    assert "D0" not in result.sql
    assert "cond_" not in result.sql


# ───────── 기준일자 확장 ─────────


# 문제 20: 구조 조회 — 상위·하위 OBJ_MAIN 모두
def test_structure_base_date() -> None:
    intent = Intent(
        kind="structure",
        target_type="카드상품",
        code="CRD001",
        direction="children",
        base_date="20261112",
    )
    result = builder().build(intent)
    assert ":base_dt BETWEEN P.VF_DT AND P.VT_DT" in result.sql
    assert ":base_dt BETWEEN C.VF_DT AND C.VT_DT" in result.sql
    assert result.params["base_dt"] == "20261112"
    assert "base_dt" not in builder().build(intent.model_copy(update={"base_date": None})).sql


# 문제 21: 속성 조회
def test_attribute_base_date() -> None:
    intent = Intent(
        kind="attribute",
        target_type="ETF",
        code="ETF01",
        attributes=["기초지수"],
        base_date="20261112",
    )
    result = builder().build(intent)
    assert ":base_dt BETWEEN M.VF_DT AND M.VT_DT" in result.sql
    assert result.params["base_dt"] == "20261112"


# 문제 22: 계층 조회 — 모든 단계(L0, L1)
def test_hierarchy_base_date() -> None:
    result = builder().build(hierarchy(code="CRD001", base_date="20261112"))
    assert ":base_dt BETWEEN L0.VF_DT AND L0.VT_DT" in result.sql
    assert ":base_dt BETWEEN L1.VF_DT AND L1.VT_DT" in result.sql
    assert result.params["base_dt"] == "20261112"


# 문제 23: 단건·목록은 [현행]대로 하나만, 목록+조건에도 붙는다
def test_list_base_date_with_condition() -> None:
    intent = list_intent("연회비", c("국내전용연회비", "le", "30000")).model_copy(
        update={"base_date": "20261112"}
    )
    result = builder().build(intent)
    assert result.sql.count(":base_dt BETWEEN M.VF_DT AND M.VT_DT") == 1
    assert result.params["base_dt"] == "20261112"


# ───────── 검증기·렌더링·서비스 ─────────


ALLOWED = {"OBJ_MAIN", "OBJ_INV", "OBJ_DETAIL"}


# 문제 24: 조건 SQL은 검증기를 통과하고, 값이 채워진 SQL로 렌더링된다 (방언 둘 다)
@pytest.mark.parametrize("dialect", ["postgresql", "oracle"])
def test_condition_sql_passes_validator_and_renders(dialect: str) -> None:
    intents = [
        list_intent("연회비", c("국내전용연회비", "le", "30000")),
        list_intent("ETF", c("총보수", "le", "0.5")),
        list_intent("펀드", c("운용사", "contains", "한화")),
        hierarchy(c("국내전용연회비", "le", "30000"), base_date="20261112"),
    ]
    for intent in intents:
        result = builder(dialect).build(intent)
        SqlValidator(ALLOWED).validate(result.sql)
        rendered = render_sql(result.sql, result.params)
        assert ":cond_" not in rendered
    first = builder().build(intents[0])
    assert "<= 30000" in render_sql(first.sql, first.params)
    decimal = builder().build(intents[1])
    assert "CAST('0.5' AS NUMERIC)" in render_sql(decimal.sql, decimal.params)
    contains = builder().build(intents[2])
    assert "LIKE '%한화%'" in render_sql(contains.sql, contains.params)


# 문제 25: 서비스 전체 흐름 (가짜 추출기)
def test_service_end_to_end() -> None:
    intent = hierarchy(c("국내전용연회비", "le", "30000"))
    service = TextToSqlService(
        FakeIntentExtractor({"q": intent}), builder(), SqlValidator(ALLOWED)
    )
    result = service.generate("q", request_id="r1")
    assert result.status == "ok"
    assert result.rendered_sql is not None and "<= 30000" in result.rendered_sql
