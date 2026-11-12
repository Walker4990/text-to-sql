"""구글 시트 출처 (설계 07, 8장·9장, 13장 7단계).

실제 구글에 접속하지 않고, gspread 클라이언트 모양의 가짜 객체로 확인한다.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest
from gspread.exceptions import SpreadsheetNotFound

from text_to_sql.errors import ProfileSourceError
from text_to_sql.repositories.sheet_source import GoogleSheetSource, open_google_sheet


class FakeWorksheet:
    def __init__(self, title: str, values: list[list[object]]) -> None:
        self._title = title
        self.values = values

    @property
    def title(self) -> str:
        return self._title

    def get_all_values(self) -> list[list[object]]:
        return self.values


class FakeSpreadsheet:
    def __init__(self, sheets: list[FakeWorksheet]) -> None:
        self.sheets = sheets

    def worksheets(self) -> Sequence[FakeWorksheet]:
        return self.sheets


class FakeClient:
    def __init__(self, spreadsheet: FakeSpreadsheet | None = None, error: Exception | None = None):
        self.spreadsheet = spreadsheet
        self.error = error
        self.keys: list[str] = []

    def open_by_key(self, key: str) -> FakeSpreadsheet:
        self.keys.append(key)
        if self.error is not None:
            raise self.error
        assert self.spreadsheet is not None
        return self.spreadsheet


# 문제 1: 탭 이름 → 행 목록, 값은 모두 문자열 (None은 빈 칸)
def test_read_tabs() -> None:
    client = FakeClient(
        FakeSpreadsheet(
            [
                FakeWorksheet("설정", [["키", "값"], ["site", "demo"]]),
                FakeWorksheet("유형", [["유형명", "TYPEID"], ["보험상품", 1100], ["특약", None]]),
            ]
        )
    )
    tabs = GoogleSheetSource(client, "sheet-1").read_tabs()
    assert client.keys == ["sheet-1"]
    assert tabs == {
        "설정": [["키", "값"], ["site", "demo"]],
        "유형": [["유형명", "TYPEID"], ["보험상품", "1100"], ["특약", ""]],
    }


# 문제 2: gspread 오류·네트워크 오류는 ProfileSourceError (원인 예외 연결)
@pytest.mark.parametrize("error", [SpreadsheetNotFound("없음"), ConnectionError("끊김")])
def test_errors_wrapped(error: Exception) -> None:
    with pytest.raises(ProfileSourceError) as info:
        GoogleSheetSource(FakeClient(error=error), "sheet-1").read_tabs()
    assert info.value.__cause__ is error


# 문제 3: 서비스 계정 키 파일이 없거나 형식이 틀리면 ProfileSourceError
def test_open_with_bad_credentials(tmp_path: Path) -> None:
    with pytest.raises(ProfileSourceError):
        open_google_sheet(str(tmp_path / "none.json"), "sheet-1")
    broken = tmp_path / "key.json"
    broken.write_text("{}", encoding="utf-8")
    with pytest.raises(ProfileSourceError):
        open_google_sheet(str(broken), "sheet-1")
