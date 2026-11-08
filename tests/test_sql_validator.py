"""SQL 검증기 문제지.

SqlValidator는 만들어진 SQL이 안전한지 실행 전에 확인한다.
[스프링] 쿼리 실행 직전에 거는 Validator. 통과하면 아무것도 안 하고, 위반이면 예외를 던진다.

구현할 곳: src/text_to_sql/services/sql_validator.py
- class SqlValidator: __init__(self, allowed_tables: set[str])
- SqlValidator.validate(sql: str) -> None
  - 통과하면 None, 위반이면 SqlValidationError (src/text_to_sql/errors.py)
  - 오류 메시지는 반드시 "[SV-02] ..."처럼 위반한 규칙 ID로 시작한다.
    (엉뚱한 규칙에 걸려서 통과하는 구현을 막고, 로그에서 원인을 바로 찾기 위해서다)

규칙 SV-01~SV-09는 docs/05_구현명세서.md 2.4절. 이 문제지는 "일부러 우회하려는 SQL"도 다룬다.
각 문제가 어떤 함정을 노리는지 주석에 적어 두었으니, 막힐 때 힌트로 읽는다.
"""

import re

import pytest

from text_to_sql.domain.intent import Intent
from text_to_sql.errors import SqlValidationError
from text_to_sql.repositories.metadata_repository import (
    load_common_metadata,
    load_site_profile,
)
from text_to_sql.services.sql_builder import SqlBuilder
from text_to_sql.services.sql_validator import SqlValidator

ALLOWED = {"OBJ_MAIN", "OBJ_INV", "OBJ_DETAIL"}
OK_SQL = "SELECT M.SID, M.BIZCODE, M.OBJ_NAME FROM OBJ_MAIN M WHERE M.TYPEID = :type_id"


def make_validator() -> SqlValidator:
    return SqlValidator(ALLOWED)


def assert_blocked(sql: str, rule: str) -> None:
    """SqlValidationError가 나고, 메시지가 해당 규칙 ID로 시작하는지 확인한다."""
    with pytest.raises(SqlValidationError, match=rf"^{re.escape(f'[{rule}]')}"):
        make_validator().validate(sql)


# ───────────── 통과해야 하는 SQL ─────────────


# 문제 1: 올바른 SELECT는 통과한다 (예외가 나지 않으면 통과)
def test_valid_select_passes() -> None:
    make_validator().validate(OK_SQL)


# 문제 2: 빌더가 만든 SQL은 전부 통과한다 (lookup 3종, list, structure 2방향)
@pytest.mark.parametrize(
    "intent",
    [
        Intent(kind="lookup", target_type="기본상품", code="BP001"),
        Intent(kind="lookup", target_type="기본상품", name_keyword="약정"),
        Intent(kind="lookup", target_type="기본상품", code="BP001", base_date="20260101"),
        Intent(kind="list", target_type="기본상품"),
        Intent(kind="structure", target_type="기본상품", code="BP001", direction="children"),
        Intent(kind="structure", target_type="기본상품", code="BP001", direction="parents"),
    ],
)
def test_builder_output_passes(intent: Intent) -> None:
    common = load_common_metadata("src/text_to_sql/metadata/common.json")
    profile = load_site_profile("src/text_to_sql/metadata/sites/sample.json")
    built = SqlBuilder(common, profile).build(intent)
    make_validator().validate(built.sql)


# 문제 3 (SV-01): 앞 공백·개행·탭과 소문자 select도 통과한다
def test_leading_whitespace_and_lowercase_select_passes() -> None:
    make_validator().validate("  \n\t select M.SID\nfrom OBJ_MAIN M")


# 문제 4: 바인드 변수는 IN 목록에서도, 여러 개여도 통과한다
def test_bind_parameters_pass() -> None:
    make_validator().validate(
        "SELECT M.SID FROM OBJ_MAIN M WHERE M.TYPEID IN (:a, :b) AND :d BETWEEN M.VF_DT AND M.VT_DT"
    )


# 문제 5 (SV-06): 허용 테이블끼리 JOIN은 통과한다. LEFT/INNER 같은 종류가 붙어도 마찬가지
def test_allowed_joins_pass() -> None:
    make_validator().validate(
        "SELECT M.SID FROM OBJ_MAIN M "
        "INNER JOIN OBJ_INV I ON I.SID = M.SID "
        "LEFT JOIN OBJ_DETAIL D ON D.SID = M.SID"
    )


