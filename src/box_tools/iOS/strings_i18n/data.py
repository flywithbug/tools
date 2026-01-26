from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml


DEFAULT_TEMPLATE_NAME = "strings_i18n.yaml"
DEFAULT_LANGUAGES_NAME = "languages.json"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Locale:
    code: str
    name_en: str


@dataclass(frozen=True)
class SwiftCodegenCfg:
    enabled: bool
    input_file: str
    output_file: str
    enum_name: str


@dataclass(frozen=True)
class StringsI18nConfig:
    project_root: Path
    languages_path: Path
    lang_root: Path
    base_folder: str
    base_locale: Locale
    source_locale: Locale
    core_locales: List[Locale]
    target_locales: List[Locale]
    options: Dict[str, Any]
    swift_codegen: SwiftCodegenCfg


def _pkg_file(name: str) -> Path:
    # 约定：strings_i18n.yaml / languages.json 与 data.py 同目录（像 slang_i18n 一样）
    return Path(__file__).with_name(name)


def ensure_languages_json(project_root: Path) -> Path:
    dst = (project_root / DEFAULT_LANGUAGES_NAME).resolve()
    if dst.exists():
        return dst

    src = _pkg_file(DEFAULT_LANGUAGES_NAME)
    if not src.exists():
        raise FileNotFoundError(f"内置默认 {DEFAULT_LANGUAGES_NAME} 不存在：{src}")

    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def load_target_locales_from_languages_json(languages_path: Path, exclude_codes: List[str]) -> List[Dict[str, str]]:
    arr = json.loads(languages_path.read_text(encoding="utf-8"))
    if not isinstance(arr, list):
        raise ValueError(f"{DEFAULT_LANGUAGES_NAME} 顶层必须是数组")

    exclude = set(exclude_codes)
    seen = set()
    out: List[Dict[str, str]] = []

    for it in arr:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code", "")).strip()
        name_en = str(it.get("name_en", "")).strip()
        if not code or not name_en:
            continue
        if code in exclude:
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name_en": name_en})

    return out


def _yaml_block_for_target_locales(locales: List[Dict[str, str]]) -> str:
    lines = ["target_locales:"]
    if not locales:
        lines.append("  # init 时生成")
        return "\n".join(lines) + "\n"

    for it in locales:
        lines.append(f"  - code: {it['code']}")
        lines.append(f"    name_en: {it['name_en']}")
    return "\n".join(lines) + "\n"


def replace_target_locales_block(template_text: str, new_locales: List[Dict[str, str]]) -> str:
    new_block = _yaml_block_for_target_locales(new_locales)

    m = re.search(r"(?m)^target_locales:\s*$", template_text)
    if not m:
        raise ValueError("模板中未找到 target_locales: 段落")

    start = m.start()
    after = template_text[m.end():]
    next_key = re.search(r"(?m)^(?!target_locales:)[A-Za-z_][A-Za-z0-9_]*:\s*$", after)

    end = m.end() + (next_key.start() if next_key else len(after))
    return template_text[:start] + new_block + template_text[end:]


def init_config(project_root: Path, cfg_path: Path) -> None:
    project_root = project_root.resolve()
    cfg_path = cfg_path.resolve()

    languages_path = ensure_languages_json(project_root)

    if not cfg_path.exists():
        tpl = _pkg_file(DEFAULT_TEMPLATE_NAME)
        if not tpl.exists():
            raise FileNotFoundError(f"内置默认配置模板不存在：{tpl}")

        tpl_text = tpl.read_text(encoding="utf-8")
        raw_tpl = yaml.safe_load(tpl_text) or {}
        validate_config(raw_tpl)

        src_code = raw_tpl["source_locale"]["code"]
        base_code = raw_tpl["base_locale"]["code"]

        # 排除 source/base/core（core 从模板里读取）
        core_codes = [it["code"] for it in (raw_tpl.get("core_locales") or []) if isinstance(it, dict) and it.get("code")]
        targets = load_target_locales_from_languages_json(
            languages_path,
            exclude_codes=[src_code, base_code] + core_codes,
        )

        out_text = replace_target_locales_block(tpl_text, targets)
        cfg_path.write_text(out_text, encoding="utf-8")

    # init 后也要做一次校验（但不做目录存在性强校验）
    assert_config_ok(cfg_path, project_root=project_root)


