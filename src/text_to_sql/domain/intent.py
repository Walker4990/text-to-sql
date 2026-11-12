"""의도(Intent) 모델: LLM이 추출하는 JSON의 스키마.

- kind: lookup / list / structure / attribute / pattern / pattern_catalog / unsupported
- 종류별 필수값 검증, code·name_keyword·base_date 형식 검증 (인젝션 첫 관문)"""

import re
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

# 코드 값·항목명칭 형식. Ollama에 넘기는 스키마(build_intent_schema)도 같은 값을 쓴다
CODE_PATTERN = r"^[A-Za-z0-9_.-]{1,32}$"
CODE_ITEM_PATTERN = r"^[가-힣A-Za-z0-9_]{1,30}$"


# 값 조건의 연산자. SQL 기호가 아니라 이름이라 LLM이 기호를 직접 쓰지 않는다
CONDITION_OPS = ("eq", "ne", "lt", "le", "gt", "ge", "contains")
# 조건을 쓸 수 있는 질문 종류
CONDITION_KINDS = ("list", "attribute", "hierarchy")


class Condition(BaseModel):
    """항목 값 조건 한 개 (예: 국내전용연회비 <= 30000).

    숫자 형식 검사는 항목 형식을 아는 빌더가 한다."""

    item: str = Field(pattern=CODE_ITEM_PATTERN)
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "contains"]
    value: str = Field(min_length=1, max_length=50)


class Intent(BaseModel):
    kind: Literal[
        "lookup",
        "list",
        "structure",
        "attribute",
        "hierarchy",
        "pattern",
        "pattern_catalog",
        "unsupported",
    ]
    target_type: str | None = None
    code: str | None = Field(default=None, pattern=CODE_PATTERN)
    name_keyword: str | None = Field(default=None, max_length=50)
    direction: Literal["parents", "children"] | None = None
    base_date: str | None = None
    reason: str | None = None
    attributes: list[str] | None = Field(default=None, max_length=10)
    code_item: str | None = Field(default=None, pattern=CODE_ITEM_PATTERN)
    # DB 룰에서 BIZCODE는 보통 여러 값으로 받으므로 기본은 IN 조건. 값이 하나라고 할 때만 False
    code_item_multi: bool = True
    # hierarchy 전용: target_type 아래로 내려가는 유형 경로 (예: 주계약 → 특약 → 보장), 최대 4단계
    path: list[str] | None = Field(default=None, min_length=1, max_length=4)
    # hierarchy 전용: 단계(유형)별로 꺼낼 항목. 없으면 경로의 각 유형 항목을 전부 꺼낸다
    path_attributes: dict[str, list[str]] | None = None
    # list·attribute·hierarchy 전용: 항목 값 조건. 모두 AND로 묶는다
    conditions: list[Condition] | None = Field(default=None, min_length=1, max_length=5)

    @field_validator("base_date")
    @classmethod
    def check_base_date(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if len(value) != 8 or not value.isdigit():
            raise ValueError("base_date는 YYYYMMDD 형식이어야 합니다.")
        datetime.strptime(value, "%Y%m%d")
        return value

    @model_validator(mode="after")
    def check_required_fileds(self) -> Self:
        if self.kind == "lookup" and not (self.code or self.name_keyword or self.code_item):
            raise ValueError("lookup에는 code, name_keyword, code_item 중 하나가 필요합니다")
        if self.kind == "list" and not self.target_type:
            raise ValueError("list에는 target_type이 필요합니다.")
        if self.kind == "structure" and (
            not self.target_type or not (self.code or self.code_item) or not self.direction
        ):
            raise ValueError(
                "structure에는 target_type, code 또는 code_item, direction이 필요합니다."
            )
        if self.kind == "attribute" and (
            not self.target_type
            or not (self.code or self.name_keyword or self.code_item or self.conditions)
            or not self.attributes
        ):
            raise ValueError(
                "attribute에는 target_type, attributes와 "
                "code·name_keyword·code_item·conditions 중 하나가 필요합니다."
            )
        # 코드도 조건도 없는 계층 전체 덤프는 허용하지 않는다 (조건만으로 거르는 질문은 허용)
        if self.kind == "hierarchy" and (
            not self.target_type
            or not (self.code or self.code_item or self.conditions)
            or not self.path
        ):
            raise ValueError(
                "hierarchy에는 target_type, path와 code·code_item·conditions 중 하나가 필요합니다."
            )
        if self.conditions and self.kind not in CONDITION_KINDS:
            raise ValueError(f"conditions는 {', '.join(CONDITION_KINDS)}에서만 쓸 수 있습니다.")
        return self

    @field_validator("path_attributes")
    @classmethod
    def check_path_attributes(
        cls, value: dict[str, list[str]] | None
    ) -> dict[str, list[str]] | None:
        if value is None:
            return value
        for type_name, names in value.items():
            for name in [type_name, *names]:
                if not re.fullmatch(r"[가-힣A-Za-z0-9_]{1,30}", name):
                    raise ValueError(f"이름 형식이 잘못되었습니다: {name!r}")
            if len(names) > 10:
                raise ValueError(f"한 단계의 항목은 10개까지입니다: {type_name}")
        return value

    @field_validator("attributes")
    @classmethod
    def check_attributes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        for name in value:
            if not re.fullmatch(r"[가-힣A-Za-z0-9_]{1,30}", name):
                raise ValueError(f"항목 이름 형식이 잘못되었습니다: {name!r}")
        return value