# 문제 6 (SV-06): 테이블 이름 대소문자는 무시한다
def test_table_name_is_case_insensitive() -> None:
    make_validator().validate("select m.sid from obj_main m")


# 문제 7 (SV-06): FROM 뒤가 서브쿼리 "(" 인 경우, 안쪽 테이블이 허용이면 통과한다
# 함정: "FROM 다음 단어"만 보는 구현은 "("를 테이블로 착각해 오탐한다
def test_derived_table_with_allowed_inner_passes() -> None:
    make_validator().validate("SELECT T.SID FROM (SELECT M.SID FROM OBJ_MAIN M) T")


# 문제 8 (SV-06): FROM·JOIN 사이를 개행·탭으로 띄워도 통과한다
def test_newline_and_tab_between_keyword_and_table_pass() -> None:
    make_validator().validate("SELECT M.SID FROM\n\tOBJ_MAIN M\nJOIN\tOBJ_INV I ON I.SID = M.SID")


# 문제 9 (SV-04): 단어의 일부일 뿐이면 차단하지 않는다
# 함정: \b 없이 포함 여부로 검사하면 OFFSET·ASSET의 SET, UPDATED_AT의 UPDATE, REDO의 DO가 걸린다
def test_keyword_inside_identifier_passes() -> None:
    make_validator().validate(
        "SELECT M.ASSET, M.UPDATED_AT, M.REDO, M.CALLER, M.DOC FROM OBJ_MAIN M OFFSET 5"
    )


# 문제 10 (SV-07): 문자열 리터럴 안의 키워드·FROM은 차단하지 않는다
def test_keyword_and_from_inside_literal_pass() -> None:
    make_validator().validate(
        "SELECT M.SID FROM OBJ_MAIN M WHERE M.END_DT = '99991231' "
        "AND M.OBJ_NAME = 'SET DELETE pg_sleep FROM USERS'"
    )


# 문제 11 (SV-07): 리터럴 안의 작은따옴표 이스케이프('')를 올바르게 처리한다
# 함정: r"'[^']*'" 로 지우면 'it''s DELETE'가 'it' + s DELETE + '' 로 쪼개져 DELETE가 드러난다
# 힌트: r"'(?:[^']|'')*'"
def test_escaped_quote_inside_literal_passes() -> None:
    make_validator().validate("SELECT M.SID FROM OBJ_MAIN M WHERE M.OBJ_NAME = 'it''s DELETE me'")


# 문제 12: 같은 검증기로 여러 번 불러도 결과가 같다 (내부 상태를 남기지 않는다)
def test_validator_is_stateless() -> None:
    v = make_validator()
    v.validate(OK_SQL)
    assert_blocked("DELETE FROM OBJ_MAIN", "SV-01")
    v.validate(OK_SQL)


# ───────────── SV-01: SELECT로 시작 ─────────────


# 문제 13: SELECT로 시작하지 않으면 차단. 빈 문자열·공백만 있는 문자열도 차단
@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM OBJ_MAIN",
        "UPDATE OBJ_MAIN SET OBJ_NAME = 'x'",
        "INSERT INTO OBJ_MAIN (SID) VALUES (1)",
        "DROP TABLE OBJ_MAIN",
        "",
        "   \n\t ",
    ],
)
def test_non_select_is_blocked(sql: str) -> None:
    assert_blocked(sql, "SV-01")


# 문제 14: WITH(CTE)로 시작하는 문장도 차단 (이번 PoC는 SELECT로 시작하는 것만 허용)
def test_with_clause_is_blocked() -> None:
    assert_blocked("WITH x AS (SELECT 1 FROM OBJ_MAIN) SELECT * FROM x", "SV-01")


# 문제 15: SELECT로 시작하는 척하는 단어도 차단 (SELECTED, SELECT1)
# 함정: sql.upper().startswith("SELECT")만 쓰면 통과해 버린다
@pytest.mark.parametrize("sql", ["SELECTED FROM OBJ_MAIN", "SELECT1 FROM OBJ_MAIN"])
def test_select_lookalike_is_blocked(sql: str) -> None:
    assert_blocked(sql, "SV-01")


# ───────────── SV-02: 세미콜론 ─────────────


# 문제 16: 세미콜론은 모두 차단 (이어 붙이기, 끝의 세미콜론 하나, 리터럴 안의 세미콜론까지)
# 리터럴 안까지 막는 이유: 값은 바인드 변수로 넘기므로 SQL 본문에 ';'가 있을 이유가 없다 (보수적)
@pytest.mark.parametrize(
    "sql",
    [
        OK_SQL + "; DROP TABLE OBJ_MAIN",
        OK_SQL + ";",
        "SELECT M.SID FROM OBJ_MAIN M WHERE M.OBJ_NAME = 'a;b'",
    ],
)
def test_semicolon_is_blocked(sql: str) -> None:
    assert_blocked(sql, "SV-02")


