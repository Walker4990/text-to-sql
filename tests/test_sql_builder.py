"""SQL 빌더 문제지 (문제 1~10: 단건 조회 lookup, 11~18: 목록 list, 19~26: 구조 structure).

SqlBuilder는 Intent(질문의 의도)를 받아 SQL 문장을 조립한다.
[스프링] @Service 클래스. 생성자로 의존 객체(공통 메타데이터, 사이트 프로필)를 받는다 (생성자 주입).

구현할 곳: src/text_to_sql/services/sql_builder.py
- class SqlBuilder: __init__(self, common: Metadata, profile: SiteProfile)
- SqlBuilder.build(intent: Intent) -> 결과 객체 (속성 sql, params)
  - sql: 값 자리에 :이름 바인드 변수를 쓴 SQL 문자열
    [스프링] NamedParameterJdbcTemplate의 :name, 또는 PreparedStatement의 ?
  - params: 바인드 변수에 들어갈 값 딕셔너리, 예: {"type_id": 901, "code": "BP001"}

이 문제지는 줄 바꿈·공백 차이는 신경 쓰지 않는다. (비교 전에 공백을 한 칸으로 정리한다)
"""

import pytest

from text_to_sql.domain.intent import Intent
from text_to_sql.errors import UnsupportedQuestionError
from text_to_sql.repositories.metadata_repository import (
    Metadata,
    SiteProfile,
    TableProfile,
    load_common_metadata,
    load_site_profile,
)
from text_to_sql.services.sql_builder import SqlBuilder


def make_builder() -> SqlBuilder:
    common = load_common_metadata("src/text_to_sql/metadata/common.json")
    profile = load_site_profile("src/text_to_sql/metadata/sites/sample.json")
    return SqlBuilder(common, profile)


def flat(sql: str) -> str:
    """줄 바꿈과 여러 칸 공백을 한 칸으로 정리한다 (비교하기 쉽게)."""
    return " ".join(sql.split())


# 문제 1: "BP001 기본상품 알려줘" → OBJ_MAIN을 TYPEID와 코드로 조회하는 SQL
# 기본상품의 TYPEID는 사이트 프로필에서 찾는다 (sample.json: 901)
def test_lookup_by_code() -> None:
    result = make_builder().build(Intent(kind="lookup", target_type="기본상품", code="BP001"))
    sql = flat(result.sql)
    select_from = "SELECT M.SID, M.BIZCODE, M.OBJ_NAME FROM OBJ_MAIN M"
    assert select_from in sql
    assert "M.TYPEID = :type_id" in sql
    assert "M.BIZCODE = :code" in sql
    assert result.params["type_id"] == 901
    assert result.params["code"] == "BP001"


# 문제 2: 최신 버전 조건(END_DT = '99991231')이 항상 붙어야 한다
# 이 조건이 빠지면 같은 오브젝트가 버전 수만큼 중복으로 조회된다
def test_lookup_has_latest_version_condition() -> None:
    result = make_builder().build(Intent(kind="lookup", target_type="기본상품", code="BP001"))
    assert "M.END_DT = '99991231'" in flat(result.sql)


# 문제 3: 기준정보를 관리하는 솔루션이라 결과를 잘라내지 않는다 (행 수 제한을 붙이지 않는다)
def test_lookup_has_no_row_limit() -> None:
    result = make_builder().build(Intent(kind="lookup", target_type="기본상품", code="BP001"))
    assert "FETCH" not in flat(result.sql)


# 문제 4: 별칭("옵션")으로 말해도 부가옵션의 TYPEID(903)로 조회한다
def test_lookup_by_alias() -> None:
    result = make_builder().build(Intent(kind="lookup", target_type="옵션", code="AO001"))
    assert result.params["type_id"] == 903


# 문제 5: 이름 검색어로 조회하면 OBJ_NAME LIKE 조건이 붙는다 (앞뒤에 % 를 붙여 부분 일치)
def test_lookup_by_name_keyword() -> None:
    intent = Intent(kind="lookup", target_type="기본상품", name_keyword="프리미엄")
    result = make_builder().build(intent)
    sql = flat(result.sql)
    assert "M.OBJ_NAME LIKE :name_keyword" in sql
    assert result.params["name_keyword"] == "%프리미엄%"
    assert "M.BIZCODE = :code" not in sql


