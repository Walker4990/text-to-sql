"""사이트 프로필 파싱 분리 (설계 07, 13장 1단계).

- parse_site_profile(data, origin): 파일을 읽지 않고 dict만 검사·변환한다
- load_site_profile(path)는 파일을 읽어 parse_site_profile에 넘긴다 (동작은 그대로)
- 스냅샷의 _meta.version을 SiteProfile.version으로 읽는다 (없으면 None)
"""

import json
from pathlib import Path

import pytest

from text_to_sql.errors import MetadataError
from text_to_sql.repositories.metadata_repository import load_site_profile, parse_site_profile

SAMPLE_PATH = "src/text_to_sql/metadata/sites/sample.json"


def _sample_data() -> dict[str, object]:
    data: dict[str, object] = json.loads(Path(SAMPLE_PATH).read_text(encoding="utf-8"))
    return data


# 문제 1: 파일을 읽은 결과와 dict를 넘긴 결과가 같다
def test_parse_matches_load() -> None:
    assert parse_site_profile(_sample_data(), "test") == load_site_profile(SAMPLE_PATH)


# 문제 2: _meta가 없으면 version은 None
def test_version_none_without_meta() -> None:
    assert parse_site_profile(_sample_data(), "test").version is None


# 문제 3: _meta.version을 읽는다 (다른 _meta 키는 무시)
def test_version_from_meta() -> None:
    data = {**_sample_data(), "_meta": {"version": "2026-11-12T10:30:00+09:00", "source": "csv"}}
    assert parse_site_profile(data, "test").version == "2026-11-12T10:30:00+09:00"


# 문제 4: _meta가 객체가 아니거나 version이 문자열이 아니면 MetadataError
@pytest.mark.parametrize("meta", ["v1", [1], {"version": 1}])
def test_bad_meta_rejected(meta: object) -> None:
    with pytest.raises(MetadataError):
        parse_site_profile({**_sample_data(), "_meta": meta}, "test")


# 문제 5: 최상위가 객체가 아니면 MetadataError
@pytest.mark.parametrize("data", [[], "text", 1, None])
def test_non_object_rejected(data: object) -> None:
    with pytest.raises(MetadataError):
        parse_site_profile(data, "test")


# 문제 6: 오류 메시지에 출처(origin)가 들어간다
def test_origin_in_error_message() -> None:
    data = _sample_data()
    del data["types"]
    with pytest.raises(MetadataError, match="sheet:demo"):
        parse_site_profile(data, "sheet:demo")


# 문제 7: 파일 오류는 지금처럼 MetadataError
def test_load_missing_and_broken_file(tmp_path: Path) -> None:
    with pytest.raises(MetadataError):
        load_site_profile(str(tmp_path / "none.json"))
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(MetadataError):
        load_site_profile(str(broken))