# ───────────── SV-03: 주석 ─────────────


# 문제 17: 주석 차단 (--, /* */, 개행으로 끝나는 --). 리터럴 안 '--'도 문제 16과 같은 이유로 차단
@pytest.mark.parametrize(
    "sql",
    [
        OK_SQL + " -- AND 1=1",
        "SELECT M.SID /* 숨김 */ FROM OBJ_MAIN M",
        "SELECT M.SID FROM OBJ_MAIN M --\nWHERE 1=1",
        "SELECT M.SID FROM OBJ_MAIN M WHERE M.OBJ_NAME = 'a--b'",
    ],
)
def test_comment_is_blocked(sql: str) -> None:
    assert_blocked(sql, "SV-03")


# ───────────── SV-04: 금지 키워드 ─────────────

FORBIDDEN = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "MERGE", "GRANT", "EXEC", "CALL", "COPY", "DO", "SET", "INTO",
]  # fmt: skip


# 문제 18: 금지 키워드 15개가 SELECT 안 어디에 있어도 차단 (대소문자·섞어 쓰기 포함)
# 함정: 목록에서 하나를 빠뜨리면 그 키워드만 실패한다. 15개를 모두 돌린다
@pytest.mark.parametrize("keyword", FORBIDDEN)
@pytest.mark.parametrize("variant", [str.upper, str.lower, str.title])
def test_every_forbidden_keyword_is_blocked(keyword: str, variant) -> None:  # type: ignore[no-untyped-def]
    word = variant(keyword)
    assert_blocked(f"SELECT M.SID FROM OBJ_MAIN M WHERE M.X = 1 AND {word} = 1", "SV-04")


# 문제 19: 서브쿼리·UNION·SELECT INTO·FOR UPDATE 같은 "SELECT인 척" 우회도 차단
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT M.SID FROM OBJ_MAIN M WHERE M.SID IN (DELETE FROM OBJ_MAIN)",
        "SELECT M.SID INTO NEW_TABLE FROM OBJ_MAIN M",
        "SELECT M.SID FROM OBJ_MAIN M FOR UPDATE",
        "SELECT M.SID FROM OBJ_MAIN M UNION SELECT 1 FROM OBJ_MAIN WHERE 1=1 GRANT",
    ],
)
def test_select_disguised_write_is_blocked(sql: str) -> None:
    assert_blocked(sql, "SV-04")


# 문제 20: 키워드 사이를 개행·탭으로 띄워도 단어로 인식해 차단
def test_keyword_separated_by_newline_is_blocked() -> None:
    assert_blocked(
        "SELECT M.SID FROM OBJ_MAIN M\nWHERE\tM.SID IN (\nDELETE\nFROM OBJ_MAIN)", "SV-04"
    )


# ───────────── SV-05: 위험 함수 ─────────────


# 문제 21: 위험 함수 차단. 목록에 있는 것(lo_import 등)과 pg_ 로 시작하는 함수 전체, 대소문자 무시
@pytest.mark.parametrize(
    "func",
    [
        "pg_sleep(10)",
        "PG_READ_FILE('/etc/passwd')",
        "pg_ls_dir('.')",
        "pg_terminate_backend(1)",
        "lo_import('/etc/passwd')",
        "LO_EXPORT(1, '/tmp/x')",
        "dblink('host=x', 'select 1')",
        "Dblink_Exec('x', 'y')",
    ],
)
def test_dangerous_function_is_blocked(func: str) -> None:
    assert_blocked(f"SELECT {func} FROM OBJ_MAIN M", "SV-05")


# 문제 22: 위험 함수 이름이 문자열 리터럴 안에만 있으면 통과 (SV-07)
def test_dangerous_function_name_inside_literal_passes() -> None:
    make_validator().validate("SELECT M.SID FROM OBJ_MAIN M WHERE M.OBJ_NAME = 'pg_sleep dblink'")


# ───────────── SV-06: 허용 테이블 ─────────────


