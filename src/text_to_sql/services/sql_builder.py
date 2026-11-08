"""Intent + 메타데이터 → SQL 템플릿 조립.

- T1 lookup, T2 list, T3/T4 structure, T5 attribute, T6 pattern, T7 pattern_catalog
- 모든 테이블에 최신 버전 조건 자동 부착, 값은 바인드 변수, 행 수 제한
- 결과: sql(:name 바인드), params, rendered_sql(복사용)"""

import re
from dataclasses import dataclass

from text_to_sql.domain.intent import Intent
from text_to_sql.errors import UnsupportedQuestionError
from text_to_sql.repositories.metadata_repository import Metadata, SiteProfile

# 템플릿이 있는 질문 종류 (T1 lookup, T2 list, T3·T4 structure)
_IMPLEMENTED_KINDS = frozenset({"lookup", "list", "structure"})


# [스프링] 응답 VO. build()가 돌려주는 결과 묶음
@dataclass
class BuiltSql:
    sql: str
    params: dict[str, object]


def _escape_like(keyword: str) -> str:
    """LIKE 검색어의 \\ % _ 앞에 \\ 를 붙인다.

    \\ 를 먼저 바꿔야 새로 붙인 \\ 가 두 번 바뀌지 않는다."""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlBuilder:
    def __init__(self, common: Metadata, profile: SiteProfile) -> None:
        self.common = common
        self.profile = profile

    def build(self, intent: Intent) -> BuiltSql:
        if intent.kind == "unsupported":
            raise UnsupportedQuestionError("지원하지 않는 질문입니다.")
        # attribute·pattern·pattern_catalog(T5~T7)는 아직 템플릿이 없다.
        # Intent 모델은 허용하므로 여기서 막지 않으면 lookup처럼 처리되어 잘못된 SQL이 나온다
        if intent.kind not in _IMPLEMENTED_KINDS:
            raise UnsupportedQuestionError(f"아직 지원하지 않는 질문 종류입니다: {intent.kind}")
        if intent.kind == "structure":
            return self.build_structure(intent)
        if intent.target_type is None:
            raise UnsupportedQuestionError("유형이 없습니다.")

        type_id = self.profile.find_typeid(intent.target_type)
        table = self.common.find_table("OBJ_MAIN")
        m = self.profile.table_aliases["main"]
        conditions: list[str] = [f"{m}.TYPEID = :type_id"]
        params: dict[str, object] = {"type_id": type_id}

        if intent.code and intent.kind == "lookup":
            conditions.append(f"{m}.BIZCODE = :code")
            params["code"] = intent.code
        elif intent.name_keyword:
            # 검색어 안의 % _ 는 글자 그대로 찾도록 \ 로 이스케이프 (PostgreSQL·Oracle 공통 ESCAPE)
            conditions.append(f"{m}.OBJ_NAME LIKE :name_keyword ESCAPE '\\'")
            params["name_keyword"] = f"%{_escape_like(intent.name_keyword)}%"

        if intent.base_date:
            conditions.append(f":base_dt BETWEEN {m}.VF_DT AND {m}.VT_DT")
            params["base_dt"] = intent.base_date

        conditions.append(f"{m}.{table.version_column} = '{table.version_value}'")
        where = " AND ".join(conditions)
        query = f"SELECT {m}.SID, {m}.BIZCODE, {m}.OBJ_NAME FROM OBJ_MAIN {m} WHERE {where} "
        order_by = ""
        if intent.kind == "list":
            order_by = f"ORDER BY {m}.BIZCODE"

        return BuiltSql(sql=query + order_by, params=params)

    def build_structure(self, intent: Intent) -> BuiltSql:
        if intent.target_type is None:
            raise UnsupportedQuestionError("유형이 없습니다.")
        type_id = self.profile.find_typeid(intent.target_type)
        table1 = self.common.find_table("OBJ_MAIN")
        table2 = self.common.find_table("OBJ_INV")

        aliases = self.profile.table_aliases
        p, c, i = aliases["parent"], aliases["child"], aliases["inv"]
        if intent.direction == "children":
            base, result = p, c
        elif intent.direction == "parents":
            base, result = c, p
        else:
            raise UnsupportedQuestionError("방향이 없습니다.")
        conditions = [
            f"{base}.TYPEID = :type_id",
            f"{base}.BIZCODE = :code",
            f"{i}.INV_TYPE = :inv_type",
        ]
        params: dict[str, object] = {"type_id": type_id, "code": intent.code, "inv_type": 1}

        conditions.append(f"{p}.{table1.version_column} = '{table1.version_value}'")
        conditions.append(f"{i}.{table2.version_column} = '{table2.version_value}'")
        conditions.append(f"{c}.{table1.version_column} = '{table1.version_value}'")

        where = " AND ".join(conditions)
        select = ", ".join(f"{result}.{col}" for col in ("SID", "BIZCODE", "OBJ_NAME"))

        query = (
            f"SELECT {select} "
            f"FROM OBJ_MAIN {p} JOIN OBJ_INV {i} ON {i}.SID = {p}.SID "
            f"JOIN OBJ_MAIN {c} ON {c}.SID = {i}.SUBSID "
            f"WHERE {where} "
            f"ORDER BY {result}.BIZCODE"
        )

        return BuiltSql(sql=query, params=params)


_BIND_PARAM = re.compile(r"(?<!:):(\w+)")


def render_sql(sql: str, params: dict[str, object]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise ValueError(f"params에 없는 바인드 변수입니다.{name}")
        value = params[name]

        if isinstance(value, bool):
            raise ValueError("타입이 잘못되었습니다.")
        elif isinstance(value, str):
            return "'" + value.replace("'", "''") + "'"
        elif isinstance(value, int):
            return str(value)
        else:
            raise ValueError("타입이 잘못되었습니다.")

    return _BIND_PARAM.sub(replace, sql)
