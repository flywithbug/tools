#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strings_i18n.py — iOS .strings 多語言管理工具（Refactor: slang_i18n style）

目录约定（Xcode）：
- Base.lproj 与 en.lproj / zh-Hant.lproj 等同级
- Base.lproj 下的 *.strings 视为"需要处理的文件清单"
- 其他语言目录：{code}.lproj
- 每个语言目录里应包含与 Base 相同文件名的 *.strings

配置文件：strings_i18n.yaml（NEW schema, 带注释模板）
languages.json：语言清单（用于 sync & init 生成 target_locales 等）
翻译：使用 ./comm/translate.py 中的 translate_flat_dict（flat dict 翻译）

功能（actions）：
- init               生成 strings_i18n.yaml（若存在则校验不覆盖）
- doctor             检查依赖 / 目录结构 / 配置 / API Key
- scan               扫描 Base.lproj 的 *.strings 文件
- sync               按 languages.json 补齐 {code}.lproj + *.strings
- sort               对所有语言的 *.strings 做排序（按 Base key 顺序 + prefix 分组 + 保留注释/空行）
- dupcheck           重复 key 检查（先汇总显示）
- dedupe             删除重复 key（先汇总显示，最后确认一次；--keep first/last；--yes 跳过确认）
- check              冗余 key 检查（Base 没有、目标有）
- clean              删除冗余 key（先汇总显示，最后确认一次；--yes 跳过确认）
- translate-core     增量翻译：base_locale → core_locales（源：Base.lproj/*.strings）
- translate-target   增量翻译：source_locale → target_locales（源：{source_code}.lproj/*.strings）

Exit codes:
- 0 成功
- 1 执行失败
- 2 环境/配置错误
- 3 check/dupcheck 发现问题（默认返回 3，便于 CI）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    from openai import OpenAI  # noqa: F401
except Exception:
    OpenAI = None  # type: ignore

# ✅ 使用同目录下 comm/translate 模块（与 slang_i18n 对齐）
from .comm.translate import OpenAIModel, TranslationError, translate_flat_dict  # type: ignore


# =========================================================
# BOX_TOOL (对齐 slang_i18n)
# =========================================================
BOX_TOOL = {
    "id": "ios.strings_i18n",
    "name": "strings_i18n",
    "category": "ios",
    "summary": "iOS/Xcode .strings 多语言：扫描/同步/排序/重复与冗余清理/增量翻译（支持交互）",
    "usage": [
        "strings_i18n",
        "strings_i18n options",
        "strings_i18n init",
        "strings_i18n doctor",
        "strings_i18n scan",
        "strings_i18n sync",
        "strings_i18n sort",
        "strings_i18n dupcheck",
        "strings_i18n dedupe --yes --keep first",
        "strings_i18n check",
        "strings_i18n clean --yes",
        "strings_i18n translate-core --api-key $OPENAI_API_KEY",
        "strings_i18n translate-target --api-key $OPENAI_API_KEY",
        "strings_i18n gen-l10n",
    ],
    "options": [
        {"flag": "--config", "desc": "配置文件路径（默认 strings_i18n.yaml）"},
        {"flag": "--languages", "desc": "languages.json 路径（默认 languages.json）"},
        {"flag": "--api-key", "desc": "OpenAI API key（也可用环境变量 OPENAI_API_KEY）"},
        {"flag": "--model", "desc": "模型（命令行优先；不传则用配置 openAIModel；默认 gpt-4o）"},
        {"flag": "--full", "desc": "全量翻译（默认增量：只补缺失/空值 key）"},
        {"flag": "--yes", "desc": "clean/dedupe 删除时跳过确认"},
        {"flag": "--keep", "desc": "dedupe 保留策略：first/last（默认 first）"},
        {"flag": "--no-exitcode-3", "desc": "check/dupcheck 发现问题时仍返回 0（默认返回 3）"},
        {"flag": "--dry-run", "desc": "预览模式（不写入文件）"},
    ],
    "dependencies": [
        "PyYAML>=6.0",
        "openai>=1.0.0",
    ],
}


# =========================================================
# Constants / Exit codes
# =========================================================
CONFIG_FILE = "strings_i18n.yaml"
LANG_FILE = "languages.json"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BAD = 2
EXIT_FOUND = 3

ALLOWED_OPENAI_MODELS = (
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
)


# =========================================================
# Lazy import for PyYAML
# =========================================================
def _require_yaml():
    try:
        import yaml  # type: ignore
        return yaml
    except Exception:
        raise SystemExit(
            "❌ 缺少依赖 PyYAML（import yaml 失败）\n"
            "修复方式：\n"
            "1) pipx 安装：pipx inject box pyyaml\n"
            "2) 或在 pyproject.toml dependencies 加入 PyYAML>=6.0 后重新发布/安装\n"
        )


# =========================================================
# Config schema (NEW)
# =========================================================
def _schema_error(msg: str) -> ValueError:
    return ValueError(
        "strings_i18n.yaml 格式错误：\n"
        f"- {msg}\n\n"
        "期望结构（新 schema）示例：\n"
        "openAIModel: gpt-4o\n"
        "lang_root: ./TimeTrails/TimeTrails/SupportFiles/\n"
        "base_folder: Base.lproj\n"
        "languages: ./languages.json\n"
        "base_locale:\n"
        "  - code: zh-Hans\n"
        "    name_en: Simplified Chinese\n"
        "source_locale:\n"
        "  - code: en\n"
        "    name_en: English\n"
        "core_locales:\n"
        "  - code: zh-Hant\n"
        "    name_en: Traditional Chinese\n"
        "target_locales:\n"
        "  - code: de\n"
        "    name_en: German\n"
        "prompts:\n"
        "  default_en: |\n"
        "    Translate UI strings naturally.\n"
        "  by_locale_en:\n"
        "    zh-Hant: |\n"
        "      Use Taiwan Traditional Chinese UI style.\n"
        "options:\n"
        "  sort_keys: true\n"
        "  cleanup_extra_keys: true\n"
        "  incremental_translate: true\n"
    )


def _need_nonempty_str(obj: Dict[str, Any], key: str, path: str) -> str:
    v = obj.get(key)
    if not isinstance(v, str) or not v.strip():
        raise _schema_error(f"{path}.{key} 必须是非空字符串")
    return v.strip()


def _need_bool(obj: Dict[str, Any], key: str, path: str) -> bool:
    v = obj.get(key)
    if not isinstance(v, bool):
        raise _schema_error(f"{path}.{key} 必须是 bool（true/false）")
    return v


def _need_openai_model(cfg: Dict[str, Any]) -> str:
    v = cfg.get("openAIModel", OpenAIModel.GPT_4O.value)
    if v is None:
        v = OpenAIModel.GPT_4O.value
    if not isinstance(v, str) or not v.strip():
        raise _schema_error("openAIModel 必须是非空字符串")
    v = v.strip()
    if v not in set(ALLOWED_OPENAI_MODELS):
        raise _schema_error(f"openAIModel 不合法：{v!r}，可选：{', '.join(ALLOWED_OPENAI_MODELS)}")
    return v


def _parse_locale_list(cfg: Dict[str, Any], key: str) -> List[Dict[str, str]]:
    raw = cfg.get(key)
    if not isinstance(raw, list) or not raw:
        raise _schema_error(f"{key} 必须是非空数组（每项为 {{code,name_en}}）")
    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            raise _schema_error(f"{key}[{i}] 必须是 object/map（包含 code / name_en）")
        code = _need_nonempty_str(it, "code", f"{key}[{i}]")
        name_en = _need_nonempty_str(it, "name_en", f"{key}[{i}]")
        if code in seen:
            raise _schema_error(f"{key}[{i}].code 重复：{code}")
        seen.add(code)
        out.append({"code": code, "name_en": name_en})
    return out


def validate_config(cfg: Any) -> Dict[str, Any]:
    if not isinstance(cfg, dict):
        raise _schema_error("根节点必须是 YAML object/map")

    openai_model = _need_openai_model(cfg)

    lang_root = cfg.get("lang_root")
    base_folder = cfg.get("base_folder")
    languages_path = cfg.get("languages", "./languages.json")

    if not isinstance(lang_root, str) or not lang_root.strip():
        raise _schema_error("lang_root 必须是非空字符串")
    if not isinstance(base_folder, str) or not base_folder.strip():
        raise _schema_error("base_folder 必须是非空字符串（通常 Base.lproj）")
    if not isinstance(languages_path, str) or not languages_path.strip():
        raise _schema_error("languages 必须是非空字符串（languages.json 路径）")

    prompts = cfg.get("prompts") or {}
    if not isinstance(prompts, dict):
        raise _schema_error("prompts 必须是 object/map（可省略）")
    default_en = prompts.get("default_en", "")
    if default_en is None:
        default_en = ""
    if not isinstance(default_en, str):
        raise _schema_error("prompts.default_en 必须是字符串（可为空）")
    by_locale_en = prompts.get("by_locale_en", {}) or {}
    if not isinstance(by_locale_en, dict):
        raise _schema_error("prompts.by_locale_en 必须是 object/map（可省略）")
    by_locale_en2: Dict[str, str] = {}
    for k, v in by_locale_en.items():
        if not isinstance(k, str) or not k.strip():
            raise _schema_error("prompts.by_locale_en 的 key 必须是非空字符串（locale code）")
        if not isinstance(v, str):
            raise _schema_error(f"prompts.by_locale_en[{k!r}] 必须是字符串")
        by_locale_en2[k.strip()] = v

    opts = cfg.get("options")
    if not isinstance(opts, dict):
        raise _schema_error("options 必须是 object/map")

    normalize_filenames = opts.get("normalize_filenames", True)
    if not isinstance(normalize_filenames, bool):
        raise _schema_error("options.normalize_filenames 必须是 bool（true/false）")

    base_locale = _parse_locale_list(cfg, "base_locale")
    source_locale = _parse_locale_list(cfg, "source_locale")
    core_locales = _parse_locale_list(cfg, "core_locales")
    target_locales = _parse_locale_list(cfg, "target_locales")

    return {
        "openAIModel": openai_model,
        "lang_root": lang_root.strip(),
        "base_folder": base_folder.strip(),
        "languages": languages_path.strip(),
        "base_locale": base_locale,
        "source_locale": source_locale,
        "core_locales": core_locales,
        "target_locales": target_locales,
        "prompts": {"default_en": default_en, "by_locale_en": by_locale_en2},
        "options": {
            "sort_keys": _need_bool(opts, "sort_keys", "options"),
            "cleanup_extra_keys": _need_bool(opts, "cleanup_extra_keys", "options"),
            "incremental_translate": _need_bool(opts, "incremental_translate", "options"),
            "normalize_filenames": normalize_filenames,
        },
    }


def read_config(path: Path) -> Dict[str, Any]:
    yaml = _require_yaml()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return validate_config(raw)


def read_config_or_throw(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"❌ 未找到 {CONFIG_FILE}（请先 strings_i18n init）")
    return read_config(path)


def _config_template_text() -> str:
    # 模板文本：为了保留注释（对齐 slang 的"模板文本 init"思路）
    return """# strings_i18n.yaml
# iOS/Xcode .strings 多语言配置（NEW schema）
#
# 目录约定（在项目根目录运行）：
# - {lang_root}/{base_folder} 必须存在（通常 Base.lproj）
# - Base.lproj 与其它 *.lproj 同级（Xcode 约定）
# - 需要处理的文件：扫描 Base.lproj 下的 *.strings
#
# languages.json：用于 sync 补齐语言目录/文件、以及 init 生成 target_locales

# OpenAI 模型（默认 gpt-4o）
# 可选值（枚举）：
# - gpt-4o
# - gpt-4o-mini
# - gpt-4.1
# - gpt-4.1-mini
openAIModel: gpt-4o

# 语言目录根路径（相对项目根目录）
lang_root: ./TimeTrails/TimeTrails/SupportFiles/

# Base 目录名（通常 Base.lproj）
base_folder: Base.lproj

# languages.json 路径（相对项目根目录）
languages: ./languages.json

# 基础语言（用于 translate-core：Base.lproj -> core_locales）
base_locale:
  - code: zh-Hans
    name_en: Simplified Chinese

# 源语言（用于 translate-target：{source}.lproj -> target_locales）
source_locale:
  - code: en
    name_en: English

# 核心语言（常驻优先翻译）
core_locales:
  - code: zh-Hant
    name_en: Traditional Chinese
  - code: en
    name_en: English
  - code: ja
    name_en: Japanese
  - code: ko
    name_en: Korean

# 目标语言（通常由 init 从 languages.json 自动生成，并排除 core_locales）
target_locales:
  - code: de
    name_en: German
  - code: es
    name_en: Spanish

# 提示词（英文）：支持 default + by_locale "追加"
prompts:
  default_en: |
    Translate UI strings naturally for a mobile app.
    Be concise, clear, and consistent.
    Preserve placeholders and formatting tokens unchanged.

  by_locale_en:
    zh-Hant: |
      Use Taiwan-style Traditional Chinese for UI.
      Prefer common Taiwan wording (e.g., "帳號", "登入", "請稍後再試").

    ja: |
      Use polite and concise Japanese UI tone suitable for mobile apps.

    ko: |
      Use natural Korean UI style suitable for mobile apps.

# 选项（布尔值）
options:
  # sort 会按 Base key 顺序 + prefix 分组输出；此开关用于未来扩展（当前默认 true）
  sort_keys: true

  # translate 时是否先过滤目标文件里的冗余 key（避免幽灵 key 扩散）
  cleanup_extra_keys: true

  # 是否增量翻译：true=只补缺失/空值；false=全量覆盖（等价 --full）
  incremental_translate: true

  # 预留：是否规范化文件名（iOS .strings 通常不需要重命名，保持 false/true 都不影响核心功能）
  normalize_filenames: true
"""


def init_config(cfg_path: Path, project_root: Path, languages_path: Path) -> None:
    _require_yaml()  # ensure deps

    if cfg_path.exists():
        _ = read_config(cfg_path)  # 存在就校验，不覆盖
        print(f"✅ {CONFIG_FILE} 已存在且格式正确（不会覆盖）")
        return

    # 生成模板
    cfg_path.write_text(_config_template_text(), encoding="utf-8")
    print(f"📝 已生成 {CONFIG_FILE}（新 schema，含详细注释）")

    # 如果 languages.json 存在：尽力生成/更新 target_locales（不覆盖整个 yaml，只给提示）
    if not languages_path.exists():
        print(f"⚠️ 未找到 {languages_path}，无法自动从 languages.json 补齐 target_locales（可稍后再运行 init）")
        return

    print("ℹ️ 已生成配置模板。建议下一步：strings_i18n doctor / scan / sync")


# =========================================================
# languages.json helpers
# =========================================================
def load_languages_json(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"❌ 找不到 languages.json：{path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("❌ languages.json 顶层必须是 list")

    out: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for it in data:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code", "")).strip()
        name_en = str(it.get("name_en", "")).strip()
        if not code or not name_en:
            continue
        if code.lower() in ("base", "base.lproj"):
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name_en": name_en})

    out.sort(key=lambda x: x["code"].lower())
    return out


def code_to_lproj(code: str) -> str:
    return code if code.endswith(".lproj") else f"{code}.lproj"


# =========================================================
# iOS/Xcode scanning helpers
# =========================================================
def project_paths(project_root: Path, cfg: Dict[str, Any]) -> Tuple[Path, Path]:
    lang_root = (project_root / Path(cfg["lang_root"])).resolve()
    base_dir = (lang_root / Path(cfg["base_folder"])).resolve()
    return lang_root, base_dir


def scan_base_strings(base_dir: Path) -> List[Path]:
    if not base_dir.exists() or not base_dir.is_dir():
        raise FileNotFoundError(f"❌ Base 目录不存在：{base_dir}")
    files = [p for p in base_dir.iterdir() if p.is_file() and p.suffix == ".strings"]
    files.sort(key=lambda p: p.name.lower())
    if not files:
        raise FileNotFoundError(f"❌ Base 目录下未找到任何 *.strings：{base_dir}")
    return files


def ensure_dir(p: Path, dry: bool) -> bool:
    if p.exists():
        if not p.is_dir():
            raise FileExistsError(f"路径存在但不是目录：{p}")
        return False
    if not dry:
        p.mkdir(parents=True, exist_ok=True)
    return True


def ensure_file(p: Path, dry: bool) -> bool:
    if p.exists():
        if not p.is_file():
            raise FileExistsError(f"路径存在但不是文件：{p}")
        return False
    if not dry:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    return True


def sync_language_dirs_and_files(
        *,
        lang_root_dir: Path,
        base_files: List[Path],
        locale_codes: List[str],
        dry: bool,
) -> Dict[str, Any]:
    created_dirs: List[str] = []
    created_files: List[str] = []
    existing_dirs = 0
    existing_files = 0

    for code in locale_codes:
        lproj_dir = lang_root_dir / code_to_lproj(code)
        if ensure_dir(lproj_dir, dry):
            created_dirs.append(str(lproj_dir))
        else:
            existing_dirs += 1

        for bf in base_files:
            target = lproj_dir / bf.name
            if ensure_file(target, dry):
                created_files.append(str(target))
            else:
                existing_files += 1

    return {
        "created_dirs": created_dirs,
        "created_files": created_files,
        "existing_dirs": existing_dirs,
        "existing_files": existing_files,
    }


# =========================================================
# .strings parse / sort (保留注释/空行)
# =========================================================
ENTRY_RE = re.compile(
    r'^\s*"([^"\\]*(?:\\.[^"\\]*)*)"\s*=\s*"([^"\\]*(?:\\.[^"\\]*)*)"\s*;\s*(?://.*)?$'
)
COMMENT_START_RE = re.compile(r"^\s*/\*")
COMMENT_END_RE = re.compile(r"\*/\s*$")
LINE_COMMENT_RE = re.compile(r"^\s*//")


