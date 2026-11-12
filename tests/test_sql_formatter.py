"""챗봇에 보여 줄 SQL 줄바꿈·들여쓰기 (services/sql_formatter.py, format_sql).

규칙
- 절(FROM, JOIN, LEFT JOIN, WHERE, ORDER BY)마다 새 줄
- WHERE의 AND는 새 줄에 두 칸 들여쓰기
- JOIN의 ON 조건, BETWEEN … AND …, 괄호·CASE 안은 나누지 않는다
- SELECT 컬럼이 짧으면(60자 이하) 한 줄, 길면 한 줄에 하나씩 네 칸 들여쓰기
- 따옴표 문자열·별칭("…")·룰 변수(<$@ … @$>) 안은 건드리지 않는다
- 공백만 바꾼다 (공백을 지우면 원래 SQL과 같다)
"""

import pytest

from text_to_sql.services.sql_formatter import format_sql


def squash(sql: str) -> str:
    return "".join(sql.split())


# 문제 1: 단건 조회 — 절마다 새 줄, WHERE의 AND는 두 칸 들여쓰기, 끝 공백 제거
def test_lookup() -> None:
    sql = (
        "SELECT M.SID, M.BIZCODE, M.OBJ_NAME FROM OBJ_MAIN M WHERE M.TYPEID = 1100 "
        "AND M.BIZCODE = 'INS001' AND M.END_DT = '99991231' "
    )
    assert format_sql(sql) == (
        "SELECT M.SID, M.BIZCODE, M.OBJ_NAME\n"
        "FROM OBJ_MAIN M\n"
        "WHERE M.TYPEID = 1100\n"
        "  AND M.BIZCODE = 'INS001'\n"
        "  AND M.END_DT = '99991231'"
    )


# 문제 2: JOIN·LEFT JOIN은 한 줄에 하나. ON 조건의 AND는 그 줄에 둔다. ORDER BY도 새 줄
def test_joins_keep_on_conditions() -> None:
    sql = (
        "SELECT C.SID, C.BIZCODE, C.OBJ_NAME FROM OBJ_MAIN P JOIN OBJ_INV I ON I.SID = P.SID "
        "JOIN OBJ_MAIN C ON C.SID = I.SUBSID AND C.TYPEID = 2120 "
        "LEFT JOIN OBJ_DETAIL D ON D.SID = C.SID AND D.KIND = 1 "
        "WHERE P.BIZCODE = 'CRD001' AND P.END_DT = '99991231' ORDER BY C.BIZCODE"
    )
    assert format_sql(sql) == (
        "SELECT C.SID, C.BIZCODE, C.OBJ_NAME\n"
        "FROM OBJ_MAIN P\n"
        "JOIN OBJ_INV I ON I.SID = P.SID\n"
        "JOIN OBJ_MAIN C ON C.SID = I.SUBSID AND C.TYPEID = 2120\n"
        "LEFT JOIN OBJ_DETAIL D ON D.SID = C.SID AND D.KIND = 1\n"
        "WHERE P.BIZCODE = 'CRD001'\n"
        "  AND P.END_DT = '99991231'\n"
        "ORDER BY C.BIZCODE"
    )


# 문제 3: BETWEEN … AND …의 AND는 나누지 않는다
def test_between_and_stays_together() -> None:
    sql = (
        "SELECT M.SID FROM OBJ_MAIN M WHERE M.TYPEID = 2100 "
        "AND '20261112' BETWEEN M.VF_DT AND M.VT_DT AND M.END_DT = '99991231'"
    )
    assert format_sql(sql).splitlines()[2:] == [
        "WHERE M.TYPEID = 2100",
        "  AND '20261112' BETWEEN M.VF_DT AND M.VT_DT",
        "  AND M.END_DT = '99991231'",
    ]


# 문제 4: 긴 SELECT 목록은 한 줄에 컬럼 하나. 괄호·별칭 안의 쉼표에서는 나누지 않는다
def test_long_select_list() -> None:
    sql = (
        'SELECT L0.BIZCODE "카드상품", L0.OBJ_NAME "카드상품명", L1.BIZCODE "연회비", '
        'COALESCE(D1.CHAR01, \'\') "국내, 전용" FROM OBJ_MAIN L0'
    )
    assert format_sql(sql) == (
        "SELECT\n"
        '    L0.BIZCODE "카드상품",\n'
        '    L0.OBJ_NAME "카드상품명",\n'
        '    L1.BIZCODE "연회비",\n'
        "    COALESCE(D1.CHAR01, '') \"국내, 전용\"\n"
        "FROM OBJ_MAIN L0"
    )


# 문제 5: 따옴표 문자열 안의 키워드·공백, 룰 변수, LIKE ESCAPE는 그대로 둔다
@pytest.mark.parametrize(
    "condition",
    [
        "M.OBJ_NAME LIKE '%A  AND FROM%' ESCAPE '\\'",
        "M.BIZCODE IN (<$@distinct([상품코드].array)@$>)",
        "M.OBJ_NAME = 'it''s WHERE'",
    ],
)
def test_literals_untouched(condition: str) -> None:
    sql = f"SELECT M.SID FROM OBJ_MAIN M WHERE M.TYPEID = 3100 AND {condition}"
    assert format_sql(sql).splitlines()[-1] == f"  AND {condition}"


# 문제 6: CASE … END와 괄호 안은 나누지 않는다
def test_case_expression_not_split() -> None:
    condition = (
        "CASE WHEN D1.CHAR01 ~ '^-?[0-9]+(\\.[0-9]+)?$' "
        "THEN CAST(D1.CHAR01 AS NUMERIC) END <= 30000"
    )
    sql = f"SELECT L0.SID FROM OBJ_MAIN L0 WHERE L0.TYPEID = 2100 AND {condition}"
    assert format_sql(sql).splitlines()[-1] == f"  AND {condition}"


# 문제 7: 공백만 바뀐다. 두 번 정렬해도 결과가 같다
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT M.SID FROM OBJ_MAIN M WHERE M.TYPEID = 1  AND   M.BIZCODE = 'A  B'",
        'SELECT L0.BIZCODE "보험상품", L0.OBJ_NAME "보험상품명", L1.BIZCODE "주계약", '
        'D1.CHAR01 "보험가입금액" FROM OBJ_MAIN L0 JOIN OBJ_INV I1 ON I1.SID = L0.SID '
        "WHERE L0.TYPEID = 1100 ORDER BY L0.BIZCODE, L1.BIZCODE",
    ],
)
def test_only_whitespace_changes_and_idempotent(sql: str) -> None:
    once = format_sql(sql)
    assert squash(once) == squash(sql)
    assert "'A  B'" in once or "A  B" not in sql
    assert format_sql(once) == once
