"""조립(create_service) 테스트.

챗봇은 create_service(load_settings())로 서비스를 하나 만들어 계속 쓴다.
[스프링] @Configuration의 @Bean 메서드 테스트
"""

import httpx

from text_to_sql.config import Settings, load_settings
from text_to_sql.factory import create_service
from text_to_sql.services.intent_extractor import OpenAiIntentExtractor


def settings() -> Settings:
    return load_settings({"OPENAI_API_KEY": "sk-test", "TTS_OPENAI_MODEL": "gpt-test"})


def test_default_client_has_base_url_auth_and_timeout() -> None:
    service = create_service(settings())

    extractor = service.extractor
    assert isinstance(extractor, OpenAiIntentExtractor)
    assert str(extractor.client.base_url) == "https://api.openai.com/v1/"
    assert extractor.client.headers["Authorization"] == "Bearer sk-test"
    assert extractor.client.timeout.read == 30.0
    assert extractor.model == "gpt-test"


def test_end_to_end_with_mock_openai() -> None:
    """가짜 OpenAI 응답으로 질문 → SQL까지 한 번에 확인한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        content = '{"kind": "lookup", "target_type": "기본상품", "code": "BP001"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.Client(base_url="http://openai.test/v1", transport=httpx.MockTransport(handler))
    service = create_service(settings(), client=client)

    result = service.generate("BP001 기본상품 알려줘", "req-1")

    assert result.status == "ok"
    assert result.params == {"type_id": 901, "code": "BP001"}
    assert result.rendered_sql is not None
    assert "M.BIZCODE = 'BP001'" in result.rendered_sql
