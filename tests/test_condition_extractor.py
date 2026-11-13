"""추출기의 값 조건 처리 (설계 08, 6장, 8장 5단계).

스키마(conditions 칸), 프롬프트(숫자 표시·예시), 근거 확인(모델이 지어낸 조건 차단), 가드.
"""

import json
from typing import Any

import httpx
import pytest

from text_to_sql.repositories.metadata_repository import ItemDef, SiteProfile
from text_to_sql.services.intent_extractor import (
    OllamaIntentExtractor,
    build_intent_schema,
    build_system_prompt,
)

PROFILE = SiteProfile(
    site="t",
    types={"카드상품": 2100, "연회비": 2120},
    aliases={"카드상품": "카드상품", "연회비": "연회비", "카드": "카드상품"},
    items={
        "카드상품": {"카드등급": ItemDef("CHAR01")},
        "연회비": {
            "국내전용연회비": ItemDef("CHAR01", type="number"),
            "해외겸용연회비": ItemDef("CHAR02", type="number"),
            "비고": ItemDef("TEXT01"),
        },
    },
)


def branches(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {branch["properties"]["kind"]["const"]: branch for branch in schema["anyOf"]}


# ───────── 스키마 ─────────


# 문제 1: list·attribute·hierarchy 갈래에만 conditions 칸이 있다
def test_schema_conditions_only_in_allowed_branches() -> None:
    by_kind = branches(build_intent_schema(PROFILE))
    for kind in ("list", "attribute", "hierarchy"):
        assert "conditions" in by_kind[kind]["properties"], kind
    for kind in ("lookup", "structure", "unsupported"):
        assert "conditions" not in by_kind[kind]["properties"], kind
    # 필수는 아니다 (조건 없는 질문도 있다)
    assert "conditions" not in by_kind["list"]["required"]


# 문제 2: 조건 한 개의 모양 — 항목은 프로필 항목 enum, 연산자 enum, 값은 문자열, 모르는 칸 금지
def test_schema_condition_shape() -> None:
    spec = branches(build_intent_schema(PROFILE))["hierarchy"]["properties"]["conditions"]
    assert spec["type"] == "array"
    assert (spec["minItems"], spec["maxItems"]) == (1, 5)
    item = spec["items"]
    assert item["additionalProperties"] is False
    # 값은 value(질문에 적힌 값) 또는 value_item(입력 항목명칭) 중 하나라 필수는 item·op뿐
    assert set(item["required"]) == {"item", "op"}
    assert set(item["properties"]["item"]["enum"]) == {
        "카드등급",
        "국내전용연회비",
        "해외겸용연회비",
        "비고",
    }
    assert item["properties"]["op"]["enum"] == ["eq", "ne", "lt", "le", "gt", "ge", "contains"]
    assert item["properties"]["value"] == {"type": "string", "minLength": 1, "maxLength": 50}


# 문제 3: 항목이 없는 프로필이면 조건 칸을 만들지 않는다
def test_schema_without_items_has_no_conditions() -> None:
    profile = SiteProfile(site="t", types={"카드상품": 2100}, aliases={"카드상품": "카드상품"})
    for branch in branches(build_intent_schema(profile)).values():
        assert "conditions" not in branch["properties"]


# 문제 4: hierarchy에도 base_date 칸이 있다
def test_schema_hierarchy_has_base_date() -> None:
    props = branches(build_intent_schema(PROFILE))["hierarchy"]["properties"]
    assert props["base_date"]["pattern"] == "^[0-9]{8}$"


# ───────── 프롬프트 ─────────


# 문제 5: 숫자 항목에는 (숫자)가 붙는다
def test_prompt_marks_number_items() -> None:
    prompt = build_system_prompt(PROFILE)
    assert "연회비: 국내전용연회비(숫자), 해외겸용연회비(숫자), 비고" in prompt
    assert "카드상품: 카드등급" in prompt and "카드등급(숫자)" not in prompt


# 문제 6: 연산자 사용법과 프로필 항목으로 만든 조건 예시가 있다
def test_prompt_explains_conditions() -> None:
    prompt = build_system_prompt(PROFILE)
    for word in ("이하", "이상", "미만", "초과", "contains"):
        assert word in prompt
    assert '"conditions"' in prompt
    assert '"item": "국내전용연회비"' in prompt  # 프로필의 첫 숫자 항목
    assert '"op": "le"' in prompt


# ───────── 근거 확인·가드 (Ollama 응답을 흉내) ─────────


def extractor_for(reply: dict[str, Any]) -> OllamaIntentExtractor:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(reply, ensure_ascii=False)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return OllamaIntentExtractor(client, "qwen-test", PROFILE)


def cond(item: str = "국내전용연회비", op: str = "le", value: str = "30000") -> dict[str, str]:
    return {"item": item, "op": op, "value": value}


def fee_reply(*conditions: dict[str, str], **fields: Any) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "kind": "hierarchy",
        "target_type": "카드상품",
        "path": ["연회비"],
        **fields,
    }
    if conditions:
        reply["conditions"] = list(conditions)
    return reply


