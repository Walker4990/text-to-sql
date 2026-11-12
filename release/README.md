# 문서 챗봇 + Text-to-SQL

사내 문서 질의응답 챗봇에, PF(EAV 구조) 상품 DB용 **Text-to-SQL**을 SQL 모드로 붙인 구성이다.
LLM은 모두 **로컬 Ollama**로 돌리므로 외부 API 없이 폐쇄망에서 동작한다.

| 모드 | 하는 일 | 모델 |
|---|---|---|
| 문서 모드 | `chatbot/docs/*.txt`에서 질문과 비슷한 조각을 찾아 그 내용으로만 답한다 | 임베딩 `bge-m3`, 답변 `qwen2.5:7b` |
| SQL 모드 | 자연어 질문을 상품 조회 SQL로 바꾼다 (SQL만 만들고 DB에 실행하지 않는다) | 의도 추출 `qwen2.5:7b` |

```
chatbot/       웹 서버(FastAPI)·화면, 로그인, 문서 모드, SQL 모드 API
text-to-sql/   SQL 생성 패키지 (챗봇이 가져다 쓴다)
```

## SQL 모드의 동작 방식

**LLM은 질문에서 의도(JSON)만 뽑고, SQL은 검증된 템플릿으로 조립한다.** LLM이 SQL을 직접 쓰지 않으므로
EAV 구조의 규칙(최신 버전 조건, 구조 테이블 방향, 현장별 유형 번호)이 빠지거나 위험한 SQL이 섞이지 않는다.

```
질문 "BP001 기본상품에 연결된 하위 오브젝트"
  → ① 의도 추출 (Ollama)   {"kind": "structure", "target_type": "기본상품", "code": "BP001", "direction": "children"}
  → ② SQL 조립 (템플릿)    SELECT C.SID, C.BIZCODE, C.OBJ_NAME FROM OBJ_MAIN P JOIN OBJ_INV I ... WHERE P.BIZCODE = :code ...
  → ③ SQL 검증             SELECT만, 허용 테이블만, 주석·금지 키워드·위험 함수 차단
  → ④ 결과                 바인드 변수 SQL + 값을 채운 복사용 SQL
```

| 질문 종류 | 예 |
|---|---|
| 단건 조회 | `INS001 보험상품 알려줘`, `글로벌 들어간 펀드 찾아줘` |
| 목록 | `라이더 목록 보여줘` |
| 구조 (하위·상위) | `CRD001 카드에 연결된 하위 오브젝트`, `FEE01 수수료가 붙은 상위 상품` |
| 속성 값 | `INS001 보험상품의 보험기간이랑 납입기간 알려줘` |
| 여러 단계 계층 + 속성 | `보험상품코드를 다중값으로 받고, 주계약 특약 보장 구조의 속성값들을 출력` |
| 룰 변수 (항목명칭) | `상품코드 여러 개로 보험상품의 보험기간 알려줘` → `M.BIZCODE IN (<$@distinct([상품코드].array)@$>)` |
| 값 조건 | `국내전용연회비가 30000 이하인 카드상품 목록`, `운용사에 한화가 들어간 펀드` |
| 기준일자 | `2026년 11월 12일 기준 CRD001 카드의 하위 오브젝트` (연도가 있는 날짜만) |

- 단일/다중: 질문에 "하나·한 개·1개·단일"이 있을 때만 `=`, 그 밖에는 `IN` 조건이 된다.
- 지원하지 않는 질문, 목록에 없는 유형·항목은 SQL을 만들지 않고 사유와 함께 거절한다.
- 값 조건의 항목 이름과 값이 질문에 그대로 없으면 거절한다 (단위 환산 "3만원" 등은 지원하지 않는다).

## 준비물

