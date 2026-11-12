"""동기화 도구 CLI (설계 07, 7장, 13장 6단계).

main(argv, env, sheet_factory, now)를 직접 불러 종료 코드·출력·저장 파일을 확인한다.
출처는 CSV 폴더(--csv-dir) 또는 가짜 시트 팩토리를 쓴다 (구글 접속 없음).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from text_to_sql.errors import ProfileSourceError
from text_to_sql.repositories.metadata_repository import load_site_profile
from text_to_sql.repositories.profile_source import CsvFolderSource, ProfileSource
from text_to_sql.sync.cli import main
from text_to_sql.sync.snapshot import export_csv

DEMO = Path("src/text_to_sql/metadata/sites/demo.json")
FIXED_NOW = datetime(2026, 11, 12, 10, 30, tzinfo=timezone(timedelta(hours=9)))


@pytest.fixture
def csv_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "sheet"
    export_csv(load_site_profile(str(DEMO)), folder, owner="홍길동")
    return folder


def run(*args: str, out: Path, env: dict[str, str] | None = None) -> int:
    return main([*args, "--out-dir", str(out)], env=env or {}, now=lambda: FIXED_NOW)


def snapshot(out: Path) -> Path:
    return out / "sites" / "demo.json"


def edit_csv(folder: Path, tab: str, old: str, new: str) -> None:
    path = folder / f"{tab}.csv"
    path.write_text(path.read_text(encoding="utf-8-sig").replace(old, new), encoding="utf-8-sig")


# 문제 1: 미리보기 — 저장하지 않고 0
def test_preview(csv_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    assert run("--site", "demo", "--csv-dir", str(csv_dir), out=out) == 0
    assert not snapshot(out).exists()
    printed = capsys.readouterr().out
    assert "새로 만듦" in printed
    assert "미리보기" in printed


# 문제 2: --apply — 스냅샷 저장, _meta 포함, 다시 읽으면 원래 프로필과 같음
def test_apply_new(csv_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert run("--site", "demo", "--csv-dir", str(csv_dir), "--apply", out=out) == 0
    text = snapshot(out).read_text(encoding="utf-8")
    data = json.loads(text)
    assert list(data)[0] == "_meta"
    assert data["_meta"]["version"] == "2026-11-12T10:30:00+09:00"
    assert data["_meta"]["source"] == "csv"
    assert data["_meta"]["owner"] == "홍길동"
    assert "보험상품" in text  # 한글을 \uXXXX로 바꾸지 않는다
    assert text.endswith("\n")
    loaded = load_site_profile(str(snapshot(out)))
    assert loaded.version == "2026-11-12T10:30:00+09:00"
    assert loaded.types == load_site_profile(str(DEMO)).types


# 문제 3: 검증 실패 — 1, 스냅샷 그대로
def test_validation_failure(
    csv_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    run("--site", "demo", "--csv-dir", str(csv_dir), "--apply", out=out)
    before = snapshot(out).read_bytes()
    edit_csv(csv_dir, "유형", "보험상품,1100", "보험상품,1,100")
    assert run("--site", "demo", "--csv-dir", str(csv_dir), "--apply", out=out) == 1
    assert snapshot(out).read_bytes() == before
    assert "검증 실패" in capsys.readouterr().out


# 문제 4: 위험 변경 — --yes 없으면 3(저장 안 함), 있으면 0(저장)
def test_risky_change_needs_yes(csv_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    run("--site", "demo", "--csv-dir", str(csv_dir), "--apply", out=out)
    before = snapshot(out).read_bytes()
    edit_csv(csv_dir, "유형", "특약,1120", "특약,1125")
    assert run("--site", "demo", "--csv-dir", str(csv_dir), "--apply", out=out) == 3
    assert snapshot(out).read_bytes() == before
    assert run("--site", "demo", "--csv-dir", str(csv_dir), "--apply", "--yes", out=out) == 0
    assert load_site_profile(str(snapshot(out))).types["특약"] == 1125


# 문제 5: 안전한 변경은 --yes 없이 저장된다
def test_safe_change_saved(csv_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    run("--site", "demo", "--csv-dir", str(csv_dir), "--apply", out=out)
    with (csv_dir / "별칭.csv").open("a", encoding="utf-8") as f:
        f.write("특약상품,특약,Y,\n")
    assert run("--site", "demo", "--csv-dir", str(csv_dir), "--apply", out=out) == 0
    assert load_site_profile(str(snapshot(out))).aliases["특약상품"] == "특약"


# 문제 6: 차이가 없으면 다시 쓰지 않는다 (버전이 바뀌지 않음)
def test_no_changes_keeps_file(
    csv_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    run("--site", "demo", "--csv-dir", str(csv_dir), "--apply", out=out)
    before = snapshot(out).read_bytes()
    assert main(
        ["--site", "demo", "--csv-dir", str(csv_dir), "--apply", "--out-dir", str(out)],
        env={},
        now=lambda: FIXED_NOW + timedelta(days=1),
    ) == 0
    assert snapshot(out).read_bytes() == before
    assert "차이 없음" in capsys.readouterr().out


# 문제 7: 시트 ID·키 경로가 없으면 2
def test_sheet_settings_missing(tmp_path: Path) -> None:
    assert run("--site", "demo", out=tmp_path) == 2


# 문제 8: 출처를 읽지 못하면 2
def test_source_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("--site", "demo", "--csv-dir", str(tmp_path / "none"), out=tmp_path) == 2
    assert "접근 실패" in capsys.readouterr().out


# 문제 9: 시트 출처 — 환경변수의 ID·키 경로를 팩토리에 넘기고 _meta에 sheet_id를 남긴다
def test_sheet_source_from_env(csv_dir: Path, tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def factory(credentials: str, sheet_id: str) -> ProfileSource:
        calls.append((credentials, sheet_id))
        return CsvFolderSource(csv_dir)

    out = tmp_path / "out"
    env = {"TTS_SHEET_ID": "sheet-123", "TTS_GOOGLE_CREDENTIALS": "key.json"}
    code = main(
        ["--site", "demo", "--apply", "--out-dir", str(out)],
        env=env,
        sheet_factory=factory,
        now=lambda: FIXED_NOW,
    )
    assert code == 0
    assert calls == [("key.json", "sheet-123")]
    meta = json.loads(snapshot(out).read_text(encoding="utf-8"))["_meta"]
    assert meta["source"] == "google_sheet"
    assert meta["sheet_id"] == "sheet-123"


# 문제 10: 시트 팩토리가 실패해도 2
def test_sheet_factory_error(tmp_path: Path) -> None:
    def factory(credentials: str, sheet_id: str) -> ProfileSource:
        raise ProfileSourceError("키 파일 없음")

    args = ["--site", "demo", "--sheet-id", "x", "--credentials", "k.json"]
    code = main(
        [*args, "--out-dir", str(tmp_path)],
        env={},
        sheet_factory=factory,
    )
    assert code == 2


# 문제 11: --export-csv — 지금 스냅샷을 CSV로, 스냅샷이 없으면 1
def test_export(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (out / "sites").mkdir(parents=True)
    snapshot(out).write_bytes(DEMO.read_bytes())
    target = tmp_path / "csv"
    assert run("--site", "demo", "--export-csv", str(target), out=out) == 0
    assert (target / "유형.csv").exists()
    assert run("--site", "demo", "--export-csv", str(target), out=tmp_path / "empty") == 1


# 문제 12: 사이트 이름 형식이 틀리면 1 (파일 경로에 들어가므로)
def test_bad_site_name(csv_dir: Path, tmp_path: Path) -> None:
    assert run("--site", "../demo", "--csv-dir", str(csv_dir), out=tmp_path) == 1


# 문제 13: 출력 폴더 기본값은 TTS_METADATA_DIR
def test_out_dir_from_env(csv_dir: Path, tmp_path: Path) -> None:
    env = {"TTS_METADATA_DIR": str(tmp_path / "meta")}
    args = ["--site", "demo", "--csv-dir", str(csv_dir), "--apply"]
    code = main(args, env=env, now=lambda: FIXED_NOW)
    assert code == 0
    assert (tmp_path / "meta" / "sites" / "demo.json").exists()
