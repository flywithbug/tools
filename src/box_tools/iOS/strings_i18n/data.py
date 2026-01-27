#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strings_i18n data.py

目标：先把骨架搭起来（可被 tool.py 调用），保证：
- 配置读取/校验（strings_i18n.yaml）
- init / doctor / sort / gen-l10n 所需的函数签名齐全
- 复杂逻辑（.strings 解析、分组排序、L10n.swift 生成细节、冗余 key 清理、差异计算等）先以 TODO 标记

该模块会参考 slang_i18n 的工程结构，但数据格式是 Xcode .lproj/.strings。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

CONFIG_FILE = "strings_i18n.yaml"
LANG_FILE = "languages.json"


# -------------------------
# Config schema
# -------------------------

@dataclass(frozen=True)
class Locale:
    code: str
    name_en: str = ""


@dataclass(frozen=True)
class Options:
    cleanup_extra_keys: bool = True
    incremental_translate: bool = True
    normalize_filenames: bool = True
    sort_keys: bool = True


@dataclass(frozen=True)
class I18nConfig:
    """
    与 strings_i18n.yaml 对齐的配置对象（字段尽量直白，避免魔法）。
    注意：yaml 里 base_locale/source_locale 是 list（历史原因），这里读入后取第一个。
    """
    options: Options
    languages_path: Path

    lang_root: Path
    base_folder: str

    base_locale: Locale
    source_locale: Locale
    core_locales: List[Locale] = field(default_factory=list)
    target_locales: List[Locale] = field(default_factory=list)

    prompts: Dict[str, Any] = field(default_factory=dict)

    # translate 默认模型（可以被 CLI 覆盖）
    openai_model: str = "gpt-4o-mini"


# -------------------------
# YAML 读写
# -------------------------

def read_config_or_throw(cfg_path: Path) -> I18nConfig:
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"❌ 配置文件不存在：{cfg_path}\n"
            f"解决：在项目根目录执行 `box_strings_i18n init` 生成 strings_i18n.yaml"
        )

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    _validate_cfg_dict(raw, cfg_path)

    options = _parse_options(raw.get("options") or {})
    project_root = cfg_path.parent

    languages = raw.get("languages") or LANG_FILE
    languages_path = (project_root / languages).resolve() if not Path(languages).is_absolute() else Path(languages)

    lang_root = raw.get("lang_root")
    if not lang_root:
        raise ValueError(f"❌ 缺少 lang_root：{cfg_path}")
    lang_root_path = (project_root / lang_root).resolve() if not Path(lang_root).is_absolute() else Path(lang_root)

    base_folder = raw.get("base_folder") or "Base.lproj"

    base_locale = _parse_single_locale_list(raw.get("base_locale"), key_name="base_locale")
    source_locale = _parse_single_locale_list(raw.get("source_locale"), key_name="source_locale")

    core_locales = _parse_locales(raw.get("core_locales") or [])
    target_locales = _parse_locales(raw.get("target_locales") or [])

    prompts = raw.get("prompts") or {}
    model = (raw.get("openai_model") or raw.get("model") or "gpt-4o-mini").strip()

    return I18nConfig(
        options=options,
        languages_path=languages_path,
        lang_root=lang_root_path,
        base_folder=base_folder,
        base_locale=base_locale,
        source_locale=source_locale,
        core_locales=core_locales,
        target_locales=target_locales,
        prompts=prompts,
        openai_model=model,
    )


def _validate_cfg_dict(raw: Dict[str, Any], cfg_path: Path) -> None:
    must = ["options", "languages", "lang_root", "base_folder", "base_locale", "source_locale"]
    missing = [k for k in must if k not in raw]
    if missing:
        raise ValueError(f"❌ 配置缺少字段 {missing}：{cfg_path}")


def _parse_options(d: Dict[str, Any]) -> Options:
    return Options(
        cleanup_extra_keys=bool(d.get("cleanup_extra_keys", True)),
        incremental_translate=bool(d.get("incremental_translate", True)),
        normalize_filenames=bool(d.get("normalize_filenames", True)),
        sort_keys=bool(d.get("sort_keys", True)),
    )


