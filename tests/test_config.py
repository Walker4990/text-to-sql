"""설정(load_settings) 테스트.

환경변수 딕셔너리를 인자로 넘겨 검사한다 (실제 os.environ을 건드리지 않음).
[스프링] @ConfigurationProperties 바인딩 테스트
"""

from pathlib import Path

import pytest

from text_to_sql.config import DEFAULT_METADATA_DIR, Settings, load_settings
from text_to_sql.errors import ConfigError

KEY_ONLY = {"OPENAI_API_KEY": "sk-test"}


def test_defaults_when_only_api_key_is_set() -> None:
    settings = load_settings(KEY_ONLY)
    assert settings == Settings(
        openai_api_key="sk-test",
        openai_model="gpt-4o-mini",
        openai_base_url="https://api.openai.com/v1",
        llm_timeout=30.0,
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
            "OPENAI_API_KEY": "sk-test",
            "TTS_OPENAI_MODEL": "gpt-test",
            "TTS_OPENAI_BASE_URL": "http://localhost:9999/v1",
            "TTS_LLM_TIMEOUT": "5",
            "TTS_SITE": "other",
            "TTS_METADATA_DIR": "C:/some/dir",
        }
    )
    assert settings.openai_model == "gpt-test"
    assert settings.openai_base_url == "http://localhost:9999/v1"
    assert settings.llm_timeout == 5.0
    assert settings.site == "other"
    assert settings.metadata_dir == Path("C:/some/dir")


def test_metadata_paths() -> None:
    settings = load_settings({"OPENAI_API_KEY": "sk-test", "TTS_SITE": "sample"})
    assert settings.common_path == DEFAULT_METADATA_DIR / "common.json"
    assert settings.profile_path == DEFAULT_METADATA_DIR / "sites" / "sample.json"


@pytest.mark.parametrize("env", [{}, {"OPENAI_API_KEY": ""}, {"OPENAI_API_KEY": "   "}])
def test_missing_api_key_raises(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        load_settings(env)


@pytest.mark.parametrize("timeout", ["abc", "0", "-1"])
def test_invalid_timeout_raises(timeout: str) -> None:
    with pytest.raises(ConfigError, match="TTS_LLM_TIMEOUT"):
        load_settings({**KEY_ONLY, "TTS_LLM_TIMEOUT": timeout})


# 사이트 이름은 파일 경로에 들어가므로 ../ 같은 경로 이동을 막는다
@pytest.mark.parametrize("site", ["../etc", "a/b", "a\\b", ""])
def test_invalid_site_raises(site: str) -> None:
    with pytest.raises(ConfigError, match="TTS_SITE"):
        load_settings({**KEY_ONLY, "TTS_SITE": site})
