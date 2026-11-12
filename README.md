# text-to-sql

PF(EAV 구조) 상품 DB용 **Text-to-SQL PoC**. 자연어 질문을 상품 조회 SQL로 바꿔 준다.

**LLM은 질문에서 의도(JSON)만 뽑고, SQL은 검증된 템플릿으로 조립한다.** LLM이 SQL을 직접 쓰지 않으므로, EAV 구조의 규칙(최신 버전 조건, 구조 테이블 방향, 현장별 유형 번호)을 빠뜨리거나 위험한 SQL이 섞일 일이 없다.

```
질문 "BP001 기본상품에 연결된 하위 오브젝트"
  → ① 의도 추출 (Ollama)     {"kind": "structure", "target_type": "기본상품", "code": "BP001", "direction": "children"}
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
| 속성 (attribute) | "INS001 보험상품의 보험기간, 납입기간" | 완료 |
| 여러 단계 계층 (hierarchy) | "보험상품 → 주계약 → 특약 → 보장 구조와 속성" | 완료 |
| **값 조건 검색** | "국내전용연회비가 30000 이하인 카드상품 목록", "운용사에 한화가 들어간 펀드" | 완료 (list·attribute·hierarchy, 아래 설명) |
| 기준일자 | "2026년 11월 12일 기준 CRD001 카드의 하위 오브젝트" | 완료 (구조·속성·계층 포함, 연도가 있는 날짜만) |
| 패턴 (pattern, pattern_catalog) | | 예정 (지금은 "지원하지 않음"으로 거절) |

### 값 조건 검색

항목 값으로 거르는 질문을 지원한다. 설계는 `docs/08_값_조건_검색_설계.md`.

- 숫자 항목: `이하`, `이상`, `미만`, `초과`, `같은`, `아닌` (`<=`, `>=`, `<`, `>`, `=`, `<>`). 문자 항목: `같은`, `아닌`, `들어간`(포함).
- 숫자 비교는 `CASE WHEN 컬럼 ~ '정규식' THEN CAST(컬럼 AS NUMERIC) END` 식으로 변환해 비교한다. 숫자가 아닌 값(빈 칸, 문자)은 오류 없이 비교에서 빠진다. 방언은 `common.json`의 `dialect`(postgresql, oracle).
- 어느 항목이 숫자인지는 사이트 프로필의 항목 `type`(`number`)과 시트 `표준항목` 탭의 `형식` 열(`숫자`/`문자`)로 정한다.
- 조건은 최대 5개, 모두 AND. 값은 바인드 변수(`:cond_0`)로만 들어간다.
- **안전장치**: 조건의 항목 이름과 값이 질문에 그대로 없으면 거절한다 ("3만원"을 30000으로 바꾸는 식의 환산·지어낸 값 차단). 질문에 `이하`·`이상` 같은 비교 표현이 있는데 조건을 해석하지 못하면, 조건이 빠진 SQL을 내지 않고 거절한다.
- 한계: "3만원", "5천" 같은 단위 환산 미지원, OR 미지원, 값이 공백 없는 숫자 문자열이라고 가정한다.

## 사용법

```bash
pip install "text-to-sql @ git+https://github.com/Walker4990/text-to-sql.git@main"
```

```python
from text_to_sql.config import load_settings
from text_to_sql.factory import create_service, start_warm_up

service = create_service(load_settings())          # 환경변수에서 설정을 읽는다
start_warm_up(service)                             # 앱 시작 때 한 번. Ollama를 백그라운드에서 미리 데운다
result = service.generate("RD001 라이더 알려줘", request_id="req-1")

