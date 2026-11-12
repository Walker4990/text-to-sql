"""시트 탭(행 목록) → 사이트 프로필 변환과 검증.

네트워크·파일을 만지지 않는 순수 변환이다. 출처(구글 시트, CSV 폴더)와 상관없이 같은 규칙을 쓴다.
오류는 첫 번째에서 멈추지 않고 전부 모은다. 현장 담당자가 한 번에 고칠 수 있도록
"탭 이름, 행 번호(헤더가 1행), 무엇이 잘못됐는지"를 남긴다.
"""

import re
from dataclasses import dataclass, field

from text_to_sql.errors import MetadataError
from text_to_sql.repositories.metadata_repository import (
    DEFAULT_TABLE_ALIASES,
    SiteProfile,
    parse_site_profile,
)
from text_to_sql.repositories.profile_source import Tabs

TAB_SETTINGS = "설정"
TAB_TYPES = "유형"
TAB_ALIASES = "별칭"
TAB_ITEMS = "표준항목"
TAB_REGISTERED = "항목"  # 등록 항목명칭 (룰 변수로 쓰는 이름)
TAB_TABLE_ALIASES = "테이블별칭"

REQUIRED_TABS = (TAB_SETTINGS, TAB_TYPES)
# 탭별 필수 열. 사용·다중값·설명 열은 없어도 된다 (사용은 Y, 다중값은 N으로 본다)
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    TAB_SETTINGS: ("키", "값"),
    TAB_TYPES: ("유형명", "TYPEID"),
    TAB_ALIASES: ("별칭", "유형명"),
    TAB_ITEMS: ("유형명", "항목명", "물리컬럼"),
    TAB_REGISTERED: ("항목명칭",),
    TAB_TABLE_ALIASES: ("역할", "별칭"),
}

# 유형명·별칭·항목명·항목명칭 형식. 유형명은 계층 조회 SQL의 열 이름("보험상품")으로도 쓰인다
_NAME = re.compile(r"[가-힣A-Za-z0-9_]{1,30}")
_NAME_RULE = "한글·영문·숫자·_ 1~30자"
_SITE = re.compile(r"[A-Za-z0-9_-]+")
_TYPEID = re.compile(r"[0-9]+")
_COLUMN = re.compile(r"(?:CHAR|TEXT|DESC)\d{2,3}")
_TABLE_ALIAS = re.compile(r"[A-Z][A-Z0-9_]*")

# 아이템 스펙 JSON 모양: {"column": "CHAR01"}, {"column": "CHAR05", "multi": true, "type": "number"}
ItemSpec = dict[str, str | bool]
# 시트 `형식` 열의 값 → 항목 형식. 빈 칸은 text
_ITEM_TYPE_CELLS = {
    "숫자": "number",
    "number": "number",
    "문자": "text",
    "text": "text",
    "": "text",
}


@dataclass(frozen=True)
class RowIssue:
    """검증 오류·경고 한 건. row가 None이면 탭 전체에 대한 것."""

    code: str
    tab: str
    row: int | None
    message: str

    def __str__(self) -> str:
        where = self.tab if self.row is None else f"{self.tab} {self.row}행"
        return f"{self.code} {where}: {self.message}"


@dataclass
class ProfileBuildResult:
    """profile이 None이면 errors에 이유가 있다. 경고는 저장을 막지 않는다."""

    profile: SiteProfile | None
    owner: str | None = None
    errors: list[RowIssue] = field(default_factory=list)
    warnings: list[RowIssue] = field(default_factory=list)


@dataclass(frozen=True)
class _Row:
    number: int  # 시트 행 번호 (헤더가 1행)
    values: dict[str, str]

    def get(self, column: str) -> str:
        return self.values.get(column, "")


def rows_to_profile(tabs: Tabs, site: str) -> ProfileBuildResult:
    """탭별 행 목록을 검사해 SiteProfile을 만든다. site는 실행 인자 --site 값."""
    return _Builder(tabs, site).build()


