"""서비스 시작 시 프로필 버전 로그 (설계 07, 6장·9.4, 13장 8단계).

requestId와 함께 보면 그 질문을 어느 메타데이터로 처리했는지 재현할 수 있다.
"""

import json
import logging
import shutil
from pathlib import Path

import httpx
import pytest

from text_to_sql.config import Settings
from text_to_sql.factory import create_service

METADATA = Path("src/text_to_sql/metadata")


def settings_for(tmp_path: Path, meta: dict[str, str] | None) -> Settings:
    shutil.copy(METADATA / "common.json", tmp_path / "common.json")
    (tmp_path / "sites").mkdir()
    data = json.loads((METADATA / "sites" / "sample.json").read_text(encoding="utf-8"))
    if meta is not None:
        data["_meta"] = meta
    (tmp_path / "sites" / "sample.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    return Settings(
        ollama_url="http://localhost:11434",
        ollama_model="qwen-test",
        ollama_keep_alive=-1,
        llm_timeout=5,
        site="sample",
        metadata_dir=tmp_path,
    )


# 문제 1: _meta.version을 로그에 남긴다
def test_logs_profile_version(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    settings = settings_for(tmp_path, {"version": "2026-11-12T10:30:00+09:00"})
    with caplog.at_level(logging.INFO, logger="text_to_sql.factory"):
        create_service(settings, client=httpx.Client(base_url="http://test"))
    assert "프로필 로드 site=sample version=2026-11-12T10:30:00+09:00" in caplog.text


# 문제 2: _meta가 없으면 version=unknown
def test_logs_unknown_version(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="text_to_sql.factory"):
        create_service(settings_for(tmp_path, None), client=httpx.Client(base_url="http://test"))
    assert "프로필 로드 site=sample version=unknown" in caplog.text
