"""서비스 문제지 (문제 1~9). 명세: docs/05_구현명세서.md 4장 2~7단계 (TS-01~08).

TextToSqlService는 추출기 → 빌더 → 검증기를 차례로 부르고,
"지원하지 않는 질문"을 오류가 아니라 정상 응답(status="unsupported")으로 바꾼다.
[스프링] 여러 컴포넌트를 묶는 @Service (파사드). HTTP는 모른다.

구현할 곳: src/text_to_sql/services/text_to_sql_service.py
- @dataclass class GenerateResult: status, sql, rendered_sql, params, intent, message
- class TextToSqlService:
    __init__(self, extractor: IntentExtractor, builder: SqlBuilder, validator: SqlValidator)
    generate(self, question: str, request_id: str) -> GenerateResult

진짜 빌더·검증기에 가짜 추출기(FakeIntentExtractor)를 끼워 OpenAI 없이 검사한다.
"""

import logging

import pytest

from text_to_sql.domain.intent import Intent
from text_to_sql.errors import IntentParseError, LlmUnavailableError, SqlValidationError
from text_to_sql.repositories.metadata_repository import load_common_metadata, load_site_profile
from text_to_sql.services.intent_extractor import FakeIntentExtractor, IntentExtractor
from text_to_sql.services.sql_builder import SqlBuilder, render_sql
from text_to_sql.services.sql_formatter import format_sql
from text_to_sql.services.sql_validator import SqlValidator
from text_to_sql.services.text_to_sql_service import GenerateResult, TextToSqlService

REQUEST_ID = "req-test-123"
QUESTION = "BP001 기본상품 알려줘"
LOOKUP = Intent(kind="lookup", target_type="기본상품", code="BP001")


def make_builder() -> SqlBuilder:
    common = load_common_metadata("src/text_to_sql/metadata/common.json")
    profile = load_site_profile("src/text_to_sql/metadata/sites/sample.json")
    return SqlBuilder(common, profile)


def make_validator() -> SqlValidator:
    common = load_common_metadata("src/text_to_sql/metadata/common.json")
    return SqlValidator(set(common.tables))


def make_service(extractor: IntentExtractor) -> TextToSqlService:
    return TextToSqlService(extractor, make_builder(), make_validator())