@dataclass
class StringsEntry:
    key: str
    value: str
    comments: List[str]
    raw_before: List[str]


@dataclass
class ParsedStrings:
    header: List[str]
    entries: List[StringsEntry]
    tail: List[str]


def parse_strings_file(path: Path) -> ParsedStrings:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    header: List[str] = []
    tail: List[str] = []
    entries: List[StringsEntry] = []
    pending_comments: List[str] = []
    pending_misc: List[str] = []
    in_comment = False
    seen_entry = False

    for line in lines:
        if in_comment:
            pending_comments.append(line)
            if COMMENT_END_RE.search(line):
                in_comment = False
            continue

        if COMMENT_START_RE.search(line):
            in_comment = True
            pending_comments.append(line)
            if COMMENT_END_RE.search(line):
                in_comment = False
            continue

        if LINE_COMMENT_RE.search(line):
            pending_comments.append(line)
            continue

        m = ENTRY_RE.match(line)
        if m:
            seen_entry = True
            entries.append(
                StringsEntry(
                    key=m.group(1),
                    value=m.group(2),
                    comments=pending_comments,
                    raw_before=pending_misc,
                )
            )
            pending_comments = []
            pending_misc = []
            continue

        if not seen_entry:
            header.append(line)
        else:
            pending_misc.append(line)

    if not seen_entry:
        header = pending_comments + pending_misc
        pending_comments, pending_misc = [], []

    if pending_comments or pending_misc:
        tail.extend(pending_comments)
        tail.extend(pending_misc)

    return ParsedStrings(header, entries, tail)


