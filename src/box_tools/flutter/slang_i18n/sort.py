from __future__ import annotations

from pathlib import Path

from .fs import get_active_groups, load_json_obj, save_json, split_slang_json


def sort_all_json(i18n_dir: Path, sort_keys: bool) -> None:
    for g in get_active_groups(i18n_dir):
        for p in g.glob("*.i18n.json"):
            meta, body = split_slang_json(p, load_json_obj(p))
            save_json(p, meta, body, sort_keys=sort_keys)
