"""값 조건(Condition)과 Intent 변경 (설계 08, 3장, 8장 1단계)."""

import pytest
from pydantic import ValidationError

from text_to_sql.domain.intent import Condition, Intent


def cond(**fields: object) -> dict[str, object]:
    return {"item": "국내전용연회비", "op": "le", "value": "30000", **fields}


# 문제 1: 정상 조건 — list·attribute·hierarchy에서 쓸 수 있다
@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "list", "target_type": "연회비"},
        {"kind": "attribute", "target_type": "연회비", "code": "FEE1", "attributes": ["비고"]},
        {"kind": "hierarchy", "target_type": "카드상품", "path": ["연회비"]},
    ],
)
def test_conditions_allowed(fields: dict[str, object]) -> None:
    intent = Intent.model_validate({**fields, "conditions": [cond()]})
    assert intent.conditions == [Condition(item="국내전용연회비", op="le", value="30000")]


# 문제 2: lookup·structure·unsupported에는 조건을 쓸 수 없다
@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "lookup", "target_type": "연회비", "code": "FEE1"},
        {"kind": "structure", "target_type": "카드상품", "code": "C1", "direction": "children"},
        {"kind": "unsupported", "reason": "x"},
    ],
)
def test_conditions_rejected_for_other_kinds(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Intent.model_validate({**fields, "conditions": [cond()]})


# 문제 3: 연산자는 허용 목록만 (SQL 기호나 임의 문자열 금지)
@pytest.mark.parametrize("op", ["<=", "like", "between", "", "LE"])
def test_bad_operator(op: str) -> None:
    with pytest.raises(ValidationError):
        Condition.model_validate(cond(op=op))


@pytest.mark.parametrize("op", ["eq", "ne", "lt", "le", "gt", "ge", "contains"])
def test_all_operators(op: str) -> None:
    assert Condition.model_validate(cond(op=op)).op == op


# 문제 4: 항목 이름 형식 (공백·기호 금지), 값은 최대 50자·비어 있으면 안 됨
@pytest.mark.parametrize("item", ["국내 전용", "a;b", "", "가" * 31])
def test_bad_item_name(item: str) -> None:
    with pytest.raises(ValidationError):
        Condition.model_validate(cond(item=item))


@pytest.mark.parametrize("value", ["", "9" * 51])
def test_bad_value_length(value: str) -> None:
    with pytest.raises(ValidationError):
        Condition.model_validate(cond(value=value))


# 문제 5: 조건은 최대 5개, 빈 목록 금지
def test_condition_count() -> None:
    base = {"kind": "list", "target_type": "연회비"}
    assert Intent.model_validate({**base, "conditions": [cond()] * 5})
    for conditions in ([cond()] * 6, []):
        with pytest.raises(ValidationError):
            Intent.model_validate({**base, "conditions": conditions})


# 문제 6: hierarchy는 code·code_item·conditions 중 하나가 있으면 된다
def test_hierarchy_requires_one_of_code_or_conditions() -> None:
    base = {"kind": "hierarchy", "target_type": "카드상품", "path": ["연회비"]}
    assert Intent.model_validate({**base, "conditions": [cond()]})
    assert Intent.model_validate({**base, "code": "CRD001"})
    assert Intent.model_validate({**base, "code_item": "상품코드"})
    with pytest.raises(ValidationError):
        Intent.model_validate(base)  # 코드도 조건도 없는 전체 덤프는 거절


# 문제 6-1: attribute도 같다 — 코드·이름 없이 조건만으로 거르며 속성을 꺼낼 수 있다
def test_attribute_requires_one_of_code_or_conditions() -> None:
    base = {"kind": "attribute", "target_type": "ETF", "attributes": ["기초지수"]}
    assert Intent.model_validate({**base, "conditions": [cond(item="총보수", value="0.5")]})
    assert Intent.model_validate({**base, "code": "ETF01"})
    with pytest.raises(ValidationError):
        Intent.model_validate(base)  # 대상을 정하는 값이 하나도 없다


# 문제 7: 조건이 없으면 기존처럼 None
def test_conditions_default_none() -> None:
    assert Intent(kind="list", target_type="연회비").conditions is None