class RaisingExtractor:
    """extract를 부르면 받은 예외를 던지는 가짜 추출기 (LLM 오류 흉내)."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def extract(self, question: str) -> Intent:
        raise self.error


# ───────────── 정상 흐름 (TS-01, 02) ─────────────


# 문제 1 (TS-01): 정상 질문 → status="ok", sql·params·intent를 채우고 message는 None
def test_ok_result() -> None:
    result = make_service(FakeIntentExtractor({QUESTION: LOOKUP})).generate(QUESTION, REQUEST_ID)

    expected = make_builder().build(LOOKUP)
    assert isinstance(result, GenerateResult)
    assert result.status == "ok"
    assert result.sql == expected.sql
    assert result.params == {"type_id": 901, "code": "BP001"}
    assert result.intent == LOOKUP
    assert result.message is None


# 문제 2 (TS-02): rendered_sql은 render_sql(sql, params)를 사람이 읽기 좋게 줄바꿈한 결과다
#                 (값이 채워진 복사용 SQL. sql은 한 줄 그대로 둔다)
def test_ok_result_has_rendered_sql() -> None:
    result = make_service(FakeIntentExtractor({QUESTION: LOOKUP})).generate(QUESTION, REQUEST_ID)

    assert result.sql is not None
    assert result.rendered_sql is not None
    assert result.rendered_sql == format_sql(render_sql(result.sql, result.params))
    assert "\n" in result.rendered_sql and "\n" not in result.sql
    assert "M.BIZCODE = 'BP001'" in result.rendered_sql


# 문제 3: 추출기에는 받은 질문을 그대로 한 번 넘긴다
def test_extractor_is_called_once_with_question() -> None:
    fake = FakeIntentExtractor({QUESTION: LOOKUP})
    make_service(fake).generate(QUESTION, REQUEST_ID)
    assert fake.calls == [QUESTION]


# ───────────── unsupported로 바꾸는 경우 (TS-03, 04, 05) ─────────────


# 문제 4 (TS-03): LLM이 unsupported라고 답하면 → status="unsupported", message=intent.reason
# 빌더를 부르지 않는다. (빌더를 부르면 빌더의 예외 메시지가 message에 들어가서 이 문제가 실패한다)
def test_unsupported_intent() -> None:
    intent = Intent(kind="unsupported", reason="상품 DB 질문이 아님")
    result = make_service(FakeIntentExtractor({"날씨": intent})).generate("날씨", REQUEST_ID)

    assert result.status == "unsupported"
    assert result.message == "상품 DB 질문이 아님"
    assert result.intent == intent
    assert result.sql is None
    assert result.rendered_sql is None
    assert result.params == {}


# 문제 5 (TS-04): 프로필에 없는 유형이면 빌더의 UnsupportedQuestionError를 unsupported로 바꾼다
# intent는 LLM이 뽑은 그대로 남기고, message는 예외 메시지(비어 있지 않음)
def test_unknown_type_becomes_unsupported() -> None:
    intent = Intent(kind="lookup", target_type="없는유형", code="X1")
    result = make_service(FakeIntentExtractor({"q": intent})).generate("q", REQUEST_ID)

    assert result.status == "unsupported"
    assert result.intent == intent
    assert result.message
    assert result.sql is None
    assert result.rendered_sql is None
    assert result.params == {}


# 문제 6 (TS-05): LLM 응답을 못 알아들으면(IntentParseError) unsupported, intent는 None
def test_parse_error_becomes_unsupported() -> None:
    result = make_service(RaisingExtractor(IntentParseError("깨진 JSON"))).generate(
        QUESTION, REQUEST_ID
    )

    assert result.status == "unsupported"
    assert result.message == "질문을 이해하지 못했습니다"
    assert result.intent is None
    assert result.sql is None
    assert result.rendered_sql is None
    assert result.params == {}


# ───────────── 로그 (TS-06) ─────────────


# 문제 7 (TS-06): unsupported로 바꾼 세 경우 모두 로그에 requestId가 남는다
# caplog: pytest가 그동안 찍힌 로그를 모아 주는 도구. 구현은 평범하게 logger.info(...)만 하면 된다
@pytest.mark.parametrize(
    "extractor",
    [
        FakeIntentExtractor({}),  # 미등록 질문 → unsupported 의도
        FakeIntentExtractor({QUESTION: Intent(kind="lookup", target_type="없는유형", code="X1")}),
        RaisingExtractor(IntentParseError("깨진 JSON")),
    ],
    ids=["unsupported_intent", "unknown_type", "parse_error"],
)
def test_unsupported_is_logged_with_request_id(
    extractor: IntentExtractor, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="text_to_sql"):
        make_service(extractor).generate(QUESTION, REQUEST_ID)
    assert REQUEST_ID in caplog.text


# ───────────── 그대로 전파하는 예외 (TS-07, 08) ─────────────


# 문제 8 (TS-07): LLM 서버 오류는 바꾸지 않고 그대로 밖으로 나간다 (API가 502로 바꾼다)
def test_llm_unavailable_propagates() -> None:
    service = make_service(RaisingExtractor(LlmUnavailableError("연결 실패")))
    with pytest.raises(LlmUnavailableError):
        service.generate(QUESTION, REQUEST_ID)


# 문제 9 (TS-08): 검증기 차단은 바꾸지 않고 그대로 밖으로 나간다 (API가 500으로 바꾼다)
# 허용 테이블이 빈 검증기를 넣으면 빌더가 만든 정상 SQL도 차단되어 재현된다
def test_sql_validation_error_propagates() -> None:
    service = TextToSqlService(
        FakeIntentExtractor({QUESTION: LOOKUP}), make_builder(), SqlValidator(set())
    )
    with pytest.raises(SqlValidationError):
        service.generate(QUESTION, REQUEST_ID)
