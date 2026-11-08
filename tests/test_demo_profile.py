"""시연용 프로필(sites/demo.json) 검사.

보험·카드·증권 현장 시연용 가상 프로필이다. TYPEID는 실제 현장 값과 무관하다.
TYPEID 대역: 1xxx 보험, 2xxx 카드, 3xxx 증권.
가짜 추출기(OpenAI 없음)로 전체 흐름을 돌려, 시연 질문이 검증기를 통과하는 SQL이 되는지 본다.
"""

import pytest

from text_to_sql.domain.intent import Intent
from text_to_sql.repositories.metadata_repository import load_common_metadata, load_site_profile
from text_to_sql.services.intent_extractor import FakeIntentExtractor
from text_to_sql.services.sql_builder import SqlBuilder
from text_to_sql.services.sql_validator import SqlValidator
from text_to_sql.services.text_to_sql_service import TextToSqlService

DEMO_PATH = "src/text_to_sql/metadata/sites/demo.json"
COMMON_PATH = "src/text_to_sql/metadata/common.json"

INSURANCE = {"보험상품", "주계약", "특약", "보장", "보험료율"}
CARD = {"카드상품", "카드혜택", "연회비", "제휴가맹점"}
SECURITIES = {"펀드", "ETF", "계좌상품", "거래수수료"}


def test_demo_profile_has_three_industries() -> None:
    profile = load_site_profile(DEMO_PATH)
    assert profile.site == "demo"
    assert set(profile.types) == INSURANCE | CARD | SECURITIES


def test_typeids_are_unique_and_banded_by_industry() -> None:
    types = load_site_profile(DEMO_PATH).types
    assert len(set(types.values())) == len(types)
    assert all(1000 <= types[name] < 2000 for name in INSURANCE)
    assert all(2000 <= types[name] < 3000 for name in CARD)
    assert all(3000 <= types[name] < 4000 for name in SECURITIES)


def test_every_type_is_found_by_its_own_name() -> None:
    profile = load_site_profile(DEMO_PATH)
    for name, typeid in profile.types.items():
        assert profile.find_typeid(name) == typeid


@pytest.mark.parametrize(
    ("alias", "type_name"),
    [
        ("라이더", "특약"),
        ("담보", "보장"),
        ("카드", "카드상품"),
        ("부가서비스", "카드혜택"),
        ("수수료", "거래수수료"),
        ("상장지수펀드", "ETF"),
    ],
)
def test_field_aliases(alias: str, type_name: str) -> None:
    profile = load_site_profile(DEMO_PATH)
    assert profile.find_typeid(alias) == profile.types[type_name]


@pytest.mark.parametrize(
    "intent",
    [
        Intent(kind="lookup", target_type="보험상품", code="INS001"),
        Intent(kind="list", target_type="라이더"),
        Intent(kind="structure", target_type="카드상품", code="CRD001", direction="children"),
        Intent(kind="lookup", target_type="펀드", name_keyword="글로벌"),
        Intent(kind="structure", target_type="수수료", code="FEE01", direction="parents"),
    ],
)
def test_demo_questions_produce_validated_sql(intent: Intent) -> None:
    common = load_common_metadata(COMMON_PATH)
    profile = load_site_profile(DEMO_PATH)
    service = TextToSqlService(
        FakeIntentExtractor({"q": intent}),
        SqlBuilder(common, profile),
        SqlValidator(set(common.tables)),
    )
    result = service.generate("q", "req-demo")
    assert result.status == "ok"
    assert result.params["type_id"] == profile.find_typeid(str(intent.target_type))
