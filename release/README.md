# PF Text-to-SQL 시연

PF(EAV 구조) 상품 DB에 대한 자연어 질문을 **상품 조회 SQL**로 바꿔 주는 챗봇입니다.
LLM은 로컬 Ollama(qwen2.5:7b)로 돌아가며, 만든 SQL은 DB에 실행하지 않고 제공만 합니다.

## 시연 방법

1. https://pf-text-to-sql.tail37c7a3.ts.net 에 접속해 발급받은 계정으로 로그인합니다. (계정은 요청하시면 따로 전달드립니다)
2. 질문창의 문서/SQL 토글을 **SQL**로 바꿉니다.
3. 아래 예시 문장을 **그대로** 입력합니다. 질문 하나에 약 5~15초 걸립니다.
4. 결과 SQL은 **복사** 버튼으로 DB 도구나 PF 룰에 바로 붙여 넣을 수 있습니다.

> 시연 데이터는 보험·카드·증권 업권의 **가상** 프로필입니다. TYPEID 등은 실제 현장 값과 무관합니다.

## SQL 작성 규칙

- **표준항목**(DB에 있는 속성)은 쿼리의 **컬럼**으로 나옵니다. 예: 국내전용연회비 → `D1.CHAR01`
- **입력으로 받는 값**은 **항목**(솔루션에 등록된 항목명칭)의 룰 문법으로 나옵니다. 예: `<$@[가입최저나이]@$>`
- 여러 개로 받으면 `IN (<$@distinct([항목명칭].array)@$>)`가 됩니다.
- 모든 테이블에 최신 버전 조건(`END_DT = '99991231'`)이 자동으로 붙습니다.

## 시연 예시 (단순 → 복잡)

### 기본 조회

| # | 질문 | 보여 주는 기능 | 결과 SQL의 핵심 |
|---|---|---|---|
| 1 | `INS001 보험상품 알려줘` | 코드로 단건 조회 | `TYPEID = 1100 AND BIZCODE = 'INS001'` |
| 2 | `라이더 목록 보여줘` | 현장 용어(별칭) 인식: 라이더 → 특약 | `TYPEID = 1120`, 코드순 정렬 |
| 3 | `CRD001 카드에 연결된 하위 오브젝트` | 구조 조회 (아래로) | OBJ_INV 조인, 부모 → 자식 |
| 4 | `FEE01 수수료가 붙은 상위 상품` | 구조 조회 (위로) | OBJ_INV 조인, 자식 → 부모 |
| 5 | `INS001 보험상품의 보험기간이랑 납입기간 알려줘` | 속성 값 조회 | `D.CHAR01 "보험기간", D.CHAR02 "납입기간"` |
| 6 | `2026년 11월 12일 기준 CRD001 카드의 하위 오브젝트` | 기준일자 (유효기간) | `'20261112' BETWEEN VF_DT AND VT_DT` |

### 값 조건 (질문에 적힌 값)

| # | 질문 | 보여 주는 기능 | 결과 SQL의 핵심 |
|---|---|---|---|
| 7 | `펀드 중에 운용사에 한화가 들어간 것 목록` | 문자 속성 포함 검색 | `D.CHAR02 LIKE '%한화%'` |
| 8 | `국내전용연회비가 30000 이하인 카드상품 목록` | 하위 유형의 숫자 조건 → 계층 조회 | `CAST(D1.CHAR01 AS NUMERIC) <= 30000` |

### 룰 변수 (입력으로 받는 값)

| # | 질문 | 보여 주는 기능 | 결과 SQL의 핵심 |
|---|---|---|---|
| 9 | `상품코드 여러 개로 보험상품의 보험기간 알려줘` | 코드를 입력으로 | `M.BIZCODE IN (<$@distinct([상품코드].array)@$>)` |
| 10 | `운용사 항목으로 받아서 운용사가 같은 펀드 목록` | 속성 값을 입력으로 (같음) | `D.CHAR02 = <$@[운용사]@$>` |
| 11 | `위험등급을 여러 개로 받아서 그중 하나인 펀드 목록` | 속성 값을 여러 개 입력으로 | `D.CHAR01 IN (<$@distinct([위험등급].array)@$>)` |
| 12 | `국내전용연회비 항목으로 받아서 그 이하인 카드상품 목록` | 하위 유형 숫자 조건을 입력으로 | `CAST(D1.CHAR01 AS NUMERIC) <= <$@[국내전용연회비]@$>` |
| 13 | `상품코드 여러 개로 보험상품의 보험기간 알려줘, 가입최저나이 항목으로 받아서 그 이상인 것만` | 코드 + 조건 값 모두 입력 | `BIZCODE IN (…[상품코드]…)` + `CAST(D.CHAR03 AS NUMERIC) >= <$@[가입최저나이]@$>` |
| 14 | `보험상품코드를 다중값으로 받고, 주계약 특약 보장 구조의 속성값들을 출력` | 룰 변수 + 4단계 계층 + 단계별 속성 | `L0.BIZCODE IN (<$@distinct([보험상품코드].array)@$>)` |