# 문제 23: 허용 목록에 없는 테이블 차단 (FROM, JOIN, 서브쿼리, UNION 뒤, 시스템 테이블)
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT U.ID FROM USERS U",
        "SELECT M.SID FROM OBJ_MAIN M JOIN USERS U ON U.ID = M.SID",
        "SELECT M.SID FROM OBJ_MAIN M LEFT JOIN USERS U ON U.ID = M.SID",
        "SELECT M.SID FROM OBJ_MAIN M WHERE M.SID IN (SELECT X.SID FROM SECRET X)",
        "SELECT M.SID FROM OBJ_MAIN M UNION SELECT S.ID FROM SECRET S",
        "SELECT T.SID FROM (SELECT S.SID FROM SECRET S) T",
        "SELECT 1 FROM pg_user",
        "SELECT 1 FROM information_schema",
    ],
)
def test_unknown_table_is_blocked(sql: str) -> None:
    assert_blocked(sql, "SV-06")


# 문제 24: 쉼표로 이어 붙인 테이블도 검사한다
# 함정: FROM 바로 뒤 한 단어만 보면 "OBJ_MAIN M, USERS U"의 USERS를 놓친다
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT M.SID FROM OBJ_MAIN M, USERS U",
        "SELECT M.SID FROM OBJ_MAIN M, OBJ_INV I, USERS U WHERE I.SID = M.SID",
        "SELECT M.SID FROM OBJ_MAIN, USERS",
    ],
)
def test_comma_joined_unknown_table_is_blocked(sql: str) -> None:
    assert_blocked(sql, "SV-06")


# 문제 25: 쉼표로 이어 붙인 테이블이 모두 허용이면 통과 (과잉 차단 방지)
def test_comma_joined_allowed_tables_pass() -> None:
    make_validator().validate("SELECT M.SID FROM OBJ_MAIN M, OBJ_INV I WHERE I.SID = M.SID")


# 문제 26: 따옴표 식별자("USERS")와 스키마 접두사(public.OBJ_MAIN)는 차단
# 함정: FROM\s+(\w+) 만 쓰면 따옴표 식별자는 매칭이 안 돼 아무 검사도 없이 통과한다.
# 스키마 접두사는 허용 테이블이어도 차단 (PoC는 이름만 쓴 테이블 참조만 허용, 규칙을 단순하게 유지)
@pytest.mark.parametrize(
    "sql",
    [
        'SELECT 1 FROM "USERS"',
        'SELECT 1 FROM "OBJ_MAIN"',
        "SELECT 1 FROM public.OBJ_MAIN",
        "SELECT 1 FROM pg_catalog.pg_user",
    ],
)
def test_quoted_or_qualified_table_is_blocked(sql: str) -> None:
    assert_blocked(sql, "SV-06")


# 문제 27: 허용 테이블이 빈 집합이면 어떤 SELECT도 차단
def test_empty_allowed_tables_blocks_everything() -> None:
    with pytest.raises(SqlValidationError, match=r"^\[SV-06\]"):
        SqlValidator(set()).validate(OK_SQL)


# 문제 28: 허용 테이블 이름을 소문자로 넘겨도 동작한다 (비교는 대소문자 무시)
def test_allowed_tables_given_in_lowercase() -> None:
    SqlValidator({"obj_main"}).validate("SELECT M.SID FROM OBJ_MAIN M")


# ───────────── SV-08: 따옴표 짝 ─────────────


# 문제 29: 닫히지 않은 작은따옴표는 차단
# 함정: 짝 없는 따옴표 뒤의 DELETE를 "리터럴 안"으로 착각해 통과시킬 수 있다
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT M.SID FROM OBJ_MAIN M WHERE M.OBJ_NAME = 'abc",
        "SELECT M.SID FROM OBJ_MAIN M WHERE M.OBJ_NAME = 'a' OR 'b",
        "SELECT M.SID FROM OBJ_MAIN M WHERE M.OBJ_NAME = ''' DELETE",
    ],
)
def test_unterminated_quote_is_blocked(sql: str) -> None:
    assert_blocked(sql, "SV-08")


# ───────────── 규칙 우선순위 ─────────────