# 문제 7: 질문에 항목과 값이 있으면 조건이 그대로 통과한다
def test_grounded_condition_passes() -> None:
    intent = extractor_for(fee_reply(cond())).extract("국내전용연회비가 30000원 이하인 카드 목록")
    assert intent.kind == "hierarchy"
    assert intent.conditions is not None
    assert [(c.item, c.op, c.value) for c in intent.conditions] == [
        ("국내전용연회비", "le", "30000")
    ]


# 문제 8: 띄어 쓴 항목 이름, 쉼표가 들어간 값은 같은 것으로 본다 (값의 쉼표는 지워서 넘긴다)
def test_comma_and_spacing() -> None:
    question = "국내 전용 연회비가 30,000원 이하인 카드 목록"
    for value in ("30000", "30,000"):
        intent = extractor_for(fee_reply(cond(value=value))).extract(question)
        assert intent.conditions is not None
        assert intent.conditions[0].value == "30000"


# 문제 9: 질문에 없는 값(환산·지어낸 값)이면 거절한다
@pytest.mark.parametrize(
    "question",
    [
        "국내전용연회비가 3만원 이하인 카드 목록",  # 환산한 값
        "국내전용연회비가 50000원 이하인 카드 목록",  # 다른 값
    ],
)
def test_ungrounded_value_rejected(question: str) -> None:
    intent = extractor_for(fee_reply(cond())).extract(question)
    assert intent.kind == "unsupported"
    assert intent.reason is not None and "질문에 없습니다" in intent.reason


# 문제 10: 질문에 없는 항목 이름이면 거절한다
def test_ungrounded_item_rejected() -> None:
    intent = extractor_for(fee_reply(cond())).extract("연회비가 30000 이하인 카드 목록")
    assert intent.kind == "unsupported"
    assert intent.reason is not None and "질문에 없습니다" in intent.reason


# 문제 11: 조건 하나라도 근거가 없으면 전체를 거절한다 (일부만 남기면 조용히 틀린다)
def test_one_bad_condition_rejects_all() -> None:
    reply = fee_reply(cond(), cond("해외겸용연회비", "le", "99999"))
    question = "국내전용연회비가 30000 이하이고 해외겸용연회비도 작은 카드"
    intent = extractor_for(reply).extract(question)
    assert intent.kind == "unsupported"


# 문제 12: 비교 표현이 있는데 조건이 없으면 거절한다 (조건 없는 SQL이 나오지 않게)
@pytest.mark.parametrize(
    "word",
    ["이하", "이상", "초과", "미만", "넘는", "넘게", "보다 큰", "보다큰", "보다 작은", "보다 많은"],
)
def test_guard_rejects_comparison_without_conditions(word: str) -> None:
    reply = {"kind": "list", "target_type": "연회비"}
    intent = extractor_for(reply).extract(f"국내전용연회비가 30000원 {word} 연회비 목록")
    assert intent.kind == "unsupported"
    assert intent.reason is not None and "값 조건을 해석하지 못했습니다" in intent.reason