result.status         # "ok" 또는 "unsupported"
result.sql            # ... WHERE M.TYPEID = :type_id AND M.BIZCODE = :code ...
result.rendered_sql   # ... WHERE M.TYPEID = 1120 AND M.BIZCODE = 'RD001' ...
result.params         # {"type_id": 1120, "code": "RD001"}  (demo 프로필: 라이더 → 특약)
result.message        # unsupported일 때 사유
```

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `TTS_OLLAMA_URL` | `http://localhost:11434` | Ollama 서버 주소 |
| `TTS_OLLAMA_MODEL` | `qwen2.5:7b` | 의도 추출 모델 (채팅 모델이어야 한다. `bge-m3` 같은 임베딩 모델은 쓸 수 없다) |
| `TTS_OLLAMA_KEEP_ALIVE` | `-1` | 모델을 메모리에 남겨 둘 시간. `-1`은 계속 유지, `30m`·`2h`처럼 기간도 가능. 모델을 내리면 프롬프트 캐시도 사라져 다음 질문이 다시 느려진다 |
| `TTS_LLM_TIMEOUT` | `120` | 질문 요청의 타임아웃(초). 예열 요청은 따로 300초 |
| `TTS_SITE` | `demo` | 사이트 프로필 이름 (`metadata/sites/<이름>.json`). `demo`=보험·카드·증권 시연용, `sample`=테스트용 |
| `TTS_METADATA_DIR` | 패키지 안 `metadata/` | |

LLM은 로컬 Ollama로 돌린다. 먼저 모델을 받아 둔다.

```bash
ollama pull qwen2.5:7b
```

**예열**: CPU에서는 첫 질문이 1분 넘게 걸린다. 대부분은 모델 로드가 아니라 시스템 프롬프트(약 2,000토큰)를 처음 읽는 시간이다. Ollama는 한 번 읽은 앞부분을 캐시하므로, 앱 시작 때 `start_warm_up(service)`로 한 번 불러 두면 이후 질문은 약 8~10초다 (Ryzen 5 7530U, GPU 없음 기준. 예열 자체는 약 100초, 그동안 들어온 질문은 기다린다).

### 시연 프로필 (`demo`)

보험·카드·증권 현장 시연용 가상 프로필이다. TYPEID는 실제 현장 값과 무관하다.

| 업권 | 유형 (TYPEID) | 별칭 |
|---|---|---|
| 보험 (1xxx) | 보험상품(1100), 주계약(1110), 특약(1120), 보장(1130), 보험료율(1140) | 보험, 주보험, 라이더·선택특약, 담보, 요율 |
| 카드 (2xxx) | 카드상품(2100), 카드혜택(2110), 연회비(2120), 제휴가맹점(2130) | 카드, 부가서비스·혜택, 가맹점·제휴사 |
| 증권 (3xxx) | 펀드(3100), ETF(3110), 계좌상품(3120), 거래수수료(3130) | 상장지수펀드, 계좌, 수수료·매매수수료 |

시연 질문 예: `INS001 보험상품 알려줘`, `라이더 목록 보여줘`, `CRD001 카드에 연결된 부가서비스`, `글로벌 들어간 펀드 찾아줘`, `FEE01 수수료가 붙은 상위 상품`

오류: LLM 서버 연결·HTTP 오류(Ollama 꺼짐, 모델 미설치 404 등)는 `LlmUnavailableError`, 생성된 SQL이 검증에 걸리면 `SqlValidationError`, 설정 누락은 `ConfigError`. 지원하지 않는 질문은 예외가 아니라 `status="unsupported"`로 돌아온다.

## 메타데이터 관리 (구글 시트)

현장 담당자는 **구글 시트만 편집**하고, 서비스는 **검증을 통과한 스냅샷 JSON만** 읽는다. 시트를 잘못 고치거나 구글에 접속할 수 없어도 서비스는 영향을 받지 않는다. 설계는 `docs/07_메타데이터_관리_설계.md`.

```
구글 시트(사이트당 1개) → 동기화 도구: 검증(MV-01~09) → 차이 보고 → --apply 때만 저장 → sites/<사이트>.json → 서비스 재시작
```

