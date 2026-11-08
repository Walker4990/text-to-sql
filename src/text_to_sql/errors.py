"""예외 정의. 에러를 삼키지 않도록 종류별로 구분한다.

- MetadataError: 메타데이터 파일 없음·형식 오류
- UnsupportedQuestionError: 지원하지 않는 유형·항목·질문
- IntentParseError: LLM 출력이 JSON/스키마 위반
- LlmUnavailableError: LLM 서버 연결 실패
- SqlValidationError: 생성된 SQL이 검증기에서 차단됨
- ConfigError: 환경변수 설정 누락·형식 오류 (앱 시작 시)"""


class UnsupportedQuestionError(Exception):
    pass


class MetadataError(Exception):
    pass


class IntentParseError(Exception):
    pass


class LlmUnavailableError(Exception):
    pass


class SqlValidationError(Exception):
    pass


class ConfigError(Exception):
    pass
