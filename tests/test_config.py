"""설정(load_settings) 테스트.

환경변수 딕셔너리를 인자로 넘겨 검사한다 (실제 os.environ을 건드리지 않음).
[스프링] @ConfigurationProperties 바인딩 테스트
"""

from pathlib import Path

import pytest

from text_to_sql.config import DEFAULT_METADATA_DIR, DEFAULT_OLLAMA_MODEL, Settings, load_settings
from text_to_sql.errors import ConfigError


def test_defaults_when_env_is_empty() -> None:
    # 로컬 Ollama 기본 주소로 붙는다. API 키 같은 필수값이 없어서 빈 환경변수로도 시작된다
    settings = load_settings({})
    assert settings == Settings(
        ollama_url="http://localhost:11434",
        ollama_model=DEFAULT_OLLAMA_MODEL,
        ollama_keep_alive=-1,
        llm_timeout=120.0,
        site="demo",
        metadata_dir=DEFAULT_METADATA_DIR,
    )


def test_default_metadata_dir_is_inside_package() -> None:
    # 실행 위치(cwd)와 무관하게 패키지 안의 메타데이터를 찾아야 챗봇 폴더에서 실행해도 동작한다
    assert DEFAULT_METADATA_DIR.is_absolute()
    assert (DEFAULT_METADATA_DIR / "common.json").is_file()
    assert (DEFAULT_METADATA_DIR / "sites" / "sample.json").is_file()
    assert (DEFAULT_METADATA_DIR / "sites" / "demo.json").is_file()


def test_values_are_read_from_env() -> None:
    settings = load_settings(
        {
            "TTS_OLLAMA_URL": "http://gpu-server:11434",
            "TTS_OLLAMA_MODEL": "qwen-test",
            "TTS_LLM_TIMEOUT": "5",
            "TTS_SITE": "other",
            "TTS_METADATA_DIR": "C:/some/dir",
        }
    )
    assert settings.ollama_model == "qwen-test"
    assert settings.ollama_url == "http://gpu-server:11434"
    assert settings.llm_timeout == 5.0
    assert settings.site == "other"
    assert settings.metadata_dir == Path("C:/some/dir")


def test_metadata_paths() -> None:
    settings = load_settings({"TTS_SITE": "sample"})
    assert settings.common_path == DEFAULT_METADATA_DIR / "common.json"
    assert settings.profile_path == DEFAULT_METADATA_DIR / "sites" / "sample.json"


# 빈 값으로 두면 요청 주소·모델 이름이 비어 Ollama 호출 때에야 실패한다 → 시작할 때 막는다
@pytest.mark.parametrize("name", ["TTS_OLLAMA_URL", "TTS_OLLAMA_MODEL"])
@pytest.mark.parametrize("value", ["", "   "])
def test_blank_ollama_setting_raises(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        load_settings({name: value})


@pytest.mark.parametrize("timeout", ["abc", "0", "-1"])
def test_invalid_timeout_raises(timeout: str) -> None:
    with pytest.raises(ConfigError, match="TTS_LLM_TIMEOUT"):
        load_settings({"TTS_LLM_TIMEOUT": timeout})


# 사이트 이름은 파일 경로에 들어가므로 ../ 같은 경로 이동을 막는다
@pytest.mark.parametrize("site", ["../etc", "a/b", "a\\b", ""])
def test_invalid_site_raises(site: str) -> None:
    with pytest.raises(ConfigError, match="TTS_SITE"):
        load_settings({"TTS_SITE": site})


# keep_alive: 숫자(초, -1은 계속 유지)는 int로, 단위가 붙은 값("30m")은 글자 그대로 Ollama에 보낸다
@pytest.mark.parametrize(
    ("value", "expected"),
    [("-1", -1), ("0", 0), ("600", 600), ("30m", "30m"), ("2h", "2h"), ("45s", "45s")],
)
def test_keep_alive_values(value: str, expected: int | str) -> None:
    assert load_settings({"TTS_OLLAMA_KEEP_ALIVE": value}).ollama_keep_alive == expected


# Ollama가 못 알아듣는 값은 시작할 때 막는다 ("1d"처럼 일 단위는 받지 않는다)
@pytest.mark.parametrize("value", ["", "abc", "1d", "30 m", "-"])
def test_invalid_keep_alive_raises(value: str) -> None:
    with pytest.raises(ConfigError, match="TTS_OLLAMA_KEEP_ALIVE"):
        load_settings({"TTS_OLLAMA_KEEP_ALIVE": value})
