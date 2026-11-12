"""항목 형식(type: text/number) (설계 08, 4장, 8장 2단계).

로더, 시트 `형식` 열, 차이 보고, 스냅샷, CSV 왕복.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from text_to_sql.errors import MetadataError
from text_to_sql.repositories.metadata_repository import (
    ItemDef,
    SiteProfile,
    parse_site_profile,
)
from text_to_sql.repositories.profile_source import CsvFolderSource
from text_to_sql.sync.diff import diff_profiles
from text_to_sql.sync.sheet_rows import rows_to_profile
from text_to_sql.sync.snapshot import export_csv, profile_to_data, snapshot_text


def data(spec: object) -> dict[str, object]:
    return {
        "site": "t",
        "types": {"연회비": 2120},
        "aliases": {"연회비": "연회비"},
        "items": {"연회비": {"국내전용연회비": spec}},
    }


# 문제 1: type 기본값은 text, number를 읽는다
def test_loader_type() -> None:
    plain = parse_site_profile(data({"column": "CHAR01"}), "t")
    assert plain.items["연회비"]["국내전용연회비"] == ItemDef("CHAR01", multi=False, type="text")
    number = parse_site_profile(data({"column": "CHAR01", "type": "number"}), "t")
    assert number.items["연회비"]["국내전용연회비"].type == "number"


# 문제 2: text·number 말고는 MetadataError
@pytest.mark.parametrize("bad", ["int", "NUMBER", "", 1, None, True])
def test_loader_bad_type(bad: object) -> None:
    with pytest.raises(MetadataError):
        parse_site_profile(data({"column": "CHAR01", "type": bad}), "t")


# 문제 3: 시트 `형식` 열 — 숫자/number/문자/text/빈 칸, 대소문자 무시
@pytest.mark.parametrize(
    ("cell", "expected"),
    [("숫자", "number"), ("NUMBER", "number"), (" number ", "number"),
     ("문자", "text"), ("Text", "text"), ("", "text")],
)
def test_sheet_type_column(cell: str, expected: str) -> None:
    tabs = {
        "설정": [["키", "값"], ["site", "t"]],
        "유형": [["유형명", "TYPEID"], ["연회비", "2120"]],
        "항목": [
            ["유형명", "항목명", "물리컬럼", "형식"],
            ["연회비", "국내전용연회비", "CHAR01", cell],
        ],
    }
    result = rows_to_profile(tabs, "t")
    assert result.errors == []
    assert result.profile is not None
    assert result.profile.items["연회비"]["국내전용연회비"].type == expected


# 문제 4: 형식 열이 없는 옛 시트도 text로 읽힌다 (선택 열)
def test_sheet_without_type_column() -> None:
    tabs = {
        "설정": [["키", "값"], ["site", "t"]],
        "유형": [["유형명", "TYPEID"], ["연회비", "2120"]],
        "항목": [["유형명", "항목명", "물리컬럼"], ["연회비", "국내전용연회비", "CHAR01"]],
    }
    result = rows_to_profile(tabs, "t")
    assert result.profile is not None
    assert result.profile.items["연회비"]["국내전용연회비"].type == "text"


# 문제 5: 형식 칸에 다른 값이 있으면 MV-05와 행 번호
def test_sheet_bad_type() -> None:
    tabs = {
        "설정": [["키", "값"], ["site", "t"]],
        "유형": [["유형명", "TYPEID"], ["연회비", "2120"]],
        "항목": [
            ["유형명", "항목명", "물리컬럼", "형식"],
            ["연회비", "국내전용연회비", "CHAR01", "정수"],
        ],
    }
    result = rows_to_profile(tabs, "t")
    assert [(e.code, e.row) for e in result.errors] == [("MV-05", 2)]


BASE = SiteProfile(
    site="t",
    types={"연회비": 2120},
    aliases={"연회비": "연회비"},
    items={"연회비": {"국내전용연회비": ItemDef("CHAR01")}},
)


# 문제 6: 형식 변경은 위험 변경으로 보고한다
def test_diff_type_change_is_risky() -> None:
    new = replace(BASE, items={"연회비": {"국내전용연회비": ItemDef("CHAR01", type="number")}})
    changes = diff_profiles(BASE, new)
    assert [(c.kind, c.target, c.risky) for c in changes] == [
        ("change", "항목 연회비.국내전용연회비", True)
    ]
    assert "형식 text → number" in changes[0].detail


# 문제 7: 스냅샷에는 number일 때만 type을 쓴다 (text는 기본값이라 생략)
def test_snapshot_writes_type_only_for_number() -> None:
    profile = replace(
        BASE,
        items={
            "연회비": {
                "국내전용연회비": ItemDef("CHAR01", type="number"),
                "비고": ItemDef("TEXT01"),
            }
        },
    )
    items = profile_to_data(profile)["items"]
    assert items == {
        "연회비": {
            "국내전용연회비": {"column": "CHAR01", "type": "number"},
            "비고": {"column": "TEXT01"},
        }
    }
    loaded = parse_site_profile(json.loads(snapshot_text(profile, {"version": "v"})), "t")
    assert loaded.items == profile.items


# 문제 8: CSV 왕복 — 형식이 유지된다, 내보내기는 숫자/문자로 쓴다
def test_csv_round_trip_keeps_type(tmp_path: Path) -> None:
    profile = replace(
        BASE,
        items={
            "연회비": {
                "국내전용연회비": ItemDef("CHAR01", type="number"),
                "비고": ItemDef("TEXT01"),
            }
        },
    )
    export_csv(profile, tmp_path)
    text = (tmp_path / "항목.csv").read_text(encoding="utf-8-sig")
    assert "국내전용연회비,CHAR01,N,숫자,Y," in text
    assert "비고,TEXT01,N,문자,Y," in text
    result = rows_to_profile(CsvFolderSource(tmp_path).read_tabs(), "t")
    assert result.errors == []
    assert result.profile == profile