def _parse_single_locale_list(v: Any, key_name: str) -> Locale:
    if not isinstance(v, list) or not v:
        raise ValueError(f"❌ 配置 {key_name} 必须是非空 list")
    one = v[0] or {}
    return Locale(code=str(one.get("code") or "").strip(), name_en=str(one.get("name_en") or "").strip())


def _parse_locales(v: Any) -> List[Locale]:
    out: List[Locale] = []
    if not isinstance(v, list):
        return out
    for it in v:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "").strip()
        if not code:
            continue
        out.append(Locale(code=code, name_en=str(it.get("name_en") or "").strip()))
    return out


def pick_model(cli_model: Optional[str], cfg: I18nConfig) -> str:
    m = (cli_model or "").strip()
    if m:
        return m
    return (cfg.openai_model or "gpt-4o-mini").strip()


# -------------------------
# init / doctor
# -------------------------

_TEMPLATE_YAML = """# iOS 多語言配置檔（strings_i18n.yaml）
# 由 box_strings_i18n init 自動生成
# 可手動調整，但建議保持與 languages.json 同步

options:
  cleanup_extra_keys: true       # 是否自動清理目標語言中 Base 沒有的 key
  incremental_translate: true    # 是否支援增量翻譯（僅翻譯新增/變更的 key）
  normalize_filenames: true      # 是否規範化文件名（可選）
  sort_keys: true                # 是否按 key 排序輸出

languages: ./languages.json      # 語言定義檔路徑（code + name_en）

# 語言檔案存放根目錄與 Base 資料夾名稱
lang_root: ./YourApp/SupportFiles/
base_folder: Base.lproj

# 基礎語言（通常是簡體中文）
base_locale:
  - code: zh-Hans
    name_en: Simplified Chinese

# 原始語言（通常是英文，翻譯的起點）
source_locale:
  - code: en
    name_en: English

# 核心語言（常駐、優先翻譯、常在應用內顯示的語言）
core_locales:
  - code: zh-Hant
    name_en: Traditional Chinese
  - code: zh-Hans
    name_en: Simplified Chinese
  - code: en
    name_en: English

# 目標翻譯語言（可由 languages.json 派生，也可手動維護）
target_locales: []

prompts:
  default_en: |
    Translate UI strings naturally for a mobile app.
    Be concise, clear, and consistent.
"""


def init_config(cfg_path: Path, project_root: Path, languages_path: Path) -> None:
    """
    生成/校验配置文件，并确保 languages.json 存在（骨架版本：仅做最小检查）。
    """
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    if not languages_path.exists():
        raise FileNotFoundError(
            f"❌ languages.json 不存在：{languages_path}\n"
            f"解决：把 languages.json 放到该路径，或通过 --languages 指定。"
        )

    if not cfg_path.exists():
        cfg_path.write_text(_TEMPLATE_YAML, encoding="utf-8")
        print(f"✅ 已生成配置文件：{cfg_path}")
    else:
        # 仅校验，不重写
        _ = read_config_or_throw(cfg_path)
        print(f"✅ 配置文件已存在且校验通过：{cfg_path}")

    # 骨架：只确保 lang_root 目录存在（若 yaml 里还是模板路径，则需要用户手动改）
    try:
        cfg = read_config_or_throw(cfg_path)
        cfg.lang_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        # init 阶段尽量不因为模板路径导致 hard fail
        pass


def doctor(cfg_path: Path, languages_path: Path, project_root: Path, api_key: Optional[str]) -> None:
    """
    环境/结构诊断（骨架版本）：
    - 依赖：能读 yaml / json
    - 文件：cfg / languages 是否存在
    - 目录：lang_root/base_folder 是否存在
    """
    problems: List[str] = []

    if not cfg_path.exists():
        problems.append(f"- 缺少配置：{cfg_path}（请运行 box_strings_i18n init）")

    if not languages_path.exists():
        problems.append(f"- 缺少 languages.json：{languages_path}")

    if cfg_path.exists():
        try:
            cfg = read_config_or_throw(cfg_path)
        except Exception as e:
            problems.append(f"- 配置解析失败：{e}")
            cfg = None  # type: ignore
        if cfg:
            if not cfg.lang_root.exists():
                problems.append(f"- lang_root 不存在：{cfg.lang_root}")
            base_dir = cfg.lang_root / cfg.base_folder
            if not base_dir.exists():
                problems.append(f"- Base 目录不存在：{base_dir}")
            base_strings = base_dir / "Localizable.strings"
            if not base_strings.exists():
                problems.append(f"- Base 缺少 Localizable.strings：{base_strings}")

    if api_key is None:
        # doctor 不强制要求 key，但给提示
        pass

    if problems:
        print("❌ Doctor 发现问题：")
        for p in problems:
            print(p)
        raise SystemExit(2)

    print("✅ Doctor 通过：结构与配置基本正常（骨架检查）")


