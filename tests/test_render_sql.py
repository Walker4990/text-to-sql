"""복사용 SQL(render_sql) 문제지 (문제 1~9). 명세: docs/05_구현명세서.md 4장 1단계 (RS-01~05).

render_sql은 바인드 변수 SQL(:code)의 자리에 실제 값을 채운 문자열을 만든다.
사용자가 DB 도구(DBeaver 등)에 바로 붙여 넣어 실행해 볼 수 있게 하는 "복사용"이다.
실행용 SQL은 여전히 바인드 변수 버전(BuiltSql.sql)이다.

구현할 곳: src/text_to_sql/services/sql_builder.py (BuiltSql·SqlBuilder와 같은 파일, 모듈 함수)
- def render_sql(sql: str, params: dict[str, object]) -> str
"""

import pytest

from text_to_sql.domain.intent import Intent
from text_to_sql.repositories.metadata_repository import load_common_metadata, load_site_profile
from text_to_sql.services.sql_builder import SqlBuilder, render_sql


# 문제 1 (RS-01): 문자열 값은 작은따옴표로 감싼다
def test_string_value_is_quoted() -> None:
    assert render_sql("WHERE M.BIZCODE = :code", {"code": "BP001"}) == "WHERE M.BIZCODE = 'BP001'"


# 문제 2 (RS-01): 문자열 안의 작은따옴표는 두 개('')로 바꾼다 (SQL 이스케이프)
def test_single_quote_in_string_is_escaped() -> None:
    assert render_sql("WHERE M.OBJ_NAME = :name", {"name": "O'Neil"}) == (
        "WHERE M.OBJ_NAME = 'O''Neil'"
    )


# 문제 3 (RS-02): 정수 값은 따옴표 없이 그대로
def test_int_value_is_not_quoted() -> None:
    assert render_sql("WHERE M.TYPEID = :type_id", {"type_id": 901}) == "WHERE M.TYPEID = 901"


# 문제 4: 바인드 변수가 여러 개여도 각자 제 값으로 바뀐다
def test_multiple_parameters() -> None:
    sql = "WHERE M.TYPEID = :type_id AND M.BIZCODE = :code"
    assert render_sql(sql, {"type_id": 901, "code": "BP001"}) == (
        "WHERE M.TYPEID = 901 AND M.BIZCODE = 'BP001'"
    )


# 문제 5: 이름이 다른 이름의 앞부분과 겹쳐도 정확히 바뀐다
# 함정: str.replace(":code", ...)를 쓰면 :code2의 앞부분 :code까지 바뀌어 'A'2가 된다
def test_parameter_name_prefix_does_not_collide() -> None:
    sql = "WHERE A = :code AND B = :code2"
    assert render_sql(sql, {"code": "A", "code2": "B"}) == "WHERE A = 'A' AND B = 'B'"


# 문제 6 (RS-03): PostgreSQL 형변환 ::date는 바인드 변수가 아니므로 그대로 둔다
def test_postgres_cast_is_not_a_parameter() -> None:
    sql = "WHERE M.VF_DT::date <= :base_dt"
    assert render_sql(sql, {"base_dt": "20260101"}) == "WHERE M.VF_DT::date <= '20260101'"


# 문제 7 (RS-04): params에 없는 이름이 SQL에 있으면 ValueError (빌더 버그이므로 삼키지 않는다)
def test_missing_parameter_raises() -> None:
    with pytest.raises(ValueError):
        render_sql("WHERE M.BIZCODE = :code", {})


# 문제 8 (RS-05): str·int가 아닌 값은 ValueError. bool도 막는다
# 함정: 파이썬에서 bool은 int의 자식이라 isinstance(True, int)가 True다. bool을 먼저 걸러야 한다
@pytest.mark.parametrize("value", [True, None, 1.5, ["a"]])
def test_unsupported_value_type_raises(value: object) -> None:
    with pytest.raises(ValueError):
        render_sql("WHERE X = :v", {"v": value})


# 문제 9: 실제 빌더 출력에 적용하면 바인드 변수가 하나도 남지 않고, 기존 리터럴은 그대로다
@pytest.mark.parametrize(
    ("intent", "expected_parts"),
    [
        (
            Intent(kind="lookup", target_type="기본상품", code="BP001"),
            ["M.TYPEID = 901", "M.BIZCODE = 'BP001'", "M.END_DT = '99991231'"],
        ),
        (
            Intent(kind="list", target_type="부가옵션", name_keyword="약정"),
            ["M.TYPEID = 903", "M.OBJ_NAME LIKE '%약정%'"],
        ),
        (
            Intent(kind="structure", target_type="기본상품", code="BP001", direction="children"),
            ["P.TYPEID = 901", "P.BIZCODE = 'BP001'", "I.END_DT = '99991231'"],
        ),
    ],
)
def test_render_builder_output(intent: Intent, expected_parts: list[str]) -> None:
    common = load_common_metadata("src/text_to_sql/metadata/common.json")
    profile = load_site_profile("src/text_to_sql/metadata/sites/sample.json")
    built = SqlBuilder(common, profile).build(intent)

    rendered = render_sql(built.sql, built.params)

    for part in expected_parts:
        assert part in rendered, part
    assert ":" not in rendered
