"""두 사이트 프로필의 차이와 위험 판정.

위험(risky) = 같은 질문의 SQL이 다른 데이터를 조회하거나 모양이 바뀌는 변경.
TYPEID·물리컬럼·테이블별칭 변경, 유형·항목 삭제, 별칭 대상 변경이 해당한다.
동기화 도구는 위험 변경이 있으면 --yes 없이는 저장하지 않는다.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Literal

from text_to_sql.repositories.metadata_repository import (
    DEFAULT_TABLE_ALIASES,
    ItemDef,
    SiteProfile,
)

_MARK = {"add": "+", "remove": "-", "change": "~"}


@dataclass(frozen=True)
class Change:
    kind: Literal["add", "remove", "change"]
    target: str  # 예: "유형 특약", "항목 보험상품.보험기간"
    detail: str  # 예: "1120", "1120 → 1125"
    risky: bool = False

    def __str__(self) -> str:
        mark = " [위험]" if self.risky else ""
        return f"{_MARK[self.kind]} {self.target}: {self.detail}{mark}"


def diff_profiles(old: SiteProfile | None, new: SiteProfile) -> list[Change]:
    """old가 None이면(스냅샷 없음) 전부 추가로 보고하고 위험으로 보지 않는다."""
    changes: list[Change] = []
    changes += _diff_map(
        "유형", old.types if old else {}, new.types, str, remove_risky=True, change_risky=True
    )
    changes += _diff_map(
        "별칭",
        _real_aliases(old) if old else {},
        _real_aliases(new),
        str,
        remove_risky=False,
        change_risky=True,
    )
    changes += _diff_items(_flat_items(old) if old else {}, _flat_items(new))
    changes += _diff_registered(old.registered_items if old else [], new.registered_items)
    changes += _diff_map(
        "테이블별칭",
        old.table_aliases if old else DEFAULT_TABLE_ALIASES,
        new.table_aliases,
        str,
        remove_risky=True,
        change_risky=True,
    )
    if old is None:
        return [replace(change, risky=False) for change in changes]
    return changes


def _diff_map[V](
    label: str,
    old: Mapping[str, V],
    new: Mapping[str, V],
    show: Callable[[V], str],
    *,
    remove_risky: bool,
    change_risky: bool,
) -> list[Change]:
    """키 단위 비교. 추가는 항상 안전. 출력 순서는 새 값 순서, 삭제는 끝에 옛 값 순서."""
    changes: list[Change] = []
    for key, value in new.items():
        if key not in old:
            changes.append(Change("add", f"{label} {key}", show(value)))
        elif old[key] != value:
            detail = f"{show(old[key])} → {show(value)}"
            changes.append(Change("change", f"{label} {key}", detail, change_risky))
    for key, value in old.items():
        if key not in new:
            changes.append(Change("remove", f"{label} {key}", show(value), remove_risky))
    return changes


def _real_aliases(profile: SiteProfile) -> dict[str, str]:
    """정식 이름 자기 연결("특약": "특약")을 뺀, 사람이 적은 별칭만."""
    return {alias: name for alias, name in profile.aliases.items() if alias != name}


def _flat_items(profile: SiteProfile) -> dict[str, ItemDef]:
    return {
        f"{type_name}.{name}": item
        for type_name, items in profile.items.items()
        for name, item in items.items()
    }


def _show_item(item: ItemDef) -> str:
    notes = [name for name, on in (("다중값", item.multi), ("숫자", item.type == "number")) if on]
    return f"{item.column} ({', '.join(notes)})" if notes else item.column


def _yes_no(value: bool) -> str:
    return "Y" if value else "N"


def _diff_items(old: dict[str, ItemDef], new: dict[str, ItemDef]) -> list[Change]:
    """항목은 물리컬럼(위험)과 다중값(안전)을 따로 본다."""
    changes: list[Change] = []
    for key, item in new.items():
        target = f"항목 {key}"
        before = old.get(key)
        if before is None:
            changes.append(Change("add", target, _show_item(item)))
        elif before.column != item.column:
            detail = f"물리컬럼 {before.column} → {item.column}"
            changes.append(Change("change", target, detail, True))
        elif before.multi != item.multi:
            detail = f"다중값 {_yes_no(before.multi)} → {_yes_no(item.multi)}"
            changes.append(Change("change", target, detail))
        elif before.type != item.type:
            # 형식이 바뀌면 값 조건 SQL(숫자 변환 여부)이 달라진다
            detail = f"형식 {before.type} → {item.type}"
            changes.append(Change("change", target, detail, True))
    for key, item in old.items():
        if key not in new:
            changes.append(Change("remove", f"항목 {key}", _show_item(item), True))
    return changes


def _diff_registered(old: list[str], new: list[str]) -> list[Change]:
    changes = [Change("add", f"등록항목 {name}", "추가") for name in new if name not in old]
    changes += [Change("remove", f"등록항목 {name}", "삭제") for name in old if name not in new]
    return changes
