"""조립(create_service) 테스트.

챗봇은 create_service(load_settings())로 서비스를 하나 만들어 계속 쓴다.
[스프링] @Configuration의 @Bean 메서드 테스트
"""

import logging

import httpx
import pytest

from text_to_sql.config import Settings, load_settings
from text_to_sql.factory import create_service, start_warm_up
from text_to_sql.services.intent_extractor import FakeIntentExtractor, OllamaIntentExtractor


def settings() -> Settings:
    return load_settings({"TTS_OLLAMA_MODEL": "qwen-test"})


def test_default_client_has_base_url_and_timeout() -> None:
    service = create_service(settings())

    extractor = service.extractor
    assert isinstance(extractor, OllamaIntentExtractor)
    assert str(extractor.client.base_url) == "http://localhost:11434"
    assert extractor.client.timeout.read == 120.0
    assert extractor.model == "qwen-test"
    assert extractor.keep_alive == -1


def test_end_to_end_with_mock_ollama() -> None:
    """가짜 Ollama 응답으로 질문 → SQL까지 한 번에 확인한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        content = '{"kind": "lookup", "target_type": "라이더", "code": "RD001"}'
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    service = create_service(settings(), client=client)

    result = service.generate("RD001 라이더 알려줘", "req-1")

    assert result.status == "ok"
    assert result.params == {"type_id": 1120, "code": "RD001"}  # 기본 프로필 demo: 라이더 → 특약
    assert result.rendered_sql is not None
    assert "M.BIZCODE = 'RD001'" in result.rendered_sql


# 예열: 챗봇이 시작할 때 부른다. 백그라운드 스레드로 돌려 앱 시작을 막지 않는다
def test_start_warm_up_calls_ollama_in_background() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "{}"}})

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    thread = start_warm_up(create_service(settings(), client=client))

    assert thread is not None
    assert thread.daemon  # 앱이 끝날 때 예열이 종료를 막지 않는다
    thread.join(timeout=5)
    assert len(requests) == 1
    assert requests[0].url.path == "/api/chat"


# 예열이 실패해도 앱은 계속 뜬다. 오류는 삼키지 않고 로그(스택 포함)로 남긴다
def test_start_warm_up_logs_failure(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("서버 응답 없음", request=request)

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.ERROR):
        thread = start_warm_up(create_service(settings(), client=client))
        assert thread is not None
        thread.join(timeout=5)
    assert "예열" in caplog.text
    assert any(record.exc_info for record in caplog.records)


# Ollama 추출기가 아니면(테스트용 Fake 등) 예열할 것이 없다
def test_start_warm_up_skips_other_extractors() -> None:
    service = create_service(settings())
    service.extractor = FakeIntentExtractor({})
    assert start_warm_up(service) is None