- Python 3.12 이상
- [Ollama](https://ollama.com) 와 모델 `qwen2.5:7b`, `bge-m3`
- PostgreSQL (로그인 사용자와 질문 이력 저장)

### Ollama 모델 넣기

인터넷이 되는 환경이면 바로 받는다.

```bash
ollama pull qwen2.5:7b
ollama pull bge-m3
```

**폐쇄망**이면 인터넷이 되는 PC에서 위 명령으로 받은 뒤, 그 PC의 모델 폴더
(Windows `C:\Users\<사용자>\.ollama\models`, Linux `~/.ollama/models`)를 통째로 서버의 같은 위치
(또는 Ollama 환경변수 `OLLAMA_MODELS`가 가리키는 폴더)에 복사한다. 서버에서 `ollama list`에 두 모델이 보이면 된다.

## 설치

저장소 루트에서:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   Linux: source .venv/bin/activate
pip install -e ./text-to-sql
pip install -r chatbot/requirements.txt
```

## 설정

`chatbot/.env.example`을 `chatbot/.env`로 복사하고 값을 채운다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | 문서 모드가 쓰는 Ollama 주소 |
| `OLLAMA_EMBED_MODEL` | `bge-m3` | 문서 임베딩 모델 |
| `OLLAMA_CHAT_MODEL` | `qwen2.5:7b` | 문서 모드 답변 모델 |
| `OLLAMA_TIMEOUT` | `180` | 문서 모드 답변 타임아웃(초) |
| `RAG_SIMILARITY_THRESHOLD` | `0.55` | 이 점수 미만이면 답변을 만들지 않고 미답변 처리 (bge-m3 기준값) |
| `TOP_K` | `3` | 답변 근거로 쓸 문서 조각 수 |
| `TTS_OLLAMA_URL` | `http://localhost:11434` | SQL 모드가 쓰는 Ollama 주소 |
| `TTS_OLLAMA_MODEL` | `qwen2.5:7b` | SQL 모드 의도 추출 모델 (채팅 모델이어야 한다) |
| `TTS_OLLAMA_KEEP_ALIVE` | `-1` | 모델을 메모리에 남겨 둘 시간. `-1`은 계속 유지, `30m`·`2h`도 가능 |
| `TTS_LLM_TIMEOUT` | `120` | SQL 질문 타임아웃(초). 예열 요청은 따로 300초 |
| `TTS_SITE` | `demo` | 사이트 프로필 이름 (`text-to-sql/src/text_to_sql/metadata/sites/<이름>.json`) |
| `TTS_METADATA_DIR` | 패키지 안 `metadata/` | 메타데이터 폴더를 따로 둘 때 |
| `JWT_SECRET` | 없음 (필수) | 로그인 토큰 서명 키 |
| `DATABASE_URL` | 없음 (필수) | PostgreSQL 접속 주소 |

## 실행

```bash
cd chatbot
python -m app.create_admin <아이디> <비밀번호>   # 처음 한 번, 관리자 계정 만들기
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://<서버>:8000` 에 접속해 로그인하고, 질문창의 문서/SQL 토글로 모드를 고른다.

- 서버가 처음 켜질 때 `chatbot/docs/*.txt`를 임베딩해 `chatbot/app/embeddings.json`에 저장한다.
  문서를 바꾸거나 임베딩 모델을 바꾸면 이 파일을 지우고 다시 켠다 (모델이 바뀐 것은 자동으로 감지한다).
- **예열**: 서버가 켜지면 SQL 모드용 모델을 백그라운드에서 미리 데운다. 로그에 `Ollama 예열 완료`가 찍히면
  준비된 것이다. 예열 전에 들어온 SQL 질문은 예열이 끝날 때까지 기다린다.

### 속도 참고 (GPU 없는 노트북, Ryzen 5 7530U · RAM 16GB)

| 항목 | 시간 |
|---|---|
| SQL 모드 예열 (서버 시작 후 한 번) | 약 100초 |
| SQL 질문 (예열 후) | 질문당 약 8~16초, 여러 단계 계층 약 30초 |

GPU가 있는 서버에서는 훨씬 짧다. 예열로 데운 상태는 Ollama 메모리에 남으므로 사용자가 접속할 때마다
느려지지 않는다. Ollama를 다시 켜거나 서버를 재부팅하면 챗봇 서버도 다시 켜서 예열한다.

## 사이트 프로필 (현장별 메타데이터)

현장마다 유형 번호(TYPEID)·별칭·항목·테이블 별칭이 다르므로 사이트 프로필로 분리한다.
기본 `demo`는 보험·카드·증권 시연용 **가상** 프로필이다 (TYPEID는 실제 현장 값과 무관).

새 현장은 `templates/sheet_template/demo/*.csv` 양식으로 탭별 값을 채운 뒤 동기화 도구로 검증·저장한다.
구글 시트에서 바로 읽을 수도 있다.

```bash
cd text-to-sql
# CSV 폴더에서 읽어 검증하고 차이만 출력 (저장 안 함)
python -m text_to_sql.sync --site <사이트> --csv-dir <CSV 폴더>
# 저장 (TYPEID·물리컬럼 변경, 유형·항목 삭제 같은 위험 변경이 있으면 --yes도 필요)
python -m text_to_sql.sync --site <사이트> --csv-dir <CSV 폴더> --apply

# 구글 시트에서 읽을 때 (pip install -e "./text-to-sql[sheets]" 필요)
python -m text_to_sql.sync --site <사이트> --sheet-id <ID> --credentials <서비스 계정 키.json>
```

- 탭: `설정`, `유형`, `별칭`, `표준항목`, `항목`(등록 항목명칭), `테이블별칭`
- 종료 코드: 0 성공, 1 검증 실패, 2 출처 접근 실패, 3 위험 변경인데 `--yes` 없음
- 저장한 뒤에는 챗봇 서버를 다시 켠다. 서버는 시작할 때 `프로필 로드 site=... version=...` 로그를 남긴다.

## 오류 응답 (SQL 모드)

| 상황 | 응답 |
|---|---|
| 빈 질문, 500자 초과 | 400 |
| Ollama 꺼짐·모델 미설치 | 502 "LLM 서버에 연결할 수 없습니다" |
| 생성된 SQL이 검증에 걸림 | 500 |
| SQL 서비스 준비 실패 (설정·메타데이터 오류) | 503 (문서 모드는 계속 동작) |
| 지원하지 않는 질문 | 정상 응답 + 사유 |

## 보안

- SQL 모드는 SELECT 문만 만들고 DB에 실행하지 않는다. 사용자 값은 바인드 변수로만 들어간다.
- `chatbot/.env`, 구글 서비스 계정 키는 커밋하지 않는다 (`.gitignore`에 포함).