### 지원하지 않는 질문

| 질문 | 결과 |
|---|---|
| `오늘 날씨 어때` | SQL 없이 "상품 DB 질문이 아님"으로 거절 |
| `운용사명 항목으로 받아서 운용사가 같은 펀드 목록` | 등록되지 않은 항목명칭이라 거절 |

## 결과 예시

### 8. 국내전용연회비가 30000 이하인 카드상품 목록

```sql
SELECT
    L0.BIZCODE "카드상품",
    L0.OBJ_NAME "카드상품명",
    L1.BIZCODE "연회비",
    L1.OBJ_NAME "연회비명",
    D1.CHAR01 "국내전용연회비",
    D1.CHAR02 "해외겸용연회비",
    D1.CHAR03 "가족카드연회비"
FROM OBJ_MAIN L0
JOIN OBJ_INV I1 ON I1.SID = L0.SID
JOIN OBJ_MAIN L1 ON L1.SID = I1.SUBSID AND L1.TYPEID = 2120
LEFT JOIN OBJ_DETAIL D1 ON D1.SID = L1.SID AND D1.KIND = 1 AND D1.END_DT = '99991231'
WHERE L0.TYPEID = 2100
  AND L0.END_DT = '99991231'
  AND I1.END_DT = '99991231'
  AND L1.END_DT = '99991231'
  AND CAST(D1.CHAR01 AS NUMERIC) <= 30000
ORDER BY L0.BIZCODE, L1.BIZCODE
```

### 11. 위험등급을 여러 개로 받아서 그중 하나인 펀드 목록

```sql
SELECT M.SID, M.BIZCODE, M.OBJ_NAME, D.CHAR01 "위험등급"
FROM OBJ_MAIN M
JOIN OBJ_DETAIL D ON D.SID = M.SID
WHERE M.TYPEID = 3100
  AND D.KIND = 1
  AND D.CHAR01 IN (<$@distinct([위험등급].array)@$>)
  AND M.END_DT = '99991231'
  AND D.END_DT = '99991231'
ORDER BY M.BIZCODE
```

### 13. 코드와 조건 값을 모두 입력으로

```sql
SELECT M.BIZCODE, M.OBJ_NAME, D.CHAR01 "보험기간", D.CHAR03 "가입최저나이"
FROM OBJ_MAIN M
JOIN OBJ_DETAIL D ON D.SID = M.SID
WHERE M.TYPEID = 1100
  AND M.BIZCODE IN (<$@distinct([상품코드].array)@$>)
  AND D.KIND = 1
  AND CAST(D.CHAR03 AS NUMERIC) >= <$@[가입최저나이]@$>
  AND M.END_DT = '99991231'
  AND D.END_DT = '99991231'
ORDER BY M.BIZCODE
```

## 입력으로 쓸 수 있는 항목명칭 (시연 프로필)

| 구분 | 항목명칭 (alias) |
|---|---|
| 코드 | 상품코드 (productCode), 보험상품코드 (insuranceProductCode), 주계약코드 (mainContractCode), 특약코드 (riderCode), 보장코드 (coverageCode), 요율코드 (rateCode), 카드상품코드 (cardProductCode), 카드혜택코드 (cardBenefitCode), 가맹점코드 (merchantCode), 펀드코드 (fundCode), ETF코드 (etfCode), 계좌상품코드 (accountProductCode), 수수료코드 (feeCode) |
| 속성 값 | 가입최저나이 (minJoinAge), 납입주기 (paymentCycle), 특약보험료 (riderPremium), 보장금액 (coverageAmount), 카드등급 (cardGrade), 국내전용연회비 (domesticAnnualFee), 할인율 (discountRate), 운용사 (assetManager), 위험등급 (riskGrade), 총보수 (totalExpenseRatio), 최소가입금액 (minJoinAmount), 수수료율 (feeRate) |

alias는 API 송수신용 이름이고, SQL에는 항목명칭이 들어갑니다.

## 질문할 때 참고

- **입력값으로 받기**: "○○ 항목으로 받아서", "○○를 여러 개로 받아서"처럼 말합니다. ○○는 위 표의 항목명칭이어야 합니다.
- **단일/다중**: 항목명칭 바로 뒤에 "여러·다중"이 있으면 `IN`, 없으면 `=`(또는 비교 연산)입니다. 코드는 "하나·한 개·단일"이라고 할 때만 `=`이고, 그 밖에는 `IN`입니다.
- **값 조건**: 항목 이름과 값이 질문에 그대로 있어야 합니다. "3만원"처럼 단위를 붙이면 환산하지 않고 거절합니다.
- **기준일자**: 연도가 있는 날짜만 인식합니다.
- 목록에 없는 유형·항목이나 상품 DB와 무관한 질문은 SQL 없이 사유와 함께 거절합니다.
- LLM 특성상 같은 질문에도 가끔 다르게 답할 수 있습니다. 결과가 이상하면 예시 문장 그대로 다시 물어봐 주세요.
