"""TextToSqlService.generate(question, request_id).

추출 → (unsupported면 거절) → 빌드 → 검증 → 결과.
IntentParseError·UnsupportedQuestionError는 unsupported 응답으로 바꾸되 로그를 남기고,
LlmUnavailableError·SqlValidationError는 그대로 전파한다."""

import logging
from dataclasses import dataclass
from typing import Literal

from text_to_sql.domain.intent import Intent
from text_to_sql.errors import IntentParseError, UnsupportedQuestionError
from text_to_sql.services.intent_extractor import IntentExtractor
from text_to_sql.services.sql_builder import SqlBuilder, render_sql
from text_to_sql.services.sql_validator import SqlValidator

logger = logging.getLogger(__name__)


@dataclass
class GenerateResult:
    status: Literal["ok", "unsupported"]
    sql: str | None
    rendered_sql: str | None
    params: dict[str, object]
    intent: Intent | None
    message: str | None


def _unsupported(message: str | None, intent: Intent | None) -> GenerateResult:
    return GenerateResult(
        status="unsupported", sql=None, rendered_sql=None, params={}, intent=intent, message=message
    )


class TextToSqlService:
    def __init__(
        self, extractor: IntentExtractor, builder: SqlBuilder, validator: SqlValidator
    ) -> None:
        self.extractor = extractor
        self.builder = builder
        self.validator = validator

    def generate(self, question: str, request_id: str) -> GenerateResult:
        try:
            intent = self.extractor.extract(question)
        except IntentParseError as err:
            logger.info("의도 파싱 실패 requestId=%s error=%s", request_id, err)
            return _unsupported("질문을 이해하지 못했습니다", None)

        if intent.kind == "unsupported":
            logger.info("unsupported requestId=%s reason=%s", request_id, intent.reason)
            return _unsupported(intent.reason, intent)
        try:
            built = self.builder.build(intent)
        except UnsupportedQuestionError as err:
            logger.info("지원하지 않는 질문 requestId=%s error=%s", request_id, err)
            return _unsupported(str(err), intent)

        self.validator.validate(built.sql)
        rendered = render_sql(built.sql, built.params)

        return GenerateResult(
            status="ok",
            sql=built.sql,
            rendered_sql=rendered,
            params=built.params,
            intent=intent,
            message=None,
        )