def prefix_of_key(key: str) -> str:
    return key.split(".", 1)[0] if "." in key else key


def _group_entries_by_prefix(entries: List[StringsEntry]) -> Dict[str, List[StringsEntry]]:
    grouped: Dict[str, List[StringsEntry]] = {}
    for e in entries:
        pref = prefix_of_key(e.key)
        grouped.setdefault(pref, []).append(e)
    return grouped


def sort_base_file_inplace(base_file: Path, dry: bool) -> bool:
    """对 Base.lproj 下的单个 *.strings 做：按前缀分组、组名排序、组内按 key 排序；保留注释/空行。"""
    parsed = parse_strings_file(base_file)
    if not parsed.entries:
        return False

    # Base：需要“按前缀分组 + 组间留空行”。
    grouped = _group_entries_by_prefix(parsed.entries)
    prefix_order = sorted(grouped.keys(), key=str.lower)

    out: List[str] = parsed.header[:]
    first_group = True
    for pref in prefix_order:
        if not first_group:
            # 组间间隔：一行空行（与之前目标语言分组的视觉一致，但只用于 Base）
            out.extend(["\n"])
        first_group = False

        group_entries = sorted(grouped[pref], key=lambda e: e.key.lower())
        for e in group_entries:
            out.extend(format_entry(e))

    if parsed.tail:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.extend(parsed.tail)

    new_content = "".join(out)
    old_content = base_file.read_text(encoding="utf-8", errors="replace")
    changed = new_content != old_content
    if changed and not dry:
        base_file.write_text(new_content, encoding="utf-8")
    return changed


def format_entry(e: StringsEntry) -> List[str]:
    # 约定：注释必须贴在 key 上方。
    # raw_before 通常是空行/杂项，应该放在“注释块之前”，避免出现“注释在上，但 key 在更下面”的视觉断裂。
    return e.raw_before + e.comments + [f'"{e.key}" = "{e.value}";\n']


def sort_one_file(base_file: Path, target_file: Path, dry: bool) -> bool:
    base = parse_strings_file(base_file)
    tgt = parse_strings_file(target_file)

    base_order = [e.key for e in base.entries]
    base_set = set(base_order)

    tgt_multi: Dict[str, List[StringsEntry]] = {}
    for e in tgt.entries:
        tgt_multi.setdefault(e.key, []).append(e)

    # 1) in-base: follow base key order (keep duplicates as-is, just relocate)
    in_base: List[StringsEntry] = []
    for k in base_order:
        if k in tgt_multi:
            in_base.extend(tgt_multi[k])

    # 2) extras: keys not in base, sorted by key
    extras: List[StringsEntry] = []
    extra_keys = sorted((k for k in tgt_multi.keys() if k not in base_set), key=str.lower)
    for k in extra_keys:
        extras.extend(tgt_multi[k])

    # 3) 目标语言：不需要前缀分组、不需要组间间隔。
    #    只按 Base key 顺序输出（包含重复 key 的“原样搬运”），最后追加 extras（按 key 排序）。
    out: List[str] = tgt.header[:]
    for e in (in_base + extras):
        # 目标语言：不做分组/不加间隔，也不保留 raw_before 的空行噪声；
        # 但保留“紧贴在 key 上方”的注释。
        out.extend(e.comments)
        out.append(f'"{e.key}" = "{e.value}";\n')

    if tgt.tail:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.extend(tgt.tail)

    new_content = "".join(out)
    old_content = target_file.read_text(encoding="utf-8", errors="replace")

    changed = new_content != old_content
    if changed and not dry:
        target_file.write_text(new_content, encoding="utf-8")
    return changed


def sort_all(
        *,
        lang_root_dir: Path,
        base_dir: Path,
        base_files: List[Path],
        locale_codes: List[str],
        dry: bool,
) -> Dict[str, int]:
    # 0) 先把 Base.lproj 自己的文件排序好（按前缀分组 + 组名/组内排序），为后续语言提供稳定顺序。
    base_changed = 0
    for bf in base_files:
        if sort_base_file_inplace(base_dir / bf.name, dry):
            base_changed += 1

    total = 0
    changed = 0
    missing = 0

    for code in locale_codes:
        lproj = lang_root_dir / code_to_lproj(code)
        for bf in base_files:
            total += 1
            target = lproj / bf.name
            if not target.exists():
                missing += 1
                continue
            if sort_one_file(base_dir / bf.name, target, dry):
                changed += 1

    return {"total": total, "changed": changed, "missing": missing, "base_changed": base_changed}


# =========================================================
# Duplicate / Redundant helpers (batch confirm once)
# =========================================================
def find_duplicates(entries: List[StringsEntry]) -> Dict[str, List[int]]:
    idx: Dict[str, List[int]] = {}
    for i, e in enumerate(entries):
        idx.setdefault(e.key, []).append(i)
    return {k: v for k, v in idx.items() if len(v) > 1}


def filter_entries_with_carry(parsed: ParsedStrings, keep_predicate) -> ParsedStrings:
    new_entries: List[StringsEntry] = []
    carry: List[str] = []

    for e in parsed.entries:
        keep = keep_predicate(e)
        if keep:
            if carry:
                e = StringsEntry(
                    key=e.key,
                    value=e.value,
                    comments=carry + e.comments,
                    raw_before=e.raw_before,
                )
                carry = []
            new_entries.append(e)
        else:
            carry.extend(e.comments)
            carry.extend(e.raw_before)

    new_tail = parsed.tail[:]
    if carry:
        new_tail = carry + new_tail
    return ParsedStrings(parsed.header[:], new_entries, new_tail)


