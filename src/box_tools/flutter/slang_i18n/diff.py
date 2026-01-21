from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set

from .fs import I18N_DIR, get_active_groups, group_file_name, load_json_obj, split_slang_json
from .model import ProjectConfig


@dataclass
class RedundantItem:
    group: str
    file: Path
    locale: str
    extra_keys: List[str]


def collect_redundant(cfg: ProjectConfig, i18n_dir: Path) -> List[RedundantItem]:
    src_code = cfg.source_locale.code
    targets = cfg.target_codes()

    items: List[RedundantItem] = []
    for group in get_active_groups(i18n_dir):
        module_name = group.name if group.name != I18N_DIR else "i18n"

        src_path = group_file_name(group, src_code)
        _, src_body = split_slang_json(src_path, load_json_obj(src_path))
        src_keys = set(src_body.keys())

        for code in targets:
            tgt_path = group_file_name(group, code)
            _, tgt_body = split_slang_json(tgt_path, load_json_obj(tgt_path))
            extra = sorted(set(tgt_body.keys()) - src_keys)
            if extra:
                items.append(RedundantItem(group=module_name, file=tgt_path, locale=code, extra_keys=extra))
    return items


def report_redundant(items: List[RedundantItem], max_keys_preview: int = 40) -> None:
    if not items:
        print("✅ 未发现冗余 key")
        return

    total_keys = sum(len(x.extra_keys) for x in items)
    print(f"⚠️ 发现冗余：{len(items)} 个文件，合计 {total_keys} 个 key\n")
    for it in items:
        preview = it.extra_keys[:max_keys_preview]
        more = len(it.extra_keys) - len(preview)
        print(f"- module={it.group} locale={it.locale} file={it.file}")
        for k in preview:
            print(f"    • {k}")
        if more > 0:
            print(f"    … and {more} more")
        print("")