# 문제 6 (보안): 사용자가 입력한 값은 SQL 문장 안에 직접 들어가면 안 된다 (바인드 변수로만)
# [스프링] 문자열을 이어 붙인 쿼리 대신 PreparedStatement를 쓰는 이유와 같다
def test_user_values_are_not_in_sql_text() -> None:
    result = make_builder().build(Intent(kind="lookup", target_type="기본상품", code="BP001"))
    assert "BP001" not in result.sql


# 문제 7: 없는 유형 이름이면 UnsupportedQuestionError
def test_unknown_type_is_unsupported() -> None:
    with pytest.raises(UnsupportedQuestionError):
        make_builder().build(Intent(kind="lookup", target_type="없는유형", code="X1"))


# 문제 8: 지원하지 않는 의도(unsupported)는 SQL을 만들지 않고 UnsupportedQuestionError
def test_unsupported_intent_raises() -> None:
    with pytest.raises(UnsupportedQuestionError):
        make_builder().build(Intent(kind="unsupported", reason="가격 조회는 지원하지 않습니다"))


# 문제 9: 기준일자가 있으면 적용기간 조건(:base_dt BETWEEN M.VF_DT AND M.VT_DT)이 붙고,
# 없으면 붙지 않는다
def test_lookup_with_base_date() -> None:
    builder = make_builder()
    with_date = builder.build(
        Intent(kind="lookup", target_type="기본상품", code="BP001", base_date="20261102")
    )
    assert ":base_dt BETWEEN M.VF_DT AND M.VT_DT" in flat(with_date.sql)
    assert with_date.params["base_dt"] == "20261102"

    without_date = builder.build(Intent(kind="lookup", target_type="기본상품", code="BP001"))
    assert "BETWEEN" not in flat(without_date.sql)


# 문제 10: 최신 버전 조건은 코드에 박아 두지 않고 공통 메타데이터에서 읽어야 한다
# (메타데이터를 바꾸면 SQL이 바뀌어야 한다)
def test_conditions_come_from_metadata() -> None:
    common = Metadata(
        dialect="postgresql",
        max_rows=5,
        tables={
            "OBJ_MAIN": TableProfile(
                name="OBJ_MAIN", label="x", version_column="DEL_DT", version_value="X"
            )
        },
    )
    profile = SiteProfile(site="x", types={"기본상품": 1}, aliases={"기본상품": "기본상품"})
    result = SqlBuilder(common, profile).build(
        Intent(kind="lookup", target_type="기본상품", code="BP001")
    )
    sql = flat(result.sql)
    assert "M.DEL_DT = 'X'" in sql
    assert result.params["type_id"] == 1


# ---------------------------------------------------------------------------
# T2 목록 조회 (list): "부가옵션 목록 보여줘"
# T1과 같은 틀에, 코드 조건 없이 유형 전체를 조회하고 정렬을 붙인다.
# ---------------------------------------------------------------------------


# 문제 11: 유형 전체를 조회한다. 유형(TYPEID) 조건은 있고 코드(BIZCODE) 조건은 없다
def test_list_by_type() -> None:
    result = make_builder().build(Intent(kind="list", target_type="부가옵션"))
    sql = flat(result.sql)
    assert "FROM OBJ_MAIN M" in sql
    assert "M.TYPEID = :type_id" in sql
    assert "M.BIZCODE = :code" not in sql
    assert result.params["type_id"] == 903
    assert "code" not in result.params


# 문제 12: 목록은 BIZCODE 순으로 정렬한다. 정렬은 SQL의 맨 끝에 온다
# (DISP_ORDER는 PF에서 화면 표시 순서라 DB 규칙에서는 거의 쓰이지 않아 정렬 기준에서 뺐다)
def test_list_is_ordered() -> None:
    result = make_builder().build(Intent(kind="list", target_type="부가옵션"))
    assert flat(result.sql).endswith("ORDER BY M.BIZCODE")


# 문제 13: 목록에도 최신 버전 조건이 항상 붙는다
def test_list_has_latest_version_condition() -> None:
    result = make_builder().build(Intent(kind="list", target_type="부가옵션"))
    assert "M.END_DT = '99991231'" in flat(result.sql)