```bash
pip install -e ".[sheets]"     # 동기화 도구를 돌리는 곳에만 (챗봇은 필요 없음)

# 미리보기: 검증하고 차이만 출력 (저장 안 함)
python -m text_to_sql.sync --site demo --sheet-id <스프레드시트 ID> --credentials <서비스 계정 키.json>
# 저장. TYPEID·물리컬럼 변경, 유형·항목 삭제 같은 위험 변경이 있으면 --yes도 필요
python -m text_to_sql.sync --site demo --sheet-id <ID> --credentials <키> --apply
# 시트 대신 탭별 CSV 폴더에서 읽기 / 지금 스냅샷을 CSV로 내보내기
python -m text_to_sql.sync --site demo --csv-dir templates/sheet_template/demo
python -m text_to_sql.sync --site demo --export-csv <폴더>
```

- 시트 탭: `설정`, `유형`, `별칭`, `표준항목`, `항목`(등록 항목명칭), `테이블별칭`. 템플릿은 `templates/sheet_template/demo/*.csv`(demo 프로필을 내보낸 것)를 탭마다 가져오면 된다.
- 시트는 서비스 계정 이메일에 **뷰어**로만 공유한다. 키 파일은 커밋하지 않는다.
- 환경변수: `TTS_SHEET_ID`, `TTS_GOOGLE_CREDENTIALS`. 스냅샷 폴더는 `--out-dir`, 없으면 `TTS_METADATA_DIR`, 그것도 없으면 패키지 `metadata/`.
- 종료 코드: 0 성공, 1 검증 실패, 2 시트 접근 실패, 3 위험 변경인데 `--yes` 없음.
- 서비스는 시작할 때 `프로필 로드 site=... version=...` 로그를 남긴다. requestId와 함께 보면 어느 메타데이터로 SQL을 만들었는지 알 수 있다.

## 구조

```
src/text_to_sql/
  config.py, factory.py          설정, 조립 (create_service)
  domain/intent.py               의도 모델 (LLM 출력 스키마, 형식 검증)
  repositories/                  메타데이터·사이트 프로필 읽기, 프로필 출처(CSV 폴더·구글 시트)
  sync/                          프로필 동기화 도구 (시트 행 → 검증 → 차이 → 스냅샷)
  services/
    intent_extractor.py          의도 추출 (Ollama, 테스트용 Fake)
    sql_builder.py               SQL 템플릿 조립, render_sql
    sql_validator.py             SQL 안전 검사
    text_to_sql_service.py       추출 → 조립 → 검증 → 결과
  metadata/                      공통 메타데이터 + 사이트 프로필 (현장마다 유형 번호·별칭·테이블 별칭이 다름)
```

설계 문서는 `docs/` (요구사항 정의서, 기능 명세서, 구현 명세서, 메타데이터 관리 설계).

## 개발

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
.venv/Scripts/python -m mypy
.venv/Scripts/python -m ruff check src tests
```

## 브랜치와 제공본

| 버전 | 위치 | 비고 |
|---|---|---|
| OpenAI | 이 저장소 `openai` 브랜치 (챗봇 저장소도 `openai` 브랜치) | 보관용. 더 이상 개발하지 않는다 |
| Ollama (명세 포함) | 이 저장소 `main` | 개발은 여기서만 한다 |
| Ollama (소스 제공용) | 별도 공개 저장소 | 직접 고치지 않는다. 아래 스크립트로 만든다 |

```bash
python scripts/export_release.py <제공 저장소 폴더>
```

- 이 저장소와 챗봇 저장소(`C:\side_chatbot`)에서 **허용 목록**에 있는 파일만 복사한다 (`scripts/export_release.py`의 `SOURCES`). 명세(`docs/`), 테스트, 학습용 파일은 나가지 않는다. 새 파일을 제공본에 넣으려면 허용 목록에 추가한다.
- 제공본 맨 위의 `README.md`·`.gitignore`는 `release/`에서 관리한다.
- 제공 폴더는 비어 있거나 제공용 Git 저장소여야 한다 (`.git`만 남기고 다시 채운다). 내보낸 뒤 제공 저장소에서 차이를 확인하고 커밋한다.