class _Builder:
    def __init__(self, tabs: Tabs, site: str) -> None:
        self.tabs = tabs
        self.site = site
        self.errors: list[RowIssue] = []
        self.warnings: list[RowIssue] = []
        # 유형 탭에 있지만 그 행에 오류가 있는 유형. 이 유형을 가리키는 줄은 오류를 겹쳐 내지 않는다
        self.broken_types: set[str] = set()

    def build(self) -> ProfileBuildResult:
        owner = self._settings()
        types, disabled = self._types()
        aliases = self._aliases(types, disabled)
        items = self._items(types, disabled)
        registered = self._registered()
        table_aliases = self._table_aliases()
        if self.errors:
            return self._result(None, owner)

        data: dict[str, object] = {
            "site": self.site,
            "types": types,
            "aliases": aliases,
            "table_aliases": table_aliases,
            "items": items,
            "registered_items": registered,
        }
        # MV-09: 로더와 같은 검사를 한 번 더.
        # 시트 규칙과 로더 규칙이 어긋나도 잘못된 스냅샷이 저장되지 않는다
        try:
            profile = parse_site_profile(data, f"sheet:{self.site}")
        except MetadataError as err:
            self.errors.append(RowIssue("MV-09", "전체", None, f"로더 검사 실패: {err}"))
            return self._result(None, owner)
        return self._result(profile, owner)

    def _result(self, profile: SiteProfile | None, owner: str | None) -> ProfileBuildResult:
        return ProfileBuildResult(profile, owner, self.errors, self.warnings)

    # ── 공통 도구 ─────────────────────────────────────────────

    def _error(self, code: str, tab: str, row: int | None, message: str) -> None:
        self.errors.append(RowIssue(code, tab, row, message))

    def _warn(self, code: str, tab: str, row: int | None, message: str) -> None:
        self.warnings.append(RowIssue(code, tab, row, message))

    def _rows(self, tab: str) -> list[_Row] | None:
        """탭의 데이터 행. 탭이 없으면 선택 탭은 [], 필수 탭은 MV-01과 None. 헤더 오류도 None."""
        raw = self.tabs.get(tab)
        if not raw:
            if tab in REQUIRED_TABS:
                self._error("MV-01", tab, None, "필수 탭이 없습니다")
                return None
            return []
        header = [cell.strip() for cell in raw[0]]
        missing = [name for name in REQUIRED_COLUMNS[tab] if name not in header]
        if missing:
            self._error("MV-01", tab, 1, f"헤더에 필수 열이 없습니다: {', '.join(missing)}")
            return None
        rows: list[_Row] = []
        for number, cells in enumerate(raw[1:], start=2):
            values = {
                name: (cells[index].strip() if index < len(cells) else "")
                for index, name in enumerate(header)
                if name
            }
            if any(values.values()):  # 완전히 빈 행은 건너뛴다
                rows.append(_Row(number, values))
        return rows

    def _flag(self, row: _Row, tab: str, column: str, default: bool, code: str) -> bool | None:
        """Y/N 칸. 빈 칸은 default. 다른 값이면 오류를 남기고 None."""
        text = row.get(column).upper()
        if text == "":
            return default
        if text in ("Y", "N"):
            return text == "Y"
        self._error(code, tab, row.number, f"{column} 칸은 Y 또는 N만 쓸 수 있습니다: '{text}'")
        return None

    def _used(self, row: _Row, tab: str) -> bool | None:
        return self._flag(row, tab, "사용", True, "MV-08")

    def _type_ref(
        self, row: _Row, tab: str, code: str, name: str, types: dict[str, int], disabled: set[str]
    ) -> bool:
        """가리키는 유형이 사용 중인지. 사용=N 유형이면 경고하고 제외, 없는 유형이면 오류."""
        if name in types:
            return True
        if name in self.broken_types:  # 원인 오류는 유형 탭에서 이미 보고했다
            return False
        if name in disabled:
            self._warn(code, tab, row.number, f"사용하지 않는 유형 '{name}'을 가리켜 제외합니다")
        else:
            self._error(code, tab, row.number, f"없는 유형입니다: '{name}'")
        return False

    # ── 탭별 변환 ─────────────────────────────────────────────

    def _settings(self) -> str | None:
        """설정 탭(키·값). site를 검사하고 담당자를 돌려준다."""
        rows = self._rows(TAB_SETTINGS)
        if rows is None:
            return None
        values = {row.get("키"): row for row in rows}
        site_row = values.get("site")
        if site_row is None or not site_row.get("값"):
            self._error("MV-02", TAB_SETTINGS, None, "site가 없습니다")
        else:
            site = site_row.get("값")
            if not _SITE.fullmatch(site):
                message = f"site '{site}'는 영문·숫자·_·- 만 쓸 수 있습니다"
                self._error("MV-02", TAB_SETTINGS, site_row.number, message)
            elif site != self.site:
                message = f"시트의 site '{site}'와 실행 인자 --site '{self.site}'가 다릅니다"
                self._error("MV-02", TAB_SETTINGS, site_row.number, message)
        owner_row = values.get("담당자")
        return (owner_row.get("값") or None) if owner_row else None

    def _types(self) -> tuple[dict[str, int], set[str]]:
        """유형 탭 → (사용 유형 {유형명: TYPEID}, 사용=N 유형 이름)."""
        types: dict[str, int] = {}
        disabled: set[str] = set()
        rows = self._rows(TAB_TYPES)
        if rows is None:
            return types, disabled
        seen_names: dict[str, int] = {}
        seen_ids: dict[int, int] = {}
        error_count = len(self.errors)
        for row in rows:
            used = self._used(row, TAB_TYPES)
            name, typeid_text = row.get("유형명"), row.get("TYPEID")
            valid = True
            if not _NAME.fullmatch(name):
                message = f"유형명 '{name}' 형식이 맞지 않습니다 ({_NAME_RULE})"
                self._error("MV-03", TAB_TYPES, row.number, message)
                valid = False
            elif name in seen_names:
                message = f"유형명 '{name}'이 두 번 나옵니다 ({seen_names[name]}행과 중복)"
                self._error("MV-03", TAB_TYPES, row.number, message)
                valid = False
            else:
                seen_names[name] = row.number
            typeid = int(typeid_text) if _TYPEID.fullmatch(typeid_text) else 0
            if typeid <= 0:
                message = (
                    f"TYPEID '{typeid_text}'는 양의 정수가 아닙니다 "
                    "(시트 서식을 일반 텍스트로 두고 숫자만 적습니다)"
                )
                self._error("MV-03", TAB_TYPES, row.number, message)
                valid = False
            elif typeid in seen_ids:
                message = f"TYPEID {typeid}가 두 번 나옵니다 ({seen_ids[typeid]}행과 중복)"
                self._error("MV-03", TAB_TYPES, row.number, message)
                valid = False
            else:
                seen_ids[typeid] = row.number
            if not valid or used is None:
                if name and name not in types:
                    self.broken_types.add(name)
                continue
            if used:
                types[name] = typeid
            else:
                disabled.add(name)
        if not types and len(self.errors) == error_count:
            self._error("MV-03", TAB_TYPES, None, "사용(Y)하는 유형이 하나도 없습니다")
        return types, disabled

    def _aliases(self, types: dict[str, int], disabled: set[str]) -> dict[str, str]:
        """별칭 탭 → {별칭: 정식 이름}. 정식 이름 자기 연결은 자동으로 앞에 채운다 (로더 형식)."""
        aliases = {name: name for name in types}
        rows = self._rows(TAB_ALIASES) or []
        seen: dict[str, int] = {}
        for row in rows:
            if not self._used(row, TAB_ALIASES):
                continue
            alias, target = row.get("별칭"), row.get("유형명")
            if not _NAME.fullmatch(alias):
                message = f"별칭 '{alias}' 형식이 맞지 않습니다 ({_NAME_RULE})"
                self._error("MV-04", TAB_ALIASES, row.number, message)
                continue
            if alias in seen:
                message = f"별칭 '{alias}'이 두 번 나옵니다 ({seen[alias]}행과 중복)"
                self._error("MV-04", TAB_ALIASES, row.number, message)
                continue
            seen[alias] = row.number
            if not self._type_ref(row, TAB_ALIASES, "MV-04", target, types, disabled):
                continue
            if alias == target:  # 자기 자신을 가리키는 줄은 이미 채웠으므로 무시
                continue
            if alias in types or alias in disabled:
                message = f"별칭 '{alias}'이 다른 유형의 정식 이름과 같습니다"
                self._error("MV-04", TAB_ALIASES, row.number, message)
                continue
            aliases[alias] = target
        return aliases

    def _items(self, types: dict[str, int], disabled: set[str]) -> dict[str, dict[str, ItemSpec]]:
        """항목 탭 → {유형명: {항목명: {"column": ..., "multi": true?}}}."""
        items: dict[str, dict[str, ItemSpec]] = {}
        rows = self._rows(TAB_ITEMS) or []
        seen_names: dict[tuple[str, str], int] = {}
        seen_columns: dict[tuple[str, str], int] = {}
        for row in rows:
            if not self._used(row, TAB_ITEMS):
                continue
            type_name, name = row.get("유형명"), row.get("항목명")
            column = row.get("물리컬럼").upper()
            multi = self._flag(row, TAB_ITEMS, "다중값", False, "MV-05")
            valid = multi is not None
            item_type = _ITEM_TYPE_CELLS.get(row.get("형식").lower())
            if item_type is None:
                original_type = row.get("형식")
                message = f"형식 '{original_type}'은 숫자(number) 또는 문자(text)만 쓸 수 있습니다"
                self._error("MV-05", TAB_ITEMS, row.number, message)
                valid = False
            if not _NAME.fullmatch(name):
                message = f"항목명 '{name}' 형식이 맞지 않습니다 ({_NAME_RULE})"
                self._error("MV-05", TAB_ITEMS, row.number, message)
                valid = False
            elif (type_name, name) in seen_names:
                first = seen_names[(type_name, name)]
                message = f"'{type_name}' 유형에 항목 '{name}'이 두 번 나옵니다 ({first}행과 중복)"
                self._error("MV-05", TAB_ITEMS, row.number, message)
                valid = False
            else:
                seen_names[(type_name, name)] = row.number
            if not _COLUMN.fullmatch(column):
                original = row.get("물리컬럼")
                message = f"물리컬럼 '{original}' 형식이 맞지 않습니다 (예: CHAR01, TEXT01)"
                self._error("MV-05", TAB_ITEMS, row.number, message)
                valid = False
            elif (type_name, column) in seen_columns:
                first = seen_columns[(type_name, column)]
                message = f"'{type_name}' 유형에서 {column}을 두 항목이 씁니다 ({first}행과 중복)"
                self._error("MV-05", TAB_ITEMS, row.number, message)
                valid = False
            else:
                seen_columns[(type_name, column)] = row.number
            if not valid:
                continue
            if not self._type_ref(row, TAB_ITEMS, "MV-05", type_name, types, disabled):
                continue
            spec: ItemSpec = {"column": column}
            if multi:
                spec["multi"] = True
            if item_type == "number":
                spec["type"] = "number"
            items.setdefault(type_name, {})[name] = spec
        return items

    def _registered(self) -> list[str]:
        """항목 탭(등록 항목명칭) → 항목명칭 목록.

        시트 순서를 유지한다. 첫 항목이 프롬프트 예시에 쓰인다.
        """
        names: list[str] = []
        seen: dict[str, int] = {}
        for row in self._rows(TAB_REGISTERED) or []:
            if not self._used(row, TAB_REGISTERED):
                continue
            name = row.get("항목명칭")
            if not _NAME.fullmatch(name):
                message = f"항목명칭 '{name}' 형식이 맞지 않습니다 ({_NAME_RULE})"
                self._error("MV-06", TAB_REGISTERED, row.number, message)
            elif name in seen:
                message = f"항목명칭 '{name}'이 두 번 나옵니다 ({seen[name]}행과 중복)"
                self._error("MV-06", TAB_REGISTERED, row.number, message)
            else:
                seen[name] = row.number
                names.append(name)
        return names

    def _table_aliases(self) -> dict[str, str]:
        """테이블별칭 탭 → 기본값(M/P/I/C/D)에 적은 역할만 덮어쓴다."""
        result = dict(DEFAULT_TABLE_ALIASES)
        seen: dict[str, int] = {}
        for row in self._rows(TAB_TABLE_ALIASES) or []:
            role, alias = row.get("역할"), row.get("별칭")
            if role not in DEFAULT_TABLE_ALIASES:
                allowed = ", ".join(DEFAULT_TABLE_ALIASES)
                message = f"역할 '{role}'은 쓸 수 없습니다 ({allowed} 중 하나)"
                self._error("MV-07", TAB_TABLE_ALIASES, row.number, message)
            elif role in seen:
                message = f"역할 '{role}'이 두 번 나옵니다 ({seen[role]}행과 중복)"
                self._error("MV-07", TAB_TABLE_ALIASES, row.number, message)
            elif not _TABLE_ALIAS.fullmatch(alias):
                message = f"별칭 '{alias}'은 영문 대문자로 시작하고 대문자·숫자·_만 쓸 수 있습니다"
                self._error("MV-07", TAB_TABLE_ALIASES, row.number, message)
            else:
                seen[role] = row.number
                result[role] = alias
        roles_by_alias: dict[str, list[str]] = {}
        for role, alias in result.items():
            roles_by_alias.setdefault(alias, []).append(role)
        for alias, roles in roles_by_alias.items():
            if len(roles) > 1:
                message = f"별칭 '{alias}'을 여러 역할이 함께 씁니다: {', '.join(roles)}"
                self._error("MV-07", TAB_TABLE_ALIASES, None, message)
        return result