# 문제 14: 이름 검색어가 있으면 이름 LIKE 조건으로 목록을 좁힌다
def test_list_with_name_keyword() -> None:
    intent = Intent(kind="list", target_type="부가옵션", name_keyword="기본")
    result = make_builder().build(intent)
    assert "M.OBJ_NAME LIKE :name_keyword" in flat(result.sql)
    assert result.params["name_keyword"] == "%기본%"


# 문제 15: 기준일자가 있을 때만 적용기간 조건이 붙는다 (T1과 같은 규칙)
def test_list_with_base_date() -> None:
    builder = make_builder()
    with_date = builder.build(Intent(kind="list", target_type="부가옵션", base_date="20261102"))
    assert ":base_dt BETWEEN M.VF_DT AND M.VT_DT" in flat(with_date.sql)

    without_date = builder.build(Intent(kind="list", target_type="부가옵션"))
    assert "BETWEEN" not in flat(without_date.sql)


# 문제 16: 없는 유형이면 목록도 UnsupportedQuestionError
def test_list_unknown_type_is_unsupported() -> None:
    with pytest.raises(UnsupportedQuestionError):
        make_builder().build(Intent(kind="list", target_type="없는유형"))


# 문제 17: 단건 조회(lookup)에는 정렬이 붙지 않는다 (목록에만 붙는다)
def test_lookup_has_no_order_by() -> None:
    result = make_builder().build(Intent(kind="lookup", target_type="기본상품", code="BP001"))
    assert "ORDER BY" not in flat(result.sql)


# 문제 18: 목록에 code가 같이 들어와도 무시한다 (목록은 유형 전체가 대상)
def test_list_ignores_code() -> None:
    intent = Intent(kind="list", target_type="부가옵션", code="AO001")
    result = make_builder().build(intent)
    assert "M.BIZCODE = :code" not in flat(result.sql)
    assert "code" not in result.params


# ---------------------------------------------------------------------------
# T3·T4 구조 조회 (structure): 오브젝트 사이의 연결 관계
# 세 테이블을 JOIN 한다: OBJ_MAIN P(상위) → OBJ_INV I(연결) → OBJ_MAIN C(하위)
#   FROM OBJ_MAIN P JOIN OBJ_INV I ON I.SID = P.SID JOIN OBJ_MAIN C ON C.SID = I.SUBSID
# - children (T3): "BP001에 연결된 서비스" → 기준 오브젝트가 P, 결과는 C
# - parents  (T4): "AO001이 속한 기본상품"   → 기준 오브젝트가 C, 결과는 P
# 결과 컬럼은 결과 쪽 별칭으로: SID, BIZCODE, OBJ_NAME
# (TYPEID, END_DT, VF_DT, VT_DT 등은 출력하지 않고 WHERE에서만 쓴다)
# ---------------------------------------------------------------------------

JOIN_CLAUSE = "FROM OBJ_MAIN P JOIN OBJ_INV I ON I.SID = P.SID JOIN OBJ_MAIN C ON C.SID = I.SUBSID"


def children_intent() -> Intent:
    return Intent(kind="structure", target_type="기본상품", code="BP001", direction="children")


def parents_intent() -> Intent:
    return Intent(kind="structure", target_type="부가옵션", code="AO001", direction="parents")


# 문제 19 (T3): children → P를 기준(유형+코드)으로 찾고 결과는 C 컬럼
def test_structure_children() -> None:
    result = make_builder().build(children_intent())
    sql = flat(result.sql)
    select = "SELECT C.SID, C.BIZCODE, C.OBJ_NAME"
    assert sql.startswith(f"{select} {JOIN_CLAUSE}")
    assert "P.TYPEID = :type_id" in sql
    assert "P.BIZCODE = :code" in sql
    assert result.params["type_id"] == 901
    assert result.params["code"] == "BP001"


# 문제 20 (T4): parents → C를 기준(유형+코드)으로 찾고 결과는 P 컬럼
def test_structure_parents() -> None:
    result = make_builder().build(parents_intent())
    sql = flat(result.sql)
    select = "SELECT P.SID, P.BIZCODE, P.OBJ_NAME"
    assert sql.startswith(f"{select} {JOIN_CLAUSE}")
    assert "C.TYPEID = :type_id" in sql
    assert "C.BIZCODE = :code" in sql
    assert result.params["type_id"] == 903
    assert result.params["code"] == "AO001"


