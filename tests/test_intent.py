"""의도(Intent) 문제지.

Intent는 "질문에서 뽑아낸 조회 의도"를 담는 객체다. LLM이 질문을 읽고 이 모양의 JSON을 만든다.
[스프링] 요청 DTO + Bean Validation(@Valid, @NotNull, @Pattern)과 같다.

구현할 곳: src/text_to_sql/domain/intent.py 의 Intent 클래스.
pydantic의 BaseModel로 만든다. ([스프링] DTO에 @Valid가 붙어서 잘못된 값이면 예외가 나는 것과 같다)
잘못된 값이면 ValueError가 나야 한다. (pydantic의 ValidationError는 ValueError의 자식이다)
"""

import pytest

from text_to_sql.domain.intent import Intent


# 문제 1: 단건 조회 의도를 만들 수 있다. ("BP001 기본상품 알려줘")
def test_lookup_intent() -> None:
    intent = Intent(kind="lookup", target_type="기본상품", code="BP001")
    assert intent.kind == "lookup"
    assert intent.target_type == "기본상품"
    assert intent.code == "BP001"


# 문제 2: kind는 lookup, list, structure, unsupported 중 하나여야 한다
def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        Intent(kind="hack", target_type="기본상품")


# 문제 3: lookup은 코드(code)나 이름 검색어(name_keyword) 중 하나는 있어야 한다
def test_lookup_requires_code_or_keyword() -> None:
    with pytest.raises(ValueError):
        Intent(kind="lookup", target_type="기본상품")


def test_lookup_with_name_keyword_is_ok() -> None:
    intent = Intent(kind="lookup", target_type="기본상품", name_keyword="프리미엄")
    assert intent.name_keyword == "프리미엄"


# 문제 4 (보안): 코드에 SQL이 섞여 있으면 거부한다
# 허용: 영문, 숫자, _ . - 만, 1~32자
@pytest.mark.parametrize(
    "bad_code",
    ["BP001'; DROP TABLE OBJ_MAIN; --", "OF 001", "", "A" * 33],
)
def test_bad_code_is_rejected(bad_code: str) -> None:
    with pytest.raises(ValueError):
        Intent(kind="lookup", target_type="기본상품", code=bad_code)


# 문제 5: list(목록)는 target_type(유형 이름)이 꼭 있어야 한다
def test_list_requires_target_type() -> None:
    with pytest.raises(ValueError):
        Intent(kind="list")


# 문제 6: structure(상·하위 구성)는 target_type, code, direction이 모두 있어야 한다
# direction은 children(하위) 또는 parents(상위)만 허용
def test_structure_intent() -> None:
    intent = Intent(kind="structure", target_type="기본상품", code="BP001", direction="children")
    assert intent.direction == "children"


def test_structure_requires_direction() -> None:
    with pytest.raises(ValueError):
        Intent(kind="structure", target_type="기본상품", code="BP001")


def test_structure_rejects_unknown_direction() -> None:
    with pytest.raises(ValueError):
        Intent(kind="structure", target_type="기본상품", code="BP001", direction="sideways")


# 문제 7: 기준일자(base_date)는 YYYYMMDD 형식의 실제 있는 날짜여야 한다
def test_base_date_is_accepted() -> None:
    intent = Intent(kind="list", target_type="기본상품", base_date="20261102")
    assert intent.base_date == "20261102"


@pytest.mark.parametrize("bad_date", ["2026-11-02", "20261340", "abc"])
def test_bad_base_date_is_rejected(bad_date: str) -> None:
    with pytest.raises(ValueError):
        Intent(kind="list", target_type="기본상품", base_date=bad_date)


# 문제 8: unsupported(지원하지 않는 질문)는 유형 없이도 만들 수 있고 사유(reason)를 담는다
def test_unsupported_intent() -> None:
    intent = Intent(kind="unsupported", reason="가격 조회는 아직 지원하지 않습니다")
    assert intent.kind == "unsupported"
    assert intent.reason == "가격 조회는 아직 지원하지 않습니다"


# 문제 9: 이름 검색어(name_keyword)는 50자까지만 허용한다
def test_too_long_name_keyword_is_rejected() -> None:
    with pytest.raises(ValueError):
        Intent(kind="lookup", target_type="기본상품", name_keyword="가" * 51)
