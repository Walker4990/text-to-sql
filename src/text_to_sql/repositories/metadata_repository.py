"""공통 메타데이터·사이트 프로필 모델과 읽기.

- load_site_profile(path): 스냅샷 JSON 파일을 읽어 parse_site_profile에 넘긴다
- parse_site_profile(data, origin): 파일과 무관하게 dict를 검사·변환한다.
  동기화 도구(text_to_sql.sync)가 구글 시트에서 만든 프로필도 같은 검사를 거친다
- 출처(시트·CSV) 교체 지점은 repositories/profile_source.py의 ProfileSource"""

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from text_to_sql.errors import MetadataError, UnsupportedQuestionError

DEFAULT_TABLE_ALIASES = {"main": "M", "parent": "P", "inv": "I", "child": "C", "detail": "D"}
_ITEM_NAME = re.compile(r"[가-힣A-Za-z0-9_]{1,30}")
_ITEM_COLUMN = re.compile(r"(?:CHAR|TEXT|DESC)\d{2,3}")
# 등록 항목 alias: camelCase (소문자로 시작, 영문·숫자)
ALIAS_PATTERN = re.compile(r"[a-z][A-Za-z0-9]{0,49}")


# 항목 값 형식. number면 값 조건에서 숫자로 변환해 비교한다
ItemType = Literal["text", "number"]
ITEM_TYPES: tuple[ItemType, ...] = ("text", "number")


@dataclass(frozen=True)
class ItemDef:
    column: str
    multi: bool = False
    type: ItemType = "text"


# 사이트 프로필: 현장마다 다른 유형 번호(TYPEID)·별칭·테이블 별칭·항목 정의
@dataclass
class SiteProfile:
    site: str
    types: dict[str, int]
    aliases: dict[str, str]
    table_aliases: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TABLE_ALIASES))
    items: dict[str, dict[str, ItemDef]] = field(default_factory=dict)
    registered_items: list[str] = field(default_factory=list)
    # 등록 항목명칭 → camelCase alias (API 송수신용. SQL에는 항목명칭을 쓴다)
    registered_aliases: dict[str, str] = field(default_factory=dict)
    # 스냅샷 _meta.version (동기화 시각). 손으로 쓴 옛 파일은 None
    version: str | None = None

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

    # 별칭 → 정식 이름. 없으면 UnsupportedQuestionError
    def find_alias(self, alias_name: str) -> str:
        try:
            return self.aliases[alias_name]
        except KeyError as err:
            raise UnsupportedQuestionError(f"Alias not found: {alias_name}") from err

    def find_item(self, type_name: str, item_name: str) -> ItemDef:
        standard = self.find_alias(type_name)
        item = self.items.get(standard, {}).get(item_name)
        if item is None:
            raise UnsupportedQuestionError(f"'{standard}' 유형에 '{item_name}' 항목이 없습니다")
        if item.multi:
            raise UnsupportedQuestionError(f"다중값 항목은 아직 지원하지 않습니다: {item_name}")
        return item

    def find_registered_item(self, name: str) -> str:
        if name not in self.registered_items:
            raise UnsupportedQuestionError(f"등록되지 않은 항목입니다: {name}")
        return name


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


# JSON 파일을 읽어 SiteProfile을 만든다. 문제가 있으면 전부 MetadataError로 바꿔 던진다
def load_site_profile(path: str) -> SiteProfile:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as err:
        raise MetadataError(f"File not found: {path}") from err
    except json.JSONDecodeError as err:
        raise MetadataError(f"Invalid JSON format in file: {path}") from err
    return parse_site_profile(data, path)


def _read_version(data: dict[str, object], origin: str) -> str | None:
    """스냅샷 _meta.version. _meta가 없으면 None (손으로 쓴 옛 파일)."""
    meta = data.get("_meta")
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise MetadataError(f"_meta must be an object in file: {origin}")
    version = meta.get("version")
    if version is not None and not isinstance(version, str):
        raise MetadataError(f"_meta.version must be a string in file: {origin}")
    return version


# 파일을 읽지 않고 dict만 검사·변환한다. origin은 오류 메시지에 쓸 출처 (파일 경로, sheet:<사이트>)
def parse_site_profile(data: object, origin: str) -> SiteProfile:
    if not isinstance(data, dict):
        raise MetadataError(f"Profile must be a JSON object in file: {origin}")
    path = origin
    try:
        # 별칭이 없는 유형을 가리키면 거부
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

        items: dict[str, dict[str, ItemDef]] = {}
        for type_name, type_items in data.get("items", {}).items():
            if type_name not in data["types"]:
                raise MetadataError(f"Items for unknown type '{type_name}' in file: {path}")
            items[type_name] = {}
            for item_name, spec in type_items.items():
                if not _ITEM_NAME.fullmatch(item_name):
                    raise MetadataError(f"Invalid item name '{item_name}' in file: {path}")
                column = spec["column"]
                if not isinstance(column, str) or not _ITEM_COLUMN.fullmatch(column):
                    raise MetadataError(f"Invalid column '{column}' in file: {path}")
                multi = spec.get("multi", False)
                if not isinstance(multi, bool):
                    raise MetadataError(f"multi must be true/false in file: {path}")
                item_type = spec.get("type", "text")
                if item_type not in ITEM_TYPES:
                    raise MetadataError(f"item type must be text or number in file: {path}")
                items[type_name][item_name] = ItemDef(column=column, multi=multi, type=item_type)

        registered_items = data.get("registered_items", [])
        if not isinstance(registered_items, list):
            raise MetadataError(f"registered_items must be a list in file: {path}")
        for name in registered_items:
            if not isinstance(name, str) or not _ITEM_NAME.fullmatch(name):
                raise MetadataError(f"Invalid registered item '{name}' in file: {path}")
        registered_aliases = _parse_registered_aliases(data, registered_items, path)

        # 필수 키(site, types, aliases)가 없으면 KeyError → MetadataError
        return SiteProfile(
            site=data["site"],
            types=data["types"],
            aliases=data["aliases"],
            table_aliases=table_aliases,
            items=items,
            registered_items=registered_items,
            registered_aliases=registered_aliases,
            version=_read_version(data, path),
        )
    except KeyError as err:
        raise MetadataError(f"Missing required keys in JSON file: {path}") from err


def _parse_registered_aliases(
    data: dict[str, object], registered_items: list[str], origin: str
) -> dict[str, str]:
    """registered_aliases: {항목명칭: camelCase alias}. 없으면 빈 사전.

    등록된 항목명칭만, alias 형식(camelCase)과 중복을 검사한다."""
    raw = data.get("registered_aliases", {})
    if not isinstance(raw, dict):
        raise MetadataError(f"registered_aliases must be an object in file: {origin}")
    aliases: dict[str, str] = {}
    for name, alias in raw.items():
        if name not in registered_items:
            raise MetadataError(f"Alias for unregistered item '{name}' in file: {origin}")
        if not isinstance(alias, str) or not ALIAS_PATTERN.fullmatch(alias):
            raise MetadataError(f"Invalid alias '{alias}' (camelCase) in file: {origin}")
        if alias in aliases.values():
            raise MetadataError(f"Duplicate alias '{alias}' in file: {origin}")
        aliases[name] = alias
    return aliases


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
