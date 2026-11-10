"""Intent + 메타데이터 → SQL 템플릿 조립.

- T1 lookup, T2 list, T3/T4 structure, T5 attribute, T6 pattern, T7 pattern_catalog
- 모든 테이블에 최신 버전 조건 자동 부착, 값은 바인드 변수, 행 수 제한
- 결과: sql(:name 바인드), params, rendered_sql(복사용)"""

import re
from dataclasses import dataclass

from text_to_sql.domain.intent import Intent
from text_to_sql.errors import UnsupportedQuestionError
from text_to_sql.repositories.metadata_repository import ItemDef, Metadata, SiteProfile

# 템플릿이 있는 질문 종류 (T1 lookup, T2 list, T3·T4 structure, T5 attribute, 계층 hierarchy)
_IMPLEMENTED_KINDS = frozenset({"lookup", "list", "structure", "attribute", "hierarchy"})


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

    def _code_condition(self, alias: str, intent: Intent, params: dict[str, object]) -> str:
        if intent.code_item:
            name = self.profile.find_registered_item(intent.code_item)
            if intent.code_item_multi:
                return f"{alias}.BIZCODE IN (<$@distinct([{name}].array)@$>)"
            return f"{alias}.BIZCODE = <$@[{name}]@$>"
        params["code"] = intent.code
        return f"{alias}.BIZCODE = :code"

    def build(self, intent: Intent) -> BuiltSql:
        if intent.kind == "unsupported":
            raise UnsupportedQuestionError("지원하지 않는 질문입니다.")
        # attribute·pattern·pattern_catalog(T5~T7)는 아직 템플릿이 없다.
        # Intent 모델은 허용하므로 여기서 막지 않으면 lookup처럼 처리되어 잘못된 SQL이 나온다
        if intent.kind not in _IMPLEMENTED_KINDS:
            raise UnsupportedQuestionError(f"아직 지원하지 않는 질문 종류입니다: {intent.kind}")
        if intent.kind == "structure":
            return self.build_structure(intent)
        if intent.kind == "attribute":
            return self.build_attribute(intent)
        if intent.kind == "hierarchy":
            return self.build_hierarchy(intent)
        if intent.target_type is None:
            raise UnsupportedQuestionError("유형이 없습니다.")

        type_id = self.profile.find_typeid(intent.target_type)
        table = self.common.find_table("OBJ_MAIN")
        m = self.profile.table_aliases["main"]
        conditions: list[str] = [f"{m}.TYPEID = :type_id"]
        params: dict[str, object] = {"type_id": type_id}

        if (intent.code or intent.code_item) and intent.kind == "lookup":
            conditions.append(self._code_condition(m, intent, params))
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
        # 관계 종류(INV_TYPE)는 거르지 않는다 (현장 DB 룰 관행, 2026-11-10)
        params: dict[str, object] = {"type_id": type_id}
        conditions = [
            f"{base}.TYPEID = :type_id",
            self._code_condition(base, intent, params),
        ]

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

    def build_attribute(self, intent: Intent) -> BuiltSql:
        if intent.target_type is None:
            raise UnsupportedQuestionError("유형이 없습니다.")
        type_id = self.profile.find_typeid(intent.target_type)
        names = list(dict.fromkeys(intent.attributes or []))
        items = [(name, self.profile.find_item(intent.target_type, name)) for name in names]
        main = self.common.find_table("OBJ_MAIN")
        detail = self.common.find_table("OBJ_DETAIL")
        m = self.profile.table_aliases["main"]
        d = self.profile.table_aliases["detail"]

        # 항목 컬럼·별칭은 사전에서 검증된 값만 들어온다 (바인드 불가 위치라 직접 삽입)
        columns = [f"{m}.BIZCODE", f"{m}.OBJ_NAME"]
        columns += [f'{d}.{item.column} "{name}"' for name, item in items]
        conditions = [f"{m}.TYPEID = :type_id"]
        params: dict[str, object] = {"type_id": type_id}
        if intent.code or intent.code_item:
            conditions.append(self._code_condition(m, intent, params))
        elif intent.name_keyword:
            conditions.append(f"{m}.OBJ_NAME LIKE :name_keyword ESCAPE '\\'")
            params["name_keyword"] = f"%{_escape_like(intent.name_keyword)}%"
        # KIND = 1: 오브젝트 기본 속성 행
        conditions.append(f"{d}.KIND = :detail_kind")
        params["detail_kind"] = 1
        conditions.append(f"{m}.{main.version_column} = '{main.version_value}'")
        conditions.append(f"{d}.{detail.version_column} = '{detail.version_value}'")

        query = (
            f"SELECT {', '.join(columns)} "
            f"FROM OBJ_MAIN {m} JOIN OBJ_DETAIL {d} ON {d}.SID = {m}.SID "
            f"WHERE {' AND '.join(conditions)} "
            f"ORDER BY {m}.BIZCODE"
        )
        return BuiltSql(sql=query, params=params)

    def build_hierarchy(self, intent: Intent) -> BuiltSql:
        """여러 단계 계층 + 단계별 속성 (예: 보험상품 → 주계약 → 특약 → 보장).

        L0은 질문에서 지목한 기준 오브젝트, L1부터 path 순서대로 OBJ_INV를 따라 내려간다.
        각 단계는 유형(TYPEID)으로 거르고, 속성이 있는 단계만 OBJ_DETAIL을 LEFT JOIN 한다.
        """
        if intent.target_type is None or not intent.path:
            raise UnsupportedQuestionError("유형 경로가 없습니다.")
        main = self.common.find_table("OBJ_MAIN")
        inv = self.common.find_table("OBJ_INV")
        detail = self.common.find_table("OBJ_DETAIL")

        level_types = [self.profile.find_alias(name) for name in [intent.target_type, *intent.path]]
        if len(set(level_types)) != len(level_types):
            raise UnsupportedQuestionError("경로에 같은 유형이 두 번 나옵니다.")
        wanted: dict[str, list[str]] | None = None
        if intent.path_attributes is not None:
            wanted = {
                self.profile.find_alias(name): items
                for name, items in intent.path_attributes.items()
            }
            outside = set(wanted) - set(level_types)
            if outside:
                raise UnsupportedQuestionError(f"경로에 없는 유형의 항목입니다: {sorted(outside)}")

        params: dict[str, object] = {}
        columns: list[str] = []
        joins: list[str] = []
        conditions: list[str] = []
        used_labels: set[str] = set()
        for n, type_name in enumerate(level_types):
            # 유형 이름은 SQL 별칭(" ")에 들어가므로 형식을 한 번 더 확인한다
            if not _LABEL.fullmatch(type_name):
                raise UnsupportedQuestionError(f"유형 이름 형식이 잘못되었습니다: {type_name}")
            level, key = f"L{n}", ("type_id" if n == 0 else f"type_id_{n}")
            params[key] = self.profile.find_typeid(type_name)
            columns += [f'{level}.BIZCODE "{type_name}"', f'{level}.OBJ_NAME "{type_name}명"']
            if n == 0:
                conditions.append(f"{level}.TYPEID = :{key}")
                conditions.append(self._code_condition(level, intent, params))
            else:
                link = f"I{n}"
                joins.append(f"JOIN OBJ_INV {link} ON {link}.SID = L{n - 1}.SID")
                joins.append(
                    f"JOIN OBJ_MAIN {level} ON {level}.SID = {link}.SUBSID "
                    f"AND {level}.TYPEID = :{key}"
                )
                conditions.append(f"{link}.{inv.version_column} = '{inv.version_value}'")
            conditions.append(f"{level}.{main.version_column} = '{main.version_value}'")

            items = self._level_items(type_name, n, wanted)
            if items:
                d = f"D{n}"
                params["detail_kind"] = 1
                joins.append(
                    f"LEFT JOIN OBJ_DETAIL {d} ON {d}.SID = {level}.SID "
                    f"AND {d}.KIND = :detail_kind "
                    f"AND {d}.{detail.version_column} = '{detail.version_value}'"
                )
                for name, item in items:
                    # 다른 단계에 같은 항목 이름이 있으면 "유형_항목"으로 구분한다
                    label = name if name not in used_labels else f"{type_name}_{name}"
                    used_labels.add(label)
                    columns.append(f'{d}.{item.column} "{label}"')

        order = ", ".join(f"L{n}.BIZCODE" for n in range(len(level_types)))
        query = (
            f"SELECT {', '.join(columns)} "
            f"FROM OBJ_MAIN L0 {' '.join(joins)} "
            f"WHERE {' AND '.join(conditions)} "
            f"ORDER BY {order}"
        )
        return BuiltSql(sql=query, params=params)

    def _level_items(
        self, type_name: str, level: int, wanted: dict[str, list[str]] | None
    ) -> list[tuple[str, ItemDef]]:
        """단계별로 꺼낼 항목. 항목을 지정하지 않았으면 경로 유형(1단계부터)의 단일값 항목 전부."""
        if wanted is None:
            if level == 0:
                return []
            type_items = self.profile.items.get(type_name, {})
            return [(name, item) for name, item in type_items.items() if not item.multi]
        names = list(dict.fromkeys(wanted.get(type_name, [])))
        return [(name, self.profile.find_item(type_name, name)) for name in names]


# 계층 조회에서 SQL 별칭으로 쓰는 유형 이름 형식
_LABEL = re.compile(r"[가-힣A-Za-z0-9_]{1,30}")

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