# 문제 30: 위반이 여러 개면 검사 순서가 빠른 규칙으로 보고한다
# 검사 순서: SV-09(솔루션 주석 짝) → (주석 제거) → SV-01 → SV-02 → SV-03 → SV-08(따옴표 짝)
#           → (리터럴 제거) → SV-04 → SV-05 → SV-06
# SV-08이 SV-04보다 앞인 이유: 따옴표 짝이 틀리면 "리터럴을 지운 사본"을 믿을 수 없기 때문이다
@pytest.mark.parametrize(
    ("sql", "rule"),
    [
        ("<$@ 열기만 DELETE FROM USERS; --", "SV-09"),
        ("DELETE FROM USERS; --", "SV-01"),
        ("SELECT 1 FROM USERS; --", "SV-02"),
        ("SELECT 1 FROM USERS --", "SV-03"),
        ("SELECT 'abc DELETE FROM USERS", "SV-08"),
        ("SELECT 1 FROM USERS WHERE DELETE = 1", "SV-04"),
        ("SELECT pg_sleep(1) FROM USERS", "SV-05"),
    ],
)
def test_first_violated_rule_is_reported(sql: str, rule: str) -> None:
    assert_blocked(sql, rule)


# ───────────── SV-09: 솔루션 주석 <$@ ... @$> ─────────────
# 솔루션 권장 주석 형식. 주석이 필요하면 이 형식만 쓴다 (--, /* */는 계속 차단, SV-03).
# 검증기는 주석을 먼저 공백 한 칸으로 바꾼 뒤 나머지 규칙을 검사한다.


# 문제 31: SQL 앞에 붙은 솔루션 주석은 통과한다 (SV-01이 주석 다음 첫 단어를 본다)
def test_solution_comment_header_passes() -> None:
    make_validator().validate("<$@ 기본상품 단건 조회 @$>\n" + OK_SQL)


# 문제 32: 주석 안의 내용은 검사하지 않는다 (키워드, 세미콜론, 따옴표, --, 테이블 이름)
# 함정: 주석을 지우기 전에 SV-02·03·08을 검사하면 주석 안의 ; ' --에 걸린다
def test_solution_comment_content_is_ignored() -> None:
    make_validator().validate("SELECT M.SID <$@ DELETE; it's -- FROM USERS @$> FROM OBJ_MAIN M")


# 문제 33: 여러 줄 주석, 주석 여러 개도 통과한다
def test_multiline_and_multiple_solution_comments_pass() -> None:
    make_validator().validate(
        "<$@ 첫째 줄\n둘째 줄 @$>\nSELECT M.SID <$@ 출력 @$> FROM OBJ_MAIN M <$@ 끝 @$>"
    )


# 문제 34: 주석은 공백 한 칸으로 바뀐다 (붙어 있던 단어가 이어 붙지 않는다)
# 함정: 빈 문자열로 지우면 FROM<$@x@$>USERS가 FROMUSERS가 되어 테이블 검사를 빠져나간다
def test_solution_comment_is_replaced_with_space() -> None:
    assert_blocked("SELECT M.SID FROM<$@x@$>USERS U", "SV-06")
    make_validator().validate("SELECT M.SID FROM<$@x@$>OBJ_MAIN M")


# 문제 35: 짝이 맞지 않는 솔루션 주석은 차단 (열기만, 닫기만, 중첩)
# 중첩 <$@ a <$@ b @$> c @$>는 짧게 지우면 " c @$>"가 남는다 → 남은 표시가 있으면 차단
@pytest.mark.parametrize(
    "sql",
    [
        "<$@ 열기만 SELECT M.SID FROM OBJ_MAIN M",
        "SELECT M.SID FROM OBJ_MAIN M 닫기만 @$>",
        "SELECT M.SID <$@ a <$@ b @$> c @$> FROM OBJ_MAIN M",
    ],
)
def test_unbalanced_solution_comment_is_blocked(sql: str) -> None:
    assert_blocked(sql, "SV-09")


# 문제 36: 주석 뒤에 SELECT가 아닌 문장이 오면 차단 (주석으로 시작을 숨길 수 없다)
def test_solution_comment_does_not_hide_statement_start() -> None:
    assert_blocked("<$@ 조회 @$> DELETE FROM OBJ_MAIN", "SV-01")


# 문제 37 (SV-05·SV-07): 괄호까지 포함한 함수 모양이 리터럴 안에만 있으면 통과
# 함정: SV-05를 사본(stripped)이 아니라 주석 제거본(text)으로 검사하면 값인 'pg_sleep(1)'을 오탐한다
# (문제 22는 리터럴 안에 괄호가 없어서 이 실수를 잡지 못한다)
@pytest.mark.parametrize(
    "literal",
    ["'pg_sleep(1)'", "'dblink(''host=x'')'", "'LO_IMPORT (x)'"],
)
def test_dangerous_function_call_inside_literal_passes(literal: str) -> None:
    make_validator().validate(f"SELECT M.SID FROM OBJ_MAIN M WHERE M.OBJ_NAME = {literal}")