def write_parsed_strings(path: Path, parsed: ParsedStrings, dry: bool) -> bool:
    out: List[str] = []
    out.extend(parsed.header)
    for e in parsed.entries:
        out.extend(format_entry(e))
    out.extend(parsed.tail)

    new_content = "".join(out)
    old_content = path.read_text(encoding="utf-8", errors="replace")
    changed = new_content != old_content
    if changed and not dry:
        path.write_text(new_content, encoding="utf-8")
    return changed


def collect_existing_target_files(
        *,
        lang_root_dir: Path,
        base_files: List[Path],
        locale_codes: List[str],
) -> List[Path]:
    out: List[Path] = []
    for code in locale_codes:
        lproj = lang_root_dir / code_to_lproj(code)
        for bf in base_files:
            p = lproj / bf.name
            if p.exists():
                out.append(p)
    return out


def dupcheck_report(files: List[Path]) -> Dict[str, Dict[str, int]]:
    report: Dict[str, Dict[str, int]] = {}
    for p in files:
        parsed = parse_strings_file(p)
        dups = find_duplicates(parsed.entries)
        if dups:
            report[str(p)] = {k: len(v) for k, v in dups.items()}
    return report


def dedupe_batch(files: List[Path], keep: str, dry: bool) -> Dict[str, int]:
    changed_files = 0
    for p in files:
        parsed = parse_strings_file(p)
        dups = find_duplicates(parsed.entries)
        if not dups:
            continue

        if keep == "first":
            seen: Set[str] = set()

            def keep_pred(e: StringsEntry) -> bool:
                if e.key in seen:
                    return False
                seen.add(e.key)
                return True

        else:
            last_idx: Dict[str, int] = {}
            for i, e in enumerate(parsed.entries):
                last_idx[e.key] = i
            cur = {"i": -1}

            def keep_pred(e: StringsEntry) -> bool:
                cur["i"] += 1
                return last_idx.get(e.key) == cur["i"]

        new_parsed = filter_entries_with_carry(parsed, keep_pred)
        if write_parsed_strings(p, new_parsed, dry):
            changed_files += 1

    return {"changed_files": changed_files}


def redundant_report(
        *,
        base_dir: Path,
        base_files: List[Path],
        targets: List[Path],
) -> Dict[str, Dict[str, List[str]]]:
    base_keys_map: Dict[str, Set[str]] = {}
    for bf in base_files:
        base_path = base_dir / bf.name
        base_keys_map[bf.name] = {e.key for e in parse_strings_file(base_path).entries}

    rep: Dict[str, Dict[str, List[str]]] = {}
    for t in targets:
        parsed = parse_strings_file(t)

        # 找到该 target 对应的 base 文件名（按文件名）
        base_name = t.name
        base_keys = base_keys_map.get(base_name, set())

        extra = sorted({e.key for e in parsed.entries if e.key not in base_keys}, key=str.lower)
        if extra:
            rep.setdefault(str(t), {})
            rep[str(t)][base_name] = extra

    return rep


def clean_redundant_batch(report: Dict[str, Dict[str, List[str]]], dry: bool) -> Dict[str, int]:
    changed_files = 0
    removed_keys = 0

    for file_path, per_base in report.items():
        redundant_keys: Set[str] = set()
        for _, ks in per_base.items():
            redundant_keys.update(ks)

        p = Path(file_path)
        parsed = parse_strings_file(p)
        new_parsed = filter_entries_with_carry(parsed, lambda e: e.key not in redundant_keys)

        if write_parsed_strings(p, new_parsed, dry):
            changed_files += 1
        removed_keys += len(redundant_keys)

    return {"changed_files": changed_files, "removed_keys": removed_keys, "files": len(report)}


# =========================================================
# Translation helpers
# =========================================================
def _get_api_key(passed: Optional[str]) -> Optional[str]:
    if passed:
        return passed
    env = os.getenv("OPENAI_API_KEY", "").strip()
    if env:
        return env
    s = input("未检测到 OPENAI_API_KEY。请输入 apiKey（直接回车取消翻译）: ").strip()
    return s or None


