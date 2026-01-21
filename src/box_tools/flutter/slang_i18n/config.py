from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from .models import (
    LocaleSpec,
    Options,
    SlangI18nConfig,
    Issue,
    IssueCode,
    IssueLevel,
    Report,
    META_KEYS,
)


# =========================
# Errors
# =========================

class ConfigError(RuntimeError):
    pass


# =========================
# Helpers
# =========================

def _p(path: str | Path) -> Path:
    return path if isinstance(path, Path) else Path(path)


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
    # 你当前 languages.json 里用的是 zh_Hant 这种风格，这里先“原样保留”
    # 后续如果要强制 normalize（比如 zh-Hant -> zh_Hant），在这里扩展即可
    return _as_str(code)


# =========================
# languages.json
# =========================

@dataclass(frozen=True)
class LanguageRow:
    code: str
    name_en: str = ""
    display_name: str = ""


def load_languages_json(
        *,
        root_dir: Path,
        filename: str = "languages.json",
) -> Tuple[LanguageRow, ...]:
    """
    读取当前目录的 languages.json，作为语言真理源。
    期望格式：list[ {code, name_en?, displayName? ...} ]  (见你提供的示例)。
    """
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
    规则：
    - source_locale 默认 en
    - target_locales = languages.json 中除 source 之外的其他语言
    - 生成后再次检查：target 不得包含 source（去重）
    """
    src = _normalize_locale_code(source_locale) or "en"

    # 取 name_en：优先 name_en，其次 displayName，其次空
    def _name(row: LanguageRow) -> str:
        return row.name_en or row.display_name or ""

    # 构造 locale map（按 languages.json 顺序）
    codes = [_normalize_locale_code(r.code) for r in languages if _normalize_locale_code(r.code)]
    codes = _dedupe_keep_order(codes)

    src_spec = None
    for r in languages:
        if _normalize_locale_code(r.code) == src:
            src_spec = LocaleSpec(code=src, name_en=_name(r) or "English")
            break
    if src_spec is None:
        # languages.json 里没 en 也没关系：仍然固定 source=en
        src_spec = LocaleSpec(code=src, name_en="English")

    targets: List[LocaleSpec] = []
    for r in languages:
        c = _normalize_locale_code(r.code)
        if not c or c == src:
            continue
        targets.append(LocaleSpec(code=c, name_en=_name(r)))

    # 再保险：如果 targets 中仍有 src（异常数据），剔除
    targets = [t for t in targets if t.code != src]

    # 再去重（按出现顺序）
    seen = set()
    dedup_targets: List[LocaleSpec] = []
    for t in targets:
        if t.code in seen:
            continue
        seen.add(t.code)
        dedup_targets.append(t)

    return src_spec, tuple(dedup_targets)


# =========================
# YAML load / validate
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


def parse_config_dict(
        *,
        root_dir: Path,
        raw: Dict[str, object],
) -> SlangI18nConfig:
    """
    将 YAML dict 解析为强类型 Config（做基础校验，不做目录扫描）。
    """
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

    # 去掉 target 中与 source 重复的 code（你明确要求）
    targets = [t for t in targets if t.code != source_locale.code]

    # 目标去重
    seen = set()
    dedup_targets: List[LocaleSpec] = []
    for t in targets:
        if t.code in seen:
            continue
        seen.add(t.code)
        dedup_targets.append(t)

    model_name = _as_str(raw.get("openAIModel"), "gpt-4o")

    prompt_by_locale: Dict[str, str] = {}
    pbl_raw = raw.get("prompt_by_locale") or {}
    if pbl_raw is not None:
        if not isinstance(pbl_raw, dict):
            raise ConfigError("prompt_by_locale 必须是 object")
        for k, v in pbl_raw.items():
            prompt_by_locale[_normalize_locale_code(_as_str(k))] = _as_str(v)

    opt_raw = raw.get("options") or {}
    if not isinstance(opt_raw, dict):
        raise ConfigError("options 必须是 object")

    options = Options(
        sort_keys=bool(opt_raw.get("sort_keys", True)),
        incremental_translate=bool(opt_raw.get("incremental_translate", True)),
        cleanup_extra_keys=bool(opt_raw.get("cleanup_extra_keys", True)),
        normalize_filenames=bool(opt_raw.get("normalize_filenames", True)),
    )

    return SlangI18nConfig(
        i18n_dir=i18n_path,
        source_locale=source_locale,
        target_locales=tuple(dedup_targets),
        openai_model=model_name,
        prompt_by_locale=prompt_by_locale,
        options=options,
    )


def validate_config(cfg: SlangI18nConfig) -> Report:
    """
    结构校验（不触及文件命名推断、不读 JSON）。
    """
    issues: List[Issue] = []

    if not cfg.i18n_dir:
        issues.append(Issue(IssueLevel.ERROR, IssueCode.CONFIG_INVALID, "i18nDir 不能为空"))
    # i18nDir 不一定必须存在（init 可以创建），但 doctor 会更严格
    if not cfg.source_locale.code:
        issues.append(Issue(IssueLevel.ERROR, IssueCode.CONFIG_INVALID, "source_locale.code 不能为空"))

    # 检查重复 locale（source + targets）
    all_codes = [cfg.source_locale.code] + [t.code for t in cfg.target_locales]
    dups = [c for c in _dedupe_keep_order(all_codes) if all_codes.count(c) > 1]
    if dups:
        issues.append(Issue(
            IssueLevel.WARN,
            IssueCode.CONFIG_INVALID,
            f"检测到重复 locale code：{dups}（建议去重；source 不应出现在 target）",
            details={"duplicates": dups},
        ))

    return Report(action="validate_config", issues=tuple(issues))


# =========================
# init: commented YAML template generation
# =========================

def generate_commented_yaml_template(
        *,
        cfg: SlangI18nConfig,
        languages_json_path: Optional[Path] = None,
) -> str:
    """
    生成带详细注释的 slang_i18n.yaml 文本。
    注意：这里手写 YAML 文本（而不是 yaml.dump），以便保证注释可控、可读。
    """
    lj = f"{languages_json_path}" if languages_json_path else "languages.json"
    src = cfg.source_locale.code
    # target locales dump lines
    tgt_lines = []
    for t in cfg.target_locales:
        name = t.name_en.replace('"', '\\"')
        tgt_lines.append(f"  - code: {t.code}\n    name_en: \"{name}\"")

    # prompt_by_locale lines（只写已有的）
    pbl_lines = []
    if cfg.prompt_by_locale:
        for k, v in cfg.prompt_by_locale.items():
            vv = (v or "").rstrip("\n")
            pbl_lines.append(f"  {k}: |\n" + "\n".join([f"    {line}" for line in vv.splitlines()]))

    return (
            "# slang_i18n.yaml\n"
            "# ---------------------------------------------\n"
            "# Flutter slang 多语言管理与 AI 翻译工具配置\n"
            "# 生成规则：\n"
            f"# - init 会读取当前目录 {lj} 作为语言真理源\n"
            f"# - source_locale 默认 {src}\n"
            "# - target_locales = languages.json 中除 source 外的其他语言\n"
            "# ---------------------------------------------\n\n"
            "# 多语言根目录：\n"
            "# - 单业务模式：i18nDir 下直接放 {{locale}}.json（例如 en.json）\n"
            "# - 多业务模式：i18nDir 下有子目录（home/trade），每个目录一组 {{prefix}}_{{locale}}.json\n"
            "i18nDir: i18n\n\n"
            "# 源语言（唯一权威语言，默认 en）\n"
            "source_locale:\n"
            f"  code: {cfg.source_locale.code}\n"
            f"  name_en: \"{cfg.source_locale.name_en.replace('\"','\\\\\"')}\"\n\n"
            "# 目标语言列表（由 languages.json 生成；会自动剔除与 source 重复的 code）\n"
            "target_locales:\n"
            + ("\n".join(tgt_lines) if tgt_lines else "  []") + "\n\n"
                                                                "# OpenAI 模型名（用于 translate）\n"
                                                                f"openAIModel: {cfg.openai_model}\n\n"
                                                                "# 按语言附加翻译提示（可选）：用于约束特定语言的口吻/术语\n"
                                                                "# 例如：zh_hant 使用台湾用语；ja 使用更自然的日语 UI 表达\n"
                                                                "prompt_by_locale:\n"
            + ("\n".join(pbl_lines) if pbl_lines else "  {}") + "\n\n"
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
# init: reconcile existing config with languages.json
# =========================

@dataclass(frozen=True)
class UpgradeDiff:
    source_code: str
    config_all: Tuple[str, ...]
    languages_all: Tuple[str, ...]
    added: Tuple[str, ...]    # languages.json 有，但 config 没有
    removed: Tuple[str, ...]  # config 有，但 languages.json 没有


def diff_config_with_languages(
        *,
        cfg: SlangI18nConfig,
        languages: Tuple[LanguageRow, ...],
) -> UpgradeDiff:
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
        prompt_by_locale: Optional[Dict[str, str]] = None,
        options: Optional[Options] = None,
        source_locale_code: str = "en",
        languages_filename: str = "languages.json",
) -> Tuple[SlangI18nConfig, Path]:
    """
    init 生成新配置时使用：读取 languages.json 并产出 cfg。
    """
    root_dir = root_dir.resolve()
    languages = load_languages_json(root_dir=root_dir, filename=languages_filename)
    src, targets = derive_locales_from_languages_json(languages=languages, source_locale=source_locale_code)

    cfg = SlangI18nConfig(
        i18n_dir=(root_dir / i18n_dir).resolve(),
        source_locale=src,
        target_locales=targets,
        openai_model=openai_model,
        prompt_by_locale=prompt_by_locale or {},
        options=options or Options(),
    )
    return cfg, (root_dir / languages_filename)


def update_cfg_targets_from_languages_json(
        *,
        cfg: SlangI18nConfig,
        languages: Tuple[LanguageRow, ...],
) -> SlangI18nConfig:
    """
    升级配置时使用：
    - source_locale 保持不变（默认 en）
    - target_locales 根据 languages.json 重新生成（排除 source，并去重）
    - 其他字段（i18nDir/openAIModel/options/prompt_by_locale）保持原样
    """
    src_code = cfg.source_locale.code or "en"
    src_spec, targets = derive_locales_from_languages_json(languages=languages, source_locale=src_code)

    # source 仍沿用 cfg 的 name_en（避免被 languages.json 覆盖）
    source_locale = LocaleSpec(code=src_spec.code, name_en=cfg.source_locale.name_en or src_spec.name_en or "English")

    return SlangI18nConfig(
        i18n_dir=cfg.i18n_dir,
        source_locale=source_locale,
        target_locales=targets,
        openai_model=cfg.openai_model,
        prompt_by_locale=dict(cfg.prompt_by_locale),
        options=cfg.options,
    )
