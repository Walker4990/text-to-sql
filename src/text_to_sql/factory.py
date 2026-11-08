"""조립: 설정을 받아 TextToSqlService를 만든다.

[스프링] @Configuration 클래스의 @Bean 메서드. 부품(메타데이터, 빌더, 검증기, 추출기)을 만들어
생성자 주입으로 서비스에 꽂는다. 챗봇은 앱이 시작할 때 이 함수를 한 번 불러 서비스를 계속 쓴다.
"""

import httpx

from text_to_sql.config import Settings
from text_to_sql.repositories.metadata_repository import load_common_metadata, load_site_profile
from text_to_sql.services.intent_extractor import OpenAiIntentExtractor
from text_to_sql.services.sql_builder import SqlBuilder
from text_to_sql.services.sql_validator import SqlValidator
from text_to_sql.services.text_to_sql_service import TextToSqlService


def create_service(settings: Settings, client: httpx.Client | None = None) -> TextToSqlService:
    """설정대로 서비스를 만든다.

    client를 넘기지 않으면 OpenAI 주소·API 키·타임아웃을 넣은 httpx.Client를 새로 만든다.
    테스트에서는 가짜 서버(MockTransport)를 단 client를 넘긴다.
    메타데이터·프로필 파일이 없거나 잘못되면 MetadataError가 난다 (시작할 때 바로 실패).
    """
    common = load_common_metadata(str(settings.common_path))
    profile = load_site_profile(str(settings.profile_path))

    if client is None:
        client = httpx.Client(
            base_url=settings.openai_base_url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            timeout=settings.llm_timeout,
        )

    extractor = OpenAiIntentExtractor(client, settings.openai_model, profile)
    builder = SqlBuilder(common, profile)
    validator = SqlValidator(set(common.tables))
    return TextToSqlService(extractor, builder, validator)