def _fmt_pct(n: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{(n * 100.0 / total):5.1f}%"


def _fmt_eta(elapsed_s: float, done: int, total: int) -> str:
    if done <= 0 or total <= 0:
        return "--:--"
    remain = max(total - done, 0)
    rate = elapsed_s / done
    eta = int(remain * rate)
    mm = eta // 60
    ss = eta % 60
    return f"{mm:02d}:{ss:02d}"


def _fmt_locale_names(locales: List[Dict[str, str]], *, max_show: int = 10) -> str:
    names = [str(x.get("name_en", "")).strip() for x in locales if str(x.get("name_en", "")).strip()]
    if len(names) <= max_show:
        return ", ".join(names)
    head = ", ".join(names[:max_show])
    return f"{head} ...（共 {len(names)} 个）"


def _prompt_for_target(cfg: Dict[str, Any], src_code: str, src_name_en: str, tgt_code: str, tgt_name_en: str) -> Optional[str]:
    prompts = cfg.get("prompts") or {}
    default_en = (prompts.get("default_en") or "").strip()
    by_locale = prompts.get("by_locale_en") or {}
    extra = (by_locale.get(tgt_code) or by_locale.get(tgt_code.replace("_", "-")) or "").strip()

    guard = (
        "You are translating UI strings for an iOS app.\n"
        f"Source locale code: {src_code}\n"
        f"Source language (English name): {src_name_en}\n"
        f"Target locale code: {tgt_code}\n"
        f"Target language (English name): {tgt_name_en}\n"
        "Rules:\n"
        f"- Output MUST be written in {tgt_name_en}.\n"
        "- Do NOT output any other language.\n"
        "- Do NOT output Chinese unless the target language is Chinese.\n"
        "- Keep placeholders/variables/formatting tokens unchanged.\n"
        "- Keep meaning accurate and natural for iOS UI.\n"
    ).strip()

    parts: List[str] = []
    if default_en:
        parts.append(default_en)
    if extra:
        parts.append(extra)
    parts.append(guard)
    combo = "\n\n".join(parts).strip()
    return combo or None


def _parsed_to_first_dict(parsed: ParsedStrings) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for e in parsed.entries:
        if e.key not in out:
            out[e.key] = e.value
    return out


def _update_or_append_entries(parsed: ParsedStrings, updates: Dict[str, str]) -> ParsedStrings:
    if not updates:
        return parsed

    seen: Set[str] = set()
    new_entries: List[StringsEntry] = []
    for e in parsed.entries:
        if e.key in updates and e.key not in seen:
            seen.add(e.key)
            new_entries.append(StringsEntry(e.key, updates[e.key], e.comments, e.raw_before))
        else:
            new_entries.append(e)

    existing = {e.key for e in parsed.entries}
    for k, v in updates.items():
        if k not in existing:
            new_entries.append(StringsEntry(k, v, [], []))

    return ParsedStrings(parsed.header[:], new_entries, parsed.tail[:])


def incremental_translate_one_file(
        *,
        cfg: Dict[str, Any],
        api_key: str,
        model: str,
        src_file: Path,
        src_name_en: str,
        tgt_file: Path,
        tgt_code: str,
        tgt_name_en: str,
        full: bool,
        cleanup_extra: bool,
        dry: bool,
) -> Dict[str, int]:
    src_parsed = parse_strings_file(src_file)
    src_map = _parsed_to_first_dict(src_parsed)
    if not src_map:
        return {"needed": 0, "changed": 0}

    tgt_parsed = parse_strings_file(tgt_file)
    tgt_map = _parsed_to_first_dict(tgt_parsed)

    if cleanup_extra:
        tgt_map = {k: v for k, v in tgt_map.items() if k in src_map}

    if full:
        need = dict(src_map)
    else:
        need = {k: v for k, v in src_map.items() if (k not in tgt_map) or (str(tgt_map.get(k, "")).strip() == "")}

    if not need:
        return {"needed": 0, "changed": 0}

    prompt_en = _prompt_for_target(cfg, src_code="(strings)", src_name_en=src_name_en, tgt_code=tgt_code, tgt_name_en=tgt_name_en)
    translated = translate_flat_dict(
        prompt_en=prompt_en,
        src_dict=need,
        src_lang=src_name_en,
        tgt_locale=tgt_name_en,
        model=model,
        api_key=api_key,
    )

    new_parsed = _update_or_append_entries(tgt_parsed, translated)
    changed = write_parsed_strings(tgt_file, new_parsed, dry)
    return {"needed": len(need), "changed": 1 if changed else 0}


def translate_batch(
        *,
        project_root: Path,
        cfg: Dict[str, Any],
        base_dir: Path,
        base_files: List[Path],
        lang_root_dir: Path,
        src_dir: Path,
        src_name_en: str,
        targets: List[Dict[str, str]],
        api_key: str,
        model: str,
        full: bool,
        dry: bool,
        src_code: Optional[str] = None,
) -> Dict[str, int]:
    cleanup_extra = bool(cfg["options"]["cleanup_extra_keys"])

    # 预扫描：收集所有需要翻译的任务
    task_queue: List[Tuple[Dict[str, str], Path, int]] = []
    src_display = src_code if src_code else "Base.lproj"

    for t in targets:
        tgt_code = t["code"]
        tgt_lproj = lang_root_dir / code_to_lproj(tgt_code)
        ensure_dir(tgt_lproj, dry=True)
        for bf in base_files:
            src_file = src_dir / bf.name
            if not src_file.exists():
                continue
            tgt_file = tgt_lproj / bf.name
            ensure_file(tgt_file, dry=True)
            r = incremental_translate_one_file(
                cfg=cfg,
                api_key=api_key,
                model=model,
                src_file=src_file,
                src_name_en=src_name_en,
                tgt_file=tgt_file,
                tgt_code=tgt_code,
                tgt_name_en=t["name_en"],
                full=full,
                cleanup_extra=cleanup_extra,
                dry=True,
            )
            if r["needed"] > 0:
                task_queue.append((t, bf, r["needed"]))

    effective_tasks = len(task_queue)
    if effective_tasks == 0:
        print("✅ 无需翻译：所有目标文件已齐全")
        return {"effective_tasks": 0, "files_changed": 0, "keys_translated": 0}

    # 显示翻译任务概览
    mode_text = "全量" if full else "增量"
    print(f"\n🌍 翻译任务：{src_display} ({src_name_en}) → "
          f"{len(targets)} 个目标语言")
    print(f"🧮 有效任务数（需翻译）：{effective_tasks:,} 个；"
          f"模式={mode_text}；model={model}")

    # 显示排队中的任务列表
    if effective_tasks > 0:
        print(f"\n📋 排队中的任务（{effective_tasks:,} 个）：")
        for idx, (t, bf, needed) in enumerate(task_queue[:10], 1):
            print(f"   {idx}. {src_display} → {t['code']} ({t['name_en']}) / "
                  f"{bf.name} | 需翻译 {needed} keys")
        if effective_tasks > 10:
            print(f"   ... 还有 {effective_tasks - 10} 个任务")
        print()

    done = 0
    changed_files = 0
    translated_keys = 0
    start = time.time()

    for idx, (t, bf, expected_needed) in enumerate(task_queue, 1):
        tgt_code = t["code"]
        tgt_name_en = t["name_en"]
        tgt_lproj = lang_root_dir / code_to_lproj(tgt_code)
        ensure_dir(tgt_lproj, dry)

        src_file = src_dir / bf.name
        tgt_file = tgt_lproj / bf.name
        ensure_file(tgt_file, dry)

        # 显示翻译中状态
        print(f"🔄 [{idx}/{effective_tasks}] 翻译中："
              f"{src_display} → {tgt_code} ({tgt_name_en}) / {bf.name}")

        r = incremental_translate_one_file(
            cfg=cfg,
            api_key=api_key,
            model=model,
            src_file=src_file,
            src_name_en=src_name_en,
            tgt_file=tgt_file,
            tgt_code=tgt_code,
            tgt_name_en=tgt_name_en,
            full=full,
            cleanup_extra=cleanup_extra,
            dry=dry,
        )

        if r["needed"] <= 0:
            print("   ⏭️  跳过（无需翻译）")
            continue

        done += 1
        translated_keys += int(r["needed"])
        changed_files += int(r["changed"])

        elapsed = time.time() - start
        eta = _fmt_eta(elapsed, done, effective_tasks)
        pct = _fmt_pct(done, effective_tasks)
        flag = "已写入" if r["changed"] else "无变化"
        print(
            f"   ✅ 完成 [{done}/{effective_tasks} | {pct} | "
            f"预计剩余 {eta}] | 需翻译={r['needed']:<4} | {flag}"
        )

    elapsed = time.time() - start
    mm, ss = divmod(int(elapsed), 60)
    print(
        f"\n✅ 翻译完成：用时 {mm:02d}:{ss:02d}；"
        f"有效任务 {effective_tasks:,} 个；"
        f"改动文件 {changed_files:,} 个；"
        f"翻译 keys {translated_keys:,} 个"
    )
    return {"effective_tasks": effective_tasks, "files_changed": changed_files, "keys_translated": translated_keys}




# =========================================================
# L10n.swift generator (from Base.lproj/Localizable.strings)
# =========================================================
_SWIFT_KEYWORDS = {
    "associatedtype", "class", "deinit", "enum", "extension", "fileprivate", "func", "import", "init",
    "inout", "internal", "let", "open", "operator", "private", "protocol", "public", "rethrows",
    "static", "struct", "subscript", "typealias", "var", "break", "case", "continue", "default",
    "defer", "do", "else", "fallthrough", "for", "guard", "if", "in", "repeat", "return", "switch",
    "where", "while", "as", "Any", "catch", "false", "is", "nil", "super", "self", "Self", "throw",
    "throws", "true", "try", "_", "#available", "#colorLiteral", "#column", "#file", "#function",
    "#line", "#selector", "#sourceLocation",
}


def _swift_escape_string(s: str) -> str:
    # Swift string literal escaping for " and \\ plus newlines.
    s = s.replace('\\', r'\\')
    s = s.replace('"', r'\"')
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    s = s.replace('\n', r'\n')
    return s


def _upper_camel(parts: List[str]) -> str:
    out: List[str] = []
    for p in parts:
        p2 = re.sub(r"[^0-9A-Za-z]+", " ", p).strip()
        if not p2:
            continue
        ws = [w for w in p2.split() if w]
        for w in ws:
            out.append(w[:1].upper() + w[1:])
    return "".join(out) or "X"


def _lower_camel(parts: List[str]) -> str:
    uc = _upper_camel(parts)
    if not uc:
        return "x"
    return uc[:1].lower() + uc[1:]


def _swift_identifier(name: str, *, upper: bool) -> str:
    # name: raw segment(s)
    parts = [name]
    ident = _upper_camel(parts) if upper else _lower_camel(parts)
    if not ident:
        ident = "x"
    # identifiers cannot start with digit
    if ident[:1].isdigit():
        ident = "_" + ident
    if ident in _SWIFT_KEYWORDS:
        ident = ident + "_"
    return ident


def _swift_identifier_from_parts(parts: List[str], *, upper: bool) -> str:
    ident = _upper_camel(parts) if upper else _lower_camel(parts)
    if not ident:
        ident = "x"
    if ident[:1].isdigit():
        ident = "_" + ident
    if ident in _SWIFT_KEYWORDS:
        ident = ident + "_"
    return ident


def _comments_to_doc(lines: List[str]) -> List[str]:
    # Convert .strings comments into Swift doc comments.
    out: List[str] = []
    buf: List[str] = []
    for raw in lines:
        s = raw.strip("\n")
        s2 = s.strip()
        if not s2:
            continue
        # strip common comment markers
        if s2.startswith("/*"):
            s2 = s2[2:]
        if s2.endswith("*/"):
            s2 = s2[:-2]
        if s2.startswith("//"):
            s2 = s2[2:]
        s2 = s2.strip(" *\t")
        if s2:
            buf.append(s2)
    for line in buf:
        out.append(f"        /// {line}\n")
    return out


def generate_l10n_swift(
        *,
        project_root: Path,
        cfg: Dict[str, Any],
        out_path_arg: Optional[str],
        dry: bool,
) -> Path:
    lang_root_dir, base_dir = project_paths(project_root, cfg)
    src = base_dir / "Localizable.strings"
    if not src.exists():
        raise FileNotFoundError(f"❌ 未找到 Base 的 Localizable.strings：{src}")

    # output path
    if out_path_arg and str(out_path_arg).strip():
        op = Path(str(out_path_arg).strip()).expanduser()
        if not op.is_absolute():
            op = (project_root / op).resolve()
        out_path = op
    else:
        out_path = (lang_root_dir / "L10n.swift").resolve()

    parsed = parse_strings_file(src)

    # Preserve base file order.
    groups: Dict[str, List[StringsEntry]] = {}
    group_order: List[str] = []
    for e in parsed.entries:
        parts = e.key.split(".")
        grp_raw = parts[0] if len(parts) > 1 else "Ungrouped"
        if grp_raw not in groups:
            groups[grp_raw] = []
            group_order.append(grp_raw)
        groups[grp_raw].append(e)

    # Build Swift
    out: List[str] = []
    out.append("// Auto-generated from Base.lproj/Localizable.strings\n")
    out.append("import Foundation\n\n")
    out.append("extension String {\n")
    out.append("    func callAsFunction(_ arguments: CVarArg...) -> String {\n")
    out.append("        String(format: self, locale: Locale.current, arguments: arguments)\n")
    out.append("    }\n")
    out.append("}\n\n")
    out.append("enum L10n {\n")

    for grp_raw in group_order:
        entries = groups[grp_raw]
        grp_name = _swift_identifier(grp_raw, upper=True)
        out.append(f"    enum {grp_name} {{\n")

        used: Dict[str, int] = {}
        for e in entries:
            # doc comments from Base entry
            doc = _comments_to_doc(e.comments)
            out.extend(doc)

            parts = e.key.split(".")
            if len(parts) > 1:
                rest = parts[1:]
            else:
                rest = parts
            prop = _swift_identifier_from_parts(rest, upper=False)
            if prop in used:
                used[prop] += 1
                prop2 = f"{prop}_{used[prop]}"
            else:
                used[prop] = 0
                prop2 = prop

            key_lit = _swift_escape_string(e.key)
            val_lit = _swift_escape_string(e.value)
            out.append(
                f"        static var {prop2}: String {{ return NSLocalizedString(\"{key_lit}\", value: \"{val_lit}\", comment: \"{val_lit}\") }}\n\n"
            )

        # trim last blank line inside group
        if out and out[-1] == "\n":
            out.pop()
        if out and out[-1].endswith("\n\n"):
            out[-1] = out[-1][:-1]

        out.append("    }\n\n")

    if out and out[-1] == "\n\n":
        out.pop()

    out.append("}\n")

    content = "".join(out)

    if dry:
        print(f"（dry-run）将生成：{out_path}")
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(f"✅ 已生成：{out_path}")
    return out_path

# =========================================================
# Doctor
# =========================================================
def doctor(cfg_path: Path, api_key: Optional[str], languages_path: Path, project_root: Path) -> None:
    ok = True

    if OpenAI is None:
        ok = False
        print("❌ OpenAI SDK 不可用：pipx: pipx inject box 'openai>=1.0.0'")
    else:
        print("✅ OpenAI SDK OK")

    try:
        _require_yaml()
        print("✅ PyYAML OK")
    except SystemExit as e:
        ok = False
        print(str(e).strip())

    if not cfg_path.exists():
        ok = False
        print(f"❌ 未找到 {CONFIG_FILE}（请先 strings_i18n init）")
        cfg = None
    else:
        try:
            cfg = read_config(cfg_path)
            print(f"✅ {CONFIG_FILE} OK（model={cfg.get('openAIModel')}）")
        except Exception as e:
            ok = False
            cfg = None
            print(f"❌ {CONFIG_FILE} 解析失败：{e}")

    if not languages_path.exists():
        ok = False
        print(f"❌ 未找到 languages.json：{languages_path}")
    else:
        try:
            langs = load_languages_json(languages_path)
            print(f"✅ languages.json OK（{len(langs)} languages）")
        except Exception as e:
            ok = False
            print(f"❌ languages.json 解析失败：{e}")

    if cfg is not None:
        try:
            lang_root_dir, base_dir = project_paths(project_root, cfg)
            if base_dir.exists() and base_dir.is_dir():
                base_files = [p.name for p in scan_base_strings(base_dir)]
                print(f"✅ Base.lproj OK（{len(base_files)} files: {', '.join(base_files)}）")
            else:
                ok = False
                print(f"❌ Base 目录不存在：{base_dir}")
        except Exception as e:
            ok = False
            print(f"❌ 目录结构检查失败：{e}")

    ak = api_key or os.getenv("OPENAI_API_KEY")
    if not ak:
        print("⚠️ 未提供 API Key：--api-key 或环境变量 OPENAI_API_KEY（翻译时需要）")
    else:
        print("✅ API Key 已配置（来源：参数或环境变量）")

    if not ok:
        raise SystemExit(EXIT_BAD)
    print("✅ doctor 完成")


# =========================================================
# Interactive (slang_i18n style)
# =========================================================
def _read_choice(prompt: str, valid: Iterable[str]) -> str:
    valid_set = {v.lower() for v in valid}
    while True:
        s = input(prompt).strip().lower()
        if s in valid_set:
            return s
        if s in ("q", "quit", "exit"):
            return "0"
        print(f"请输入 {' / '.join(sorted(valid_set))}（或 q 退出）")


def choose_action_interactive(project_root: Path, cfg_path: Path) -> str:
    # 尽力检测 L10n.swift 是否已存在：
    # - 若 strings_i18n.yaml 可读，则以 lang_root/L10n.swift 为准
    # - 否则回退到项目根目录的 ./L10n.swift
    l10n_path = project_root / "L10n.swift"
    if cfg_path.exists():
        try:
            cfg = read_config(cfg_path)
            lang_root_dir, _ = project_paths(project_root, cfg)
            l10n_path = lang_root_dir / "L10n.swift"
        except Exception:
            pass

    exists_flag = "✅ 已存在" if l10n_path.exists() else "➕ 将生成"

    print("=== strings_i18n 操作台 ===")
    print(f"1 - gen-l10n（生成 L10n.swift：{exists_flag}）")
    print("2 - sort（先排序 Base：保留注释、按前缀分组；再按 Base 顺序排序其它语言）")
    print("3 - translate-core（增量翻译：base_locale → core_locales；打印 {{base_locale.name_en}} → {{core_locales.name_en 列表}}）")
    print("4 - translate-target（增量翻译：source_locale → target_locales；目标超过 10 个截断并显示总数）")
    print("5 - cleanup（清理重复/冗余字段：Base 重复 key 列出；其它语言冗余 key 全列并提示是否删除）")
    print("6 - doctor（检查环境/配置/目录结构）")
    print("0 / q - 退出")
    choice = _read_choice("请输入 0 / 1 / ... / 6（或 q 退出）: ", valid=[str(i) for i in range(0, 7)])
    if choice == "0":
        return "exit"
    return {
        "1": "gen-l10n",
        "2": "sort",
        "3": "translate-core",
        "4": "translate-target",
        "5": "cleanup",
        "6": "doctor",
    }[choice]


# =========================================================
# CLI
# =========================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="strings_i18n",
        description="iOS/Xcode .strings 多语言：扫描/同步/排序/重复&冗余清理/增量翻译（支持交互）",
    )
    p.add_argument(
        "action",
        nargs="?",
        choices=[
            "options",
            "init",
            "doctor",
            "scan",
            "sync",
            "sort",
            "dupcheck",
            "dedupe",
            "check",
            "clean",
            "cleanup",
            "translate-core",
            "translate-target",
            "gen-l10n",
        ],
        help="动作（不填则进入交互菜单）",
    )
    p.add_argument("--config", default=CONFIG_FILE, help="配置文件路径（默认 strings_i18n.yaml）")
    p.add_argument("--languages", default=LANG_FILE, help="languages.json 路径（默认 languages.json）")
    p.add_argument("--project-root", default=".", help="项目根目录（默认当前目录）")
    p.add_argument("--api-key", default=None, help="OpenAI API key（也可用环境变量 OPENAI_API_KEY）")
    p.add_argument("--model", default=None, help=f"模型（命令行优先；不传则用配置 openAIModel；允许：{', '.join(ALLOWED_OPENAI_MODELS)}）")
    p.add_argument("--full", action="store_true", help="全量翻译（默认增量）")
    p.add_argument("--yes", action="store_true", help="删除时跳过确认（clean/dedupe）")
    p.add_argument("--keep", default="first", choices=["first", "last"], help="dedupe 保留策略（默认 first）")
    p.add_argument("--no-exitcode-3", action="store_true", help="check/dupcheck 发现问题时仍返回 0（默认返回 3）")
    p.add_argument("--dry-run", action="store_true", help="预览模式（不写入文件）")
    p.add_argument("--l10n-out", default=None, help="L10n.swift 输出路径（默认写入 {lang_root}/L10n.swift；可传相对 project-root 的路径）")
    return p


