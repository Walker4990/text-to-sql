"""MetadataRepository 인터페이스(Protocol)와 JSON 파일 구현.

- load(): 공통 메타데이터 + SITE 프로필을 합쳐 반환
- 나중에 구글 시트 구현체로 교체할 수 있도록 인터페이스를 먼저 정의한다"""

import json
import re
from dataclasses import dataclass, field

from text_to_sql.errors import MetadataError, UnsupportedQuestionError

DEFAULT_TABLE_ALIASES = {"main": "M", "parent": "P", "inv": "I", "child": "C"}


# [스프링] Lombok @Data를 붙인 VO 클래스. 필드는 site, types, aliases 3개
# @dataclass가 생성자/equals/toString을 자동으로 만들어 준다
@dataclass
class SiteProfile:
    site: str
    types: dict[str, int]
    aliases: dict[str, str]
    table_aliases: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TABLE_ALIASES))

    # [스프링] public int findTypeid(String typeName) 인스턴스 메서드 (self = this)
    # 이름이나 별칭으로 TYPEID를 찾는다. 없으면 UnsupportedQuestionError
    def find_typeid(
        self,
        type_name: str,
    ) -> int:
        try:
            type_name = self.find_alias(type_name)
            return self.types[type_name]
        except KeyError as err:
            raise UnsupportedQuestionError(f"Type not found: {type_name}") from err

    # [스프링] 별칭 → 정식 이름. aliases(Map)에서 get 했는데 없으면 KeyError가 난다
    # (자바 Map은 null을 주지만 파이썬 dict는 예외를 던짐)
    def find_alias(self, alias_name: str) -> str:
        try:
            return self.aliases[alias_name]
        except KeyError as err:
            raise UnsupportedQuestionError(f"Alias not found: {alias_name}") from err


@dataclass
class TableProfile:
    name: str
    label: str
    version_column: str
    version_value: str


@dataclass
class Metadata:
    dialect: str
    max_rows: int
    tables: dict[str, TableProfile]

    def find_table(self, name: str) -> TableProfile:
        try:
            return self.tables[name]
        except KeyError as err:
            raise UnsupportedQuestionError(f"Table not found: {name}") from err


# [스프링] 클래스 밖에 있는 독립 함수. 자바라면 SiteProfile.load(path) 정적 팩토리 메서드
# JSON 파일을 읽어 SiteProfile을 만든다. 문제가 있으면 전부 MetadataError로 바꿔 던진다
def load_site_profile(path: str) -> SiteProfile:
    try:
        # [스프링] try-with-resources. 블록이 끝나면 파일이 자동으로 닫힌다
        with open(path, encoding="utf-8") as f:
            # [스프링] Jackson readValue와 같다. 결과는 Map(dict)
            data = json.load(f)
        # [스프링] for (Map.Entry e : aliases.entrySet()). 별칭이 없는 유형을 가리키면 거부
        for alias, type_name in data["aliases"].items():
            if type_name not in data["types"]:
                raise MetadataError(
                    f"Alias '{alias}' points to unknown type '{type_name}' in file: {path}"
                )

        table_aliases = {**DEFAULT_TABLE_ALIASES, **data.get("table_aliases", {})}
        duplicate = len(table_aliases) != len(set(table_aliases.values()))
        unknown = set(table_aliases) - set(DEFAULT_TABLE_ALIASES)

        if unknown:
            raise MetadataError(f"Unknown table_aliases keys {sorted(unknown)} in file: {path}")
        if duplicate:
            raise MetadataError(f"Table_Alias : {table_aliases} duplicate error")
        for role, alias in table_aliases.items():
            if not isinstance(alias, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", alias):
                raise MetadataError(f"Invalid table alias '{alias}' for '{role}' in file: {path}")

        # [스프링] new SiteProfile(site, types, aliases). data["키"]에 키가 없으면 KeyError
        return SiteProfile(
            site=data["site"],
            types=data["types"],
            aliases=data["aliases"],
            table_aliases=table_aliases,
        )
    # [스프링] catch (FileNotFoundException e) { throw new MetadataException(msg, e); }
    # "from err"는 원래 예외를 cause로 연결한다 (new X(msg, cause))
    except FileNotFoundError as err:
        raise MetadataError(f"File not found: {path}") from err
    except json.JSONDecodeError as err:
        raise MetadataError(f"Invalid JSON format in file: {path}") from err
    except KeyError as err:
        raise MetadataError(f"Missing required keys in JSON file: {path}") from err


def load_common_metadata(path: str) -> Metadata:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for table_name, table_data in data["tables"].items():
            if not all(k in table_data for k in ("label", "version_column", "version_value")):
                raise MetadataError(
                    f"Table '{table_name}' is missing required keys in file: {path}"
                )
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", table_name):
                raise MetadataError(f"Invalid table name '{table_name}' in file: {path}")
        tables = {
            name: TableProfile(
                name=name,
                label=table_data["label"],
                version_column=table_data["version_column"],
                version_value=table_data["version_value"],
            )
            for name, table_data in data["tables"].items()
        }
        return Metadata(dialect=data["dialect"], max_rows=data["max_rows"], tables=tables)
    except FileNotFoundError as err:
        raise MetadataError(f"File not found: {path}") from err
    except json.JSONDecodeError as err:
        raise MetadataError(f"Invalid JSON format in file: {path}") from err
    except KeyError as err:
        raise MetadataError(f"Missing required keys in JSON file: {path}") from err
