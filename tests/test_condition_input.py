"""값 조건의 값을 입력으로 받기 (항목 탭의 항목명칭 → 룰 변수) + 항목 alias.

규칙 (2026-11-13 사용자 확정)
- 표준항목은 쿼리의 컬럼으로, 입력으로 받는 값은 항목 탭의 항목명칭으로 쓴다
- 룰 변수는 따옴표·CAST 없이: CAST(D.CHAR01 AS NUMERIC) <= <$@[국내전용연회비]@$>
- 여러 개로 받으면 IN (<$@distinct([이름].array)@$>), 포함(LIKE) 조건은 입력값을 지원하지 않는다
- 항목명칭은 표준항목 이름과 같아도 된다
- alias(camelCase)는 API 송수신용. SQL에는 항목명칭이 들어간다
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from text_to_sql.domain.intent import Condition, Intent
from text_to_sql.errors import MetadataError, UnsupportedQuestionError
from text_to_sql.repositories.metadata_repository import (
    ItemDef,
    SiteProfile,
    load_common_metadata,
    load_site_profile,
    parse_site_profile,
)
from text_to_sql.repositories.profile_source import CsvFolderSource
from text_to_sql.services.intent_extractor import (
    OllamaIntentExtractor,
    build_intent_schema,
    build_system_prompt,
)
from text_to_sql.services.sql_builder import SqlBuilder
from text_to_sql.sync.diff import diff_profiles
from text_to_sql.sync.sheet_rows import rows_to_profile
from text_to_sql.sync.snapshot import export_csv, profile_to_data

COMMON_PATH = "src/text_to_sql/metadata/common.json"
DEMO_PATH = "src/text_to_sql/metadata/sites/demo.json"

PROFILE = SiteProfile(
    site="t",
    types={"카드상품": 2100, "연회비": 2120, "펀드": 3100},
    aliases={"카드상품": "카드상품", "연회비": "연회비", "펀드": "펀드", "카드": "카드상품"},
    items={
        "카드상품": {"카드등급": ItemDef("CHAR01")},
        "연회비": {"국내전용연회비": ItemDef("CHAR01", type="number")},
        "펀드": {"운용사": ItemDef("CHAR02"), "위험등급": ItemDef("CHAR01")},
    },
    registered_items=["상품코드", "국내전용연회비", "운용사", "위험등급"],
    registered_aliases={"상품코드": "productCode", "국내전용연회비": "domesticAnnualFee"},
)


def builder() -> SqlBuilder:
    return SqlBuilder(load_common_metadata(COMMON_PATH), PROFILE)


def cond(**fields: Any) -> Condition:
    return Condition.model_validate(fields)


def list_intent(target: str, *conditions: Condition) -> Intent:
    return Intent(kind="list", target_type=target, conditions=list(conditions))


# ───────── 1단계: Condition 모델 ─────────


# 문제 1: 값(value)과 입력 항목(value_item) 중 정확히 하나만 있어야 한다
def test_condition_needs_exactly_one_value() -> None:
    assert cond(item="운용사", op="eq", value_item="운용사").value is None
    assert cond(item="운용사", op="eq", value="한화").value_item is None
    with pytest.raises(ValidationError):
        cond(item="운용사", op="eq")
    with pytest.raises(ValidationError):
        cond(item="운용사", op="eq", value="한화", value_item="운용사")


# 문제 2: value_item은 항목명칭 형식(한글·영문·숫자·_ 1~30자)이고, value_multi는 value_item에만 쓴다
def test_condition_value_item_format_and_multi() -> None:
    with pytest.raises(ValidationError):
        cond(item="운용사", op="eq", value_item="운용 사")
    with pytest.raises(ValidationError):
        cond(item="운용사", op="eq", value="한화", value_multi=True)
    assert cond(item="위험등급", op="eq", value_item="위험등급", value_multi=True).value_multi


# ───────── 2단계: SQL 조립 ─────────


# 문제 3: 숫자 항목 + 입력값 → CAST(컬럼) <= <$@[이름]@$> (바인드 변수 없음)
def test_number_condition_with_input() -> None:
    intent = list_intent(
        "연회비", cond(item="국내전용연회비", op="le", value_item="국내전용연회비")
    )
    result = builder().build(intent)
    assert "CAST(D.CHAR01 AS NUMERIC) <= <$@[국내전용연회비]@$>" in result.sql
    assert "cond_0" not in result.params


# 문제 4: 문자 항목 + 입력값 → = / <> <$@[이름]@$>
@pytest.mark.parametrize(("op", "symbol"), [("eq", "="), ("ne", "<>")])
def test_text_condition_with_input(op: str, symbol: str) -> None:
    result = builder().build(list_intent("펀드", cond(item="운용사", op=op, value_item="운용사")))
    assert f"D.CHAR02 {symbol} <$@[운용사]@$>" in result.sql


# 문제 5: 여러 개로 받으면 IN (<$@distinct([이름].array)@$>)
def test_multi_input_becomes_in() -> None:
    condition = cond(item="위험등급", op="eq", value_item="위험등급", value_multi=True)
    result = builder().build(list_intent("펀드", condition))
    assert "D.CHAR01 IN (<$@distinct([위험등급].array)@$>)" in result.sql


# 문제 6: 거절 — 등록되지 않은 입력 이름, 입력값 포함(LIKE), 여러 개인데 같음(eq)이 아님
@pytest.mark.parametrize(
    "condition",
    [
        {"item": "운용사", "op": "eq", "value_item": "운용사명"},
        {"item": "운용사", "op": "contains", "value_item": "운용사"},
        {"item": "국내전용연회비", "op": "le", "value_item": "국내전용연회비", "value_multi": True},
    ],
)
def test_input_condition_rejections(condition: dict[str, Any]) -> None:
    target = "연회비" if condition["item"] == "국내전용연회비" else "펀드"
    with pytest.raises(UnsupportedQuestionError):
        builder().build(list_intent(target, cond(**condition)))


# 문제 7: 계층 조회의 하위 단계 조건도 입력값으로 받을 수 있다
def test_hierarchy_input_condition() -> None:
    intent = Intent(
        kind="hierarchy",
        target_type="카드상품",
        path=["연회비"],
        conditions=[cond(item="국내전용연회비", op="le", value_item="국내전용연회비")],
    )
    sql = builder().build(intent).sql
    assert "CAST(D1.CHAR01 AS NUMERIC) <= <$@[국내전용연회비]@$>" in sql


# ───────── 3단계: 추출기 (스키마·프롬프트·근거 확인) ─────────


def branches(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {branch["properties"]["kind"]["const"]: branch for branch in schema["anyOf"]}


# 문제 8: 조건 칸에 value_item이 있고(형식만 검사), 필수는 item·op뿐이다. value_multi는 없다
def test_schema_condition_has_value_item() -> None:
    spec = branches(build_intent_schema(PROFILE))["list"]["properties"]["conditions"]["items"]
    assert set(spec["required"]) == {"item", "op"}
    assert "pattern" in spec["properties"]["value_item"]
    assert "value_multi" not in spec["properties"]


# 문제 9: 프롬프트가 "항목으로 받아서"면 value_item을 쓰라고 알려 준다
def test_prompt_explains_value_item() -> None:
    prompt = build_system_prompt(PROFILE)
    assert "value_item" in prompt
    assert "받아서" in prompt


def extractor_for(reply: dict[str, Any]) -> OllamaIntentExtractor:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(reply, ensure_ascii=False)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return OllamaIntentExtractor(client, "qwen-test", PROFILE)


def fund_reply(**condition: Any) -> dict[str, Any]:
    return {"kind": "list", "target_type": "펀드", "conditions": [{"item": "운용사", **condition}]}


# 문제 10: 입력 항목 이름이 질문에 있으면 통과, 단일이면 value_multi False
def test_grounded_input_condition() -> None:
    reply = fund_reply(op="eq", value_item="운용사")
    intent = extractor_for(reply).extract("운용사 항목으로 받아서 운용사가 같은 펀드 목록")
    assert intent.conditions is not None
    assert intent.conditions[0].value_item == "운용사"
    assert intent.conditions[0].value_multi is False


# 문제 11: 질문에 "여러"가 있으면 value_multi True (모델이 아니라 코드가 정한다)
def test_multi_word_sets_value_multi() -> None:
    reply = {
        "kind": "list",
        "target_type": "펀드",
        "conditions": [{"item": "위험등급", "op": "eq", "value_item": "위험등급"}],
    }
    intent = extractor_for(reply).extract("위험등급을 여러 개로 받아서 그중 하나인 펀드 목록")
    assert intent.conditions is not None
    assert intent.conditions[0].value_multi is True


# 문제 11-1: "여러"는 그 입력 항목명칭 바로 뒤에 있을 때만 본다 (다른 항목의 "여러"에 끌려가지 않음)
#           크기 비교(le 등)는 여러 값이 될 수 없다
def test_multi_word_must_follow_input_name() -> None:
    reply = {
        "kind": "list",
        "target_type": "연회비",
        "conditions": [{"item": "국내전용연회비", "op": "le", "value_item": "국내전용연회비"}],
    }
    question = "상품코드 여러 개로 받고, 국내전용연회비 항목으로 받아서 그 이하인 연회비 목록"
    intent = extractor_for(reply).extract(question)
    assert intent.conditions is not None
    assert intent.conditions[0].value_multi is False
    many = "국내전용연회비를 여러 개로 받아서 그 이하인 연회비 목록"
    intent = extractor_for(reply).extract(many)
    assert intent.conditions is not None
    assert intent.conditions[0].value_multi is False  # le는 IN이 될 수 없다


# 문제 11-2: 여러 개로 받는데 모델이 포함(contains)을 고르면 같음(eq)으로 고친다 (그중 하나 = IN)
def test_multi_contains_becomes_eq() -> None:
    reply = {
        "kind": "list",
        "target_type": "펀드",
        "conditions": [{"item": "위험등급", "op": "contains", "value_item": "위험등급"}],
    }
    intent = extractor_for(reply).extract("위험등급을 여러 개로 받아서 그중 하나인 펀드 목록")
    assert intent.conditions is not None
    assert (intent.conditions[0].op, intent.conditions[0].value_multi) == ("eq", True)


# 문제 12: 입력 항목 이름이 질문에 없으면 거절 (모델이 지어낸 입력)
def test_ungrounded_input_rejected() -> None:
    reply = fund_reply(op="eq", value_item="위험등급")
    intent = extractor_for(reply).extract("운용사가 같은 펀드 목록")
    assert intent.kind == "unsupported"


# ───────── 4단계: 항목 alias (프로필·시트·스냅샷·차이) ─────────


def profile_data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "site": "t",
        "types": {"펀드": 3100},
        "aliases": {"펀드": "펀드"},
        "registered_items": ["상품코드", "운용사"],
        "registered_aliases": {"상품코드": "productCode"},
    }
    data.update(changes)
    return data


# 문제 13: registered_aliases를 읽는다. 없으면 빈 사전
def test_parse_registered_aliases() -> None:
    assert parse_site_profile(profile_data(), "t").registered_aliases == {"상품코드": "productCode"}
    data = profile_data()
    del data["registered_aliases"]
    assert parse_site_profile(data, "t").registered_aliases == {}


# 문제 14: alias는 camelCase(소문자로 시작, 영문·숫자), 등록된 항목명칭만, 중복 금지
@pytest.mark.parametrize(
    "aliases",
    [
        {"상품코드": "ProductCode"},
        {"상품코드": "product_code"},
        {"없는항목": "noItem"},
        {"상품코드": "code", "운용사": "code"},
        ["productCode"],
    ],
)
def test_parse_rejects_bad_aliases(aliases: object) -> None:
    with pytest.raises(MetadataError):
        parse_site_profile(profile_data(registered_aliases=aliases), "t")


SHEET_BASE: dict[str, list[list[str]]] = {
    "설정": [["키", "값"], ["site", "t"]],
    "유형": [["유형명", "TYPEID"], ["펀드", "3100"]],
}


# 문제 15: 시트 항목 탭의 alias 열을 읽는다 (빈 칸은 alias 없음). 형식·중복 오류는 MV-06
def test_sheet_alias_column() -> None:
    tabs = {
        **SHEET_BASE,
        "항목": [
            ["항목명칭", "alias", "사용"],
            ["상품코드", "productCode", "Y"],
            ["운용사", "", "Y"],
        ],
    }
    result = rows_to_profile(tabs, "t")
    assert result.profile is not None
    assert result.profile.registered_aliases == {"상품코드": "productCode"}
    bad = {**SHEET_BASE, "항목": [["항목명칭", "alias"], ["상품코드", "Product"]]}
    assert any(e.code == "MV-06" for e in rows_to_profile(bad, "t").errors)
    dup = {**SHEET_BASE, "항목": [["항목명칭", "alias"], ["상품코드", "code"], ["운용사", "code"]]}
    assert any(e.code == "MV-06" for e in rows_to_profile(dup, "t").errors)


# 문제 16: 스냅샷·CSV 내보내기에 alias가 들어가고, CSV로 왕복해도 같다
def test_snapshot_and_csv_keep_aliases(tmp_path: Path) -> None:
    assert profile_to_data(PROFILE)["registered_aliases"] == PROFILE.registered_aliases
    export_csv(PROFILE, tmp_path)
    header = (tmp_path / "항목.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.split(",")[:2] == ["항목명칭", "alias"]
    result = rows_to_profile(CsvFolderSource(tmp_path).read_tabs(), "t")
    assert result.profile is not None
    assert result.profile.registered_aliases == PROFILE.registered_aliases


# 문제 17: alias 변경은 차이로 보고하되 위험이 아니다 (SQL이 바뀌지 않음)
def test_alias_change_is_safe() -> None:
    new = replace(PROFILE, registered_aliases={"상품코드": "prodCode"})
    changes = [c for c in diff_profiles(PROFILE, new) if "alias" in c.target]
    assert len(changes) >= 1
    assert all(not c.risky for c in changes)


# ───────── 5단계: demo 프로필 ─────────

DEMO_INPUTS = {
    "가입최저나이": "minJoinAge",
    "납입주기": "paymentCycle",
    "특약보험료": "riderPremium",
    "보장금액": "coverageAmount",
    "카드등급": "cardGrade",
    "국내전용연회비": "domesticAnnualFee",
    "할인율": "discountRate",
    "운용사": "assetManager",
    "위험등급": "riskGrade",
    "총보수": "totalExpenseRatio",
    "최소가입금액": "minJoinAmount",
    "수수료율": "feeRate",
}


# 문제 18: demo 항목 탭에 입력값 12개가 추가되고, 등록 항목 전부에 alias가 있다
def test_demo_inputs_and_aliases() -> None:
    profile = load_site_profile(DEMO_PATH)
    assert profile.registered_items[:2] == ["상품코드", "수수료코드"]  # 프롬프트 예시는 첫 항목
    for name, alias in DEMO_INPUTS.items():
        assert name in profile.registered_items
        assert profile.registered_aliases[name] == alias
    assert set(profile.registered_aliases) == set(profile.registered_items)
    assert profile.registered_aliases["상품코드"] == "productCode"
