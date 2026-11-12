"""구글 시트 출처. gspread를 쓰는 유일한 모듈.

- 서비스 계정으로 인증하고, 읽기 전용 범위(READONLY_SCOPES)만 요청한다. 시트에 쓰지 않는다
- 탭마다 get_all_values()로 읽는다. get_all_records()는 숫자처럼 보이는 값을 숫자로 바꾸고
  행 번호를 잃어서 쓰지 않는다 (오류에 행 번호를 붙여야 한다)
- gspread·네트워크 오류는 ProfileSourceError로 바꾼다 (원인 예외 연결)
선택 설치: pip install "text-to-sql[sheets]". 챗봇(서비스)은 스냅샷만 읽으므로 필요 없다.
"""

from collections.abc import Sequence
from typing import Protocol

import gspread
from gspread.auth import READONLY_SCOPES
from gspread.exceptions import GSpreadException

from text_to_sql.errors import ProfileSourceError
from text_to_sql.repositories.profile_source import Tabs


# gspread 객체 중 우리가 쓰는 부분만 적은 모양 (테스트에서 가짜로 바꿔 끼우기 위해)
class _Worksheet(Protocol):
    @property
    def title(self) -> str: ...

    def get_all_values(self) -> Sequence[Sequence[object]]: ...


class _Spreadsheet(Protocol):
    def worksheets(self) -> Sequence[_Worksheet]: ...


class SheetClient(Protocol):
    def open_by_key(self, key: str) -> _Spreadsheet: ...


def _cell(value: object) -> str:
    return "" if value is None else str(value)


class GoogleSheetSource:
    def __init__(self, client: SheetClient, sheet_id: str) -> None:
        self.client = client
        self.sheet_id = sheet_id

    def read_tabs(self) -> Tabs:
        try:
            spreadsheet = self.client.open_by_key(self.sheet_id)
            return {
                worksheet.title: [[_cell(v) for v in row] for row in worksheet.get_all_values()]
                for worksheet in spreadsheet.worksheets()
            }
        # 네트워크 오류(requests.ConnectionError 등)는 OSError의 하위 클래스다
        except (GSpreadException, OSError) as err:
            raise ProfileSourceError(
                f"구글 시트를 읽지 못했습니다 sheet_id={self.sheet_id}: {err}"
            ) from err


def open_google_sheet(credentials_path: str, sheet_id: str) -> GoogleSheetSource:
    """서비스 계정 키 파일로 인증해 출처를 만든다.

    키 파일이 없거나 형식이 틀리면 ProfileSourceError."""
    try:
        client = gspread.service_account(filename=credentials_path, scopes=READONLY_SCOPES)
    except (OSError, ValueError, KeyError) as err:
        raise ProfileSourceError(f"서비스 계정 키를 읽지 못했습니다: {credentials_path}") from err
    return GoogleSheetSource(client, sheet_id)
