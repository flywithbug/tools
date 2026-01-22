from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from box_tools._share.openai_translate.models import OpenAIModel

from .models import (
    LocaleSpec,
    Options,
    Prompts,
    SlangI18nConfig,
)

# =========================
# Errors
# =========================

class ConfigError(RuntimeError):
    pass


# =========================
# Helpers
# =========================

def _as_str(x: object, default: str = "") -> str:
    if x is None:
        return default
    s = str(x).strip()
    return s if s else default


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _normalize_locale_code(code: str) -> str:
    return _as_str(code)


# =========================
# Default prompts
# =========================

def default_prompts() -> Prompts:
    return Prompts(
        default_en=(
            "Translate UI strings naturally for a mobile app.\n"
            "Be concise, clear, and consistent.\n"
        ),
        by_locale_en={
            "zh_Hant": (
                "Use Taiwan Traditional Chinese UI style.\n"
                "Add spaces between Chinese and English characters when both appear in the same sentence, for readability.\n"
            ),
            "ja": (
                "Use polite and concise Japanese UI tone.\n"
                "Avoid overly long sentences.\n"
            ),
            "ko": (
                "Use natural Korean UI style.\n"
            ),
        },
    )


# =========================
# languages.json
# =========================

@dataclass(frozen=True)
class LanguageRow:
    code: str
    name_en: str = ""
    display_name: str = ""


