"""스냅샷 차이 보고와 위험 판정 (설계 07, 5장, 13장 4단계)."""

from dataclasses import replace

from text_to_sql.repositories.metadata_repository import DEFAULT_TABLE_ALIASES, ItemDef, SiteProfile
from text_to_sql.sync.diff import Change, diff_profiles

OLD = SiteProfile(
    site="demo",
    types={"보험상품": 1100, "특약": 1120},
    aliases={"보험상품": "보험상품", "특약": "특약", "라이더": "특약"},
    items={"보험상품": {"보험기간": ItemDef("CHAR01"), "판매채널": ItemDef("CHAR05", multi=True)}},
    registered_items=["상품코드"],
)


def summary(changes: list[Change]) -> list[tuple[str, str, bool]]:
    return [(c.kind, c.target, c.risky) for c in changes]


# 문제 1: 같으면 차이 없음
def test_no_changes() -> None:
    assert diff_profiles(OLD, replace(OLD)) == []


# 문제 2: 기존 스냅샷이 없으면 전부 추가, 위험 없음
# 테이블별칭은 기본값(M/P/I/C/D)과 비교하므로 기본값과 다른 것만 "변경"으로 나온다
def test_new_snapshot() -> None:
    new = replace(OLD, table_aliases={**DEFAULT_TABLE_ALIASES, "main": "OMA"})
    changes = diff_profiles(None, new)
    assert changes
    assert not any(c.risky for c in changes)
    assert all(c.kind == "add" for c in changes if not c.target.startswith("테이블별칭"))
    assert [c.target for c in changes if c.target.startswith("테이블별칭")] == ["테이블별칭 main"]
    # 정식 이름 자기 연결은 별칭 차이에 나오지 않는다
    assert not any(c.target == "별칭 특약" for c in changes)


# 문제 3: 유형 추가는 안전, 삭제·TYPEID 변경은 위험
def test_type_changes() -> None:
    new = replace(OLD, types={"보험상품": 1101, "보장": 1130})
    new.aliases = {"보험상품": "보험상품", "보장": "보장"}
    new.items = OLD.items
    found = summary(diff_profiles(OLD, new))
    assert ("change", "유형 보험상품", True) in found
    assert ("add", "유형 보장", False) in found
    assert ("remove", "유형 특약", True) in found


# 문제 4: 별칭 추가·삭제는 안전, 대상 변경은 위험
def test_alias_changes() -> None:
    added = replace(OLD, aliases={**OLD.aliases, "선택특약": "특약"})
    assert summary(diff_profiles(OLD, added)) == [("add", "별칭 선택특약", False)]
    removed = replace(OLD, aliases={"보험상품": "보험상품", "특약": "특약"})
    assert summary(diff_profiles(OLD, removed)) == [("remove", "별칭 라이더", False)]
    moved = replace(OLD, aliases={**OLD.aliases, "라이더": "보험상품"})
    assert summary(diff_profiles(OLD, moved)) == [("change", "별칭 라이더", True)]


# 문제 5: 항목 — 삭제·물리컬럼 변경은 위험, 추가·다중값 변경은 안전
def test_item_changes() -> None:
    new = replace(
        OLD,
        items={
            "보험상품": {"보험기간": ItemDef("CHAR07"), "판매채널": ItemDef("CHAR05")},
            "특약": {"보장기간": ItemDef("CHAR01")},
        },
    )
    assert summary(diff_profiles(OLD, new)) == [
        ("change", "항목 보험상품.보험기간", True),
        ("change", "항목 보험상품.판매채널", False),
        ("add", "항목 특약.보장기간", False),
    ]
    gone = replace(OLD, items={"보험상품": {"보험기간": ItemDef("CHAR01")}})
    assert summary(diff_profiles(OLD, gone)) == [("remove", "항목 보험상품.판매채널", True)]


# 문제 6: 등록항목 추가·삭제는 안전
def test_registered_changes() -> None:
    new = replace(OLD, registered_items=["특약코드"])
    assert summary(diff_profiles(OLD, new)) == [
        ("add", "등록항목 특약코드", False),
        ("remove", "등록항목 상품코드", False),
    ]


# 문제 7: 테이블별칭 변경은 위험
def test_table_alias_change() -> None:
    new = replace(OLD, table_aliases={**DEFAULT_TABLE_ALIASES, "main": "OMA"})
    assert summary(diff_profiles(OLD, new)) == [("change", "테이블별칭 main", True)]


# 문제 8: 출력 형식 (+ - ~, 위험 표시)
def test_change_str() -> None:
    assert str(Change("add", "유형 보장", "1130")) == "+ 유형 보장: 1130"
    assert str(Change("remove", "유형 특약", "1120", risky=True)) == "- 유형 특약: 1120 [위험]"
    assert str(Change("change", "유형 보험상품", "1100 → 1101", True)) == (
        "~ 유형 보험상품: 1100 → 1101 [위험]"
    )