# -------------------------
# sort / gen-l10n
# -------------------------

@dataclass(frozen=True)
class SortStats:
    touched_files: int = 0
    skipped_files: int = 0
    changed_files: int = 0
    total_keys: int = 0


def sort_command(project_root: Path, cfg: I18nConfig, dry_run: bool) -> SortStats:
    """
    排序命令（骨架）：
    - 未来实现：读取 Base.lproj/Localizable.strings，按“前缀分组 + 2空行 + 注释跟随”排序
    - 其他语言：按 Base key 顺序对齐 + 自身排序规则
    目前仅做最小存在性检查与统计占位。
    """
    lang_root = _resolve_lang_root(project_root, cfg)
    base_dir = lang_root / cfg.base_folder
    base_strings = base_dir / "Localizable.strings"
    if not base_strings.exists():
        raise FileNotFoundError(f"Base 缺少 Localizable.strings：{base_strings}")

    # TODO: 解析 .strings（支持 // 注释、/* */ 注释、"k"="v";）
    # TODO: 分组排序规则
    # TODO: 写回（dry_run 则只打印差异摘要）

    return SortStats(touched_files=1, skipped_files=0, changed_files=0, total_keys=0)


def print_sort_summary(stats: SortStats, dry_run: bool) -> None:
    mode = "dry-run" if dry_run else "write"
    print("📚 sort 汇总（骨架）")
    print(f"- mode: {mode}")
    print(f"- touched_files: {stats.touched_files}")
    print(f"- changed_files: {stats.changed_files}")
    print(f"- total_keys: {stats.total_keys}")


def generate_l10n_swift(project_root: Path, cfg: I18nConfig, out_path_arg: Optional[str], dry_run: bool) -> Path:
    """
    生成 L10n.swift（骨架）：
    - 未来实现：从 Base.lproj/Localizable.strings 读取 key，按点号前缀分组生成 Swift 访问器
    """
    lang_root = _resolve_lang_root(project_root, cfg)
    base_dir = lang_root / cfg.base_folder
    base_strings = base_dir / "Localizable.strings"
    if not base_strings.exists():
        raise FileNotFoundError(f"Base 缺少 Localizable.strings：{base_strings}")

    if out_path_arg:
        out_path = Path(out_path_arg).expanduser()
        if not out_path.is_absolute():
            out_path = (project_root / out_path).resolve()
    else:
        out_path = lang_root / "L10n.swift"

    content = _render_l10n_swift_skeleton()
    if dry_run:
        print(f"（dry-run）将写入：{out_path}")
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
    return out_path


def _render_l10n_swift_skeleton() -> str:
    return """// Generated by box_strings_i18n gen-l10n (skeleton)
// TODO: generate real accessors from Base.lproj/Localizable.strings

import Foundation

enum L10n {
    static func tr(_ key: String) -> String {
        return NSLocalizedString(key, comment: "")
    }
}
"""


def _resolve_lang_root(project_root: Path, cfg: I18nConfig) -> Path:
    # cfg.lang_root 已经是 cfg 文件相对路径 resolve 的结果，但 tool.py 允许 --project-root
    # 这里再兜底：如果 cfg.lang_root 不是绝对路径，就以 project_root 作为基准
    p = cfg.lang_root
    return p if p.is_absolute() else (project_root / p).resolve()


# -------------------------
# languages.json helper
# -------------------------

def read_languages(languages_path: Path) -> List[Dict[str, Any]]:
    if not languages_path.exists():
        raise FileNotFoundError(f"languages.json 不存在：{languages_path}")
    return json.loads(languages_path.read_text(encoding="utf-8"))


def is_meta_key(k: str) -> bool:
    return k.startswith("@@")