def assert_config_ok(cfg_path: Path, project_root: Optional[Path] = None) -> Dict:
    cfg_path = cfg_path.resolve()
    project_root = (project_root or cfg_path.parent).resolve()

    if not cfg_path.exists():
        raise ConfigError(
            f"配置文件不存在：{cfg_path}\n"
            f"解决方法：运行 `box_strings_i18n init` 生成默认配置。"
        )

    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise ConfigError(
            f"配置文件无法解析为 YAML：{cfg_path}\n"
            f"原因：{e}\n"
            f"解决方法：修复 YAML 格式或运行 `box_strings_i18n init` 重新生成。"
        )

    try:
        validate_config(raw)
    except Exception as e:
        raise ConfigError(
            f"配置文件校验失败：{cfg_path}\n"
            f"原因：{e}\n"
            f"解决方法：修复配置字段/类型，或运行 `box_strings_i18n init` 重新生成。"
        )

    return raw


def validate_config(raw: Dict) -> None:
    # 框架版：先校验最关键字段存在，后续再按 PRD 完整细化
    required_top = [
        "options",
        "languages",
        "lang_root",
        "base_folder",
        "base_locale",
        "source_locale",
        "core_locales",
        "target_locales",
        "swift_codegen",
    ]
    for k in required_top:
        if k not in raw:
            raise ValueError(f"配置缺少字段：{k}")

    for obj_key in ["base_locale", "source_locale"]:
        v = raw[obj_key]
        if not isinstance(v, dict) or not str(v.get("code", "")).strip() or not str(v.get("name_en", "")).strip():
            raise ValueError(f"{obj_key} 必须包含非空 code + name_en")

    if not isinstance(raw["core_locales"], list):
        raise ValueError("core_locales 必须是数组（可为空数组）")

    if not isinstance(raw["target_locales"], list):
        raise ValueError("target_locales 必须是数组（可为空数组）")

    if not isinstance(raw["options"], dict):
        raise ValueError("options 必须是 object")

    if not isinstance(raw["swift_codegen"], dict):
        raise ValueError("swift_codegen 必须是 object")


def load_config(cfg_path: Path, project_root: Optional[Path] = None) -> StringsI18nConfig:
    cfg_path = cfg_path.resolve()
    project_root = (project_root or cfg_path.parent).resolve()

    raw = assert_config_ok(cfg_path, project_root=project_root)

    languages_path = (project_root / raw["languages"]).resolve()
    lang_root = (project_root / raw["lang_root"]).resolve()

    base_locale = Locale(**raw["base_locale"])
    source_locale = Locale(**raw["source_locale"])
    core_locales = [Locale(**it) for it in (raw.get("core_locales") or [])]
    target_locales = [Locale(**it) for it in (raw.get("target_locales") or [])]

    sc = raw["swift_codegen"]
    swift_codegen = SwiftCodegenCfg(
        enabled=bool(sc.get("enabled", False)),
        input_file=str(sc.get("input_file", "Localizable.strings")),
        output_file=str(sc.get("output_file", "")),
        enum_name=str(sc.get("enum_name", "L10n")),
    )

    return StringsI18nConfig(
        project_root=project_root,
        languages_path=languages_path,
        lang_root=lang_root,
        base_folder=str(raw["base_folder"]),
        base_locale=base_locale,
        source_locale=source_locale,
        core_locales=core_locales,
        target_locales=target_locales,
        options=raw["options"],
        swift_codegen=swift_codegen,
    )


# ----------------------------
# actions（框架版 stub）
# ----------------------------
def run_doctor(cfg: StringsI18nConfig) -> int:
    # 框架先只检查目录存在性，后续补：缺 key / 冗余 / 重复 / 空值
    if not cfg.lang_root.exists():
        print(f"❌ lang_root 不存在：{cfg.lang_root}")
        return 1
    base_dir = cfg.lang_root / cfg.base_folder
    if not base_dir.exists():
        print(f"❌ Base 目录不存在：{base_dir}")
        return 1

    print("✅ doctor 通过（框架版：仅检查目录存在性）")
    return 0


def run_sort(cfg: StringsI18nConfig) -> int:
    # sort 前先 doctor
    if run_doctor(cfg) != 0:
        print("❌ sort 中止：doctor 未通过")
        return 1

    print("✅ sort 完成（框架版：尚未实现 .strings 解析/写回）")
    return 0
