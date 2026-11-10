"""의도(Intent) 모델: LLM이 추출하는 JSON의 스키마.

- kind: lookup / list / structure / attribute / pattern / pattern_catalog / unsupported
- 종류별 필수값 검증, code·name_keyword·base_date 형식 검증 (인젝션 첫 관문)"""

import re
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator


class Intent(BaseModel):
    kind: Literal[
        "lookup", "list", "structure", "attribute", "pattern", "pattern_catalog", "unsupported"
    ]
    target_type: str | None = None
    code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,32}$")
    name_keyword: str | None = Field(default=None, max_length=50)
    direction: Literal["parents", "children"] | None = None
    base_date: str | None = None
    reason: str | None = None
    attributes: list[str] | None = Field(default=None, max_length=10)
    code_item: str | None = Field(default=None, pattern=r"^[가-힣A-Za-z0-9_]{1,30}$")
    code_item_multi: bool = False

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
            or not (self.code or self.name_keyword or self.code_item)
            or not self.attributes
        ):
            raise ValueError(
                "attribute에는 target_type, code 또는 name_keyword, attributes가 필요합니다."
            )
        return self

    @field_validator("attributes")
    @classmethod
    def check_attributes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        for name in value:
            if not re.fullmatch(r"[가-힣A-Za-z0-9_]{1,30}", name):
                raise ValueError(f"항목 이름 형식이 잘못되었습니다: {name!r}")
        return value
