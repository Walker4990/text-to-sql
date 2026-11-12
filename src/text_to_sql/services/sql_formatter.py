"""사람이 읽기 좋게 SQL에 줄바꿈·들여쓰기를 넣는다 (공백만 바꾼다).

빌더가 만드는 SQL 모양(SELECT · FROM · JOIN · LEFT JOIN · WHERE · ORDER BY)만 다룬다.
따옴표 문자열, 큰따옴표 별칭, 룰 변수(<$@ … @$>) 안은 건드리지 않고,
괄호·CASE … END 안과 BETWEEN … AND …, JOIN의 ON 조건은 나누지 않는다.
"""

import re
from dataclasses import dataclass

# 한 덩어리로 다룰 조각: 룰 변수, 문자열, 별칭, 단어, 그 밖의 기호 한 글자
_TOKEN = re.compile(
    r"<\$@.*?@\$>"
    r"|'(?:[^']|'')*'"
    r'|"[^"]*"'
    r"|[A-Za-z_][A-Za-z0-9_.]*"
    r"|\S",
    re.DOTALL,
)
SELECT_ONE_LINE_MAX = 60
_INDENT = "    "


@dataclass
class _Token:
    text: str
    space_before: bool

    @property
    def word(self) -> str:
        return self.text.upper()


def _tokenize(sql: str) -> list[_Token]:
    tokens: list[_Token] = []
    end = 0
    for match in _TOKEN.finditer(sql):
        tokens.append(_Token(match.group(0), match.start() > end))
        end = match.end()
    return tokens


def _join(tokens: list[_Token]) -> str:
    """원래 공백이 있던 자리에만 공백 하나를 둔다."""
    text = ""
    for i, token in enumerate(tokens):
        if i > 0 and token.space_before:
            text += " "
        text += token.text
    return text


def _split_clauses(tokens: list[_Token]) -> list[list[_Token]]:
    """괄호·CASE 밖에서 절 키워드와 WHERE의 AND 앞을 끊는다."""
    lines: list[list[_Token]] = [[]]
    depth = 0
    in_where = False
    pending_between = False
    for i, token in enumerate(tokens):
        word = token.word
        following = tokens[i + 1].word if i + 1 < len(tokens) else ""
        previous = tokens[i - 1].word if i > 0 else ""
        breaks = False
        if depth == 0:
            if word in ("FROM", "WHERE") or (word == "ORDER" and following == "BY"):
                breaks = True
                in_where = word == "WHERE"
            elif word == "LEFT" and following == "JOIN":
                breaks = True
                in_where = False
            elif word == "JOIN" and previous != "LEFT":
                breaks = True
                in_where = False
            elif word == "BETWEEN":
                pending_between = True
            elif word == "AND":
                if pending_between:
                    pending_between = False
                elif in_where:
                    breaks = True
        if token.text == "(" or word == "CASE":
            depth += 1
        elif (token.text == ")" or word == "END") and depth > 0:
            depth -= 1
        if breaks and lines[-1]:
            lines.append([])
        lines[-1].append(token)
    return lines


def _format_select(tokens: list[_Token]) -> str:
    columns: list[list[_Token]] = [[]]
    depth = 0
    for token in tokens[1:]:
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
        if token.text == "," and depth == 0:
            columns.append([])
            continue
        columns[-1].append(token)
    parts = [_join(column) for column in columns if column]
    one_line = ", ".join(parts)
    if len(one_line) <= SELECT_ONE_LINE_MAX:
        return f"{tokens[0].text} {one_line}"
    return tokens[0].text + "\n" + ",\n".join(_INDENT + part for part in parts)


def format_sql(sql: str) -> str:
    lines = _split_clauses(_tokenize(sql))
    out: list[str] = []
    for line in lines:
        if not line:
            continue
        if line[0].word == "SELECT":
            out.append(_format_select(line))
        elif line[0].word == "AND":
            out.append("  " + _join(line))
        else:
            out.append(_join(line))
    return "\n".join(out)