def print_cli_options() -> None:
    """打印命令行用法与主要选项（用于 CI / README 复制粘贴）。"""
    print("=== strings_i18n CLI 用法 ===")
    for u in BOX_TOOL.get("usage", []):
        print(f"  $ {u}")

    print("\n=== 通用选项 ===")
    for opt in BOX_TOOL.get("options", []):
        flag = opt.get("flag", "")
        desc = opt.get("desc", "")
        if flag:
            print(f"  {flag:<16} {desc}")

    print("\n（提示）每个 action 都支持 --dry-run 预览；check/dupcheck 默认在发现问题时返回 exit code 3。")


def _cfg_one(cfg: Dict[str, Any], key: str) -> Dict[str, str]:
    lst = cfg.get(key)
    if not isinstance(lst, list) or not lst or not isinstance(lst[0], dict):
        raise ValueError(f"配置缺少或格式错误：{key}（需要 list[dict] 且至少 1 个）")
    if not lst[0].get("code") or not lst[0].get("name_en"):
        raise ValueError(f"配置 {key}[0] 需要包含 code 与 name_en")
    return {"code": str(lst[0]["code"]).strip(), "name_en": str(lst[0]["name_en"]).strip()}


def _cfg_list(cfg: Dict[str, Any], key: str) -> List[Dict[str, str]]:
    lst = cfg.get(key, [])
    if not isinstance(lst, list):
        raise ValueError(f"配置字段格式错误：{key}（需要 list）")
    out: List[Dict[str, str]] = []
    for it in lst:
        if isinstance(it, dict) and it.get("code") and it.get("name_en"):
            out.append({"code": str(it["code"]).strip(), "name_en": str(it["name_en"]).strip()})
    return out