def ensure_languages_json(
        *,
        root_dir: Path,
        filename: str = "languages.json",
        dry_run: bool = False,
) -> Path:
    path = root_dir / filename
    if path.exists():
        return path

    default_data = [
        {"code": "en", "displayName": "English", "name_en": "English"},
        {"code": "zh_Hant", "displayName": "中文（繁體）", "name_en": "Traditional Chinese"},
        {"code": "ja", "displayName": "日本語", "name_en": "Japanese"},
        {"code": "ko", "displayName": "한국어", "name_en": "Korean"},
    ]

    if not dry_run:
        path.write_text(
            json.dumps(default_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return path


def load_languages_json(
        *, root_dir: Path, filename: str = "languages.json"
) -> Tuple[LanguageRow, ...]:
    path = root_dir / filename
    if not path.exists():
        raise ConfigError(f"未找到 {filename}：{path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError(f"{filename} 解析失败：{e}")

    if not isinstance(data, list):
        raise ConfigError(f"{filename} 顶层必须是数组")

    rows: List[LanguageRow] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ConfigError(f"{filename}[{i}] 不是 object")
        code = _normalize_locale_code(item.get("code"))
        if not code:
            raise ConfigError(f"{filename}[{i}] 缺少 code")
        rows.append(
            LanguageRow(
                code=code,
                name_en=_as_str(item.get("name_en")),
                display_name=_as_str(item.get("displayName")),
            )
        )

    return tuple(rows)


def derive_locales_from_languages_json(
        *,
        languages: Tuple[LanguageRow, ...],
        source_locale: str = "en",
) -> Tuple[LocaleSpec, Tuple[LocaleSpec, ...]]:
    src = _normalize_locale_code(source_locale) or "en"

    def _name(row: LanguageRow) -> str:
        return row.name_en or row.display_name or ""

    src_spec = LocaleSpec(code=src, name_en="English")
    for r in languages:
        if r.code == src:
            src_spec = LocaleSpec(code=src, name_en=_name(r))
            break

    targets: List[LocaleSpec] = []
    for r in languages:
        if r.code and r.code != src:
            targets.append(LocaleSpec(code=r.code, name_en=_name(r)))

    seen = set()
    dedup: List[LocaleSpec] = []
    for t in targets:
        if t.code in seen:
            continue
        seen.add(t.code)
        dedup.append(t)

    return src_spec, tuple(dedup)


# =========================
# YAML load / parse
# =========================

def default_config_path(root_dir: Path) -> Path:
    return root_dir / "slang_i18n.yaml"


def load_config_yaml(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise ConfigError(f"配置文件不存在：{path}")
    try:
        obj = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError(f"YAML 解析失败：{e}")
    if not isinstance(obj, dict):
        raise ConfigError("配置文件顶层必须是 mapping")
    return obj


def _parse_prompts(raw: Dict[str, object]) -> Prompts:
    prompts_raw = raw.get("prompts")
    if prompts_raw is None:
        return default_prompts()

    if not isinstance(prompts_raw, dict):
        raise ConfigError("prompts 必须是 object")

    default_en = _as_str(prompts_raw.get("default_en"))
    by_locale_raw = prompts_raw.get("by_locale_en") or {}
    if not isinstance(by_locale_raw, dict):
        raise ConfigError("prompts.by_locale_en 必须是 object")

    by_locale_en = {
        _normalize_locale_code(k): _as_str(v)
        for k, v in by_locale_raw.items()
    }

    if not default_en:
        default_en = default_prompts().default_en

    return Prompts(default_en=default_en, by_locale_en=by_locale_en)


def parse_config_dict(
        *, root_dir: Path, raw: Dict[str, object]
) -> SlangI18nConfig:
    i18n_dir = _as_str(raw.get("i18nDir"), "i18n")
    i18n_path = (root_dir / i18n_dir).resolve()

    src_raw = raw.get("source_locale") or {}
    if not isinstance(src_raw, dict):
        raise ConfigError("source_locale 必须是 object")

    source_locale = LocaleSpec(
        code=_normalize_locale_code(_as_str(src_raw.get("code"), "en")),
        name_en=_as_str(src_raw.get("name_en"), "English"),
    )

    tgt_raw = raw.get("target_locales") or []
    if not isinstance(tgt_raw, list):
        raise ConfigError("target_locales 必须是数组")

    targets: List[LocaleSpec] = []
    for i, item in enumerate(tgt_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"target_locales[{i}] 必须是 object")
        code = _normalize_locale_code(_as_str(item.get("code")))
        if not code:
            raise ConfigError(f"target_locales[{i}] 缺少 code")
        targets.append(
            LocaleSpec(code=code, name_en=_as_str(item.get("name_en")))
        )

    targets = [t for t in targets if t.code != source_locale.code]
    targets = list({t.code: t for t in targets}.values())

    raw_model = _as_str(raw.get("openAIModel"), OpenAIModel.GPT_4O.value)
    try:
        openai_model = OpenAIModel(raw_model).value
    except ValueError:
        allowed = ", ".join(m.value for m in OpenAIModel)
        raise ConfigError(
            f"openAIModel 不支持：{raw_model}，可选值：{allowed}"
        )

    options_raw = raw.get("options") or {}
    if not isinstance(options_raw, dict):
        raise ConfigError("options 必须是 object")

    options = Options(
        sort_keys=bool(options_raw.get("sort_keys", True)),
        incremental_translate=bool(options_raw.get("incremental_translate", True)),
        cleanup_extra_keys=bool(options_raw.get("cleanup_extra_keys", True)),
        normalize_filenames=bool(options_raw.get("normalize_filenames", True)),
    )

    prompts = _parse_prompts(raw)

    return SlangI18nConfig(
        i18n_dir=i18n_path,
        source_locale=source_locale,
        target_locales=tuple(targets),
        openai_model=openai_model,
        prompts=prompts,
        options=options,
    )


# =========================
# YAML template generation
# =========================

def _indent_block(text: str, spaces: int) -> str:
    prefix = " " * spaces
    lines = (text or "").rstrip("\n").splitlines()
    return "\n".join(prefix + ln for ln in lines) if lines else prefix


def generate_commented_yaml_template(
        *, cfg: SlangI18nConfig, languages_json_path: Optional[Path] = None
) -> str:
    tgt_lines = [
        f"  - code: {t.code}\n    name_en: \"{t.name_en}\""
        for t in cfg.target_locales
    ]

    model_lines = []
    for m in OpenAIModel:
        mark = " (default)" if m == OpenAIModel.GPT_4O else ""
        model_lines.append(f"#   - {m.value}{mark}")

    openai_model_block = (
            "# OpenAI 模型名（用于 translate）\n"
            "# 可选值：\n"
            + "\n".join(model_lines)
            + f"\nopenAIModel: {OpenAIModel.GPT_4O.value}\n\n"
    )

    return (
            "# slang_i18n.yaml\n\n"
            "i18nDir: i18n\n\n"
            "source_locale:\n"
            f"  code: {cfg.source_locale.code}\n"
            f"  name_en: \"{cfg.source_locale.name_en}\"\n\n"
            "target_locales:\n"
            + ("\n".join(tgt_lines) if tgt_lines else "  []")
            + "\n\n"
            + openai_model_block
            + "prompts:\n"
              "  default_en: |\n"
              f"{_indent_block(cfg.prompts.default_en, 4)}\n"
              "  by_locale_en:\n"
            + (
                "\n".join(
                    f"    {k}: |\n{_indent_block(v, 6)}"
                    for k, v in cfg.prompts.by_locale_en.items()
                )
                if cfg.prompts.by_locale_en
                else "    {}"
            )
            + "\n\n"
              "options:\n"
              f"  sort_keys: {str(cfg.options.sort_keys).lower()}\n"
              f"  incremental_translate: {str(cfg.options.incremental_translate).lower()}\n"
              f"  cleanup_extra_keys: {str(cfg.options.cleanup_extra_keys).lower()}\n"
              f"  normalize_filenames: {str(cfg.options.normalize_filenames).lower()}\n"
    )


# =========================
# Public loader
# =========================

def load_config(
        *, root_dir: Path, config_path: Optional[Path] = None
) -> SlangI18nConfig:
    path = config_path or default_config_path(root_dir)
    raw = load_config_yaml(path)
    return parse_config_dict(root_dir=root_dir, raw=raw)