# 문제 21: 세 테이블(P, I, C) 모두에 최신 버전 조건이 붙는다
# 하나라도 빠지면 이전 버전이 섞여 나온다
def test_structure_has_latest_version_condition_on_all_tables() -> None:
    for intent in (children_intent(), parents_intent()):
        sql = flat(make_builder().build(intent).sql)
        assert "P.END_DT = '99991231'" in sql
        assert "I.END_DT = '99991231'" in sql
        assert "C.END_DT = '99991231'" in sql


# 문제 22: 관계 종류는 기본 관계(INV_TYPE = 1)로 고정한다. 값은 바인드 변수로 전달한다
def test_structure_uses_basic_relation() -> None:
    result = make_builder().build(children_intent())
    assert "I.INV_TYPE = :inv_type" in flat(result.sql)
    assert result.params["inv_type"] == 1


# 문제 23: 결과는 결과 쪽 오브젝트의 BIZCODE 순으로 정렬한다 (children은 C, parents는 P)
def test_structure_is_ordered() -> None:
    builder = make_builder()
    assert flat(builder.build(children_intent()).sql).endswith("ORDER BY C.BIZCODE")
    assert flat(builder.build(parents_intent()).sql).endswith("ORDER BY P.BIZCODE")


# 문제 24 (보안): 사용자가 입력한 코드는 SQL 문장 안에 직접 들어가면 안 된다
def test_structure_user_values_are_not_in_sql_text() -> None:
    result = make_builder().build(children_intent())
    assert "BP001" not in result.sql


# 문제 25: 없는 유형이면 UnsupportedQuestionError
def test_structure_unknown_type_is_unsupported() -> None:
    intent = Intent(kind="structure", target_type="없는유형", code="X1", direction="children")
    with pytest.raises(UnsupportedQuestionError):
        make_builder().build(intent)


# 문제 26: 테이블마다 자기 버전 조건을 메타데이터에서 읽는다
# (OBJ_MAIN은 END_DT, OBJ_INV는 DEL_DT처럼 서로 다를 수 있다)
def test_structure_version_conditions_come_from_each_table_metadata() -> None:
    common = Metadata(
        dialect="postgresql",
        max_rows=100,
        tables={
            "OBJ_MAIN": TableProfile(
                name="OBJ_MAIN", label="x", version_column="END_DT", version_value="99991231"
            ),
            "OBJ_INV": TableProfile(
                name="OBJ_INV", label="x", version_column="DEL_DT", version_value="X"
            ),
        },
    )
    profile = SiteProfile(site="x", types={"기본상품": 1}, aliases={"기본상품": "기본상품"})
    intent = Intent(kind="structure", target_type="기본상품", code="BP001", direction="children")
    sql = flat(SqlBuilder(common, profile).build(intent).sql)
    assert "P.END_DT = '99991231'" in sql
    assert "C.END_DT = '99991231'" in sql
    assert "I.DEL_DT = 'X'" in sql


# ---------------------------------------------------------------------------
# 테이블 별칭이 현장마다 다를 때 (사이트 프로필의 table_aliases)
# SQL 안의 M, P, I, C가 프로필의 별칭으로 바뀌어야 한다.
# ---------------------------------------------------------------------------


def make_builder_with_aliases(table_aliases: dict[str, str]) -> SqlBuilder:
    common = load_common_metadata("src/text_to_sql/metadata/common.json")
    profile = SiteProfile(
        site="x",
        types={"기본상품": 901, "부가옵션": 903},
        aliases={"기본상품": "기본상품", "부가옵션": "부가옵션"},
        table_aliases=table_aliases,
    )
    return SqlBuilder(common, profile)


# 문제 27: 단건 조회(T1)가 main 별칭을 쓴다
def test_lookup_uses_main_alias() -> None:
    builder = make_builder_with_aliases({"main": "OMA", "parent": "P", "inv": "I", "child": "C"})
    sql = flat(builder.build(Intent(kind="lookup", target_type="기본상품", code="BP001")).sql)
    select = "SELECT OMA.SID, OMA.BIZCODE, OMA.OBJ_NAME"
    assert sql.startswith(f"{select} FROM OBJ_MAIN OMA")
    assert "OMA.TYPEID = :type_id" in sql
    assert "OMA.BIZCODE = :code" in sql
    assert "OMA.END_DT = '99991231'" in sql
    assert " M." not in sql


