"""Ollama format에 넣는 의도 JSON 스키마(build_intent_schema) 테스트.

작은 로컬 모델이 없는 유형·항목을 지어내거나, 종류별 전용 칸을 섞어 쓰는 일을 막기 위해
프로필로 스키마를 만든다. 모델은 이 스키마 밖의 글자를 출력할 수 없다.
- kind마다 쓸 수 있는 칸만 허용 (attribute에는 path 없음 등)
- target_type·path·attributes는 프로필에 있는 이름만 (enum)
[스프링] 요청마다 바뀌지 않는 설정 값을 시작할 때 한 번 만들어 두는 것과 같다
"""

from typing import Any

from text_to_sql.domain.intent import CODE_ITEM_PATTERN, CODE_PATTERN
from text_to_sql.repositories.metadata_repository import SiteProfile, load_site_profile
from text_to_sql.services.intent_extractor import build_intent_schema

SAMPLE_PATH = "src/text_to_sql/metadata/sites/sample.json"


def sample_profile() -> SiteProfile:
    return load_site_profile(SAMPLE_PATH)


def branches(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """anyOf의 각 갈래를 kind 이름으로 찾을 수 있게 바꾼다."""
    return {branch["properties"]["kind"]["const"]: branch for branch in schema["anyOf"]}


# 문제 1: kind마다 갈래가 하나씩 있다 (항목이 있는 프로필이면 attribute 포함)
def test_schema_has_branch_per_kind() -> None:
    schema = build_intent_schema(sample_profile())
    assert list(branches(schema)) == [
        "lookup",
        "list",
        "structure",
        "attribute",
        "hierarchy",
        "unsupported",
    ]


# 문제 2: 갈래마다 정해진 칸만 쓸 수 있다 (모르는 칸 금지)
def test_each_branch_forbids_other_fields() -> None:
    by_kind = branches(build_intent_schema(sample_profile()))
    for branch in by_kind.values():
        assert branch["additionalProperties"] is False
    assert "path" not in by_kind["attribute"]["properties"]
    assert "attributes" not in by_kind["lookup"]["properties"]
    assert "attributes" not in by_kind["hierarchy"]["properties"]
    assert "direction" not in by_kind["lookup"]["properties"]
    assert set(by_kind["unsupported"]["properties"]) == {"kind", "reason"}


# 문제 3: 종류별 필수 칸 (Intent 검사와 같은 기준)
def test_required_fields_per_kind() -> None:
    by_kind = branches(build_intent_schema(sample_profile()))
    assert by_kind["lookup"]["required"] == ["kind", "target_type"]
    assert by_kind["list"]["required"] == ["kind", "target_type"]
    assert by_kind["structure"]["required"] == ["kind", "target_type", "direction"]
    assert by_kind["attribute"]["required"] == ["kind", "target_type", "attributes"]
    assert by_kind["hierarchy"]["required"] == ["kind", "target_type", "path"]
    assert by_kind["unsupported"]["required"] == ["kind", "reason"]


# 문제 4: target_type·path는 프로필의 유형 이름과 별칭만 (빌더가 별칭을 정식 이름으로 바꾼다)
def test_type_names_come_from_profile() -> None:
    profile = sample_profile()
    by_kind = branches(build_intent_schema(profile))
    expected = list(profile.aliases)
    for kind in ["lookup", "list", "structure", "attribute", "hierarchy"]:
        assert by_kind[kind]["properties"]["target_type"]["enum"] == expected, kind
    assert by_kind["hierarchy"]["properties"]["path"]["items"]["enum"] == expected


# 문제 5: attributes·path_attributes의 항목은 프로필 항목 이름만 (중복 없이 정렬)
def test_item_names_come_from_profile() -> None:
    profile = sample_profile()
    by_kind = branches(build_intent_schema(profile))
    expected = sorted({name for items in profile.items.values() for name in items})
    attributes = by_kind["attribute"]["properties"]["attributes"]
    assert attributes["items"]["enum"] == expected
    assert attributes["minItems"] == 1
    assert attributes["maxItems"] == 10
    path_attributes = by_kind["hierarchy"]["properties"]["path_attributes"]
    assert path_attributes["additionalProperties"]["items"]["enum"] == expected


# 문제 6: code·code_item은 Intent와 같은 형식 검사. code_item은 enum을 걸지 않는다
# (enum을 걸면 모델이 질문에 없는 항목명칭을 억지로 채운다. 등록 여부는 빌더가 검사)
def test_code_fields_use_intent_patterns() -> None:
    lookup = branches(build_intent_schema(sample_profile()))["lookup"]["properties"]
    assert lookup["code"]["pattern"] == CODE_PATTERN
    assert lookup["code_item"] == {"type": "string", "pattern": CODE_ITEM_PATTERN}


# 문제 7: 단일·다중(code_item_multi)은 모델이 고르지 않는다 (추출기가 질문 글자로 정한다)
def test_schema_has_no_code_item_multi() -> None:
    for branch in build_intent_schema(sample_profile())["anyOf"]:
        assert "code_item_multi" not in branch["properties"]


# 문제 8: 항목이 없는 프로필이면 attribute 갈래와 path_attributes가 없다
def test_profile_without_items() -> None:
    profile = SiteProfile(site="x", types={"펀드": 3100}, aliases={"펀드": "펀드"})
    by_kind = branches(build_intent_schema(profile))
    assert "attribute" not in by_kind
    assert "path_attributes" not in by_kind["hierarchy"]["properties"]
