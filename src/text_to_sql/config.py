"""설정: 환경변수에서 Ollama 접속 정보·사이트 프로필·메타데이터 경로를 읽는다.

앱이 시작할 때 한 번 읽어 Settings 객체로 들고 다닌다.
환경변수 딕셔너리를 인자로 받을 수 있어서, 테스트에서 os.environ을 건드리지 않는다.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from text_to_sql.errors import ConfigError

# 패키지 안의 메타데이터 폴더. __file__(이 파일) 기준이라 실행 위치(cwd)와 무관하다
DEFAULT_METADATA_DIR = Path(__file__).resolve().parent / "metadata"

# 사이트 이름은 파일 경로(sites/<이름>.json)에 들어가므로 ../ 같은 경로 이동을 막는다
_SITE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

# 의도 추출에 쓰는 로컬 채팅 모델. bge-m3 같은 임베딩 모델은 문장을 만들지 못해 쓸 수 없다
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"

# Ollama keep_alive 형식: 초(정수, -1은 계속 유지) 또는 단위가 붙은 기간(30m, 2h, 45s)
_KEEP_ALIVE_SECONDS = re.compile(r"^-?\d+$")
_KEEP_ALIVE_DURATION = re.compile(r"^\d+(?:s|m|h)$")


@dataclass(frozen=True)
class Settings:
    """읽어 들인 설정 값. frozen=True라 만든 뒤 바꿀 수 없다 (불변 객체)."""

    ollama_url: str
    ollama_model: str
    ollama_keep_alive: int | str
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
    | TTS_OLLAMA_URL | http://localhost:11434 |
    | TTS_OLLAMA_MODEL | DEFAULT_OLLAMA_MODEL |
    | TTS_OLLAMA_KEEP_ALIVE | -1 (모델을 메모리에서 내리지 않음. 30m처럼 기간도 가능) |
    | TTS_LLM_TIMEOUT | 120 (초. CPU에서는 첫 호출에 모델을 읽느라 오래 걸린다) |
    | TTS_SITE | demo |
    | TTS_METADATA_DIR | 패키지 안 metadata 폴더 |
    """
    source = os.environ if env is None else env

    ollama_url = source.get("TTS_OLLAMA_URL", "http://localhost:11434").strip()
    if not ollama_url:
        raise ConfigError("TTS_OLLAMA_URL이 비어 있습니다")
    ollama_model = source.get("TTS_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip()
    if not ollama_model:
        raise ConfigError("TTS_OLLAMA_MODEL이 비어 있습니다")

    # 모델을 내리면 프롬프트 캐시도 사라져 다음 질문이 다시 느려진다 (CPU에서 약 1분)
    keep_alive_text = source.get("TTS_OLLAMA_KEEP_ALIVE", "-1").strip()
    keep_alive: int | str
    if _KEEP_ALIVE_SECONDS.fullmatch(keep_alive_text):
        keep_alive = int(keep_alive_text)
    elif _KEEP_ALIVE_DURATION.fullmatch(keep_alive_text):
        keep_alive = keep_alive_text
    else:
        raise ConfigError(
            "TTS_OLLAMA_KEEP_ALIVE는 초(정수) 또는 30m·2h·45s 형식이어야 합니다: "
            f"{keep_alive_text!r}"
        )

    timeout_text = source.get("TTS_LLM_TIMEOUT", "120")
    try:
        timeout = float(timeout_text)
    except ValueError as err:
        raise ConfigError(f"TTS_LLM_TIMEOUT은 숫자여야 합니다: {timeout_text}") from err
    if timeout <= 0:
        raise ConfigError(f"TTS_LLM_TIMEOUT은 0보다 커야 합니다: {timeout_text}")

    site = source.get("TTS_SITE", "demo")
    if not _SITE_NAME.fullmatch(site):
        raise ConfigError(f"TTS_SITE는 영문·숫자·_- 만 쓸 수 있습니다: {site!r}")

    metadata_dir_text = source.get("TTS_METADATA_DIR")
    metadata_dir = Path(metadata_dir_text) if metadata_dir_text else DEFAULT_METADATA_DIR

    return Settings(
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        ollama_keep_alive=keep_alive,
        llm_timeout=timeout,
        site=site,
        metadata_dir=metadata_dir,
    )
