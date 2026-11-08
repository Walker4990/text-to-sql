"""설정: 환경변수에서 OpenAI 접속 정보·사이트 프로필·메타데이터 경로를 읽는다.

[스프링] @ConfigurationProperties. 앱이 시작할 때 한 번 읽어 Settings 객체로 들고 다닌다.
환경변수 딕셔너리를 인자로 받을 수 있어서, 테스트에서 os.environ을 건드리지 않는다.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from text_to_sql.errors import ConfigError

# 패키지 안의 메타데이터 폴더. __file__(이 파일) 기준이라 실행 위치(cwd)와 무관하다
# [스프링] classpath:metadata/ 에 해당
DEFAULT_METADATA_DIR = Path(__file__).resolve().parent / "metadata"

# 사이트 이름은 파일 경로(sites/<이름>.json)에 들어가므로 ../ 같은 경로 이동을 막는다
_SITE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Settings:
    """읽어 들인 설정 값. frozen=True라 만든 뒤 바꿀 수 없다 (불변 객체)."""

    openai_api_key: str
    openai_model: str
    openai_base_url: str
    llm_timeout: float
    site: str
    metadata_dir: Path

    @property
    def common_path(self) -> Path:
        return self.metadata_dir / "common.json"

    @property
    def profile_path(self) -> Path:
        return self.metadata_dir / "sites" / f"{self.site}.json"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """환경변수를 읽어 Settings를 만든다. 값이 잘못되면 ConfigError (시작할 때 바로 실패).

    | 환경변수 | 기본값 |
    |---|---|
    | OPENAI_API_KEY | 없음 (필수, 챗봇과 같은 이름) |
    | TTS_OPENAI_MODEL | gpt-4o-mini |
    | TTS_OPENAI_BASE_URL | https://api.openai.com/v1 |
    | TTS_LLM_TIMEOUT | 30 (초) |
    | TTS_SITE | sample |
    | TTS_METADATA_DIR | 패키지 안 metadata 폴더 |
    """
    source = os.environ if env is None else env

    api_key = source.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ConfigError("OPENAI_API_KEY 환경변수가 없습니다")

    timeout_text = source.get("TTS_LLM_TIMEOUT", "30")
    try:
        timeout = float(timeout_text)
    except ValueError as err:
        raise ConfigError(f"TTS_LLM_TIMEOUT은 숫자여야 합니다: {timeout_text}") from err
    if timeout <= 0:
        raise ConfigError(f"TTS_LLM_TIMEOUT은 0보다 커야 합니다: {timeout_text}")

    site = source.get("TTS_SITE", "sample")
    if not _SITE_NAME.fullmatch(site):
        raise ConfigError(f"TTS_SITE는 영문·숫자·_- 만 쓸 수 있습니다: {site!r}")

    metadata_dir_text = source.get("TTS_METADATA_DIR")
    metadata_dir = Path(metadata_dir_text) if metadata_dir_text else DEFAULT_METADATA_DIR

    return Settings(
        openai_api_key=api_key,
        openai_model=source.get("TTS_OPENAI_MODEL", "gpt-4o-mini"),
        openai_base_url=source.get("TTS_OPENAI_BASE_URL", "https://api.openai.com/v1"),
        llm_timeout=timeout,
        site=site,
        metadata_dir=metadata_dir,
    )
