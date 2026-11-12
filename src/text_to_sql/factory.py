"""조립: 설정을 받아 TextToSqlService를 만든다.

부품(메타데이터, 빌더, 검증기, 추출기)을 만들어 생성자로 서비스에 넣는다.
챗봇은 앱이 시작할 때 이 함수를 한 번 불러 서비스를 계속 쓴다.
"""

import logging
import threading
import time

import httpx

from text_to_sql.config import Settings
from text_to_sql.errors import LlmUnavailableError
from text_to_sql.repositories.metadata_repository import load_common_metadata, load_site_profile
from text_to_sql.services.intent_extractor import OllamaIntentExtractor
from text_to_sql.services.sql_builder import SqlBuilder
from text_to_sql.services.sql_validator import SqlValidator
from text_to_sql.services.text_to_sql_service import TextToSqlService

logger = logging.getLogger(__name__)


def create_service(settings: Settings, client: httpx.Client | None = None) -> TextToSqlService:
    """설정대로 서비스를 만든다.

    client를 넘기지 않으면 Ollama 주소·타임아웃을 넣은 httpx.Client를 새로 만든다.
    테스트에서는 가짜 서버(MockTransport)를 단 client를 넘긴다.
    메타데이터·프로필 파일이 없거나 잘못되면 MetadataError가 난다 (시작할 때 바로 실패).
    """
    common = load_common_metadata(str(settings.common_path))
    profile = load_site_profile(str(settings.profile_path))
    # requestId 로그와 함께 보면 그 질문을 어느 메타데이터로 처리했는지 재현할 수 있다
    logger.info("프로필 로드 site=%s version=%s", profile.site, profile.version or "unknown")

    if client is None:
        client = httpx.Client(
            base_url=settings.ollama_url,
            timeout=settings.llm_timeout,
        )

    extractor = OllamaIntentExtractor(
        client, settings.ollama_model, profile, keep_alive=settings.ollama_keep_alive
    )
    builder = SqlBuilder(common, profile)
    validator = SqlValidator(set(common.tables))
    return TextToSqlService(extractor, builder, validator)


def start_warm_up(service: TextToSqlService) -> threading.Thread | None:
    """Ollama 예열을 백그라운드 스레드로 시작한다. 앱이 시작할 때 create_service 다음에 부른다.

    예열은 CPU에서 1분 가까이 걸리므로 앱 시작을 막지 않게 따로 돌린다.
    예열 중에 들어온 질문은 Ollama가 순서대로 처리하므로 예열이 끝날 때까지 기다린다.
    실패해도 앱은 계속 뜨고, 오류는 스택과 함께 로그로 남긴다 (첫 질문만 느려질 뿐이다).
    Ollama 추출기가 아니면(테스트용 Fake 등) 할 일이 없어 None을 돌려준다.
    """
    extractor = service.extractor
    if not isinstance(extractor, OllamaIntentExtractor):
        return None

    def run() -> None:
        started = time.perf_counter()
        try:
            extractor.warm_up()
        except LlmUnavailableError:
            logger.exception("Ollama 예열에 실패했습니다 model=%s", extractor.model)
            return
        logger.info(
            "Ollama 예열 완료 model=%s elapsed=%.1fs",
            extractor.model,
            time.perf_counter() - started,
        )

    thread = threading.Thread(target=run, name="ollama-warm-up", daemon=True)
    thread.start()
    return thread