def _pick_model(args_model: Optional[str], cfg: Dict[str, Any]) -> str:
    m = (args_model or "").strip() or str(cfg.get("openAIModel") or "").strip() or OpenAIModel.GPT_4O.value
    if m not in set(ALLOWED_OPENAI_MODELS):
        raise ValueError(f"❌ model 不合法：{m!r}，可选：{', '.join(ALLOWED_OPENAI_MODELS)}")
    return m


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve()
    cfg_path = Path(args.config).expanduser()
    if not cfg_path.is_absolute():
        cfg_path = project_root / cfg_path

    languages_path = Path(args.languages).expanduser()
    if not languages_path.is_absolute():
        languages_path = project_root / languages_path

    action = args.action
    interactive = False
    if not action:
        interactive = True
        action = choose_action_interactive(project_root=project_root, cfg_path=cfg_path)
        if action == "exit":
            return EXIT_OK

    if action == "init":
        try:
            init_config(cfg_path, project_root=project_root, languages_path=languages_path)
            return EXIT_OK
        except Exception as e:
            print(str(e))
            return EXIT_BAD

    if action == "options":
        print_cli_options()
        return EXIT_OK

    if action == "doctor":
        try:
            doctor(cfg_path, api_key=args.api_key, languages_path=languages_path, project_root=project_root)
            return EXIT_OK
        except SystemExit as e:
            return int(getattr(e, "code", EXIT_BAD))
        except Exception as e:
            print(str(e))
            return EXIT_BAD

    # below require cfg + project structure
    try:
        cfg = read_config_or_throw(cfg_path)
    except Exception as e:
        print(str(e))
        return EXIT_BAD

    # model selection: CLI > config > default
    try:
        model = _pick_model(args.model, cfg)
    except Exception as e:
        print(str(e))
        return EXIT_BAD

    # paths
    try:
        lang_root_dir, base_dir = project_paths(project_root, cfg)
        base_files = scan_base_strings(base_dir)
    except Exception as e:
        print(str(e))
        return EXIT_BAD

    # languages.json
    try:
        langs = load_languages_json(languages_path)
        all_codes = [x["code"] for x in langs]
    except Exception as e:
        print(str(e))
        return EXIT_BAD

    dry = bool(args.dry_run)

    if action == "cleanup":
        # 目标文件：所有语言（除 Base 目录外），存在的 *.strings
        try:
            target_files = collect_existing_target_files(
                lang_root_dir=lang_root_dir,
                base_files=base_files,
                locale_codes=all_codes,
            )
        except Exception as e:
            print(f"❌ cleanup 失败：{e}")
            return EXIT_FAIL

        # 1) 重复 key：至少包含 Base；也把目标语言一起检查，避免“只修 Base 结果别处还炸”
        base_paths = [base_dir / bf.name for bf in base_files]
        dup_files = base_paths + target_files
        dup_rep = dupcheck_report(dup_files)
        if dup_rep:
            print("=== 重复 key（含 Base）===")
            for fp, items in dup_rep.items():
                print(f"\n• {fp}")
                for k, cnt in sorted(items.items(), key=lambda x: x[0].lower()):
                    print(f"  - {k}  (重复 {cnt} 次)")

            do_dedupe = bool(args.yes)
            if not args.yes:
                ans = input(f"\n发现重复 key。是否要立即删除重复项（保留 {args.keep}）？输入 y 删除，其他键仅检查：").strip().lower()
                do_dedupe = ans in ("y", "yes", "1")

            if do_dedupe:
                stats = dedupe_batch(dup_files, keep=str(args.keep), dry=dry)
                if dry:
                    print("（dry-run：未写入）")
                print(f"✅ dedupe 完成：改动文件 {stats['changed_files']} 个（保留 {args.keep}）")
        else:
            print("✅ 未发现重复 key（含 Base）")

        # 2) 冗余 key：Base 没有但目标有（全部列出）
        red_rep = redundant_report(base_dir=base_dir, base_files=base_files, targets=target_files)
        if red_rep:
            print("\n=== 冗余 key（Base 没有但目标有）===")
            total_files = 0
            total_keys = 0
            for fp, per_base in red_rep.items():
                total_files += 1
                for _, ks in per_base.items():
                    total_keys += len(ks)
                print(f"\n• {fp}")
                # per_base 的 key 是 base 文件名（通常只有一个）
                for _, ks in per_base.items():
                    for k in ks:
                        print(f"  - {k}")

            do_delete = bool(args.yes)
            if not args.yes:
                ans = input("\n发现冗余 key。是否要立即删除？输入 y 删除，其他键仅检查：").strip().lower()
                do_delete = ans in ("y", "yes", "1")

            if do_delete:
                stats = clean_redundant_batch(red_rep, dry=dry)
                if dry:
                    print("（dry-run：未写入）")
                print(
                    f"✅ clean 完成：改动文件 {stats['changed_files']} / {stats['files']}，删除冗余 key {stats['removed_keys']} 个"
                )
        else:
            print("\n✅ 未发现冗余 key（Base 没有但目标有）")

        # 重复/冗余默认都属于“发现问题”
        found = bool(dup_rep) or bool(red_rep)
        if found and not bool(args.no_exitcode_3):
            return EXIT_FOUND
        return EXIT_OK

    if action == "gen-l10n":
        try:
            p = generate_l10n_swift(
                project_root=project_root,
                cfg=cfg,
                out_path_arg=args.l10n_out,
                dry=dry,
            )
            if dry:
                print("（dry-run：未写入）")
            return EXIT_OK
        except Exception as e:
            print(f"❌ gen-l10n 失败：{e}")
            return EXIT_FAIL

    if action == "scan":
        print(f"✅ Base 目录：{base_dir}")
        print("✅ Base 文件清单：")
        for p in base_files:
            print(f"  • {p.name}")
        return EXIT_OK

    if action == "sync":
        try:
            result = sync_language_dirs_and_files(
                lang_root_dir=lang_root_dir,
                base_files=base_files,
                locale_codes=all_codes,
                dry=dry,
            )
            if result["created_dirs"]:
                print("➕ 创建目录：")
                for d in result["created_dirs"]:
                    print(f"  • {d}")
            if result["created_files"]:
                print("➕ 创建文件：")
                for f in result["created_files"]:
                    print(f"  • {f}")
            print(f"✅ 已存在：目录 {result['existing_dirs']:,}；文件 {result['existing_files']:,}")
            if dry:
                print("（dry-run：未写入）")
            return EXIT_OK
        except Exception as e:
            print(f"❌ sync 失败：{e}")
            return EXIT_FAIL

    if action == "sort":
        try:
            res = sort_all(
                lang_root_dir=lang_root_dir,
                base_dir=base_dir,
                base_files=base_files,
                locale_codes=all_codes,
                dry=dry,
            )
            print(
                f"✅ sort 完成：Base 改动 {res.get('base_changed', 0):,} 个文件；"
                f"其它语言处理 {res['total']:,}；改动 {res['changed']:,}；缺失 {res['missing']:,}"
            )
            if dry:
                print("（dry-run：未写入）")
            return EXIT_OK
        except Exception as e:
            print(f"❌ sort 失败：{e}")
            return EXIT_FAIL

    # build existing target list for dup/redundant
    existing_targets = collect_existing_target_files(
        lang_root_dir=lang_root_dir,
        base_files=base_files,
        locale_codes=all_codes,
    )
    base_paths_for_check = [base_dir / bf.name for bf in base_files if (base_dir / bf.name).exists()]

    if action == "dupcheck":
        try:
            rep = dupcheck_report(base_paths_for_check + existing_targets)
            if not rep:
                print("✅ 未发现重复 key")
                return EXIT_OK

            files_n = len(rep)
            groups_n = sum(len(v) for v in rep.values())
            print(f"⚠️ 发现重复：涉及 {files_n} 个文件（包含 Base），共 {groups_n} 组重复 key")
            for fp, m in sorted(rep.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())):
                items = sorted(m.items(), key=lambda kv: (-kv[1], kv[0].lower()))
                show = items[:20]
                print(f"\n- {fp}（{len(items)} 组）")
                for k, c in show:
                    print(f"  • {k}  x{c}")
                if len(items) > len(show):
                    print(f"  ... 另外还有 {len(items) - len(show)} 组未显示")

            if args.no_exitcode_3:
                return EXIT_OK
            return EXIT_FOUND
        except Exception as e:
            print(f"❌ dupcheck 失败：{e}")
            return EXIT_FAIL

    if action == "dedupe":
        try:
            rep = dupcheck_report(existing_targets)
            if not rep:
                print("✅ 未发现重复 key")
                return EXIT_OK

            # 一次性确认
            if not args.yes:
                ans = _read_choice("确认批量删除以上重复 key？请输入 1 删除 / 0 取消: ", valid=["0", "1"])
                if ans != "1":
                    print("🧊 已取消")
                    return EXIT_OK

            targets = [Path(p) for p in rep.keys()]
            r = dedupe_batch(targets, keep=args.keep, dry=dry)
            print(f"✅ dedupe 完成：改动文件 {r['changed_files']:,}（keep={args.keep}）")
            if dry:
                print("（dry-run：未写入）")
            return EXIT_OK
        except Exception as e:
            print(f"❌ dedupe 失败：{e}")
            return EXIT_FAIL

    if action == "check":
        try:
            rep = redundant_report(base_dir=base_dir, base_files=base_files, targets=existing_targets)
            if not rep:
                print("✅ 未发现冗余 key（Base 没有但目标有）")
                return EXIT_OK

            files_n = len(rep)
            keys_n = sum(len(ks) for per_base in rep.values() for ks in per_base.values())
            print(f"⚠️ 发现冗余 key：涉及 {files_n} 个文件，共 {keys_n} 个冗余 key")
            for fp, per_base in sorted(rep.items(), key=lambda kv: (-sum(len(ks) for ks in kv[1].values()), kv[0].lower())):
                total_keys = sum(len(ks) for ks in per_base.values())
                print(f"\n- {fp}（{total_keys} 个冗余 key）")
                for base_name, ks in per_base.items():
                    # 需求：全部列出（避免误删）
                    for k in ks:
                        print(f"  • {k}")

            # 需求：在 check 中直接提示是否要删除
            ans = _read_choice("\n是否立即删除以上冗余 key？请输入 1 删除 / 0 仅检查: ", valid=["0", "1"])
            if ans == "1":
                r = clean_redundant_batch(rep, dry=dry)
                print(f"✅ 已删除冗余 key：改动文件 {r['changed_files']:,}；删除 key {r['removed_keys']:,}")
                if dry:
                    print("（dry-run：未写入）")
                return EXIT_OK

            if args.no_exitcode_3:
                return EXIT_OK
            return EXIT_FOUND
        except Exception as e:
            print(f"❌ check 失败：{e}")
            return EXIT_FAIL

    if action == "clean":
        try:
            rep = redundant_report(base_dir=base_dir, base_files=base_files, targets=existing_targets)
            if not rep:
                print("✅ 未发现冗余 key")
                return EXIT_OK

            # 一次性确认
            keys_n = sum(len(ks) for per_base in rep.values() for ks in per_base.values())
            if not args.yes:
                ans = _read_choice(f"确认批量删除以上 {len(rep)} 个文件中的 {keys_n} 个冗余 key？请输入 1 删除 / 0 取消: ", valid=["0", "1"])
                if ans != "1":
                    print("🧊 已取消")
                    return EXIT_OK

            r = clean_redundant_batch(rep, dry=dry)
            print(f"✅ clean 完成：改动文件 {r['changed_files']:,}；删除 key {r['removed_keys']:,}")
            if dry:
                print("（dry-run：未写入）")
            return EXIT_OK
        except Exception as e:
            print(f"❌ clean 失败：{e}")
            return EXIT_FAIL

    if action == "translate-core":
        try:
            base_locale = _cfg_one(cfg, "base_locale")
            core_locales = _cfg_list(cfg, "core_locales")
            if not core_locales:
                print("❌ core_locales 为空（配置错误）")
                return EXIT_BAD

            api_key = _get_api_key(args.api_key)
            if not api_key:
                print("❌ 未提供 API Key（翻译需要）")
                return EXIT_BAD

            full = bool(args.full) or not bool(
                cfg["options"]["incremental_translate"]
            )

            # 显示翻译任务信息
            print("\n📋 翻译任务：base_locale → core_locales")
            print(f"   源语言：Base.lproj | {base_locale['name_en']}")
            print(f"   目标语言（核心）：{_fmt_locale_names(core_locales, max_show=999)}")

            translate_batch(
                project_root=project_root,
                cfg=cfg,
                base_dir=base_dir,
                base_files=base_files,
                lang_root_dir=lang_root_dir,
                src_dir=base_dir,
                src_name_en=base_locale["name_en"],
                targets=core_locales,
                api_key=api_key,
                model=model,
                full=full,
                dry=dry,
                src_code="Base.lproj",
            )
            return EXIT_OK
        except TranslationError as e:
            print(f"❌ TranslationError: {e}")
            return EXIT_FAIL
        except Exception as e:
            print(f"❌ translate-core 失败：{e}")
            return EXIT_FAIL

    if action == "translate-target":
        try:
            source_locale = _cfg_one(cfg, "source_locale")
            target_locales = _cfg_list(cfg, "target_locales")
            if not target_locales:
                print("❌ target_locales 为空（配置错误）")
                return EXIT_BAD

            api_key = _get_api_key(args.api_key)
            if not api_key:
                print("❌ 未提供 API Key（翻译需要）")
                return EXIT_BAD

            src_code = source_locale["code"]
            src_dir = lang_root_dir / code_to_lproj(src_code)
            if not src_dir.exists() or not src_dir.is_dir():
                print(f"❌ 源语言目录不存在：{src_dir}")
                return EXIT_BAD

            full = bool(args.full) or not bool(
                cfg["options"]["incremental_translate"]
            )

            # 显示翻译任务信息
            print("\n📋 翻译任务：source_locale → target_locales")
            print(f"   源语言：{src_code} | {source_locale['name_en']}")
            print(f"   目标语言（其它）：{_fmt_locale_names(target_locales, max_show=10)}")

            translate_batch(
                project_root=project_root,
                cfg=cfg,
                base_dir=base_dir,
                base_files=base_files,
                lang_root_dir=lang_root_dir,
                src_dir=src_dir,
                src_name_en=source_locale["name_en"],
                targets=target_locales,
                api_key=api_key,
                model=model,
                full=full,
                dry=dry,
                src_code=src_code,
            )
            return EXIT_OK
        except TranslationError as e:
            print(f"❌ TranslationError: {e}")
            return EXIT_FAIL
        except Exception as e:
            print(f"❌ translate-target 失败：{e}")
            return EXIT_FAIL

    print(f"❌ 未知 action：{action}")
    return EXIT_BAD


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(130)
