"""SQL 검증기: SELECT 한 문장만, 주석·금지 키워드·위험 함수 차단, 허용 테이블만."""

import re
from typing import NoReturn

from text_to_sql.errors import SqlValidationError

FORBIDDEN_KEYWORD = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "MERGE",
    "GRANT",
    "EXEC",
    "CALL",
    "COPY",
    "DO",
    "SET",
    "INTO",
}

_SOLUTION_COMMENT = re.compile(r"<\$@.*?@\$>", re.DOTALL)
_SELECT_START = re.compile(r"\s*SELECT\b", re.IGNORECASE)
_LITERAL = re.compile(r"'(?:[^']|'')*'")
_FORBIDDEN = re.compile(r"\b(?:" + "|".join(FORBIDDEN_KEYWORD) + r")\b", re.IGNORECASE)
_DANGEROUS_FUNCTION = re.compile(r"\b(?:pg_\w*|lo_import|lo_export|dblink\w*)\s*\(", re.IGNORECASE)
_TABLE_SECTION = re.compile(
    r"\b(?:FROM|JOIN)\s+(?!\()(.*?)(?=\b(?:WHERE|JOIN|ON|GROUP|ORDER|UNION|HAVING|LEFT|RIGHT|INNER|FULL|CROSS|LIMIT|OFFSET|FOR)\b|\)|$)",
    re.IGNORECASE | re.DOTALL,
)


def _fail(rule: str, message: str) -> NoReturn:
    raise SqlValidationError(f"[{rule}] {message}")


class SqlValidator:
    def __init__(self, allowed_tables: set[str]) -> None:
        self.allowed_tables = {table.upper() for table in allowed_tables}

    def validate(self, sql: str) -> None:
        text = _SOLUTION_COMMENT.sub(" ", sql)
        stripped = _LITERAL.sub("''", text)

        if "<$@" in text or "@$>" in text:
            _fail("SV-09", "솔루션 주석의 짝이 맞지 않습니다")
        if not _SELECT_START.match(text):
            _fail("SV-01", "SELECT로 시작하는 조회 문장만 허용합니다")
        if ";" in text:
            _fail("SV-02", "세미콜론(;)은 허용하지 않습니다 (여러 문장 실행 차단)")
        if "--" in text or "/*" in text:
            _fail("SV-03", "주석은 솔루션 형식 <$@ ... @$>만 허용합니다 (--, /* */ 차단)")
        if text.count("'") % 2 == 1:
            _fail("SV-08", "작은따옴표(')의 짝이 맞지 않습니다")

        found = _FORBIDDEN.search(stripped)
        if found:
            _fail("SV-04", f"금지 키워드가 포함되어 있습니다: {found.group(0)}")

        danger = _DANGEROUS_FUNCTION.search(stripped)
        if danger:
            _fail("SV-05", f"위험 함수 호출이 포함되어 있습니다: {danger.group(0)}")

        for table in self._extract_tables(stripped):
            if not re.fullmatch(r"\w+", table):
                _fail("SV-06", f"테이블은 따옴표·스키마 없이 이름만 쓸 수 있습니다: {table}")
            if table.upper() not in self.allowed_tables:
                _fail("SV-06", f"허용되지 않은 테이블입니다: {table}")

    def _extract_tables(self, stripped: str) -> list[str]:
        tables = []
        for m in _TABLE_SECTION.finditer(stripped):
            for piece in m.group(1).split(","):
                word = piece.split()
                if word:
                    tables.append(word[0])
        return tables
