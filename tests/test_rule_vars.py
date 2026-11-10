"""BIZCODE 룰 변수 문제지 (문제 1~28). 명세: docs/05_구현명세서.md 9장.

PF 룰은 솔루션 항목관리에 등록된 **항목명칭**(상품코드, 질병코드 …)을 변수로 쓴다.
    = 조건:  C.BIZCODE = <$@[상품코드]@$>
    IN 조건: C.BIZCODE IN (<$@distinct([상품코드].array)@$>)
어느 항목명칭을 쓸지는 질문하는 사람만 안다. 그래서
1) 사람이 질문에 말하고 ("상품코드 항목으로", "상품코드 여러 개로")
2) LLM이 Intent.code_item / code_item_multi로 뽑고
3) 코드가 "질문에 실제로 있는 이름인가", "등록된 항목명칭인가"를 검사한 뒤
4) 빌더가 기준 오브젝트의 BIZCODE 조건 자리에 넣는다.
질문에 항목명칭이 없으면 지금처럼 BIZCODE = :code (값)이다.

단계별 구현할 곳 (명세 9장의 단계 순서와 같다)
- 1단계 (문제 1~3):   repositories/metadata_repository.py  registered_items, find_registered_item
- 2단계 (문제 4~8):   repositories/metadata_repository.py  load_site_profile가 등록 항목을 읽고 검사
- 3단계 (문제 9~13):  domain/intent.py                      code_item, code_item_multi
- 4단계 (문제 14~21): services/sql_builder.py               _code_condition, 3개 템플릿에 적용
- 5단계 (문제 22~26): services/intent_extractor.py          프롬프트 + 질문에 없는 항목명칭 거절
- 6단계 (문제 27~28): 전체 흐름 (demo 프로필 + 서비스)
"""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from text_to_sql.domain.intent import Intent
from text_to_sql.errors import IntentParseError, MetadataError, UnsupportedQuestionError
from text_to_sql.repositories.metadata_repository import (
    ItemDef,
    SiteProfile,
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


def item_profile() -> SiteProfile:
    """항목명칭 2개(상품코드, 옵션코드)를 등록한 프로필."""
    return SiteProfile(
        site="t",
        types={"기본상품": 901, "부가옵션": 903},
        aliases={"기본상품": "기본상품", "부가옵션": "부가옵션"},
        items={"기본상품": {"약정기간": ItemDef(column="CHAR01")}},
        registered_items=["상품코드", "옵션코드"],
    )


def make_builder() -> SqlBuilder:
    return SqlBuilder(load_common_metadata(COMMON_PATH), item_profile())


def write_profile(tmp_path: Path, registered_items: object) -> str:
    """registered_items 부분만 바꿔 가며 프로필 파일을 만든다."""
    data = {
        "site": "x",
        "types": {"기본상품": 901},
        "aliases": {"기본상품": "기본상품"},
        "registered_items": registered_items,
    }
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def fake_openai(content: str, profile: SiteProfile) -> OpenAiIntentExtractor:
    """content(JSON 문자열)를 그대로 돌려주는 가짜 OpenAI 서버를 단 추출기."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.Client(base_url="http://openai.test/v1", transport=httpx.MockTransport(handler))
    return OpenAiIntentExtractor(client, "gpt-test", profile)


# ───────────── 1단계: 등록 항목 목록 (registered_items, find_registered_item) ─────────────


# 문제 1: 등록된 항목명칭이면 그 이름을 그대로 돌려준다
def test_find_registered_item_returns_name() -> None:
    assert item_profile().find_registered_item("상품코드") == "상품코드"


# 문제 2: 등록되지 않은 이름은 UnsupportedQuestionError (사유에 "등록되지 않은 항목")
def test_find_registered_item_rejects_unknown() -> None:
    with pytest.raises(UnsupportedQuestionError, match="등록되지 않은 항목"):
        item_profile().find_registered_item("질병코드")


# 문제 3: registered_items를 넘기지 않으면 빈 리스트. 그러면 어떤 이름도 등록되지 않은 것이다
def test_registered_items_default_empty() -> None:
    profile = SiteProfile(site="t", types={"기본상품": 901}, aliases={"기본상품": "기본상품"})
    assert profile.registered_items == []
    with pytest.raises(UnsupportedQuestionError):
        profile.find_registered_item("상품코드")


# ───────────── 2단계: 프로필 파일에서 registered_items 읽기 ─────────────


# 문제 4: registered_items가 없는 프로필(sample.json)도 그대로 읽힌다
def test_load_without_registered_items() -> None:
    assert load_site_profile(SAMPLE_PATH).registered_items == []


# 문제 5: demo.json의 등록 항목을 파일에 적힌 순서대로 읽는다
def test_load_demo_registered_items() -> None:
    items = load_site_profile(DEMO_PATH).registered_items
    assert items[:2] == ["상품코드", "수수료코드"]
    assert "카드상품코드" in items


# 문제 6: registered_items는 리스트여야 한다
@pytest.mark.parametrize("value", ["상품코드", {"상품코드": 1}, 1])
def test_load_rejects_non_list(tmp_path: Path, value: object) -> None:
    with pytest.raises(MetadataError):
        load_site_profile(write_profile(tmp_path, value))


# 문제 7: 항목명칭은 SQL 문장에 그대로 들어가므로 한글·영문·숫자·_ 1~30자만 허용한다
#        ] 나 @$> 가 들어가면 룰 문법이 깨지고, 공백·따옴표는 다른 SQL을 끼워 넣는 통로가 된다
@pytest.mark.parametrize("name", ["", "A]B", "X@$>", "상품 코드", "A'B", "가" * 31, 123, None])
def test_load_rejects_invalid_item_name(tmp_path: Path, name: object) -> None:
    with pytest.raises(MetadataError):
        load_site_profile(write_profile(tmp_path, ["상품코드", name]))


# 문제 8: 형식만 맞으면 어떤 이름이든 받는다 (현장 이름 규칙은 제각각이다)
def test_load_accepts_various_names(tmp_path: Path) -> None:
    names = ["사급자재코드", "PRD_CD", "질병코드2", "가" * 30]
    assert load_site_profile(write_profile(tmp_path, names)).registered_items == names


# ───────────── 3단계: Intent (code_item, code_item_multi) ─────────────


# 문제 9: 기본값은 code_item=None, code_item_multi=True
#        DB 룰에서 BIZCODE는 보통 여러 값으로 받으므로 기본이 IN 조건이다 (2026-11-10 변경)
def test_intent_code_item_defaults() -> None:
    intent = Intent(kind="lookup", target_type="기본상품", code="BP001")
    assert intent.code_item is None
    assert intent.code_item_multi is True


# 문제 10: code_item은 code 대신 쓸 수 있다 (단건·구조·속성)
@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "lookup", "target_type": "기본상품"},
        {"kind": "structure", "target_type": "기본상품", "direction": "children"},
        {"kind": "attribute", "target_type": "기본상품", "attributes": ["약정기간"]},
    ],
)
def test_code_item_replaces_code(fields: dict[str, object]) -> None:
    intent = Intent.model_validate({**fields, "code_item": "상품코드"})
    assert intent.code is None
    assert intent.code_item == "상품코드"


# 문제 11: code_item 형식이 틀리면 ValidationError (LLM 출력에서 인젝션을 막는 첫 관문)
@pytest.mark.parametrize("name", ["상품 코드", "[상품코드]", "A@$>", "가" * 31, ""])
def test_code_item_format(name: str) -> None:
    with pytest.raises(ValidationError):
        Intent(kind="lookup", target_type="기본상품", code="BP001", code_item=name)


# 문제 12: 구조 조회는 여전히 code 또는 code_item 중 하나가 있어야 한다
def test_structure_still_needs_code_or_code_item() -> None:
    with pytest.raises(ValidationError):
        Intent(kind="structure", target_type="기본상품", direction="children")


# 문제 13: 단건 조회도 code·name_keyword·code_item이 모두 없으면 거부한다
def test_lookup_needs_some_condition() -> None:
    with pytest.raises(ValidationError):
        Intent(kind="lookup", target_type="기본상품", code_item_multi=True)


# ───────────── 4단계: 빌더 (_code_condition을 단건·구조·속성에 적용) ─────────────


# 문제 14 (단건, = 조건): 값이 하나라고 했을 때(code_item_multi=False)만 = <$@[상품코드]@$>
#         params에 code가 없다
def test_lookup_single_rule_var() -> None:
    intent = Intent(
        kind="lookup", target_type="기본상품", code_item="상품코드", code_item_multi=False
    )
    result = make_builder().build(intent)
    assert flat(result.sql) == (
        "SELECT M.SID, M.BIZCODE, M.OBJ_NAME FROM OBJ_MAIN M "
        "WHERE M.TYPEID = :type_id AND M.BIZCODE = <$@[상품코드]@$> "
        "AND M.END_DT = '99991231'"
    )
    assert result.params == {"type_id": 901}


# 문제 15 (단건, IN 조건): 기본(code_item_multi 생략)은 IN (<$@distinct([상품코드].array)@$>)
def test_lookup_multi_rule_var() -> None:
    intent = Intent(kind="lookup", target_type="기본상품", code_item="상품코드")
    sql = flat(make_builder().build(intent).sql)
    assert "M.BIZCODE IN (<$@distinct([상품코드].array)@$>)" in sql


# 문제 16 (구조): 하위 구조는 기준인 P, 상위 구조는 기준인 C의 BIZCODE에 들어간다
def test_structure_rule_var_position() -> None:
    builder = make_builder()
    children = builder.build(
        Intent(kind="structure", target_type="기본상품", code_item="상품코드", direction="children")
    )
    assert (
        "P.TYPEID = :type_id AND P.BIZCODE IN (<$@distinct([상품코드].array)@$>) AND P.END_DT"
        in flat(children.sql)
    )
    assert children.params == {"type_id": 901}

    parents = builder.build(
        Intent(
            kind="structure",
            target_type="부가옵션",
            code_item="옵션코드",
            code_item_multi=False,
            direction="parents",
        )
    )
    assert "C.BIZCODE = <$@[옵션코드]@$>" in flat(parents.sql)


# 문제 17 (속성): 속성 조회의 코드 조건도 룰 변수를 쓴다
def test_attribute_rule_var() -> None:
    intent = Intent(
        kind="attribute", target_type="기본상품", code_item="상품코드", attributes=["약정기간"]
    )
    result = make_builder().build(intent)
    assert flat(result.sql) == (
        'SELECT M.BIZCODE, M.OBJ_NAME, D.CHAR01 "약정기간" '
        "FROM OBJ_MAIN M JOIN OBJ_DETAIL D ON D.SID = M.SID "
        "WHERE M.TYPEID = :type_id AND M.BIZCODE IN (<$@distinct([상품코드].array)@$>) "
        "AND D.KIND = :detail_kind AND M.END_DT = '99991231' AND D.END_DT = '99991231' "
        "ORDER BY M.BIZCODE"
    )
    assert result.params == {"type_id": 901, "detail_kind": 1}


# 문제 18: 등록되지 않은 항목명칭이면 SQL을 만들지 않고 UnsupportedQuestionError
def test_unregistered_code_item_is_rejected() -> None:
    intent = Intent(kind="lookup", target_type="기본상품", code_item="질병코드")
    with pytest.raises(UnsupportedQuestionError, match="등록되지 않은 항목"):
        make_builder().build(intent)


# 문제 19: code_item이 없으면 지금처럼 BIZCODE = :code (값)
def test_without_code_item_keeps_bind_value() -> None:
    result = make_builder().build(Intent(kind="lookup", target_type="기본상품", code="BP001"))
    assert "M.BIZCODE = :code" in flat(result.sql)
    assert "<$@" not in result.sql
    assert result.params == {"type_id": 901, "code": "BP001"}


# 문제 20: code와 code_item이 둘 다 있으면 룰 변수가 이긴다 (값은 룰 엔진이 넣는다)
def test_code_item_wins_over_code() -> None:
    intent = Intent(kind="lookup", target_type="기본상품", code="BP001", code_item="상품코드")
    result = make_builder().build(intent)
    assert "M.BIZCODE IN (<$@distinct([상품코드].array)@$>)" in flat(result.sql)
    assert ":code" not in result.sql
    assert "code" not in result.params


# 문제 21: 룰 변수 SQL도 검증기를 통과하고, 복사용 SQL(render_sql)에 룰 문법이 그대로 남는다
#         검증기는 <$@ ... @$>를 지운 뒤 검사한다.
#         룰 변수는 바인드 변수(:이름)가 아니므로 render_sql은 건드리지 않는다
def test_rule_var_sql_passes_validator_and_renders() -> None:
    common = load_common_metadata(COMMON_PATH)
    intent = Intent(
        kind="lookup", target_type="기본상품", code_item="상품코드", code_item_multi=True
    )
    result = make_builder().build(intent)
    SqlValidator(set(common.tables)).validate(result.sql)
    rendered = render_sql(result.sql, result.params)
    assert "M.TYPEID = 901 AND M.BIZCODE IN (<$@distinct([상품코드].array)@$>)" in flat(rendered)


# ───────────── 5단계: 프롬프트와 추출기 검사 ─────────────


# 문제 22: 등록 항목이 있으면 [등록 항목] 목록이 프롬프트에 나온다. 없으면 머리줄도 없다
def test_prompt_lists_registered_items() -> None:
    lines = build_system_prompt(item_profile()).splitlines()
    assert "[등록 항목]" in lines
    assert "상품코드, 옵션코드" in lines

    without = build_system_prompt(load_site_profile(SAMPLE_PATH)).splitlines()
    assert "[등록 항목]" not in without


# 문제 23: 스키마에 code_item·code_item_multi 설명과, code 대신 쓸 수 있다는 안내가 있다
def test_prompt_describes_code_item() -> None:
    prompt = build_system_prompt(item_profile())
    assert "- code_item: " in prompt
    assert "- code_item_multi: " in prompt
    assert "- code_item은 code 대신 쓸 수 있다 (lookup·structure·attribute·hierarchy)" in prompt


# 문제 24: 예시는 첫 유형과 첫 등록 항목으로 만든다 (프로필에 없는 이름을 보여 주지 않는다)
def test_prompt_code_item_example() -> None:
    prompt = build_system_prompt(item_profile())
    assert "질문: 상품코드 항목으로 기본상품 알려줘" in prompt
    assert '출력: {"kind": "lookup", "target_type": "기본상품", "code_item": "상품코드"}' in prompt
    assert '"code_item"' not in build_system_prompt(load_site_profile(SAMPLE_PATH))


# 문제 25: LLM이 질문에 없는 항목명칭을 넣으면 IntentParseError (지어낸 이름을 막는다)
def test_extractor_rejects_code_item_not_in_question() -> None:
    content = '{"kind": "lookup", "target_type": "기본상품", "code_item": "상품코드"}'
    with pytest.raises(IntentParseError):
        fake_openai(content, item_profile()).extract("BP001 기본상품 알려줘")


# 문제 26: 질문에 띄어 써도("상품 코드") 같은 이름으로 본다
def test_extractor_accepts_code_item_with_spaces() -> None:
    content = '{"kind": "lookup", "target_type": "기본상품", "code_item": "상품코드"}'
    intent = fake_openai(content, item_profile()).extract("상품 코드 항목으로 기본상품 알려줘")
    assert intent.code_item == "상품코드"


# ───────────── 6단계: 전체 흐름 (demo 프로필) ─────────────


def demo_service(intent: Intent) -> TextToSqlService:
    common = load_common_metadata(COMMON_PATH)
    return TextToSqlService(
        FakeIntentExtractor({"q": intent}),
        SqlBuilder(common, load_site_profile(DEMO_PATH)),
        SqlValidator(set(common.tables)),
    )


# 문제 27: "FEE01 수수료가 붙은 상위 상품, 수수료코드 항목으로"
#         → C.BIZCODE IN (<$@distinct([수수료코드].array)@$>)
def test_demo_structure_with_code_item() -> None:
    intent = Intent(
        kind="structure",
        target_type="수수료",
        code="FEE01",
        code_item="수수료코드",
        direction="parents",
    )
    result = demo_service(intent).generate("q", "req-rule")
    assert result.status == "ok"
    assert result.rendered_sql is not None
    assert "C.BIZCODE IN (<$@distinct([수수료코드].array)@$>)" in flat(result.rendered_sql)
    assert "FEE01" not in result.rendered_sql


# 문제 28: 등록되지 않은 항목명칭은 오류가 아니라 unsupported 응답 (사유에 "등록되지 않은 항목")
def test_demo_unregistered_code_item_is_unsupported() -> None:
    intent = Intent(kind="lookup", target_type="보험상품", code_item="질병코드")
    result = demo_service(intent).generate("q", "req-rule")
    assert result.status == "unsupported"
    assert result.message is not None and "등록되지 않은 항목" in result.message


# ───────────── 2026-11-10 보강: 프롬프트 규칙과 실패 로그 ─────────────


# 문제 29: 항목명칭을 code에 넣지 말라는 규칙과, 단일 값(false)·속성 조합 예시가 있다
def test_prompt_guides_code_item_usage() -> None:
    prompt = build_system_prompt(item_profile())
    assert "항목명칭(예: 상품코드)은 code가 아니라 code_item에 넣는다" in prompt
    assert '"code_item": "상품코드", "code_item_multi": false}' in prompt
    assert "질문: 상품코드 여러 개로 기본상품의 약정기간 알려줘" in prompt


# 문제 30: LLM이 항목명칭을 code에 넣는 등 스키마를 어기면, 원문 JSON을 로그에 남기고 거절한다
def test_schema_violation_logs_llm_content(caplog: pytest.LogCaptureFixture) -> None:
    content = (
        '{"kind": "attribute", "target_type": "기본상품", "code": "계좌상품코드", '
        '"attributes": ["약정기간"]}'
    )
    with pytest.raises(IntentParseError):
        fake_openai(content, item_profile()).extract("계좌상품코드 여러 개로 약정기간")
    assert "계좌상품코드" in caplog.text
