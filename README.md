# text-to-sql

PF(EAV 구조) 상품 DB용 **Text-to-SQL PoC**. 자연어 질문을 상품 조회 SQL로 바꿔 준다.

**LLM은 질문에서 의도(JSON)만 뽑고, SQL은 검증된 템플릿으로 조립한다.** LLM이 SQL을 직접 쓰지 않으므로, EAV 구조의 규칙(최신 버전 조건, 구조 테이블 방향, 현장별 유형 번호)을 빠뜨리거나 위험한 SQL이 섞일 일이 없다.

```
질문 "BP001 기본상품에 연결된 하위 오브젝트"
  → ① 의도 추출 (OpenAI)     {"kind": "structure", "target_type": "기본상품", "code": "BP001", "direction": "children"}
  → ② SQL 조립 (템플릿)      SELECT C.SID, C.BIZCODE, C.OBJ_NAME FROM OBJ_MAIN P JOIN OBJ_INV I ... WHERE P.BIZCODE = :code ...
  → ③ SQL 검증 (SELECT만, 허용 테이블만, 주석·금지 키워드·위험 함수 차단)
  → ④ 결과: 바인드 변수 SQL + 값을 채운 복사용 SQL
```

결과는 SQL만 제공하고 DB에 실행하지는 않는다. 사용 예: 사내 챗봇의 SQL 모드 ([side_chatbot](https://github.com/Walker4990/side_chatbot)).

## 지원하는 질문

| 종류 | 예 | 상태 |
|---|---|---|
| 단건 조회 (lookup) | "BP001 기본상품 알려줘", 이름 검색 | 완료 |
| 목록 (list) | "부가옵션 목록" | 완료 |
| 구조 (structure) | "BP001에 연결된 하위 오브젝트", "AO001이 속한 기본상품" | 완료 |
| 속성·패턴 (attribute, pattern, pattern_catalog) | "BP001의 약정기간" | 예정 (지금은 "지원하지 않음"으로 거절) |

## 사용법

```bash
pip install "text-to-sql @ git+https://github.com/Walker4990/text-to-sql.git@main"
```

```python
from text_to_sql.config import load_settings
from text_to_sql.factory import create_service

service = create_service(load_settings())          # 환경변수에서 설정을 읽는다
result = service.generate("BP001 기본상품 알려줘", request_id="req-1")

result.status         # "ok" 또는 "unsupported"
result.sql            # ... WHERE M.TYPEID = :type_id AND M.BIZCODE = :code ...
result.rendered_sql   # ... WHERE M.TYPEID = 901 AND M.BIZCODE = 'BP001' ...
result.params         # {"type_id": 901, "code": "BP001"}
result.message        # unsupported일 때 사유
```

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | (필수) | OpenAI API 키 |
| `TTS_OPENAI_MODEL` | `gpt-4o-mini` | 의도 추출 모델 |
| `TTS_OPENAI_BASE_URL` | `https://api.openai.com/v1` | |
| `TTS_LLM_TIMEOUT` | `30` | 초 |
| `TTS_SITE` | `sample` | 사이트 프로필 이름 (`metadata/sites/<이름>.json`) |
| `TTS_METADATA_DIR` | 패키지 안 `metadata/` | |

오류: LLM 서버 연결·HTTP 오류는 `LlmUnavailableError`, 생성된 SQL이 검증에 걸리면 `SqlValidationError`, 설정 누락은 `ConfigError`. 지원하지 않는 질문은 예외가 아니라 `status="unsupported"`로 돌아온다.

## 구조

```
src/text_to_sql/
  config.py, factory.py          설정, 조립 (create_service)
  domain/intent.py               의도 모델 (LLM 출력 스키마, 형식 검증)
  repositories/                  메타데이터·사이트 프로필 읽기
  services/
    intent_extractor.py          의도 추출 (OpenAI, 테스트용 Fake)
    sql_builder.py               SQL 템플릿 조립, render_sql
    sql_validator.py             SQL 안전 검사
    text_to_sql_service.py       추출 → 조립 → 검증 → 결과
  metadata/                      공통 메타데이터 + 사이트 프로필 (현장마다 유형 번호·별칭·테이블 별칭이 다름)
```

설계 문서는 `docs/` (요구사항 정의서, 기능 명세서, 구현 명세서).

## 개발

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
.venv/Scripts/python -m mypy
.venv/Scripts/python -m ruff check src tests
```
