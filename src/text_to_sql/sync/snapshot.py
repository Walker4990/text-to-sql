"""스냅샷 JSON 쓰기와 CSV 내보내기.

- snapshot_text: SiteProfile + _meta → JSON 문자열.
  키 순서·들여쓰기를 고정해 git diff가 실제 변경만 보이게 한다
- write_snapshot: 임시 파일에 쓴 뒤 바꿔치기 (쓰다 실패해도 기존 스냅샷이 깨지지 않게)
- export_csv: 지금 프로필을 시트 양식(탭별 CSV)으로. 시트 첫 작성·이관용
"""

import csv
import json
from pathlib import Path

from text_to_sql.repositories.metadata_repository import DEFAULT_TABLE_ALIASES, SiteProfile
from text_to_sql.repositories.profile_source import Tabs
from text_to_sql.sync.sheet_rows import (
    TAB_ALIASES,
    TAB_ITEMS,
    TAB_REGISTERED,
    TAB_SETTINGS,
    TAB_TABLE_ALIASES,
    TAB_TYPES,
)


def profile_to_data(profile: SiteProfile) -> dict[str, object]:
    """SiteProfile → 스냅샷 JSON 본문 (로더가 읽는 형식, _meta 제외)."""
    items: dict[str, dict[str, dict[str, str | bool]]] = {}
    for type_name, type_items in profile.items.items():
        items[type_name] = {}
        for name, item in type_items.items():
            spec: dict[str, str | bool] = {"column": item.column}
            if item.multi:
                spec["multi"] = True
            if item.type == "number":
                spec["type"] = "number"
            items[type_name][name] = spec
    return {
        "site": profile.site,
        "types": dict(profile.types),
        "aliases": dict(profile.aliases),
        "table_aliases": dict(profile.table_aliases),
        "items": items,
        "registered_items": list(profile.registered_items),
        "registered_aliases": dict(profile.registered_aliases),
    }


def snapshot_text(profile: SiteProfile, meta: dict[str, str]) -> str:
    """_meta를 맨 앞에 둔 스냅샷 JSON.

    한글은 그대로(ensure_ascii=False), 2칸 들여쓰기, 끝에 줄바꿈."""
    body = {"_meta": meta, **profile_to_data(profile)}
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def write_snapshot(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    temp.replace(path)  # 같은 폴더 안 바꿔치기라 중간 상태가 남지 않는다


def profile_to_tabs(profile: SiteProfile, owner: str | None = None) -> Tabs:
    """SiteProfile → 시트 양식의 탭별 행. 별칭은 정식 이름 자기 연결을 뺀다."""
    settings = [["키", "값"], ["site", profile.site]]
    if owner:
        settings.append(["담당자", owner])
    types = [["유형명", "TYPEID", "사용", "설명"]]
    types += [[name, str(typeid), "Y", ""] for name, typeid in profile.types.items()]
    aliases = [["별칭", "유형명", "사용", "설명"]]
    aliases += [[alias, name, "Y", ""] for alias, name in profile.aliases.items() if alias != name]
    items = [["유형명", "항목명", "물리컬럼", "다중값", "형식", "사용", "설명"]]
    for type_name, type_items in profile.items.items():
        for name, item in type_items.items():
            kind = "숫자" if item.type == "number" else "문자"
            items.append([type_name, name, item.column, "Y" if item.multi else "N", kind, "Y", ""])
    registered = [["항목명칭", "alias", "사용", "설명"]]
    registered += [
        [name, profile.registered_aliases.get(name, ""), "Y", ""]
        for name in profile.registered_items
    ]
    table_aliases = [["역할", "별칭", "설명"]]
    table_aliases += [[role, profile.table_aliases[role], ""] for role in DEFAULT_TABLE_ALIASES]
    return {
        TAB_SETTINGS: settings,
        TAB_TYPES: types,
        TAB_ALIASES: aliases,
        TAB_ITEMS: items,
        TAB_REGISTERED: registered,
        TAB_TABLE_ALIASES: table_aliases,
    }


def export_csv(profile: SiteProfile, folder: Path, owner: str | None = None) -> list[Path]:
    """탭마다 <탭 이름>.csv를 만든다. 엑셀에서 한글이 깨지지 않도록 BOM 붙은 UTF-8로 쓴다."""
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for tab, rows in profile_to_tabs(profile, owner).items():
        path = folder / f"{tab}.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)
        paths.append(path)
    return paths
