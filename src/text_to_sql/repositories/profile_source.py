"""사이트 프로필 출처(ProfileSource)와 CSV 폴더 구현.

출처는 "탭 이름 → 행 목록(1행은 헤더, 값은 모두 문자열)"만 돌려준다.
행을 프로필로 바꾸고 검사하는 일은 text_to_sql.sync.sheet_rows가 맡는다.
그래서 구글 시트·CSV 폴더·(나중에) 현장 DB 메타 테이블 중 무엇이든 같은 변환·검사를 거친다.
"""

import csv
from pathlib import Path
from typing import Protocol

from text_to_sql.errors import ProfileSourceError

# 탭 이름 → 행 목록 (1행은 헤더)
Tabs = dict[str, list[list[str]]]


class ProfileSource(Protocol):
    def read_tabs(self) -> Tabs:
        """탭 이름 → 행 목록. 읽지 못하면 ProfileSourceError."""
        ...


class CsvFolderSource:
    """폴더 안의 <탭 이름>.csv 파일들을 탭으로 읽는다.

    구글 시트 없이 시험할 때, 시트 첫 작성·이관(--export-csv의 결과)을 확인할 때 쓴다.
    엑셀이 저장한 BOM 붙은 UTF-8도, BOM 없는 UTF-8도 읽는다 (utf-8-sig).
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def read_tabs(self) -> Tabs:
        if not self.folder.is_dir():
            raise ProfileSourceError(f"CSV 폴더가 없습니다: {self.folder}")
        tabs: Tabs = {}
        for path in sorted(self.folder.glob("*.csv")):
            try:
                with open(path, encoding="utf-8-sig", newline="") as f:
                    tabs[path.stem] = [list(row) for row in csv.reader(f)]
            except (OSError, UnicodeDecodeError, csv.Error) as err:
                raise ProfileSourceError(f"CSV 파일을 읽지 못했습니다: {path}") from err
        return tabs
