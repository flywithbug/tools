from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

I18N_DIR = "i18n"


def ensure_i18n_dir(cwd: Optional[Path] = None) -> Path:
    root = cwd or Path.cwd()
    p = root / I18N_DIR
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError("❌ 当前目录未找到 i18n/（请在项目根目录执行）")
    return p


def _has_any_subdir(i18n_dir: Path) -> bool:
    return any(c.is_dir() for c in i18n_dir.iterdir())


def get_active_groups(i18n_dir: Path) -> List[Path]:
    """
    规则：
    - i18n/ 下如果存在任何子目录：只处理子目录，不处理 i18n/ 根目录
    - 否则：处理 i18n/ 根目录
    """
    subdirs = [c for c in i18n_dir.iterdir() if c.is_dir()]
    if subdirs:
        return sorted(subdirs)
    return [i18n_dir]


def _to_camel(s: str) -> str:
    parts = [p for p in re.split(r"[_\\-\\s]+", s.strip()) if p]
    if not parts:
        return s
    head = parts[0].lower()
    tail = "".join(p[:1].upper() + p[1:] for p in parts[1:])
    return head + tail


def group_file_name(group: Path, locale_code: str) -> Path:
    """
    规则：
    - i18n/ 根目录：{locale}.i18n.json
    - i18n/<module>/：{camelFolder}_{locale}.i18n.json
    """
    if group.name == I18N_DIR:
        return group / f"{locale_code}.i18n.json"
    prefix = _to_camel(group.name)
    return group / f"{prefix}_{locale_code}.i18n.json"


def load_json_obj(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"❌ JSON 解析失败：{path} ({e})") from None
    if not isinstance(obj, dict):
        raise ValueError(f"❌ JSON 必须是 object：{path}")
    return obj


def split_slang_json(path: Path, obj: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    slang flat json:
    - 所有以 @@ 开头的是 metadata，不翻译
    - 其余 key 必须是 str -> str
    """
    meta: Dict[str, Any] = {}
    body: Dict[str, str] = {}
    for k, v in obj.items():
        if not isinstance(k, str):
            raise ValueError(f"❌ 非法 key（非字符串）：{path}")
        if k.startswith("@@"):
            meta[k] = v
            continue
        if not isinstance(v, str):
            raise ValueError(f"❌ 仅支持平铺 string->string：{path}，key={k!r} type={type(v).__name__}")
        body[k] = v
    return meta, body


def save_json(path: Path, meta: Dict[str, Any], body: Dict[str, str], sort_keys: bool) -> None:
    """
    输出顺序：
    1) @@locale（如果存在）
    2) 其它 @@meta（按 key 排序）
    3) 普通 key（可选排序）
    """
    out: Dict[str, Any] = {}
    if "@@locale" in meta:
        out["@@locale"] = meta.get("@@locale")
    for k in sorted([k for k in meta.keys() if k != "@@locale"]):
        out[k] = meta[k]
    items = sorted(body.items(), key=lambda kv: kv[0]) if sort_keys else list(body.items())
    for k, v in items:
        out[k] = v
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _match_locale_from_filename(filename: str, locales_sorted: List[str]) -> Optional[str]:
    if not filename.endswith(".i18n.json"):
        return None
    stem = filename[: -len(".i18n.json")]
    for loc in locales_sorted:
        if stem.endswith(f"_{loc}") or stem == loc:
            return loc
    return None


def normalize_group_filenames(group: Path, locale_codes: List[str], verbose: bool = True) -> None:
    """
    只规范化 i18n/<module>/ 下的文件名：{camelFolder}_{locale}.i18n.json
    只对“能从文件名明确识别 locale”的文件动手；不覆盖已有目标文件。
    """
    if group.name == I18N_DIR:
        return

    locales_sorted = sorted(set(locale_codes), key=len, reverse=True)
    expected_prefix = _to_camel(group.name)

    for p in group.glob("*.i18n.json"):
        loc = _match_locale_from_filename(p.name, locales_sorted)
        if not loc:
            continue

        expected_name = f"{expected_prefix}_{loc}.i18n.json"
        if p.name == expected_name:
            continue

        target = group / expected_name
        if target.exists():
            if verbose:
                print(f"⚠️ 跳过重命名（目标已存在）：{p.name} -> {target.name}")
            continue

        if verbose:
            print(f"🛠️ 重命名：{p.name} -> {target.name}")
        p.rename(target)


def ensure_language_files_in_group(group: Path, src_code: str, targets_code: List[str]) -> None:
    """
    只创建缺失的文件，创建内容仅包含 @@locale
    """
    src_path = group_file_name(group, src_code)
    if not src_path.exists():
        save_json(src_path, {"@@locale": src_code}, {}, sort_keys=False)
        print(f"➕ Created {src_path}")

    for code in targets_code:
        p = group_file_name(group, code)
        if not p.exists():
            save_json(p, {"@@locale": code}, {}, sort_keys=False)
            print(f"➕ Created {p}")


def ensure_all_language_files(i18n_dir: Path, src_code: str, target_codes: List[str], normalize: bool) -> None:
    groups = get_active_groups(i18n_dir)
    locale_codes = [src_code, *target_codes]

    if normalize:
        for g in groups:
            normalize_group_filenames(g, locale_codes=locale_codes, verbose=True)

    for g in groups:
        ensure_language_files_in_group(g, src_code, target_codes)