# 문제 28: 목록(T2)의 정렬과 기준일자 조건도 main 별칭을 쓴다
def test_list_uses_main_alias() -> None:
    builder = make_builder_with_aliases({"main": "OMA", "parent": "P", "inv": "I", "child": "C"})
    intent = Intent(kind="list", target_type="부가옵션", base_date="20261102")
    sql = flat(builder.build(intent).sql)
    assert sql.endswith("ORDER BY OMA.BIZCODE")
    assert ":base_dt BETWEEN OMA.VF_DT AND OMA.VT_DT" in sql
    assert " M." not in sql


# 문제 29: 구조 조회(T3)가 parent, inv, child 별칭을 쓴다
def test_structure_children_uses_custom_aliases() -> None:
    aliases = {"main": "M", "parent": "OMA", "inv": "OI", "child": "OMB"}
    sql = flat(make_builder_with_aliases(aliases).build(children_intent()).sql)
    join = (
        "FROM OBJ_MAIN OMA JOIN OBJ_INV OI ON OI.SID = OMA.SID "
        "JOIN OBJ_MAIN OMB ON OMB.SID = OI.SUBSID"
    )
    select = "SELECT OMB.SID, OMB.BIZCODE, OMB.OBJ_NAME"
    assert sql.startswith(f"{select} {join}")
    assert "OMA.TYPEID = :type_id" in sql
    assert "OMA.BIZCODE = :code" in sql
    assert "OI.INV_TYPE = :inv_type" in sql
    assert "OMA.END_DT = '99991231'" in sql
    assert "OI.END_DT = '99991231'" in sql
    assert "OMB.END_DT = '99991231'" in sql
    assert sql.endswith("ORDER BY OMB.BIZCODE")


# 문제 30: 구조 조회(T4)도 별칭을 바꿔 쓴다 (기준은 child, 결과는 parent)
def test_structure_parents_uses_custom_aliases() -> None:
    aliases = {"main": "M", "parent": "OMA", "inv": "OI", "child": "OMB"}
    sql = flat(make_builder_with_aliases(aliases).build(parents_intent()).sql)
    assert sql.startswith("SELECT OMA.SID, OMA.BIZCODE, OMA.OBJ_NAME FROM")
    assert "OMB.TYPEID = :type_id" in sql
    assert "OMB.BIZCODE = :code" in sql
    assert sql.endswith("ORDER BY OMA.BIZCODE")


# ───────────── 보완 (2026-11-08) ─────────────


# 문제 31: 아직 만들지 않은 질문 종류는 거절한다 (pattern, pattern_catalog)
# (attribute는 T5로 구현했다. tests/test_attribute.py)
# 이전에는 lookup처럼 처리되어 코드 조건 없이 유형 전체를 조회하는 SQL이 조용히 나왔다
@pytest.mark.parametrize("kind", ["pattern", "pattern_catalog"])
def test_not_implemented_kind_is_unsupported(kind: str) -> None:
    intent = Intent.model_validate({"kind": kind, "target_type": "기본상품", "code": "BP001"})
    with pytest.raises(UnsupportedQuestionError):
        make_builder().build(intent)


# 문제 32: 이름 검색어 안의 %, _, \ 는 글자 그대로 찾도록 이스케이프하고 ESCAPE 절을 붙인다
# 이스케이프하지 않으면 "100%"가 와일드카드가 되어 "100"으로 시작하는 모든 이름이 걸린다
@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        # r"..."(raw 문자열): 백슬래시를 글자 그대로 쓴다
        ("100%", r"%100\%%"),
        ("A_B", r"%A\_B%"),
        (r"C:\temp", r"%C:\\temp%"),
        ("프리미엄", "%프리미엄%"),
    ],
)
def test_name_keyword_wildcards_are_escaped(keyword: str, expected: str) -> None:
    result = make_builder().build(
        Intent(kind="lookup", target_type="기본상품", name_keyword=keyword)
    )
    assert r"M.OBJ_NAME LIKE :name_keyword ESCAPE '\'" in flat(result.sql)
    assert result.params["name_keyword"] == expected
