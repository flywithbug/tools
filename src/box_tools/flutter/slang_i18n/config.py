from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

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
    # 先原样保留（zh_Hant 等），后续如需强制 normalize 可在此处扩展
    return _as_str(code)


# =========================
# Default prompts (strict schema)
# =========================

def default_prompts() -> Prompts:
    """
    默认提示词（严格按示例 prompts 结构）：
    - default_en: 通用提示
    - by_locale_en: zh_Hant/ja/ko/fil 的额外约束
    内容参考你提供的 slang_i18n.yaml 示例。
    """
    return Prompts(
        default_en=(
            "Translate UI strings naturally for a mobile app.\n"
            "Be concise, clear, and consistent.\n"
        ),
        by_locale_en={
            "zh_Hant": (
                "Use Taiwan Traditional Chinese UI style.\n"
                "Add spaces between Chinese and English characters when both appear in the same sentence, for readability.\n"
                "Keep financial/crypto UI terms consistent.\n"
                "\n"
                "Preferred term mapping examples (not strict word-by-word translation):\n"
                '  - "Long/Short" → 做多/做空\n'
                '  - "Deposit/Withdraw" → 充值/提現\n'
                '  - "Position" → 倉位\n'
                '  - "Order" → 訂單\n'
                '  - "Subscription" → 申購\n'
                '  - "Points" → 積分\n'
                '  - "Affiliate" → 代理商\n'
                '  - "Account" → 賬戶\n'
                '  - "Size" → 數量\n'
                '  - "Bonus" → 體驗金\n'
                '  - "Omni Spot Swap" → "Omni 現貨賬戶"\n'
            ),
            "ja": (
                "Use polite and concise Japanese UI tone.\n"
                "Prefer natural app wording.\n"
                "Avoid overly long sentences.\n"
            ),
            "ko": (
                "Use natural Korean UI style.\n"
                "Prefer concise mobile UI wording.\n"
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
    """
    若 languages.json 不存在：写入默认内容（按你提供的示例），并返回路径。
    """
    path = root_dir / filename
    if path.exists():
        return path

    default_data = [
        {"code": "en", "name_zh": "英语", "asc_code": "en-US", "displayName": "English", "name_en": "English"},
        {"code": "zh_Hant", "name_zh": "中文（繁體）", "asc_code": "zh-Hant", "displayName": "中文（繁體）", "name_en": "Traditional Chinese"},
        {"code": "de", "name_zh": "德语", "asc_code": "de-DE", "displayName": "Deutsch", "name_en": "German"},
        {"code": "es", "name_zh": "西班牙语（西班牙）", "asc_code": "es-ES", "displayName": "Español (España)", "name_en": "Spanish"},
        {"code": "fil", "name_zh": "菲律宾语", "asc_code": "fil", "displayName": "Filipino", "name_en": "Filipino"},
        {"code": "fr", "name_zh": "法语", "asc_code": "fr-FR", "displayName": "Français", "name_en": "French"},
        {"code": "hi", "name_zh": "印地语", "asc_code": "hi", "displayName": "हिन्दी", "name_en": "Hindi"},
        {"code": "id", "name_zh": "印度尼西亚语", "asc_code": "id", "displayName": "Bahasa Indonesia", "name_en": "Indonesian"},
        {"code": "ja", "name_zh": "日语", "asc_code": "ja", "displayName": "日本語", "name_en": "Japanese"},
        {"code": "kk", "name_zh": "哈萨克语", "asc_code": "kk", "displayName": "Қазақ тілі", "name_en": "Kazakh"},
        {"code": "ko", "name_zh": "韩语", "asc_code": "ko", "displayName": "한국어", "name_en": "Korean"},
        {"code": "pt", "name_zh": "葡萄牙语", "asc_code": "pt", "displayName": "Português", "name_en": "Portuguese"},
        {"code": "ru", "name_zh": "俄语", "asc_code": "ru", "displayName": "Русский", "name_en": "Russian"},
        {"code": "th", "name_zh": "泰语", "asc_code": "th", "displayName": "ไทย", "name_en": "Thai"},
        {"code": "uk", "name_zh": "乌克兰语", "asc_code": "uk", "displayName": "Українська", "name_en": "Ukrainian"},
        {"code": "vi", "name_zh": "越南语", "asc_code": "vi", "displayName": "Tiếng Việt", "name_en": "Vietnamese"},
        {"code": "tr", "name_zh": "土耳其语", "asc_code": "tr", "displayName": "Türkçe", "name_en": "Turkish"},
        {"code": "nl", "name_zh": "荷兰语", "asc_code": "nl-NL", "displayName": "Nederlands", "name_en": "Dutch"},
    ]

    if not dry_run:
        path.write_text(json.dumps(default_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return path


def load_languages_json(*, root_dir: Path, filename: str = "languages.json") -> Tuple[LanguageRow, ...]:
    path = root_dir / filename
    if not path.exists():
        raise ConfigError(f"未找到 {filename}：{path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError(f"{filename} 解析失败：{e}")

    if not isinstance(data, list):
        raise ConfigError(f"{filename} 格式错误：顶层必须是数组 list")

    rows: List[LanguageRow] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ConfigError(f"{filename} 格式错误：第 {i} 项不是 object")
        code = _normalize_locale_code(_as_str(item.get("code")))
        if not code:
            raise ConfigError(f"{filename} 格式错误：第 {i} 项缺少 code")
        name_en = _as_str(item.get("name_en"), "")
        display_name = _as_str(item.get("displayName"), "")
        rows.append(LanguageRow(code=code, name_en=name_en, display_name=display_name))

    return tuple(rows)


def derive_locales_from_languages_json(
        *,
        languages: Tuple[LanguageRow, ...],
        source_locale: str = "en",
) -> Tuple[LocaleSpec, Tuple[LocaleSpec, ...]]:
    """
    - source_locale 默认 en
    - target_locales = languages.json 中除 source 之外的语言
    - 去重：target 不得含 source，target 内去重
    """
    src = _normalize_locale_code(source_locale) or "en"

    def _name(row: LanguageRow) -> str:
        return row.name_en or row.display_name or ""

    src_spec: Optional[LocaleSpec] = None
    for r in languages:
        if _normalize_locale_code(r.code) == src:
            src_spec = LocaleSpec(code=src, name_en=_name(r) or "English")
            break
    if src_spec is None:
        src_spec = LocaleSpec(code=src, name_en="English")

    targets: List[LocaleSpec] = []
    for r in languages:
        c = _normalize_locale_code(r.code)
        if not c or c == src:
            continue
        targets.append(LocaleSpec(code=c, name_en=_name(r)))

    # 再保险：剔除 source
    targets = [t for t in targets if t.code != src]

    # target 去重
    seen = set()
    dedup_targets: List[LocaleSpec] = []
    for t in targets:
        if t.code in seen:
            continue
        seen.add(t.code)
        dedup_targets.append(t)

    return src_spec, tuple(dedup_targets)


# =========================
# YAML load / parse / validate
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
        raise ConfigError("配置文件格式错误：顶层必须是 mapping/object")
    return obj


def _parse_prompts(raw: Dict[str, object]) -> Prompts:
    """
    支持两种输入：
    1) 新格式（严格示例）：prompts.default_en + prompts.by_locale_en
    2) 兼容旧字段：prompt_by_locale（仅用于迁移；生成时仍写 prompts）
    """
    prompts_raw = raw.get("prompts")
    if prompts_raw is None:
        # 兼容旧字段
        pbl = raw.get("prompt_by_locale") or {}
        if not isinstance(pbl, dict):
            return default_prompts()
        by_locale = { _normalize_locale_code(_as_str(k)): _as_str(v) for k, v in pbl.items() }
        return Prompts(default_en=default_prompts().default_en, by_locale_en=by_locale)

    if not isinstance(prompts_raw, dict):
        raise ConfigError("prompts 必须是 object")

    default_en = _as_str(prompts_raw.get("default_en"), "")
    by_locale_raw = prompts_raw.get("by_locale_en") or {}
    if not isinstance(by_locale_raw, dict):
        raise ConfigError("prompts.by_locale_en 必须是 object")

    by_locale_en: Dict[str, str] = {}
    for k, v in by_locale_raw.items():
        by_locale_en[_normalize_locale_code(_as_str(k))] = _as_str(v)

    return Prompts(default_en=default_en, by_locale_en=by_locale_en)


def parse_config_dict(*, root_dir: Path, raw: Dict[str, object]) -> SlangI18nConfig:
    i18n_dir = _as_str(raw.get("i18nDir"), "i18n")
    i18n_path = (root_dir / i18n_dir).resolve()

    src_raw = raw.get("source_locale") or {}
    if not isinstance(src_raw, dict):
        raise ConfigError("source_locale 必须是 object")
    src_code = _normalize_locale_code(_as_str(src_raw.get("code"), "en")) or "en"
    src_name_en = _as_str(src_raw.get("name_en"), "English")
    source_locale = LocaleSpec(code=src_code, name_en=src_name_en)

    tgt_raw = raw.get("target_locales") or []
    if not isinstance(tgt_raw, list):
        raise ConfigError("target_locales 必须是数组 list")
    targets: List[LocaleSpec] = []
    for i, item in enumerate(tgt_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"target_locales[{i}] 必须是 object")
        code = _normalize_locale_code(_as_str(item.get("code")))
        if not code:
            raise ConfigError(f"target_locales[{i}] 缺少 code")
        name_en = _as_str(item.get("name_en"), "")
        targets.append(LocaleSpec(code=code, name_en=name_en))

    # 去掉 target 中与 source 重复（你明确要求）
    targets = [t for t in targets if t.code != source_locale.code]

    # target 去重
    seen = set()
    dedup_targets: List[LocaleSpec] = []
    for t in targets:
        if t.code in seen:
            continue
        seen.add(t.code)
        dedup_targets.append(t)

    openai_model = _as_str(raw.get("openAIModel"), "gpt-4o")

    opt_raw = raw.get("options") or {}
    if not isinstance(opt_raw, dict):
        raise ConfigError("options 必须是 object")

    options = Options(
        sort_keys=bool(opt_raw.get("sort_keys", True)),
        incremental_translate=bool(opt_raw.get("incremental_translate", True)),
        cleanup_extra_keys=bool(opt_raw.get("cleanup_extra_keys", True)),
        normalize_filenames=bool(opt_raw.get("normalize_filenames", True)),
    )

    prompts = _parse_prompts(raw)
    if not prompts.default_en:
        # 没写就给默认
        prompts = Prompts(default_en=default_prompts().default_en, by_locale_en=dict(prompts.by_locale_en))

    return SlangI18nConfig(
        i18n_dir=i18n_path,
        source_locale=source_locale,
        target_locales=tuple(dedup_targets),
        openai_model=openai_model,
        prompts=prompts,
        options=options,
    )


# =========================
# init YAML generation (commented)
# =========================

def _indent_block(text: str, spaces: int) -> str:
    prefix = " " * spaces
    lines = (text or "").rstrip("\n").splitlines()
    if not lines:
        return prefix + ""
    return "\n".join(prefix + ln for ln in lines)


def generate_commented_yaml_template(*, cfg: SlangI18nConfig, languages_json_path: Optional[Path] = None) -> str:
    """
    生成带详细注释的 slang_i18n.yaml。
    注意：手写 YAML，确保注释与 prompts 的格式严格一致。
    """
    lj = f"{languages_json_path}" if languages_json_path else "languages.json"

    tgt_lines: List[str] = []
    for t in cfg.target_locales:
        name = (t.name_en or "").replace('"', '\\"')
        tgt_lines.append(f"  - code: {t.code}\n    name_en: \"{name}\"")

    # prompts 按严格格式输出
    default_en = cfg.prompts.default_en.rstrip("\n")
    by_locale = cfg.prompts.by_locale_en or {}

    by_locale_lines: List[str] = []
    for locale, body in by_locale.items():
        body = (body or "").rstrip("\n")
        by_locale_lines.append(f"    {locale}: |\n{_indent_block(body, 6)}")

    prompts_text = (
            "prompts:\n"
            "  # 通用提示词（所有语言都会追加这个约束）\n"
            "  default_en: |\n"
            f"{_indent_block(default_en, 4)}\n"
            "  # 针对特定语言的附加提示词（仅对该 locale 生效）\n"
            "  by_locale_en:\n"
            + ("\n".join(by_locale_lines) if by_locale_lines else "    {}")
            + "\n"
    )

    return (
            "# slang_i18n.yaml\n"
            "# ---------------------------------------------\n"
            "# Flutter slang 多语言管理与 AI 翻译工具配置\n"
            "# 生成规则：\n"
            f"# - init 会读取当前目录 {lj} 作为语言真理源\n"
            f"# - source_locale 默认 en\n"
            "# - target_locales = languages.json 中除 source 外的其他语言\n"
            "# - 若 target_locales 与 source_locale 重复，会自动剔除 target 中重复项\n"
            "# ---------------------------------------------\n\n"
            "# 多语言根目录：\n"
            "# - 单业务模式：i18nDir 下直接放 {{locale}}.json（例如 en.json）\n"
            "# - 多业务模式：i18nDir 下有子目录（home/trade），每个目录一组 {{prefix}}_{{locale}}.json\n"
            "i18nDir: i18n\n\n"
            "# 源语言（唯一权威语言，默认 en）\n"
            "source_locale:\n"
            f"  code: {cfg.source_locale.code}\n"
            f"  name_en: \"{cfg.source_locale.name_en.replace('\"','\\\\\"')}\"\n\n"
            "# 目标语言列表（由 languages.json 生成）\n"
            "target_locales:\n"
            + ("\n".join(tgt_lines) if tgt_lines else "  []") + "\n\n"
                                                                "# OpenAI 模型名（用于 translate）\n"
                                                                f"openAIModel: {cfg.openai_model}\n\n"
            + prompts_text + "\n"
                             "# 工具行为开关\n"
                             "options:\n"
                             "  # sort：是否对 key 排序（@@locale 永远置顶）\n"
                             f"  sort_keys: {str(cfg.options.sort_keys).lower()}\n"
                             "  # translate：是否默认增量翻译（只翻译 source 有、target 缺的 key）\n"
                             f"  incremental_translate: {str(cfg.options.incremental_translate).lower()}\n"
                             "  # check/clean：是否启用冗余 key 治理（target 比 source 多的 key）\n"
                             f"  cleanup_extra_keys: {str(cfg.options.cleanup_extra_keys).lower()}\n"
                             "  # 是否做文件名规范化（后续如需将 zh-Hant 统一为 zh_Hant，可扩展）\n"
                             f"  normalize_filenames: {str(cfg.options.normalize_filenames).lower()}\n"
    )


# =========================
# Upgrade diff & update
# =========================

@dataclass(frozen=True)
class UpgradeDiff:
    source_code: str
    config_all: Tuple[str, ...]
    languages_all: Tuple[str, ...]
    added: Tuple[str, ...]
    removed: Tuple[str, ...]


def diff_config_with_languages(*, cfg: SlangI18nConfig, languages: Tuple[LanguageRow, ...]) -> UpgradeDiff:
    lang_codes = _dedupe_keep_order([_normalize_locale_code(r.code) for r in languages if _normalize_locale_code(r.code)])
    cfg_codes = _dedupe_keep_order([cfg.source_locale.code] + [t.code for t in cfg.target_locales])

    lang_set = set(lang_codes)
    cfg_set = set(cfg_codes)

    added = tuple([c for c in lang_codes if c not in cfg_set])
    removed = tuple([c for c in cfg_codes if c not in lang_set])

    return UpgradeDiff(
        source_code=cfg.source_locale.code,
        config_all=tuple(cfg_codes),
        languages_all=tuple(lang_codes),
        added=added,
        removed=removed,
    )


def build_cfg_from_languages_json(
        *,
        root_dir: Path,
        i18n_dir: str = "i18n",
        openai_model: str = "gpt-4o",
        source_locale_code: str = "en",
        languages_filename: str = "languages.json",
) -> Tuple[SlangI18nConfig, Path]:
    """
    init 新建配置时使用：
    - 确保 languages.json 存在
    - source_locale 默认 en
    - targets = languages.json 除 source 外的语言
    - prompts 写入默认值（按示例）
    """
    root_dir = root_dir.resolve()
    languages = load_languages_json(root_dir=root_dir, filename=languages_filename)
    src, targets = derive_locales_from_languages_json(languages=languages, source_locale=source_locale_code)

    cfg = SlangI18nConfig(
        i18n_dir=(root_dir / i18n_dir).resolve(),
        source_locale=src,
        target_locales=targets,
        openai_model=openai_model,
        prompts=default_prompts(),
        options=Options(),
    )
    return cfg, (root_dir / languages_filename)


def update_cfg_targets_from_languages_json(*, cfg: SlangI18nConfig, languages: Tuple[LanguageRow, ...]) -> SlangI18nConfig:
    """
    升级配置：
    - source_locale 保持不变（默认 en）
    - target_locales 根据 languages.json 重建（排除 source，并去重）
    - 其他字段保留（i18nDir/openAIModel/options/prompts）
    """
    src_code = cfg.source_locale.code or "en"
    src_spec, targets = derive_locales_from_languages_json(languages=languages, source_locale=src_code)

    # 保留原 source name
    source_locale = LocaleSpec(code=src_spec.code, name_en=cfg.source_locale.name_en or src_spec.name_en or "English")

    return SlangI18nConfig(
        i18n_dir=cfg.i18n_dir,
        source_locale=source_locale,
        target_locales=targets,
        openai_model=cfg.openai_model,
        prompts=cfg.prompts,
        options=cfg.options,
    )

def load_config(*, root_dir: Path, config_path: Optional[Path] = None) -> SlangI18nConfig:
    """
    统一加载配置入口：
    - config_path 为空时，默认使用 root_dir/slang_i18n.yaml
    - 负责 YAML 加载 + parse 校验
    - 失败抛出 ConfigError（供上层打印友好提示）
    """
    path = config_path or default_config_path(root_dir)
    raw = load_config_yaml(path)
    return parse_config_dict(root_dir=root_dir, raw=raw)