# 문제 13: 비교 표현이 없으면 영향이 없다
def test_guard_ignores_plain_questions() -> None:
    intent = extractor_for({"kind": "list", "target_type": "연회비"}).extract("연회비 목록 보여줘")
    assert intent.kind == "list"


# 문제 14: 모델이 스스로 unsupported로 답했으면 모델의 사유를 그대로 둔다
def test_guard_keeps_model_unsupported_reason() -> None:
    reply = {"kind": "unsupported", "reason": "상품 DB 질문이 아님"}
    intent = extractor_for(reply).extract("오늘 기온이 30도 이상이야?")
    assert intent.kind == "unsupported"
    assert intent.reason == "상품 DB 질문이 아님"


# 문제 15: 조건이 없는 [현행] 질문은 그대로 통과한다
def test_no_conditions_unchanged() -> None:
    intent = extractor_for(fee_reply(code="CRD001")).extract("CRD001 카드와 연결된 연회비")
    assert intent.kind == "hierarchy"
    assert intent.conditions is None


# 문제 15-1: list의 조건 항목이 다른 유형의 항목이면 계층 조회로 바꾼다 (모델이 list를 골라도)
def test_list_with_child_item_becomes_hierarchy() -> None:
    reply = {"kind": "list", "target_type": "카드상품", "conditions": [cond()]}
    intent = extractor_for(reply).extract("국내전용연회비가 30000원 이하인 카드상품 목록")
    assert intent.kind == "hierarchy"
    assert intent.target_type == "카드상품"
    assert intent.path == ["연회비"]
    assert intent.conditions is not None and intent.conditions[0].item == "국내전용연회비"


# 문제 15-2: 보정하지 않는 경우 — 항목이 자기 유형 것이거나, 어느 유형에도 없거나,
# 여러 유형에 있으면 그대로 둔다 (빌더가 거절한다. 어느 유형인지 모르는데 추측하지 않는다)
def test_list_not_rerouted_when_ambiguous() -> None:
    own = extractor_for({"kind": "list", "target_type": "연회비", "conditions": [cond()]})
    assert own.extract("국내전용연회비가 30000원 이하인 연회비 목록").kind == "list"
    # 두 유형 모두에 있는 항목 → 보정하지 않는다
    profile = SiteProfile(
        site="t",
        types={"카드상품": 2100, "연회비": 2120, "펀드": 3100},
        aliases={"카드상품": "카드상품", "연회비": "연회비", "펀드": "펀드"},
        items={
            "연회비": {"비고": ItemDef("TEXT01")},
            "펀드": {"비고": ItemDef("TEXT01")},
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        reply = {
            "kind": "list",
            "target_type": "카드상품",
            "conditions": [{"item": "비고", "op": "contains", "value": "신규"}],
        }
        content = json.dumps(reply, ensure_ascii=False)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    extractor = OllamaIntentExtractor(client, "qwen-test", profile)
    ambiguous = extractor.extract("비고에 신규가 들어간 카드상품")
    assert ambiguous.kind == "list"


# 문제 15-3: attribute는 보정하지 않는다 (무엇을 꺼낼지가 달라져 추측할 수 없다)
def test_attribute_not_rerouted() -> None:
    reply = {
        "kind": "attribute",
        "target_type": "카드상품",
        "attributes": ["카드등급"],
        "conditions": [cond()],
    }
    intent = extractor_for(reply).extract("국내전용연회비가 30000원 이하인 카드상품의 카드등급")
    assert intent.kind == "attribute"


# 문제 16: hierarchy의 base_date — 질문에 연도가 있으면 남고, 없으면 [현행]대로 지운다
def test_hierarchy_base_date() -> None:
    reply = fee_reply(code="CRD001", base_date="20261112")
    kept = extractor_for(reply).extract("2026년 11월 12일 기준 CRD001 카드와 연결된 연회비")
    assert kept.base_date == "20261112"
    dropped = extractor_for(reply).extract("11월 12일 기준 CRD001 카드와 연결된 연회비")
    assert dropped.base_date is None
